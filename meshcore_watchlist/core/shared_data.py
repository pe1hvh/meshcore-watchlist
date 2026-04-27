"""
Thread-safe shared data container for meshcore-watchlist.

Slimmed-down counterpart of meshcore-gui's SharedData.  Holds only
messages, rx_log and watchlist channels — no device state, contacts,
pin store, BBS, bot or room servers.

The structure of public methods (add_message, add_rx_log,
get_snapshot, *_updated flags) mirrors meshcore-gui so the copied
public_api_service consumes it without changes.
"""

import threading
from typing import Dict, List, Optional

from meshcore_watchlist.config import debug_print
from meshcore_watchlist.core.models import Message, RxLogEntry
from meshcore_watchlist.services.message_archive import MessageArchive


class SharedData:
    """Thread-safe container for watchlist messages and rx-log entries."""

    # In-memory caps (mirrors meshcore-gui defaults)
    MAX_MESSAGES = 500
    MAX_RX_LOG = 50

    def __init__(self, archive_id: str = "watchlist") -> None:
        self.lock = threading.Lock()

        # Connection status (always "connected" — we tail a file)
        self.connected: bool = True
        self.status: str = "Tailing meshcore-gui archive"

        # Watchlist channels (populated by WatchlistStore at startup
        # and on every add/remove).  Format mirrors meshcore-gui's
        # device channel list: list of {idx, name} dicts.
        self.channels: List[Dict] = []

        # Data collections
        self.messages: List[Message] = []
        self.rx_log: List[RxLogEntry] = []

        # Dedup fingerprints to suppress reruns after JSONL truncate.
        self._message_fingerprints: set = set()
        self._rxlog_hashes: set = set()

        # Update flags for GUI render loop.
        self.channels_updated: bool = True
        self.rxlog_updated: bool = True
        self.messages_updated: bool = True

        # Persistent archive (own directory under ~/.meshcore-watchlist).
        self.archive: Optional[MessageArchive] = MessageArchive(archive_id)
        debug_print(f"MessageArchive initialized for {archive_id}")

        # Replay messages and rx-log from archive on startup so the GUI
        # is populated immediately (mirrors meshcore-gui behaviour).
        self._load_from_archive()

    # ------------------------------------------------------------------
    # Channel state (driven by WatchlistStore)
    # ------------------------------------------------------------------

    def set_channels(self, channels: List[Dict]) -> None:
        """Replace channel list (called by WatchlistStore on every change)."""
        with self.lock:
            self.channels = list(channels)
            self.channels_updated = True

    def get_channels(self) -> List[Dict]:
        with self.lock:
            return list(self.channels)

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def add_message(self, msg: Message) -> None:
        """Append a message, persist to archive, dedup by fingerprint."""
        fp = (
            msg.message_hash or "",
            msg.sender,
            msg.text,
            msg.channel,
        )
        with self.lock:
            if fp in self._message_fingerprints:
                return
            self._message_fingerprints.add(fp)

            self.messages.append(msg)
            if len(self.messages) > self.MAX_MESSAGES:
                self.messages = self.messages[-self.MAX_MESSAGES:]
            self.messages_updated = True

        if self.archive:
            self.archive.add_message(msg)

    def get_messages(self) -> List[Message]:
        with self.lock:
            return list(self.messages)

    # ------------------------------------------------------------------
    # RX log
    # ------------------------------------------------------------------

    def add_rx_log(self, entry: RxLogEntry) -> None:
        """Append an RX log entry, persist to archive, dedup by hash."""
        with self.lock:
            if entry.message_hash and entry.message_hash in self._rxlog_hashes:
                return
            if entry.message_hash:
                self._rxlog_hashes.add(entry.message_hash)

            self.rx_log.append(entry)
            if len(self.rx_log) > self.MAX_RX_LOG:
                self.rx_log = self.rx_log[-self.MAX_RX_LOG:]
            self.rxlog_updated = True

        if self.archive:
            self.archive.add_rx_log(entry)

    def get_rx_log(self) -> List[RxLogEntry]:
        with self.lock:
            return list(self.rx_log)

    # ------------------------------------------------------------------
    # Snapshot for GUI render loop
    # ------------------------------------------------------------------

    def get_snapshot(self) -> Dict:
        """Return a snapshot dict; matches the subset of meshcore-gui
        fields that the watchlist UI needs."""
        with self.lock:
            return {
                "connected": self.connected,
                "status": self.status,
                "channels": list(self.channels),
                "messages": list(self.messages),
                "rx_log": list(self.rx_log),
                "channels_updated": self.channels_updated,
                "messages_updated": self.messages_updated,
                "rxlog_updated": self.rxlog_updated,
            }

    def clear_update_flags(self) -> None:
        with self.lock:
            self.channels_updated = False
            self.messages_updated = False
            self.rxlog_updated = False

    # ------------------------------------------------------------------
    # Stubs / compatibility shims for copied public_api_service
    # ------------------------------------------------------------------

    def get_device_name(self) -> str:
        return "meshcore-watchlist"

    def get_contact_name_by_prefix(self, _prefix: str) -> str:
        return ""

    def get_contact_by_name(self, _name: str):
        return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_from_archive(self) -> None:
        """Populate in-memory caches from the archive on startup.

        Reads the archive JSON files directly to avoid coupling to
        meshcore-gui-specific helper methods.  Errors are logged and
        non-fatal.
        """
        if not self.archive:
            return

        # Messages
        try:
            msgs_path = self.archive._messages_path  # noqa: SLF001
            if msgs_path.exists():
                import json as _json
                from dataclasses import fields as _fields
                data = _json.loads(msgs_path.read_text(encoding="utf-8"))
                raw = data.get("messages", [])[-self.MAX_MESSAGES:]
                msg_field_names = {f.name for f in _fields(Message)}
                for d in raw:
                    kwargs = {k: v for k, v in d.items() if k in msg_field_names}
                    try:
                        m = Message(**kwargs)
                    except TypeError:
                        continue
                    fp = (
                        m.message_hash or "",
                        m.sender,
                        m.text,
                        m.channel,
                    )
                    self._message_fingerprints.add(fp)
                    self.messages.append(m)
        except Exception as exc:
            debug_print(f"Archive replay (messages) error: {exc}")

        # RX log
        try:
            rx_path = self.archive._rxlog_path  # noqa: SLF001
            if rx_path.exists():
                import json as _json
                from dataclasses import fields as _fields
                data = _json.loads(rx_path.read_text(encoding="utf-8"))
                raw = data.get("entries", [])[-self.MAX_RX_LOG:]
                rx_field_names = {f.name for f in _fields(RxLogEntry)}
                for d in raw:
                    kwargs = {k: v for k, v in d.items() if k in rx_field_names}
                    try:
                        r = RxLogEntry(**kwargs)
                    except TypeError:
                        continue
                    if r.message_hash:
                        self._rxlog_hashes.add(r.message_hash)
                    self.rx_log.append(r)
        except Exception as exc:
            debug_print(f"Archive replay (rx_log) error: {exc}")

        debug_print(
            f"Loaded {len(self.messages)} msgs / "
            f"{len(self.rx_log)} rx entries from archive"
        )
