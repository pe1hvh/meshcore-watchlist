"""
Watchlist store — persistent CRUD over ``~/.meshcore-watchlist/watchlist.json``.

The file format mirrors meshcore-gui's device channel list so the same
UI structure can be reused.  Each entry has a stable ``idx`` (= position
in the list) and a ``name``.

Channel kinds:

* **Public** — single, system-managed entry.  Always present, always
  at ``idx == 0``, name always equal to
  :data:`meshcore_watchlist.config.PUBLIC_CHANNEL_CANONICAL_NAME`.
  Cannot be added (already there) or removed (rejected).  This
  matches meshcore-gui's device behaviour: the firmware reserves
  channel slot 0 for Public.
* **Hashtag** — user-managed.  Names are forced to start with ``#``
  and the decryption key is derived deterministically as
  ``SHA-256(name)[:16]``.

The Public-channel decryption key is **not** derived from its name —
it is a fixed well-known 16-byte secret defined in
:data:`meshcore_watchlist.config.PUBLIC_CHANNEL_SECRET`.  See
``main.py::PacketPipeline._on_watchlist_changed`` for where that
secret is registered with the decoder.

File schema::

    {
        "version": 1,
        "channels": [
            {"idx": 0, "name": "Public"},
            {"idx": 1, "name": "#mc-radar"},
            {"idx": 2, "name": "#weather"}
        ]
    }
"""

import json
import threading
from hashlib import sha256
from pathlib import Path
from typing import Callable, Dict, List, Optional

from meshcore_watchlist.config import (
    PUBLIC_CHANNEL_CANONICAL_NAME,
    WATCHLIST_FILE,
    WATCHLIST_HOME,
    debug_print,
    is_public_channel_name,
)

WATCHLIST_VERSION = 1

# Per ADR-007: a channel name accepted by meshcore-watchlist is a
# UTF-8 string whose encoded length is at most 32 bytes — the size of
# the Channel Name field in the MeshCore Companion Protocol's
# CMD_SET_CHANNEL (0x20).  Names that do not fit in 32 UTF-8 bytes can
# never be activated on a real device.
CHANNEL_NAME_MAX_BYTES = 32


def derive_key(name: str) -> bytes:
    """Derive the 16-byte channel secret from a channel name."""
    return sha256(name.encode("utf-8")).digest()[:16]


