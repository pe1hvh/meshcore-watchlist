"""
Archive rescanner — retroactive decode of historical ``*_rxlog.jsonl``
files using the current watchlist key set.

Why
~~~
The live :class:`JsonlTailer` only moves forward: each ``*_rxlog.jsonl``
file has a byte-offset cursor in ``state.json`` and bytes before the
cursor are never re-read.  When the user adds a new hashtag channel to
the watchlist, packets that arrived **before** the channel was added
were ingested as undecoded ``RxLogEntry`` rows but the ``GroupText``
payload was never decrypted with the freshly-derived key.

The rescanner reopens every ``*_rxlog.jsonl`` from byte 0 to EOF on a
**separate task**, runs each line through the current
:class:`PacketDecoder`, and writes any newly-decoded :class:`Message`
to the archive.  It uses an archive-level dedup set (loaded once at
job start) so it does not duplicate the historical ``RxLogEntry``
rows already on disk.

Design constraints
~~~~~~~~~~~~~~~~~~
* The live tailer keeps running with its existing cursors untouched
  during a rescan — incoming packets are never lost.
* Rescan ingest goes straight to :class:`MessageArchive` via
  :meth:`SharedData.ingest_rescanned_rxlog` /
  :meth:`SharedData.ingest_rescanned_message`, bypassing the in-memory
  50/500-entry rings.  At end-of-job
  :meth:`SharedData.reload_caches_from_archive` is called so the GUI
  picks up newly-decoded messages without a service restart.
* One job at a time.  Submitting while one is running raises
  :class:`RescanBusyError` carrying the already-running ``job_id``.
* Progress reporting is per-file ``(bytes_done, bytes_total)``.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING

from meshcore_watchlist.config import (
    SOURCE_ARCHIVE_DIR,
    debug_print,
)
from meshcore_watchlist.core.models import Message, RxLogEntry

if TYPE_CHECKING:
    from meshcore_watchlist.core.shared_data import SharedData
    from meshcore_watchlist.decoder.packet_decoder import PacketDecoder
    from meshcore_watchlist.services.watchlist_store import WatchlistStore


# ---------------------------------------------------------------------------
# Job model
# ---------------------------------------------------------------------------

class RescanStatus(str, Enum):
    """Lifecycle states for a :class:`RescanJob`."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class FileProgress:
    """Per-file byte progress for a rescan job."""

    path: str
    bytes_total: int
    bytes_done: int = 0


