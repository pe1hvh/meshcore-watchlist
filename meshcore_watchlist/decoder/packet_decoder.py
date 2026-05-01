"""
Packet decoder for MeshCore GUI — single-source approach.

Wraps ``meshcoredecoder`` to decode raw LoRa packets from RX_LOG_DATA
events.  A single raw packet contains **everything**: message_hash,
path hashes, hop count, and (with channel keys) the decrypted text
and sender name.

No correlation with CHANNEL_MSG_RECV events is needed.

Channel attribution
~~~~~~~~~~~~~~~~~~~
The channel a message belongs to is determined by **which registered key
successfully decrypts the payload** — not by any channel index or the
``channel_hash`` embedded in the packet header.  The firmware-embedded
``channel_hash`` is ignored for attribution because ``ChannelCrypto``
and the firmware may compute it differently, making hash-based lookup
unreliable.

Decryption is therefore attempted per-key (one ``MeshCoreDecoder.decode``
call per registered channel).  For a typical deployment with fewer than
ten channels this cost is negligible, and the correct channel is always
identified deterministically.

Channel decryption keys are loaded at startup (fetched from the device
via ``get_channel()`` or derived from the channel name as fallback).

Identity model (ADR-001)
~~~~~~~~~~~~~~~~~~~~~~~~
The decoder identifies channels by **name**, never by watchlist index.
A channel's name is stable across watchlist mutations (add / remove /
reorder); its idx is not.  ``DecodedPacket.channel_name`` carries that
stable identity to the caller.  Mapping the name back to a current
watchlist idx — for display only — is the caller's responsibility and
happens after the decode pass, not inside it.
"""

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Dict, List, Optional

from meshcoredecoder import MeshCoreDecoder
from meshcoredecoder.crypto.key_manager import MeshCoreKeyStore
from meshcoredecoder.types.crypto import DecryptionOptions
from meshcoredecoder.types.enums import PayloadType
from meshcoredecoder.utils.enum_names import get_payload_type_name

from meshcore_watchlist.config import debug_print


# Re-export so other modules don't need to import meshcoredecoder
__all__ = ["PacketDecoder", "DecodedPacket", "PayloadType"]


# ---------------------------------------------------------------------------
# Decoded result
# ---------------------------------------------------------------------------

@dataclass
class DecodedPacket:
    """All data extracted from a single raw LoRa packet.

    Attributes:
        message_hash:  Deterministic packet identifier (hex string).
        payload_type:  Enum (GroupText, Advert, Ack, …).
        path_length:   Number of repeater hashes in the path.
        path_hashes:   2-char hex strings, one per repeater.
        sender:        Sender name (GroupText only, after decryption).
        text:          Message body (GroupText only, after decryption).
        channel_name:  Channel name resolved by key match (GroupText only,
                       after successful decryption).  Stable identity per
                       ADR-001; empty string when ``is_decrypted`` is False.
        timestamp:     Message timestamp (GroupText only).
        is_decrypted:  True if payload was successfully decrypted.
    """

    message_hash: str
    payload_type: PayloadType
    path_length: int
    path_hashes: List[str] = field(default_factory=list)

    # GroupText-specific (populated after successful decryption)
    sender: str = ""
    text: str = ""
    channel_name: str = ""
    timestamp: int = 0
    is_decrypted: bool = False


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------