class WatchlistStore:
    """Manages the watchlist.json file and notifies subscribers on change.

    Args:
        path: Override location of the watchlist file (default: config).
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or WATCHLIST_FILE
        self._lock = threading.Lock()
        self._channels: List[Dict] = []
        self._subscribers: List[Callable[[List[Dict]], None]] = []
        self._load()

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Read watchlist.json from disk; create an empty file if missing.

        Always finishes with the Public-channel invariant enforced
        (see :meth:`_ensure_public_invariant_locked`).  Persists the
        result if anything had to be changed, so the file on disk
        always reflects the canonical layout after a service start.
        """
        WATCHLIST_HOME.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._channels = []
            self._ensure_public_invariant_locked()
            self._save_locked()
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if data.get("version") != WATCHLIST_VERSION:
                debug_print(
                    f"WatchlistStore: version mismatch in {self._path}, "
                    f"starting empty"
                )
                self._channels = []
            else:
                self._channels = list(data.get("channels", []))
                # Re-index defensively in case the file was hand-edited.
                for i, ch in enumerate(self._channels):
                    ch["idx"] = i
            before = [dict(c) for c in self._channels]
            self._ensure_public_invariant_locked()
            if self._channels != before:
                self._save_locked()
            debug_print(
                f"WatchlistStore: loaded {len(self._channels)} channels "
                f"from {self._path}"
            )
        except (json.JSONDecodeError, OSError) as exc:
            debug_print(f"WatchlistStore: load error: {exc}; starting empty")
            self._channels = []
            self._ensure_public_invariant_locked()

    def _ensure_public_invariant_locked(self) -> None:
        """Make sure exactly one Public entry exists at ``idx == 0``.

        Caller MUST hold ``self._lock`` (or be running before any
        threads exist, as in ``__init__``).

        Tolerates legacy and hand-edited files: any entry whose name
        matches :func:`is_public_channel_name` (case-insensitive,
        ``#``-tolerant) is treated as the Public channel.  If multiple
        such entries exist, the first is kept and the others are
        dropped.  The kept entry is renamed to the canonical form
        and moved to position 0.  All entries are then re-indexed.

        If no Public entry exists, one is inserted at the front.
        """
        public_entries: List[Dict] = [
            ch for ch in self._channels if is_public_channel_name(ch.get("name", ""))
        ]
        non_public_entries: List[Dict] = [
            ch for ch in self._channels if not is_public_channel_name(ch.get("name", ""))
        ]

        if public_entries:
            kept = public_entries[0]
            kept["name"] = PUBLIC_CHANNEL_CANONICAL_NAME
            if len(public_entries) > 1:
                debug_print(
                    f"WatchlistStore: dropping {len(public_entries) - 1} "
                    f"duplicate Public entries"
                )
        else:
            kept = {"name": PUBLIC_CHANNEL_CANONICAL_NAME}
            debug_print(
                "WatchlistStore: Public entry missing, inserting at idx=0"
            )

        self._channels = [kept] + non_public_entries
        for i, ch in enumerate(self._channels):
            ch["idx"] = i

    def _save_locked(self) -> None:
        """Persist current channel list (caller MUST hold the lock)."""
        WATCHLIST_HOME.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        payload = {
            "version": WATCHLIST_VERSION,
            "channels": self._channels,
        }
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(self._path)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list_channels(self) -> List[Dict]:
        """Return a copy of the current channel list."""
        with self._lock:
            return [dict(c) for c in self._channels]

    def add(self, name: str) -> bool:
        """Add a channel by name.

        Public is system-managed and always present at ``idx == 0``,
        so ``add("Public")`` (any case, optional ``#``) is a no-op
        that returns ``True`` — the channel is already on the
        watchlist by virtue of the invariant, and reporting that
        as a user error would be confusing.

        For any other channel, the leading ``#`` is enforced (added
        if missing).  Duplicates (case-sensitive name match) are
        rejected silently and return ``False``.

        Names whose UTF-8 encoding exceeds
        :data:`CHANNEL_NAME_MAX_BYTES` (32 bytes) are also rejected —
        per ADR-007 and the MeshCore Companion Protocol, names that
        do not fit in the on-wire Channel Name field cannot
        correspond to a real channel.  Length is in **bytes**, not
        codepoints (e.g. ``#café`` is 6 bytes, not 5).
        """
        name = name.strip()
        if not name:
            return False
        if is_public_channel_name(name):
            # Already guaranteed present by the invariant — no work,
            # not an error.
            return True
        if not name.startswith("#"):
            name = "#" + name

        # Enforce the protocol-bounded length (ADR-007).  We count
        # bytes after enforcing the leading '#', so callers passing
        # an unprefixed 32-byte name don't overshoot once we add the
        # '#' on their behalf.
        if len(name.encode("utf-8")) > CHANNEL_NAME_MAX_BYTES:
            debug_print(
                f"WatchlistStore: rejected '{name}' — "
                f"{len(name.encode('utf-8'))} bytes UTF-8 exceeds "
                f"{CHANNEL_NAME_MAX_BYTES} (ADR-007)"
            )
            return False

        with self._lock:
            for ch in self._channels:
                if ch.get("name") == name:
                    return False
            new_idx = len(self._channels)
            self._channels.append({"idx": new_idx, "name": name})
            self._save_locked()
            snapshot = [dict(c) for c in self._channels]

        self._notify(snapshot)
        debug_print(f"WatchlistStore: added '{name}' at idx {new_idx}")
        return True

    def remove(self, idx: int) -> bool:
        """Remove the channel at the given index, then reindex.

        Public (``idx == 0``) is system-managed and cannot be
        removed; calls targeting it return ``False`` and leave the
        list unchanged.  Defensively, any entry whose name matches
        :func:`is_public_channel_name` is also protected, in case
        the on-disk file ever drifts from the canonical layout.
        """
        with self._lock:
            if not (0 <= idx < len(self._channels)):
                return False
            target = self._channels[idx]
            if idx == 0 or is_public_channel_name(target.get("name", "")):
                debug_print(
                    f"WatchlistStore: refused to remove Public "
                    f"(idx={idx}, name={target.get('name')!r})"
                )
                return False
            removed = self._channels.pop(idx)
            for i, ch in enumerate(self._channels):
                ch["idx"] = i
            self._save_locked()
            snapshot = [dict(c) for c in self._channels]

        self._notify(snapshot)
        debug_print(f"WatchlistStore: removed '{removed.get('name')}'")
        return True

    # ------------------------------------------------------------------
    # Subscribers
    # ------------------------------------------------------------------

    def subscribe(self, callback: Callable[[List[Dict]], None]) -> None:
        """Register a callback invoked on every list change.

        The callback is also invoked once immediately with the current
        list, so subscribers do not need a separate priming step.
        """
        self._subscribers.append(callback)
        callback(self.list_channels())

    def _notify(self, snapshot: List[Dict]) -> None:
        for cb in self._subscribers:
            try:
                cb(snapshot)
            except Exception as exc:
                debug_print(f"WatchlistStore: subscriber error: {exc}")

    # ------------------------------------------------------------------
    # Key map (used by PacketDecoder population)
    # ------------------------------------------------------------------

    def key_map(self) -> Dict[int, bytes]:
        """Return ``{idx: secret_bytes}`` for the current watchlist."""
        with self._lock:
            return {ch["idx"]: derive_key(ch["name"]) for ch in self._channels}
