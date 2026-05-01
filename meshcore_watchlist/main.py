"""
meshcore-watchlist — main entrypoint.

Wires the watchlist store, packet decoder, JSONL tailer, shared data,
public REST API and NiceGUI dashboard together.

Pipeline::

    [meshcore-gui *_rxlog.jsonl]
              │
        JsonlTailer (poll, byte-offset)
              │  raw entry dict (incl. raw_payload, hops, snr, ...)
              ▼
        PacketPipeline.handle_entry()
              │
              ├─ Always: persist as RxLogEntry to SharedData/archive
              │
              └─ If GroupText decrypts with a watchlist key:
                  build Message → SharedData/archive

Identity model (ADR-001).  The decoder is keyed on channel *name*.
The pipeline maps that name to a current watchlist idx after the
decode pass, purely for display in ``Message.channel``.  When the
user has deleted the channel between decode and ingest the idx is
``None`` — the message is still archived, just without a current
watchlist position.
"""

from __future__ import annotations

from typing import Dict, List

from nicegui import ui

from meshcore_watchlist.config import (
    HOST,
    PORT,
    PUBLIC_CHANNEL_SECRET,
    VERSION,
    debug_print,
    is_public_channel_name,
)
from meshcore_watchlist.api.routes import register_routes
from meshcore_watchlist.core.models import Message, RxLogEntry
from meshcore_watchlist.core.shared_data import SharedData
from meshcore_watchlist.decoder.packet_decoder import PacketDecoder
from meshcore_watchlist.gui.dashboard import build_dashboard
from meshcore_watchlist.services.archive_rescanner import (
    ArchiveRescanner,
    RescanJobManager,
)
from meshcore_watchlist.services.jsonl_tailer import JsonlTailer
from meshcore_watchlist.services.watchlist_store import WatchlistStore


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class PacketPipeline:
    """Bridges JsonlTailer → PacketDecoder → SharedData."""

    def __init__(
        self,
        shared: SharedData,
        decoder: PacketDecoder,
        store: WatchlistStore,
    ) -> None:
        self._shared = shared
        self._decoder = decoder
        self._store = store

        # Names currently registered in the decoder.  Used by
        # ``_on_watchlist_changed`` to compute add / remove deltas
        # against the new watchlist snapshot.  Keeping this set on the
        # pipeline (instead of asking the decoder) keeps the decoder
        # ignorant of watchlist semantics — the decoder just owns its
        # key registry; the pipeline owns the watchlist→decoder
        # synchronisation.
        self._registered_names: set = set()

        # Subscribe to watchlist changes: delta-update decoder keys and
        # propagate the channel list to SharedData so the GUI tabs
        # render.  Per §7 of ontwerp 0.2.6, mutations during a running
        # rescan must keep the decoder current — clear+rebuild would
        # leave a transient empty-key window which the rescan worker
        # could land in; delta-update never empties the registry.
        store.subscribe(self._on_watchlist_changed)

    # ------------------------------------------------------------------
    # Subscriber callback
    # ------------------------------------------------------------------

    def _on_watchlist_changed(self, channels: List[Dict]) -> None:
        """Sync decoder keys to the new watchlist via add/remove deltas.

        Per ontwerp 0.2.6 §7 the decoder's ``_secret_to_name`` is kept
        live during a running rescan: a freshly-added channel must
        decode the very next record processed, a freshly-removed
        channel must stop matching at all.  Delta-update via
        ``add_channel_key`` / ``remove_channel_key`` (both specified
        in §2 of the ontwerp) achieves that without ever leaving the
        registry empty.

        Note on naming changes (rename of an existing channel):
        :class:`WatchlistStore` does not currently expose a rename
        operation — channels can only be added or removed — so a
        rename surfaces here as a remove of the old name plus an add
        of the new name.  Both deltas are applied; no special-case
        needed.
        """
        new_names: set = set()
        for ch in channels:
            name = ch.get("name", "")
            if not name:
                continue
            new_names.add(name)

        # Removals first.  If a name is being replaced (rename:
        # remove old, add new) doing remove first means the
        # _secret_to_name dict never holds two entries for the same
        # logical channel even mid-update.
        for name in self._registered_names - new_names:
            self._decoder.remove_channel_key(name)

        # Additions.  Public uses a fixed well-known secret, not the
        # SHA-256(name)[:16] derivation that hashtag channels use.
        # See PUBLIC_CHANNEL_SECRET in config.py.
        for name in new_names - self._registered_names:
            if is_public_channel_name(name):
                self._decoder.add_channel_key(
                    name, PUBLIC_CHANNEL_SECRET, source="public-default",
                )
            else:
                self._decoder.add_channel_key_from_name(name)

        self._registered_names = new_names

        # Push channel list into SharedData so the GUI tabs render.
        self._shared.set_channels(channels)

    # ------------------------------------------------------------------
    # Entry handler (called by JsonlTailer)
    # ------------------------------------------------------------------

    def handle_entry(self, rec: Dict) -> None:
        """Process one JSONL record from meshcore-gui's RX stream."""
        raw_payload = rec.get("raw_payload") or ""

        # Build the RxLogEntry first — always stored, regardless of
        # decode success.  Field set mirrors meshcore-gui::on_rx_log.
        rx_entry = RxLogEntry(
            time=rec.get("time", ""),
            snr=float(rec.get("snr", 0) or 0),
            rssi=float(rec.get("rssi", 0) or 0),
            payload_type=rec.get("payload_type", "?"),
            hops=int(rec.get("hops", 0) or 0),
            message_hash=rec.get("message_hash", "") or "",
            path_hashes=list(rec.get("path_hashes") or []),
            path_names=list(rec.get("path_names") or []),
            sender=rec.get("sender", "") or "",
            receiver=rec.get("receiver", "") or "",
            raw_payload=raw_payload,
            packet_len=int(rec.get("packet_len", 0) or 0),
            payload_len=int(rec.get("payload_len", 0) or 0),
            route_type=rec.get("route_type", "") or "",
            packet_type_num=int(rec.get("packet_type_num", -1) or -1),
        )
        self._shared.add_rx_log(rx_entry)

        # If we have keys, attempt decryption.  Successful decrypt of
        # a GroupText packet → store as Message.
        if not raw_payload or not self._decoder.has_keys:
            return

        # Live tail = no scope, no priority signal — the decoder
        # iterates its registry in dict-iteration order.  Per
        # ontwerp §3 the live decode path is unchanged from 0.2.5
        # for the external observer.
        decoded = self._decoder.decode(raw_payload)
        if decoded is None or not decoded.is_decrypted:
            return
        if not decoded.channel_name:
            # Defensive: a successfully-decrypted GroupText always
            # carries a channel_name set from the matching key.  This
            # check guards against a future decoder regression where
            # is_decrypted flips True without the name being populated.
            return

        # Build the idx-by-name lookup against the *current* watchlist
        # at ingest time.  ``channel_name`` is the identity (ADR-001);
        # ``channel`` (idx) is a derived display attribute and may be
        # ``None`` if the user has just removed the channel between
        # decode and this line.
        channels = self._store.list_channels()
        idx_by_name = {ch.get("name", ""): ch.get("idx") for ch in channels}
        idx = idx_by_name.get(decoded.channel_name)

        msg = Message.incoming(
            sender=decoded.sender,
            text=decoded.text,
            channel=idx,
            time=rx_entry.time,
            snr=rx_entry.snr,
            path_len=decoded.path_length,
            path_hashes=decoded.path_hashes,
            path_names=rx_entry.path_names,
            message_hash=decoded.message_hash,
        )
        msg.channel_name = decoded.channel_name

        self._shared.add_message(msg)
        debug_print(
            f"Decoded GroupText: channel={decoded.channel_name!r} "
            f"(idx={idx}), sender={decoded.sender!r}, "
            f"text={decoded.text[:40]!r}"
        )


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def main() -> None:
    debug_print(f"meshcore-watchlist v{VERSION} starting on {HOST}:{PORT}")

    # Core services
    store = WatchlistStore()
    shared = SharedData()
    decoder = PacketDecoder()
    pipeline = PacketPipeline(shared, decoder, store)

    # Tailer
    tailer = JsonlTailer(callback=pipeline.handle_entry)
    tailer.start()

    # Rescanner: runs on demand via the REST endpoint or the GUI
    # button.  Independent of the live tailer — does not touch
    # state.json cursors.
    rescanner = ArchiveRescanner(shared=shared, decoder=decoder, store=store)
    rescan_manager = RescanJobManager(rescanner, store=store)

    # GUI
    build_dashboard(shared=shared, store=store, rescan_manager=rescan_manager)

    # Public REST API (/api/v1/...)
    register_routes(shared, rescan_manager=rescan_manager)

    # NiceGUI run.  ``reload=False`` because we manage long-lived
    # background threads (the tailer) and reload would orphan them.
    ui.run(
        host=HOST,
        port=PORT,
        title=f"meshcore-watchlist v{VERSION}",
        reload=False,
        show=False,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
