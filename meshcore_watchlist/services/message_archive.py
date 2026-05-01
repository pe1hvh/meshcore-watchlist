"""
Persistent message and RxLog archive for MeshCore GUI.

Stores all incoming messages and RX log entries with configurable retention.
Works alongside SharedData: SharedData holds the latest N items for UI display,
while MessageArchive persists everything to disk with automatic cleanup.

Storage format
~~~~~~~~~~~~~~
As of 0.2.4 the archive is JSON-Lines (``.jsonl``) — one record per line,
written append-only.  This replaces the read-merge-rewrite ``.json`` format
used in 0.2.3 and earlier, which became O(N²) on each flush as the archive
grew (every flush re-serialised the entire history).  Append-only writes
make every flush O(buffer-size) regardless of total archive size.

Storage location
~~~~~~~~~~~~~~~~
~/.meshcore-watchlist/archive/<ADDRESS>_messages.jsonl
~/.meshcore-watchlist/archive/<ADDRESS>_rxlog.jsonl

Migration from 0.2.3 archives
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
On first start of 0.2.4, any existing ``.json`` archive (format version 1)
is converted to ``.jsonl``: each entry is written as one line, then the
old ``.json`` is renamed to ``.json.migrated-v1`` (kept for recovery —
*not* deleted).  Subsequent starts skip the migration if a ``.jsonl``
already exists.

Retention strategy
~~~~~~~~~~~~~~~~~~
- Messages older than MESSAGE_RETENTION_DAYS are purged daily
- RxLog entries older than RXLOG_RETENTION_DAYS are purged daily
- Cleanup is a one-shot rewrite: read the whole .jsonl, filter, write
  to a temp .jsonl, atomic rename.  Done once per cleanup cycle, not
  per insert, so the O(N²) trap of the old format is avoided.

Thread safety
~~~~~~~~~~~~~~
All public methods use an internal lock for thread-safe operation.
The lock is separate from SharedData's lock to avoid contention.
"""

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from meshcore_watchlist.config import (
    MESSAGE_RETENTION_DAYS,
    RXLOG_RETENTION_DAYS,
    debug_print,
)
from meshcore_watchlist.core.models import Message, RxLogEntry

ARCHIVE_DIR = Path.home() / ".meshcore-watchlist" / "archive"

# Format version is encoded in the filename suffix, not inside the file:
#   *.json   = format 1 (legacy, read-merge-rewrite)
#   *.jsonl  = format 2 (current, append-only)
# A constant is kept for the in-flight legacy reader/migrator only.
LEGACY_ARCHIVE_VERSION = 1


