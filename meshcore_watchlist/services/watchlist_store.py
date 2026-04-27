"""
Watchlist store — persistent CRUD over ``~/.meshcore-watchlist/watchlist.json``.

The file format mirrors meshcore-gui's device channel list so the same
UI structure can be reused.  Each entry has a stable ``idx`` (= position
in the list) and a ``name`` (the hashtag including the leading ``#``).
The decryption key is derived deterministically from the name via
``SHA-256(name)[:16]`` and is therefore not stored.

File schema::

    {
        "version": 1,
        "channels": [
            {"idx": 0, "name": "#mc-radar"},
            {"idx": 1, "name": "#weather"}
        ]
    }
"""

import json
import threading
from hashlib import sha256
from pathlib import Path
from typing import Callable, Dict, List, Optional

from meshcore_watchlist.config import WATCHLIST_FILE, WATCHLIST_HOME, debug_print

WATCHLIST_VERSION = 1


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
        """Read watchlist.json from disk; create an empty file if missing."""
        WATCHLIST_HOME.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._channels = []
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
                return
            self._channels = list(data.get("channels", []))
            # Re-index defensively in case the file was hand-edited.
            for i, ch in enumerate(self._channels):
                ch["idx"] = i
            debug_print(
                f"WatchlistStore: loaded {len(self._channels)} channels "
                f"from {self._path}"
            )
        except (json.JSONDecodeError, OSError) as exc:
            debug_print(f"WatchlistStore: load error: {exc}; starting empty")
            self._channels = []

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
        """Add a hashtag channel by name.

        The leading ``#`` is enforced (added if missing).  Duplicates
        (case-sensitive name match) are rejected silently and return
        ``False``.
        """
        name = name.strip()
        if not name:
            return False
        if not name.startswith("#"):
            name = "#" + name

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
        """Remove the channel at the given index, then reindex."""
        with self._lock:
            if not (0 <= idx < len(self._channels)):
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