class PacketDecoder:
    """Decode raw LoRa packets with per-key channel attribution.

    Channel attribution is done by key matching: the registered secret that
    successfully decrypts a GroupText packet identifies its channel.  This
    avoids relying on the ``channel_hash`` mechanism, which requires the
    MeshCore firmware and ``meshcoredecoder`` to compute identical hashes —
    a dependency that cannot always be guaranteed.

    Identity (ADR-001): the decoder keys its registry on the channel
    *name*, not on the watchlist idx.  Idx values are vluchtige
    UI-positions and have no role inside the decoder.

    Usage::

        decoder = PacketDecoder()
        decoder.add_channel_key("Public", secret_bytes)         # device
        decoder.add_channel_key_from_name("#test")              # fallback

        result = decoder.decode(payload_hex)
        if result and result.is_decrypted:
            print(result.sender, result.text, result.channel_name)
    """

    def __init__(self) -> None:
        # secret_hex → channel_name  (primary channel attribution map).
        # Keyed on name per ADR-001; idx is never present in this map.
        self._secret_to_name: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Key management
    # ------------------------------------------------------------------

    def add_channel_key(
        self,
        channel_name: str,
        secret_bytes: bytes,
        source: str = "device",
    ) -> None:
        """Register a channel decryption key (16 raw bytes).

        Args:
            channel_name: Channel name (stable identity per ADR-001).
            secret_bytes: 16-byte channel secret from ``get_channel()``
                or otherwise derived.
            source:       Label for debug output (e.g. "device", "cache").
        """
        secret_hex = secret_bytes.hex()
        self._secret_to_name[secret_hex] = channel_name
        debug_print(
            f"PacketDecoder: key registered for {channel_name!r} "
            f"(source={source}, secret={secret_hex[:8]}…)"
        )

    def add_channel_key_from_name(self, channel_name: str) -> None:
        """Derive a channel key from the channel name (fallback).

        MeshCore derives channel secrets as
        ``SHA-256(name.encode('utf-8'))[:16]``.

        Args:
            channel_name: Channel name string (e.g. ``"#test"``).
        """
        secret_bytes = sha256(channel_name.encode("utf-8")).digest()[:16]
        self.add_channel_key(
            channel_name, secret_bytes, source=f"name '{channel_name}'",
        )

    def remove_channel_key(self, channel_name: str) -> None:
        """Drop the registered key for *channel_name*, if any.

        Called by :meth:`PacketPipeline._on_watchlist_changed` when a
        channel is removed from the watchlist.  The decoder loop must
        no longer attempt that key — both because trying to decode a
        packet against a key the user has just removed wastes work,
        and because §7 of ontwerp 0.2.6 specifies this primitive
        explicitly to keep watchlist mutations during a running rescan
        observable in the decoder.

        No error if the name is not registered: removal is idempotent.
        """
        # Iterate over a snapshot so a concurrent reader (decode() in
        # the live-tail or rescan thread) is not affected by the
        # mutation we're about to do.  At most one secret_hex maps to
        # a given name — the loop exits at the first match.
        for secret_hex, name in list(self._secret_to_name.items()):
            if name == channel_name:
                del self._secret_to_name[secret_hex]
                debug_print(
                    f"PacketDecoder: key removed for {channel_name!r} "
                    f"(secret={secret_hex[:8]}…)"
                )
                return

    @property
    def has_keys(self) -> bool:
        """True if at least one channel key has been registered."""
        return bool(self._secret_to_name)

    # ------------------------------------------------------------------
    # Decode
    # ------------------------------------------------------------------

    def decode(
        self,
        payload_hex: str,
        allowed_name: Optional[str] = None,
        priority_name_order: Optional[List[str]] = None,
    ) -> Optional[DecodedPacket]:
        """Decode a raw LoRa packet hex string.

        Two-phase approach:

        1. Decode packet **structure** without any key: extracts
           ``message_hash``, ``payload_type``, ``path_length`` and
           ``path_hashes``.  These fields are in the unencrypted header.

        2. For GroupText packets, attempt decryption with each registered
           key individually.  The key that produces a valid decryption
           **is** the channel identifier — ``channel_name`` is set
           directly from the matching key's registration.

        Args:
            payload_hex: Hex string from the RX_LOG_DATA event's
                         ``payload`` field.
            allowed_name: If given, only attempt decryption with the
                         key registered for that channel name.  All
                         other registered keys are skipped.  Used by
                         the rescan endpoint
                         ``POST /api/v1/rescan/by-name`` to scope a
                         retroactive decode pass to a single channel.
                         If *allowed_name* is not registered the call
                         returns ``None`` after the structural decode
                         (no exception, no warning — the rescan
                         continues on other records).  ``None``
                         (default) attempts every registered key.
            priority_name_order: Optional list of channel names to
                         try **first**, in the order given.  Any
                         registered key whose name is not in the list
                         is tried after the priority list, in
                         arbitrary dict-iteration order.  Intended for
                         the rescan pad: a domca-API-derived ranking
                         lets the break-on-first-match fire on the
                         dominant channels (~75 % of traffic in top-3)
                         and cuts the per-packet cost from
                         O(N_channels) to O(1) on average for matching
                         packets.  ``None`` (default) preserves
                         dict-iteration order, so the live tail is
                         unaffected.

        Returns:
            :class:`DecodedPacket` on success, ``None`` if the data
            is invalid or too short, or if *allowed_name* was given
            but is not in the decoder's registry.
        """
        if not payload_hex:
            return None

        # Early exit: scoped to a name we don't have a key for.  The
        # rescan path validates *allowed_name* against the live
        # watchlist on submit, but a user delete *between* submit and
        # job-pickup leaves us in this state.  Returning None — not
        # raising — keeps the rescan loop running and the
        # not_decryptable counter doing its job for the rest of the
        # job's records.  Per §5.1 of ontwerp 0.2.6.
        if allowed_name is not None and not any(
            n == allowed_name for n in self._secret_to_name.values()
        ):
            return None

        # ── Phase 1: structural decode (no key required) ──────────────
        try:
            packet = MeshCoreDecoder.decode(payload_hex, None)
        except Exception as exc:
            debug_print(f"PacketDecoder: structural decode error: {exc}")
            return None

        if not packet.is_valid:
            debug_print(f"PacketDecoder: invalid packet: {packet.errors}")
            return None

        result = DecodedPacket(
            message_hash=packet.message_hash,
            payload_type=packet.payload_type,
            path_length=packet.path_length,
            path_hashes=list(packet.path) if packet.path else [],
        )

        # ── Phase 2: per-key decryption (GroupText only) ──────────────
        if packet.payload_type == PayloadType.GroupText and self._secret_to_name:
            # Build the iteration order.  Default (priority_name_order
            # is None) preserves dict-iteration order so the live tail
            # is bit-for-bit unchanged from 0.2.4 / 0.2.5 behaviour.
            # When a priority list is given we yield those entries
            # first (in the given order) and then any remaining keys,
            # so a "no priority signal" channel still gets tried —
            # never silently skipped.
            if priority_name_order is None:
                ordered_items = list(self._secret_to_name.items())
            else:
                name_to_secret: Dict[str, str] = {
                    name: secret_hex
                    for secret_hex, name in self._secret_to_name.items()
                }
                ordered_items = []
                seen: set = set()
                for name in priority_name_order:
                    secret_hex = name_to_secret.get(name)
                    if secret_hex is None or name in seen:
                        continue
                    ordered_items.append((secret_hex, name))
                    seen.add(name)
                # Append any registered key not covered by the
                # priority list.  Without this a freshly-added
                # watchlist channel would never be tried until the
                # next priority refresh, even when the rest of the
                # priority data is stale.
                for secret_hex, name in self._secret_to_name.items():
                    if name in seen:
                        continue
                    ordered_items.append((secret_hex, name))
                    seen.add(name)

            for secret_hex, name in ordered_items:
                if allowed_name is not None and name != allowed_name:
                    continue
                try:
                    ks = MeshCoreKeyStore()
                    ks.add_channel_secrets([secret_hex])
                    opts = DecryptionOptions(key_store=ks)
                    dec_pkt = MeshCoreDecoder.decode(payload_hex, opts)
                    if not dec_pkt.is_valid:
                        continue
                    dec_payload = dec_pkt.payload.get("decoded")
                    if dec_payload and dec_payload.decrypted:
                        d = dec_payload.decrypted
                        result.sender = d.get("sender", "") or ""
                        result.text = d.get("message", "") or ""
                        result.timestamp = d.get("timestamp", 0)
                        result.channel_name = name
                        result.is_decrypted = True
                        debug_print(
                            f"PacketDecoder: GroupText OK — "
                            f"hash={result.message_hash}, "
                            f"sender={result.sender!r}, "
                            f"channel={result.channel_name!r} (key-matched), "
                            f"path={result.path_hashes}, "
                            f"text={result.text[:40]!r}"
                        )
                        break
                except Exception as exc:
                    debug_print(
                        f"PacketDecoder: key for {name!r} error: {exc}"
                    )
                    continue

            if not result.is_decrypted:
                debug_print(
                    f"PacketDecoder: GroupText NOT decrypted "
                    f"(hash={result.message_hash}, "
                    f"{len(self._secret_to_name)} keys tried)"
                )

        return result

    def get_payload_type_text(self, payload_type: PayloadType) -> str:
        """Get human-friendly name for a PayloadType enum value."""
        return get_payload_type_name(payload_type)