@dataclass
class RescanJob:
    """Server-side state for a single rescan job.

    Returned (in dict form) by both the submit endpoint and the
    status endpoint, so the GUI can drive a progress widget.

    Attributes:
        job_id:                Opaque hex string assigned at submit.
        status:                One of :class:`RescanStatus`.
        only_channel_idx:      ``None`` for full rescan; integer for
                               ``POST /api/v1/rescan/{idx}``.
        started_at:            ISO-8601 timestamp (UTC) when the worker
                               thread picked the job up; ``None`` while
                               still queued.
        finished_at:           ISO-8601 timestamp (UTC) when the job
                               reached ``done`` or ``failed``.
        files:                 Per-file progress, in the order they will
                               be (or were) processed.
        new_messages:          Count of GroupText messages newly written
                               to the message archive on this run.
        new_rxlog:             Count of RxLogEntry rows newly written to
                               the rxlog archive on this run (excludes
                               entries that were already present).
        skipped_dup_rxlog:     Count of historical lines whose
                               ``message_hash`` was already in the
                               archive — i.e. dedup hits.
        decode_failures:       Count of structurally-invalid packets
                               encountered during the rescan (logged
                               only at DEBUG).
        error:                 Human-readable error string when
                               ``status == "failed"``.
    """

    job_id: str
    status: RescanStatus = RescanStatus.QUEUED
    only_channel_idx: Optional[int] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    files: List[FileProgress] = field(default_factory=list)
    new_messages: int = 0
    new_rxlog: int = 0
    skipped_dup_rxlog: int = 0
    decode_failures: int = 0
    error: Optional[str] = None

    def to_dict(self) -> Dict:
        """Serialise to a JSON-friendly dict for the REST API."""
        total = sum(f.bytes_total for f in self.files) or 1
        done = sum(f.bytes_done for f in self.files)
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "only_channel_idx": self.only_channel_idx,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "progress": {
                "bytes_done": done,
                "bytes_total": total,
                "percent": round(100.0 * done / total, 1),
                "files": [
                    {
                        "path": f.path,
                        "bytes_done": f.bytes_done,
                        "bytes_total": f.bytes_total,
                    }
                    for f in self.files
                ],
            },
            "counts": {
                "new_messages": self.new_messages,
                "new_rxlog": self.new_rxlog,
                "skipped_dup_rxlog": self.skipped_dup_rxlog,
                "decode_failures": self.decode_failures,
            },
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class RescanBusyError(RuntimeError):
    """Raised when :meth:`RescanJobManager.submit` is called while a
    job is already running.  The currently-running ``job_id`` is
    attached as ``running_job_id``."""

    def __init__(self, running_job_id: str) -> None:
        super().__init__(
            f"Rescan job {running_job_id} is already running"
        )
        self.running_job_id = running_job_id


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class RescanJobManager:
    """Tracks rescan jobs.  At most one job is active at any time.

    The manager is intentionally simple — single-user deployment
    (``geen queue``).  Completed and failed jobs are kept in memory
    so the GUI can fetch a final status report; they are evicted by
    LRU once :data:`MAX_HISTORY` jobs accumulate.

    Args:
        rescanner: The :class:`ArchiveRescanner` whose ``run()`` method
                   does the actual work for each submitted job.
    """

    MAX_HISTORY = 16

    def __init__(self, rescanner: "ArchiveRescanner") -> None:
        self._rescanner = rescanner
        self._lock = threading.Lock()
        self._jobs: Dict[str, RescanJob] = {}
        self._running_job_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Submit
    # ------------------------------------------------------------------

    def submit(self, only_channel_idx: Optional[int] = None) -> RescanJob:
        """Create a :class:`RescanJob` and dispatch a worker thread.

        Args:
            only_channel_idx: When given, the rescanner restricts the
                trial decode to that single channel.  See
                :meth:`PacketDecoder.decode`.

        Raises:
            RescanBusyError: A job is already running.

        Returns:
            The freshly-created :class:`RescanJob` (status ``queued``).
        """
        with self._lock:
            if self._running_job_id is not None:
                raise RescanBusyError(self._running_job_id)

            job_id = uuid.uuid4().hex
            job = RescanJob(
                job_id=job_id,
                only_channel_idx=only_channel_idx,
            )
            self._jobs[job_id] = job
            self._running_job_id = job_id
            self._evict_old()

        threading.Thread(
            target=self._worker,
            args=(job,),
            name=f"rescan-{job_id[:8]}",
            daemon=True,
        ).start()
        return job

    def get(self, job_id: str) -> Optional[RescanJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def running_job_id(self) -> Optional[str]:
        with self._lock:
            return self._running_job_id

    def _evict_old(self) -> None:
        """Drop oldest finished jobs once history exceeds MAX_HISTORY.

        Caller MUST hold ``self._lock``.
        """
        if len(self._jobs) <= self.MAX_HISTORY:
            return
        finished = [
            jid for jid, j in self._jobs.items()
            if j.status in (RescanStatus.DONE, RescanStatus.FAILED)
        ]
        # Oldest-first by finished_at (None last)
        finished.sort(key=lambda jid: self._jobs[jid].finished_at or "")
        for jid in finished[: len(self._jobs) - self.MAX_HISTORY]:
            del self._jobs[jid]

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _worker(self, job: RescanJob) -> None:
        """Thread entrypoint: run the rescanner, capture status."""
        try:
            self._rescanner.run(job)
            with self._lock:
                if job.status not in (RescanStatus.DONE, RescanStatus.FAILED):
                    job.status = RescanStatus.DONE
        except Exception as exc:  # pragma: no cover - defensive
            debug_print(f"RescanJobManager: job {job.job_id} crashed: {exc}")
            job.error = f"{type(exc).__name__}: {exc}"
            job.status = RescanStatus.FAILED
        finally:
            job.finished_at = datetime.now(timezone.utc).isoformat()
            with self._lock:
                if self._running_job_id == job.job_id:
                    self._running_job_id = None


# ---------------------------------------------------------------------------
# Rescanner
# ---------------------------------------------------------------------------

class ArchiveRescanner:
    """One-shot pass over ``*_rxlog.jsonl`` files in the source archive.

    Architecturally a sibling of :class:`JsonlTailer`, but with three
    crucial differences:

    1. Reads each file from byte 0 to EOF, regardless of any tailer
       cursor.  Live cursors in ``state.json`` are not touched.
    2. Dedupes against the **full** rxlog archive hash set, not
       SharedData's 50-entry in-memory cache.
    3. Does not run as a background thread of its own — it is invoked
       once per :class:`RescanJob` by :class:`RescanJobManager`.
    """

    def __init__(
        self,
        shared: "SharedData",
        decoder: "PacketDecoder",
        store: "WatchlistStore",
        source_dir: Optional[Path] = None,
    ) -> None:
        self._shared = shared
        self._decoder = decoder
        self._store = store
        self._source_dir = source_dir or SOURCE_ARCHIVE_DIR

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self, job: RescanJob) -> None:
        """Execute one rescan job.  Mutates ``job`` in place.

        Loads the archive-wide dedup sets, walks each ``*_rxlog.jsonl``
        file in lexicographic order, and feeds every line through
        :meth:`_handle_line`.  Updates per-file progress and counters
        on the ``job`` so the GUI can render them.
        """
        job.status = RescanStatus.RUNNING
        job.started_at = datetime.now(timezone.utc).isoformat()

        if not self._source_dir.exists():
            job.error = f"source archive directory missing: {self._source_dir}"
            job.status = RescanStatus.FAILED
            return

        # Build channel-name lookup (mirrors PacketPipeline) so that
        # newly-decoded messages get their channel_name set.
        channel_name_by_idx = {
            ch["idx"]: ch["name"] for ch in self._store.list_channels()
        }

        # Pre-load full-archive dedup sets — one read per file,
        # one set lookup per line.
        archive = self._shared.archive
        if archive is None:
            job.error = "SharedData has no archive attached"
            job.status = RescanStatus.FAILED
            return
        rxlog_hashes = archive.load_all_rxlog_hashes()
        message_fps = archive.load_all_message_fingerprints()
        debug_print(
            f"ArchiveRescanner: starting job {job.job_id} "
            f"(only_idx={job.only_channel_idx}, "
            f"{len(rxlog_hashes)} rxlog hashes, "
            f"{len(message_fps)} message fingerprints loaded)"
        )

        # Discover files; populate progress entries up-front so the
        # GUI knows the totals immediately.
        files = sorted(self._source_dir.glob("*_rxlog.jsonl"))
        for path in files:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            job.files.append(FileProgress(path=str(path), bytes_total=size))

        # Process each file line-by-line.
        for fp in job.files:
            try:
                self._process_file(
                    Path(fp.path),
                    fp,
                    job,
                    channel_name_by_idx,
                    rxlog_hashes,
                    message_fps,
                )
            except Exception as exc:
                # Log and continue to the next file: a corrupted file
                # should not abort the whole job.
                debug_print(
                    f"ArchiveRescanner: error processing {fp.path}: {exc}"
                )

        # Flush the archive buffers so reload sees fresh data, then
        # repopulate SharedData's in-memory rings so the GUI updates.
        try:
            self._shared.reload_caches_from_archive()
        except Exception as exc:
            debug_print(f"ArchiveRescanner: reload_caches error: {exc}")

        job.status = RescanStatus.DONE
        debug_print(
            f"ArchiveRescanner: job {job.job_id} done "
            f"(new_messages={job.new_messages}, "
            f"new_rxlog={job.new_rxlog}, "
            f"skipped_dup_rxlog={job.skipped_dup_rxlog}, "
            f"decode_failures={job.decode_failures})"
        )

    # ------------------------------------------------------------------
    # Per-file
    # ------------------------------------------------------------------

    def _process_file(
        self,
        path: Path,
        progress: FileProgress,
        job: RescanJob,
        channel_name_by_idx: Dict[int, str],
        rxlog_hashes: set,
        message_fps: set,
    ) -> None:
        """Stream one ``*_rxlog.jsonl`` from byte 0 to EOF.

        We deliberately use a streaming line iterator (``for line in f``)
        rather than reading the whole file into memory: rxlog files can
        grow to tens of megabytes within the 7-day retention window.
        Byte progress is updated per line so the GUI shows movement
        even on large files.
        """
        with path.open("rb") as f:
            for raw_line in f:
                # Update progress before potentially-throwing parse work.
                progress.bytes_done += len(raw_line)

                line = raw_line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    job.decode_failures += 1
                    continue

                self._handle_line(
                    rec,
                    job,
                    channel_name_by_idx,
                    rxlog_hashes,
                    message_fps,
                )

        # Pin progress to the recorded total in case the file grew
        # during the rescan (live tailer is still writing).  We do not
        # follow that growth — the live tailer will pick it up.
        progress.bytes_done = progress.bytes_total

    # ------------------------------------------------------------------
    # Per-line
    # ------------------------------------------------------------------

    def _handle_line(
        self,
        rec: Dict,
        job: RescanJob,
        channel_name_by_idx: Dict[int, str],
        rxlog_hashes: set,
        message_fps: set,
    ) -> None:
        """Process a single historical RX log record.

        Mirrors :meth:`PacketPipeline.handle_entry` but routes through
        the rescan-aware ingest methods so dedup is performed against
        the archive-wide sets rather than the small in-memory rings.
        """
        raw_payload = rec.get("raw_payload") or ""

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

        if self._shared.ingest_rescanned_rxlog(rx_entry, rxlog_hashes):
            job.new_rxlog += 1
        else:
            job.skipped_dup_rxlog += 1

        # Even when the rxlog row was a dedup hit, the decode pass is
        # still worth running: that hit means the raw packet was seen
        # before, but the GroupText payload may not yet have been
        # decoded with the channel key the user just added.  The
        # message archive has its own fingerprint dedup downstream.
        if not raw_payload or not self._decoder.has_keys:
            return

        decoded = self._decoder.decode(
            raw_payload,
            allowed_idx=job.only_channel_idx,
        )
        if decoded is None or not decoded.is_decrypted:
            return
        if decoded.channel_idx is None:
            return

        msg = Message.incoming(
            sender=decoded.sender,
            text=decoded.text,
            channel=decoded.channel_idx,
            time=rx_entry.time,
            snr=rx_entry.snr,
            path_len=decoded.path_length,
            path_hashes=decoded.path_hashes,
            path_names=rx_entry.path_names,
            message_hash=decoded.message_hash,
        )
        ch_name = channel_name_by_idx.get(decoded.channel_idx, "")
        if ch_name:
            msg.channel_name = ch_name

        if self._shared.ingest_rescanned_message(msg, message_fps):
            job.new_messages += 1