class MessageArchive:
    """Persistent storage for messages and RX log entries.

    Args:
        device_id: Device identifier string (used to derive filenames).
    """

    def __init__(self, device_id: str) -> None:
        self._address = device_id
        self._lock = threading.Lock()

        # Sanitize address for filename
        safe_name = (
            device_id
            .replace("literal:", "")
            .replace(":", "_")
            .replace("/", "_")
        )

        # Current (format-2) paths
        self._messages_path = ARCHIVE_DIR / f"{safe_name}_messages.jsonl"
        self._rxlog_path = ARCHIVE_DIR / f"{safe_name}_rxlog.jsonl"

        # Legacy (format-1) paths used only for one-shot migration
        self._legacy_messages_path = ARCHIVE_DIR / f"{safe_name}_messages.json"
        self._legacy_rxlog_path = ARCHIVE_DIR / f"{safe_name}_rxlog.json"

        # In-memory batch buffers (flushed periodically)
        self._message_buffer: List[Dict] = []
        self._rxlog_buffer: List[Dict] = []

        # Batch write thresholds.  The legacy archive used batch_size=10
        # to keep per-flush rewrite cost down, which was a tiny gain
        # against the O(N²) cost of re-serialising the whole archive.
        # Append-only writes make batch_size irrelevant for correctness
        # and dominated by syscall overhead for performance: anything in
        # the 50–500 range performs nearly identically.  500 picked so a
        # single rescan tick rarely triggers more than a handful of
        # fsyncs.
        self._batch_size = 500
        self._last_flush = datetime.now(timezone.utc)
        self._flush_interval_seconds = 60

        # Stats
        self._total_messages = 0
        self._total_rxlog = 0

        # One-shot migration of any legacy .json archives.
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_if_needed(
            legacy=self._legacy_messages_path,
            target=self._messages_path,
            array_key="messages",
            kind="messages",
        )
        self._migrate_legacy_if_needed(
            legacy=self._legacy_rxlog_path,
            target=self._rxlog_path,
            array_key="entries",
            kind="rxlog",
        )

        # Count existing entries (cheap line-count, not a full parse).
        self._total_messages = self._count_lines(self._messages_path)
        self._total_rxlog = self._count_lines(self._rxlog_path)
        debug_print(
            f"Archive: opened (messages={self._total_messages}, "
            f"rxlog={self._total_rxlog})"
        )

    # ------------------------------------------------------------------
    # Migration from legacy format-1 (.json) to format-2 (.jsonl)
    # ------------------------------------------------------------------

    @staticmethod
    def _migrate_legacy_if_needed(
        legacy: Path,
        target: Path,
        array_key: str,
        kind: str,
    ) -> None:
        """Convert ``legacy`` (.json, format 1) to ``target`` (.jsonl).

        No-op if ``target`` already exists or ``legacy`` does not.
        On success, ``legacy`` is renamed to ``legacy.migrated-v1`` —
        we keep it for recovery rather than delete.
        """
        if target.exists():
            return
        if not legacy.exists():
            return
        debug_print(
            f"Archive: migrating legacy {kind} archive "
            f"{legacy.name} -> {target.name}"
        )
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            debug_print(
                f"Archive: legacy {kind} archive at {legacy} is unreadable, "
                f"leaving as-is and starting empty: {exc}"
            )
            return
        if data.get("version") != LEGACY_ARCHIVE_VERSION:
            debug_print(
                f"Archive: legacy {kind} archive at {legacy} has unexpected "
                f"version {data.get('version')}, leaving as-is"
            )
            return
        records = data.get(array_key, [])
        # Write atomically to a tmp .jsonl then rename, so a crash mid-
        # migration leaves the legacy file untouched.
        tmp = target.with_suffix(target.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False))
                    f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(target)
        except OSError as exc:
            debug_print(f"Archive: migration write failed: {exc}")
            try:
                tmp.unlink()
            except OSError:
                pass
            return
        # Park the legacy file for recovery rather than delete.
        try:
            legacy.replace(legacy.with_suffix(legacy.suffix + ".migrated-v1"))
        except OSError as exc:
            debug_print(
                f"Archive: post-migration rename failed (non-fatal): {exc}"
            )
        debug_print(f"Archive: migrated {len(records)} {kind} records")

    @staticmethod
    def _count_lines(path: Path) -> int:
        """Cheap line count for stats — does not parse content."""
        if not path.exists():
            return 0
        n = 0
        try:
            with path.open("rb") as f:
                for _ in f:
                    n += 1
        except OSError:
            return 0
        return n

    # ------------------------------------------------------------------
    # Read helper used by every query method
    # ------------------------------------------------------------------

    @staticmethod
    def _iter_records(path: Path) -> Iterator[Dict]:
        """Yield each record from a .jsonl file.

        Silently skips malformed lines; never raises.  Returns an
        empty iterator if the file does not exist.
        """
        if not path.exists():
            return
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        # Skip corrupted lines — ingest may have been
                        # interrupted mid-write.  The next clean line
                        # picks up where we left off.
                        continue
        except OSError as exc:
            debug_print(f"Archive: read error on {path.name}: {exc}")

    # ------------------------------------------------------------------
    # Insert
    # ------------------------------------------------------------------

    def add_message(
        self,
        msg: Message,
        timestamp_utc: Optional[str] = None,
    ) -> None:
        """Add a message to the archive (buffered append-only write).

        Args:
            msg: Message dataclass instance.
            timestamp_utc: ISO-8601 UTC timestamp to record on the
                stored row.  Defaults to ``datetime.now(timezone.utc)``,
                which is the right answer for live-tail ingest (when
                "now" is approximately the packet's arrival time) but
                wrong for historical data being replayed by the
                rescanner.  The rescanner therefore derives the
                original arrival timestamp from the rxlog source and
                passes it explicitly.
        """
        with self._lock:
            msg_dict = {
                "time": msg.time,
                "timestamp_utc": (
                    timestamp_utc
                    or datetime.now(timezone.utc).isoformat()
                ),
                "sender": msg.sender,
                "text": msg.text,
                "channel": msg.channel,
                "channel_name": msg.channel_name,
                "direction": msg.direction,
                "snr": msg.snr,
                "path_len": msg.path_len,
                "sender_pubkey": msg.sender_pubkey,
                "path_hashes": msg.path_hashes,
                "path_names": msg.path_names,
                "message_hash": msg.message_hash,
            }

            self._message_buffer.append(msg_dict)

            if len(self._message_buffer) >= self._batch_size:
                self._flush_messages()
            elif self._should_flush():
                self._flush_all()

    def add_rx_log(
        self,
        entry: RxLogEntry,
        timestamp_utc: Optional[str] = None,
    ) -> None:
        """Add an RX log entry to the archive (buffered append-only write).

        Args:
            entry: RxLogEntry dataclass instance.
            timestamp_utc: ISO-8601 UTC timestamp to record on the
                stored row.  See :meth:`add_message` for rationale.
        """
        with self._lock:
            entry_dict = {
                "time": entry.time,
                "timestamp_utc": (
                    timestamp_utc
                    or datetime.now(timezone.utc).isoformat()
                ),
                "snr": entry.snr,
                "rssi": entry.rssi,
                "payload_type": entry.payload_type,
                "hops": entry.hops,
                "message_hash": entry.message_hash,
                "path_hashes": entry.path_hashes,
                "path_names": entry.path_names,
                "sender": entry.sender,
                "receiver": entry.receiver,
                "raw_payload": entry.raw_payload,
                "packet_len": entry.packet_len,
                "payload_len": entry.payload_len,
                "route_type": entry.route_type,
                "packet_type_num": entry.packet_type_num,
            }

            self._rxlog_buffer.append(entry_dict)

            if len(self._rxlog_buffer) >= self._batch_size:
                self._flush_rxlog()
            elif self._should_flush():
                self._flush_all()

    # ------------------------------------------------------------------
    # Flush
    # ------------------------------------------------------------------

    def _should_flush(self) -> bool:
        """Check if it's time to flush based on interval."""
        elapsed = (datetime.now(timezone.utc) - self._last_flush).total_seconds()
        return elapsed >= self._flush_interval_seconds

    def _flush_messages(self) -> None:
        """Append-only flush of the message buffer (LOCK MUST BE HELD)."""
        if not self._message_buffer:
            return

        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with self._messages_path.open("a", encoding="utf-8") as f:
                for rec in self._message_buffer:
                    f.write(json.dumps(rec, ensure_ascii=False))
                    f.write("\n")
                f.flush()
                # fsync per flush for durability across crashes.  This
                # is the dominant cost on rotational disks; on SSD/SD
                # the cost is small enough that we keep it.
                os.fsync(f.fileno())
        except OSError as exc:
            debug_print(f"Archive: error appending messages: {exc}")
            # Keep buffer so the next flush retries.
            return

        self._total_messages += len(self._message_buffer)
        debug_print(
            f"Archive: flushed {len(self._message_buffer)} messages "
            f"(total: {self._total_messages})"
        )
        self._message_buffer.clear()
        self._last_flush = datetime.now(timezone.utc)

    def _flush_rxlog(self) -> None:
        """Append-only flush of the rxlog buffer (LOCK MUST BE HELD)."""
        if not self._rxlog_buffer:
            return

        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with self._rxlog_path.open("a", encoding="utf-8") as f:
                for rec in self._rxlog_buffer:
                    f.write(json.dumps(rec, ensure_ascii=False))
                    f.write("\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as exc:
            debug_print(f"Archive: error appending rxlog: {exc}")
            return

        self._total_rxlog += len(self._rxlog_buffer)
        debug_print(
            f"Archive: flushed {len(self._rxlog_buffer)} rxlog entries "
            f"(total: {self._total_rxlog})"
        )
        self._rxlog_buffer.clear()
        self._last_flush = datetime.now(timezone.utc)

    def _flush_all(self) -> None:
        """Flush both buffers (LOCK MUST BE HELD)."""
        self._flush_messages()
        self._flush_rxlog()

    def flush(self) -> None:
        """Public flush — acquires lock and writes everything pending."""
        with self._lock:
            self._flush_all()

    # ------------------------------------------------------------------
    # Retention cleanup
    # ------------------------------------------------------------------

    def cleanup_old_data(self) -> None:
        """Remove messages and rxlog entries older than retention period.

        Called periodically (e.g. daily) as a background task.  Each
        cleanup is a one-shot full rewrite: read the .jsonl, filter
        out expired entries, write to a temp .jsonl, atomic rename.
        """
        with self._lock:
            self._flush_all()
            self._cleanup_jsonl(
                self._messages_path,
                MESSAGE_RETENTION_DAYS,
                kind="messages",
            )
            self._cleanup_jsonl(
                self._rxlog_path,
                RXLOG_RETENTION_DAYS,
                kind="rxlog",
            )
            self._total_messages = self._count_lines(self._messages_path)
            self._total_rxlog = self._count_lines(self._rxlog_path)

    def _cleanup_jsonl(
        self,
        path: Path,
        retention_days: int,
        kind: str,
    ) -> None:
        """Rewrite *path*, dropping records older than *retention_days*."""
        if not path.exists():
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        tmp = path.with_suffix(path.suffix + ".tmp")
        kept = 0
        dropped = 0
        try:
            with tmp.open("w", encoding="utf-8") as out:
                for rec in self._iter_records(path):
                    if self._is_newer_than(rec.get("timestamp_utc"), cutoff):
                        out.write(json.dumps(rec, ensure_ascii=False))
                        out.write("\n")
                        kept += 1
                    else:
                        dropped += 1
                out.flush()
                os.fsync(out.fileno())
            tmp.replace(path)
        except OSError as exc:
            debug_print(f"Archive: cleanup write failed for {kind}: {exc}")
            try:
                tmp.unlink()
            except OSError:
                pass
            return

        if dropped:
            debug_print(
                f"Archive: cleanup removed {dropped} old {kind} "
                f"(retained: {kept})"
            )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _is_newer_than(timestamp_str: Optional[str], cutoff: datetime) -> bool:
        """Check if ISO timestamp is newer than cutoff date."""
        if not timestamp_str:
            return False
        try:
            timestamp = datetime.fromisoformat(timestamp_str)
            return timestamp > cutoff
        except (ValueError, TypeError):
            return False

    # ------------------------------------------------------------------
    # Channel name discovery
    # ------------------------------------------------------------------

    def get_distinct_channel_names(self) -> list:
        """Return a sorted list of distinct channel names from archived messages."""
        with self._lock:
            self._flush_messages()
            names: set = set()
            for rec in self._iter_records(self._messages_path):
                name = rec.get("channel_name", "")
                if name:
                    names.add(name)
            return sorted(names)

    # ------------------------------------------------------------------
    # Single message lookup
    # ------------------------------------------------------------------

    def get_message_by_hash(self, message_hash: str) -> Optional[Dict]:
        """Return a single archived message by its message_hash.

        Args:
            message_hash: Hex string packet identifier.

        Returns:
            Message dict, or ``None`` if not found.
        """
        if not message_hash:
            return None
        with self._lock:
            self._flush_messages()
            for rec in self._iter_records(self._messages_path):
                if rec.get("message_hash") == message_hash:
                    return rec
        return None

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict:
        """Return basic archive statistics."""
        with self._lock:
            return {
                "total_messages": self._total_messages,
                "total_rxlog": self._total_rxlog,
                "buffered_messages": len(self._message_buffer),
                "buffered_rxlog": len(self._rxlog_buffer),
                "messages_path": str(self._messages_path),
                "rxlog_path": str(self._rxlog_path),
            }

    # ------------------------------------------------------------------
    # Dedup-set loaders (used by the rescanner)
    # ------------------------------------------------------------------

    def load_all_rxlog_hashes(self) -> set:
        """Return the set of message_hash strings for every archived rxlog row."""
        with self._lock:
            self._flush_rxlog()
            return {
                rec.get("message_hash", "")
                for rec in self._iter_records(self._rxlog_path)
                if rec.get("message_hash")
            }

    def load_all_message_fingerprints(self) -> set:
        """Return ``(hash, sender, text, channel_name)`` fingerprints
        for every archived message.

        The fingerprint key is the channel **name**, not the integer
        idx.  See ``SharedData.add_message`` for the invariant: idx is
        a transient UI position that changes on watchlist mutation,
        whereas the name is stable across mutations and is the channel
        identity.
        """
        with self._lock:
            self._flush_messages()
            return {
                (
                    rec.get("message_hash", "") or "",
                    rec.get("sender", ""),
                    rec.get("text", ""),
                    rec.get("channel_name", "") or "",
                )
                for rec in self._iter_records(self._messages_path)
            }

    # ------------------------------------------------------------------
    # Sender lookup
    # ------------------------------------------------------------------

    def get_messages_by_sender_pubkey(
        self,
        pubkey_prefix: str,
        limit: int = 50,
    ) -> List[Dict]:
        """Return archived messages whose ``sender_pubkey`` starts with
        *pubkey_prefix*.

        Args:
            pubkey_prefix: Lowercase hex prefix.
            limit: Maximum number of messages (newest first).

        Returns:
            List of message dicts, newest first.
        """
        if not pubkey_prefix:
            return []
        prefix = pubkey_prefix.lower()
        matched: List[Dict] = []
        with self._lock:
            self._flush_messages()
            for rec in self._iter_records(self._messages_path):
                pk = (rec.get("sender_pubkey", "") or "").lower()
                if pk.startswith(prefix):
                    matched.append(rec)
        matched.sort(key=lambda m: m.get("timestamp_utc", ""))
        return matched[-limit:][::-1]

    # ------------------------------------------------------------------
    # Filtered query (for the public API)
    # ------------------------------------------------------------------

    def query_messages(
        self,
        after: Optional[datetime] = None,
        before: Optional[datetime] = None,
        channel_name: Optional[str] = None,
        sender: Optional[str] = None,
        text_search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple:
        """Query archived messages with filters.

        Args:
            after: Only messages after this timestamp (UTC).
            before: Only messages before this timestamp (UTC).
            channel_name: Filter by channel name (exact match).
            sender: Filter by sender name (case-insensitive substring match).
            text_search: Search in message text (case-insensitive substring match).
            limit: Maximum number of results to return.
            offset: Skip this many results (for pagination).

        Returns:
            Tuple of (messages, total_count):
            - messages: List of message dicts matching the filters, newest first
            - total_count: Total number of messages matching filters (for pagination)
        """
        with self._lock:
            self._flush_messages()

            sender_lower = sender.lower() if sender else None
            text_lower = text_search.lower() if text_search else None

            filtered: List[Dict] = []
            for rec in self._iter_records(self._messages_path):
                # Time filters
                if after or before:
                    try:
                        msg_time = datetime.fromisoformat(rec.get("timestamp_utc", ""))
                    except (ValueError, TypeError):
                        continue
                    if after and msg_time < after:
                        continue
                    if before and msg_time > before:
                        continue

                if channel_name is not None and rec.get("channel_name", "") != channel_name:
                    continue

                if sender_lower:
                    msg_sender = rec.get("sender", "")
                    if sender_lower not in msg_sender.lower():
                        continue

                if text_lower:
                    msg_text = rec.get("text", "")
                    if text_lower not in msg_text.lower():
                        continue

                filtered.append(rec)

            filtered.sort(
                key=lambda m: m.get("timestamp_utc", ""),
                reverse=True,
            )

            total_count = len(filtered)
            paginated = filtered[offset:offset + limit]
            return paginated, total_count
