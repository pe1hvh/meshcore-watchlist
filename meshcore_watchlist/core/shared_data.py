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

        # Retention sweep first, replay second.  The sweep bounds both
        # archive files to the configured retention window, which in
        # turn bounds the fingerprint scan performed by
        # :meth:`_load_from_archive`.
        self.run_retention_cleanup()

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
        """Append a message, persist to archive, dedup by fingerprint.

        Fingerprint is keyed off ``channel_name``, not ``channel`` (the
        int idx).  See module-level invariant: ``channel_name`` is the
        stable channel identity; ``channel`` is a vluchtige UI-positie
        en mag nooit deel zijn van een identity-key.  When a watchlist
        is reordered, an unchanged channel keeps its name but gets a
        new idx, and a fingerprint that included idx would re-ingest
        every historical message under the new number.
        """
        fp = (
            msg.message_hash or "",
            msg.sender,
            msg.text,
            msg.channel_name,
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
    # Rescan support
    # ------------------------------------------------------------------

    def ingest_rescanned_rxlog(
        self,
        entry: RxLogEntry,
        archive_rxlog_hashes: set,
        timestamp_utc: Optional[str] = None,
    ) -> bool:
        """Persist an rxlog entry from the rescan job, deduped against
        the **full archive** rather than the 50-entry in-memory ring.

        SharedData's normal :meth:`add_rx_log` deduplicates against
        ``_rxlog_hashes``, which is seeded from only the most recent
        :data:`MAX_RX_LOG` archive entries at startup.  That window
        is too small to suppress duplicates when a rescan reprocesses
        days of history, so the rescan job loads the full archive hash
        set up-front and passes it in here.

        The in-memory ring buffer is **not** updated — the "live"
        Messages and RX Log tabs should keep showing recent traffic,
        not get flooded with re-ingested historical entries.  Callers
        that want the in-memory caches to reflect new archive contents
        should call :meth:`reload_caches_from_archive` once, after the
        whole rescan job finishes.

        Args:
            entry: RxLogEntry built from the historical JSONL line.
            archive_rxlog_hashes: Set of message_hash strings already
                in the archive at the start of the rescan job.  This
                method **mutates** the set: newly persisted hashes are
                added so subsequent duplicates within the same job are
                also suppressed.
            timestamp_utc: ISO-8601 UTC timestamp to record on the
                archive row.  Should be the **original** packet
                arrival time (derived from the rxlog source by the
                rescanner), not the moment the rescan happens to be
                running, otherwise downstream consumers that sort or
                filter by ``timestamp_utc`` see all rescanned rows
                clustered at the rescan moment.

        Returns:
            ``True`` if the entry was new and persisted, ``False`` if
            it was a duplicate of an already-archived entry.
        """
        if entry.message_hash and entry.message_hash in archive_rxlog_hashes:
            return False
        if entry.message_hash:
            archive_rxlog_hashes.add(entry.message_hash)
        if self.archive:
            self.archive.add_rx_log(entry, timestamp_utc=timestamp_utc)
        return True

    def ingest_rescanned_message(
        self,
        msg: Message,
        archive_message_fps: set,
        timestamp_utc: Optional[str] = None,
    ) -> bool:
        """Persist a Message from the rescan job, deduped against the
        full archive fingerprint set.

        See :meth:`ingest_rescanned_rxlog` for the rationale.  The
        archive fingerprint set is mutated in place.

        Args:
            msg: Message dataclass instance.
            archive_message_fps: Set of
                ``(hash, sender, text, channel_name)`` tuples already
                in the archive at the start of the rescan job.
            timestamp_utc: ISO-8601 UTC timestamp to record on the
                archive row.  Should be the original packet arrival
                time.  See :meth:`ingest_rescanned_rxlog`.

        Returns:
            ``True`` if the message was new and persisted, ``False`` if
            it was a duplicate.
        """
        fp = (
            msg.message_hash or "",
            msg.sender,
            msg.text,
            msg.channel_name,
        )
        if fp in archive_message_fps:
            return False
        archive_message_fps.add(fp)
        if self.archive:
            self.archive.add_message(msg, timestamp_utc=timestamp_utc)
        return True

    def reload_caches_from_archive(self) -> None:
        """Clear the in-memory rings and re-populate them from the
        on-disk archive.

        Called once at the end of a rescan job so the dashboard's
        Messages tab reflects messages that were freshly decoded
        from historical packets.  Sets ``messages_updated`` and
        ``rxlog_updated`` so the next render-loop tick picks the
        new state up.

        Also flushes the archive's pending write buffers first so any
        rescan output that was still in memory makes it to disk before
        being read back.
        """
        if self.archive:
            self.archive.flush()
        with self.lock:
            self.messages.clear()
            self.rx_log.clear()
            self._message_fingerprints.clear()
            self._rxlog_hashes.clear()
        # _load_from_archive acquires no lock itself (it appends only
        # during initial construction or here) but operates on the same
        # collections.  We hold no lock during the read so concurrent
        # add_message/add_rx_log from the live tailer is still possible;
        # a brief race would at worst show a duplicate row for one tick.
        self._load_from_archive()
        with self.lock:
            self.messages_updated = True
            self.rxlog_updated = True

    # ------------------------------------------------------------------
    # Archive lifecycle
    # ------------------------------------------------------------------

    def run_retention_cleanup(self) -> None:
        """Drop archive rows older than the configured retention window.

        SharedData owns the :class:`MessageArchive` instance, so it owns
        the entry point too; ``main`` schedules the repeat call.  Safe to
        call while the tailer is running — ``cleanup_old_data`` flushes
        and rewrites under the archive's own lock.
        """
        if not self.archive:
            return
        try:
            self.archive.cleanup_old_data()
        except Exception as exc:
            debug_print(f"Archive retention cleanup error: {exc}")

    def flush_archive(self) -> None:
        """Write any buffered rows to disk.

        Called from the shutdown hook.  Without it a ``systemctl
        restart`` discarded up to ``_batch_size`` buffered rows while
        the tailer cursor had already advanced past the source lines
        they came from, so those packets were lost for good.
        """
        if not self.archive:
            return
        try:
            self.archive.flush()
        except Exception as exc:
            debug_print(f"Archive flush error: {exc}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_from_archive(self) -> None:
        """Populate in-memory caches and dedup sets from the archive.

        Two distinct concerns, with two distinct scopes:

        *Display caches* (``messages``, ``rx_log``) only ever show the
        newest :data:`MAX_MESSAGES` / :data:`MAX_RX_LOG` rows, so they
        are filled from the archive **tail** via
        :meth:`MessageArchive.load_recent_messages` /
        :meth:`~MessageArchive.load_recent_rxlog`.  Up to 0.3.5 this
        path parsed every line of both archive files and then discarded
        all but the trailing window — on a never-purged archive that
        turned every restart into a multi-minute stall that looked like
        the daemon reprocessing its whole history.

        *Dedup sets* (``_message_fingerprints``, ``_rxlog_hashes``) must
        cover the **whole** archive, not the display window.  Seeding
        them from the trailing 500 / 50 rows (0.3.5 behaviour) left the
        daemon unable to recognise its own history: when
        :class:`JsonlTailer` resets a cursor to 0 after the source file
        shrinks — meshcore-gui rewrites its rxlog on its own retention
        sweep — the replayed window was re-appended to the archive
        almost in full.  The rescanner already loads the complete sets
        for exactly this reason; the live path now does the same.  The
        cost is bounded because :meth:`run_retention_cleanup` runs
        first.

        Errors are logged and non-fatal.
        """
        if not self.archive:
            return

        # Dedup sets — full archive scope.
        try:
            self._message_fingerprints |= (
                self.archive.load_all_message_fingerprints()
            )
        except Exception as exc:
            debug_print(f"Archive replay (message fingerprints) error: {exc}")

        try:
            self._rxlog_hashes |= self.archive.load_all_rxlog_hashes()
        except Exception as exc:
            debug_print(f"Archive replay (rxlog hashes) error: {exc}")

        # Display caches — trailing window only.
        from dataclasses import fields as _fields

        try:
            msg_field_names = {f.name for f in _fields(Message)}
            for d in self.archive.load_recent_messages(self.MAX_MESSAGES):
                kwargs = {k: v for k, v in d.items() if k in msg_field_names}
                try:
                    self.messages.append(Message(**kwargs))
                except TypeError:
                    continue
        except Exception as exc:
            debug_print(f"Archive replay (messages) error: {exc}")

        try:
            rx_field_names = {f.name for f in _fields(RxLogEntry)}
            for d in self.archive.load_recent_rxlog(self.MAX_RX_LOG):
                kwargs = {k: v for k, v in d.items() if k in rx_field_names}
                try:
                    self.rx_log.append(RxLogEntry(**kwargs))
                except TypeError:
                    continue
        except Exception as exc:
            debug_print(f"Archive replay (rx_log) error: {exc}")

        debug_print(
            f"Loaded {len(self.messages)} msgs / "
            f"{len(self.rx_log)} rx entries from archive "
            f"({len(self._message_fingerprints)} message fingerprints, "
            f"{len(self._rxlog_hashes)} rxlog hashes seeded)"
        )
