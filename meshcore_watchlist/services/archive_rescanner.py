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

Identity model (ADR-001)
~~~~~~~~~~~~~~~~~~~~~~~~
The rescanner scopes jobs by channel **name**, never by watchlist
idx.  ``RescanJob.only_channel_name`` is the stable identity; the
priority order fetched once at job start is a list of names; the
decoder is invoked with name-based parameters.  The decoder's key
registry is kept current under watchlist mutations during the
rescan (see :meth:`PacketPipeline._on_watchlist_changed`).  Reorder
of the watchlist during a job has no effect on job scope or priority
order — that is precisely the property ADR-001 buys us.

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
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import date as _date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING

from meshcore_watchlist.config import (
    SOURCE_ARCHIVE_DIR,
    debug_print,
)
from meshcore_watchlist.core.models import Message, RxLogEntry
from meshcore_watchlist.decoder.packet_decoder import PayloadType
from meshcore_watchlist.services.channel_priority import (
    fetch_priority_name_order,
)

if TYPE_CHECKING:
    from meshcore_watchlist.core.shared_data import SharedData
    from meshcore_watchlist.decoder.packet_decoder import PacketDecoder
    from meshcore_watchlist.services.watchlist_store import WatchlistStore


# ---------------------------------------------------------------------------
# Timestamp recovery for historical records
# ---------------------------------------------------------------------------
#
# The live tail stamps ``timestamp_utc = datetime.now()`` at the moment of
# ingest, which is approximately correct because "now" ≈ the packet's
# arrival time.  For historical replay that is catastrophically wrong: it
# clusters every rescanned row at the rescan moment, breaking
# timestamp-based sorts, time-window filters, and any downstream consumer
# that uses ``timestamp_utc`` as a cursor (the message archive's
# ``query_messages`` sorts by it; the public REST API exposes it; the
# Stats endpoint windows on it).
#
# We try, in order of confidence, to recover the original arrival time:
#
#   1. The JSONL record itself.  If meshcore-gui already writes a
#      ``timestamp_utc`` (or any other ISO-8601 field), use it verbatim.
#   2. The record's ``time`` field combined with a date extracted from
#      the rxlog filename.  Common pattern: ``YYYY-MM-DD_*_rxlog.jsonl``
#      or any embedded ``YYYY-MM-DD`` / ``YYYYMMDD`` substring.
#   3. The file's mtime — wrong by at most one retention window, but at
#      least same-day-correct rather than cluster-on-rescan.
#   4. ``now()`` as the absolute last resort, with a debug log so we can
#      tell from the logs that the heuristic failed.

_DATE_RE = re.compile(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})")


def _try_iso(value: object) -> Optional[str]:
    """Return ``value`` if it parses as ISO-8601, else ``None``."""
    if not isinstance(value, str) or not value:
        return None
    candidate = value.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(candidate)
        return value
    except ValueError:
        return None


def _extract_date_from_filename(name: str) -> Optional[str]:
    """Return ``YYYY-MM-DD`` if found in *name*, else ``None``."""
    m = _DATE_RE.search(name)
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{y}-{mo}-{d}"


def _looks_like_hms(value: object) -> bool:
    """Cheap check for ``HH:MM:SS`` (no date)."""
    return (
        isinstance(value, str)
        and len(value) <= 12
        and value.count(":") == 2
        and "T" not in value
        and "-" not in value
    )


def derive_record_timestamp_utc(rec: Dict, file_path: Path) -> str:
    """Best-effort recovery of a record's original UTC arrival time.

    See the module-level comment for the priority order.  Always
    returns a non-empty ISO-8601 string — never raises, never
    returns ``None``.  Falls back loudly (debug log) before resorting
    to ``now()``.
    """
    # 1. Direct ISO timestamp on the record.
    for field_name in ("timestamp_utc", "timestamp", "received_at", "ts"):
        iso = _try_iso(rec.get(field_name))
        if iso is not None:
            return iso

    # 2. Date from filename + HH:MM:SS from record's ``time`` field.
    time_field = rec.get("time", "")
    if _looks_like_hms(time_field):
        date_part = _extract_date_from_filename(file_path.name)
        if date_part:
            try:
                dt = datetime.fromisoformat(f"{date_part}T{time_field}")
                # Treat the wall-clock time as UTC.  meshcore-gui's rxlog
                # files don't carry an explicit timezone; the convention
                # in the rest of this codebase (live tail's now() also
                # uses UTC) is that the wall clock is UTC.
                return dt.replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                pass

    # 3. File mtime.  Same-day-correct in the common case where
    #    rxlog files rotate daily.
    try:
        mtime = file_path.stat().st_mtime
        return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    except OSError:
        pass

    # 4. Last resort.  Loud about it.
    debug_print(
        f"ArchiveRescanner: could not recover timestamp for record in "
        f"{file_path.name}; falling back to now() — historical sort "
        f"order will be wrong for this row."
    )
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Date-window validation (template 2, mechanism 2)
# ---------------------------------------------------------------------------
#
# Every rescan job is bounded by a mandatory ``start_date`` / ``end_date``.
# Both values are ISO-8601 ``YYYY-MM-DD`` *date* strings, no time
# component.  They are interpreted as **UTC day bounds**:
#
#   start_date = 2026-04-15  →  inclusive from 2026-04-15 00:00:00 UTC
#   end_date   = 2026-04-22  →  inclusive through 2026-04-22 23:59:59 UTC
#
# Inclusive-end is the convention here.  Half-open intervals lead to
# off-by-one errors in UI and logs in practice; the user expects "from
# the 15th through the 22nd" to include both days.
#
# On a Pi in NL (UTC+1 / UTC+2) this means a record received at
# 2026-04-23 00:30 *local* falls outside an ``end_date = 2026-04-22``
# window — it lives in 2026-04-22 23:30 UTC.  For day-level rescans
# this is acceptable; the user can pick an extra day of overlap if
# needed and the existing dedup absorbs it.


class InvalidRescanWindow(ValueError):
    """Raised when ``start_date`` / ``end_date`` are missing, malformed,
    or in the wrong order.  Carries a human-readable message that the
    REST layer surfaces verbatim in the 400 response body.
    """


class UnknownChannelName(ValueError):
    """Raised when a per-channel rescan submit names a channel that is
    not in the current watchlist.

    Validated on submit-time per ontwerp 0.2.6 §9.2: a delete *after*
    submit but *before* the worker picks up the job will not cause a
    failure — the job runs but every record falls under
    ``not_decryptable``, which is observable in the job counters.

    Carries the offending name as ``channel_name`` so the REST layer
    can echo it in the 404 response.
    """

    def __init__(self, channel_name: str) -> None:
        super().__init__(
            f"channel_name {channel_name!r} is not in the current watchlist"
        )
        self.channel_name = channel_name


def parse_window_date(value: object, field_name: str) -> _date:
    """Parse ``value`` as a ``YYYY-MM-DD`` string and return a ``date``.

    Args:
        value: Caller-supplied value.  Accepts ``str`` only — a stray
            ``datetime`` slipping through gives a clear error rather
            than a silent timezone surprise.
        field_name: ``"start_date"`` or ``"end_date"``; used in the
            error message so the caller can tell which side was bad.

    Raises:
        InvalidRescanWindow: missing / wrong type / unparseable.
    """
    if value is None or value == "":
        raise InvalidRescanWindow(
            f"{field_name} is required (ISO-8601 date, e.g. 2026-04-15)"
        )
    if not isinstance(value, str):
        raise InvalidRescanWindow(
            f"{field_name} must be a YYYY-MM-DD string, "
            f"got {type(value).__name__}"
        )
    try:
        return _date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidRescanWindow(
            f"{field_name} is not a valid YYYY-MM-DD date: {exc}"
        )


def validate_window(
    start_date: object, end_date: object,
) -> tuple[_date, _date]:
    """Validate both ends and the ordering.  Returns the parsed dates.

    Raises:
        InvalidRescanWindow: any individual parse failure, or
            ``start_date`` > ``end_date``.
    """
    start = parse_window_date(start_date, "start_date")
    end = parse_window_date(end_date, "end_date")
    if start > end:
        raise InvalidRescanWindow(
            f"start_date ({start.isoformat()}) is after "
            f"end_date ({end.isoformat()})"
        )
    return start, end


def _record_in_window(
    ts_iso: str, start: _date, end: _date,
) -> bool:
    """True if *ts_iso* falls inside the inclusive-day window.

    *ts_iso* comes from :func:`derive_record_timestamp_utc`, so it
    is always a non-empty ISO-8601 string.  We treat unparseable
    timestamps as inside-the-window: the record then falls through
    to the existing dedup pipeline rather than being silently
    dropped by a clock-recovery bug.  That preserves the pre-0.2.5
    behaviour for malformed records — better than throwing them away.
    """
    candidate = ts_iso.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    rec_date = dt.astimezone(timezone.utc).date()
    return start <= rec_date <= end


def _filename_window_skip(
    filename: str, start: _date, end: _date,
) -> bool:
    """True if *filename* embeds a date-stamp wholly outside the
    window, i.e. it can be skipped in its entirety.

    Returns ``False`` (do **not** skip) for filenames without an
    unambiguous date substring — those have to be parsed
    record-by-record.  Returns ``False`` for the unbounded
    ``*_rxlog.json`` snapshot which has no date in its filename.

    The match logic uses the same :data:`_DATE_RE` regex that
    ``derive_record_timestamp_utc`` uses for filename-derived
    dates, so the two paths agree on what counts as "the date for
    this file".
    """
    m = _DATE_RE.search(filename)
    if not m:
        return False
    y, mo, d = m.groups()
    try:
        file_date = _date(int(y), int(mo), int(d))
    except ValueError:
        return False
    return file_date < start or file_date > end


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
        only_channel_name:     ``None`` for full rescan; channel name
                               (stable identity per ADR-001) for
                               ``POST /api/v1/rescan/by-name``.
        start_date:            Inclusive lower bound, ``YYYY-MM-DD``
                               UTC day.  **Required** since 0.2.5
                               (template 2, mechanism 2).  Validated
                               by :func:`validate_window` before the
                               job is ever queued.
        end_date:              Inclusive upper bound, ``YYYY-MM-DD``
                               UTC day.  **Required** since 0.2.5.
        started_at:            ISO-8601 timestamp (UTC) when the worker
                               thread picked the job up; ``None`` while
                               still queued.
        finished_at:           ISO-8601 timestamp (UTC) when the job
                               reached ``done`` or ``failed``.
        files:                 Per-file progress, in the order they will
                               be (or were) processed.
        new_messages:          Count of GroupText messages newly written
                               to the message archive on this run.
        skipped_dup_message:   Count of GroupText messages successfully
                               decoded whose fingerprint was already in
                               the archive.  Together with
                               ``new_messages`` this equals
                               ``decoded_total`` on the message side.
                               **New in 0.2.6** — without it the "+0
                               new" diagnosis cannot distinguish
                               "nothing decoded" from "everything was
                               a duplicate".
        new_rxlog:             Count of RxLogEntry rows newly written to
                               the rxlog archive on this run (excludes
                               entries that were already present).
        skipped_dup_rxlog:     Count of historical lines whose
                               ``message_hash`` was already in the
                               archive — i.e. dedup hits.
        decoded_total:         Count of GroupText packets successfully
                               decrypted by the decoder during the job.
                               **New in 0.2.6** — equals
                               ``new_messages + skipped_dup_message``.
                               Surfaces "decoder is doing work" even
                               when every decoded packet is a duplicate.
        not_decryptable:       Count of GroupText packets the decoder
                               could not decrypt with any registered
                               key (or with the scoped key when
                               ``only_channel_name`` is set).
                               **New in 0.2.6** — distinguishes "no key
                               for this packet" from a structural
                               decode failure.
        skipped_window:        Count of historical lines whose recovered
                               timestamp fell outside ``[start_date,
                               end_date]``.  Reported separately so the
                               operator can verify the window-filter
                               actually fired.
        skipped_files:         Count of source files that the
                               filename-skip layer dropped wholesale
                               (rxlog filename embeds a date outside
                               the window).
        decode_failures:       Count of structurally-invalid packets
                               encountered during the rescan (logged
                               only at DEBUG).
        priority_source:       ``"domca"`` if the priority list came
                               from the domca-API fetch, ``"fallback"``
                               if the API was unreachable / malformed
                               and the rescan continued on watchlist
                               order.  Reported in ``to_dict`` so the
                               GUI / log can surface the degraded mode.
        error:                 Human-readable error string when
                               ``status == "failed"``.
    """

    job_id: str
    start_date: str = ""
    end_date: str = ""
    status: RescanStatus = RescanStatus.QUEUED
    only_channel_name: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    files: List[FileProgress] = field(default_factory=list)
    new_messages: int = 0
    skipped_dup_message: int = 0
    new_rxlog: int = 0
    skipped_dup_rxlog: int = 0
    decoded_total: int = 0
    not_decryptable: int = 0
    skipped_window: int = 0
    skipped_files: int = 0
    decode_failures: int = 0
    priority_source: str = "fallback"
    error: Optional[str] = None

    def to_dict(self) -> Dict:
        """Serialise to a JSON-friendly dict for the REST API."""
        total = sum(f.bytes_total for f in self.files) or 1
        done = sum(f.bytes_done for f in self.files)
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "only_channel_name": self.only_channel_name,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "priority_source": self.priority_source,
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
                "skipped_dup_message": self.skipped_dup_message,
                "new_rxlog": self.new_rxlog,
                "skipped_dup_rxlog": self.skipped_dup_rxlog,
                "decoded_total": self.decoded_total,
                "not_decryptable": self.not_decryptable,
                "skipped_window": self.skipped_window,
                "skipped_files": self.skipped_files,
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
        store: The :class:`WatchlistStore` used to validate per-channel
                   rescan submits against the live watchlist on
                   submit-time (per ontwerp 0.2.6 §9.2).
    """

    MAX_HISTORY = 16

    def __init__(
        self,
        rescanner: "ArchiveRescanner",
        store: "WatchlistStore",
    ) -> None:
        self._rescanner = rescanner
        self._store = store
        self._lock = threading.Lock()
        self._jobs: Dict[str, RescanJob] = {}
        self._running_job_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Submit
    # ------------------------------------------------------------------

    def submit(
        self,
        start_date: str,
        end_date: str,
        only_channel_name: Optional[str] = None,
    ) -> RescanJob:
        """Create a :class:`RescanJob` and dispatch a worker thread.

        Args:
            start_date: Inclusive lower bound, ``YYYY-MM-DD`` UTC day.
                Required since 0.2.5 (template 2, mechanism 2).  An
                explicit window prevents accidental full-history
                rescans on a 426-channel watchlist.
            end_date: Inclusive upper bound, ``YYYY-MM-DD`` UTC day.
                Required since 0.2.5.
            only_channel_name: When given, the rescanner restricts the
                trial decode to that single channel.  Validated
                against the current watchlist on submit (per
                ontwerp 0.2.6 §9.2 the validation moment is submit,
                not job-start: a delete *after* submit but *before*
                worker pickup leaves the job to run with all records
                falling under ``not_decryptable``).  See
                :meth:`PacketDecoder.decode`.

        Raises:
            InvalidRescanWindow: ``start_date`` / ``end_date`` are
                missing, malformed, or in the wrong order.  Surfaced
                by the REST layer as a 400 response.
            UnknownChannelName: ``only_channel_name`` is not in the
                current watchlist.  Surfaced by the REST layer as a
                404 response.
            RescanBusyError: A job is already running.

        Returns:
            The freshly-created :class:`RescanJob` (status ``queued``).
        """
        # Validate up-front so a bad-window submission never occupies
        # the single running-job slot.
        start, end = validate_window(start_date, end_date)

        # Validate the channel-name scope against the live watchlist
        # on submit-time per ontwerp 0.2.6 §9.2.  Doing this outside
        # ``self._lock`` is fine: the channel-list snapshot is
        # whatever WatchlistStore has at the moment of the call;
        # any mutation between this check and ``run()`` is by design
        # tolerated and surfaces as not_decryptable in the counters.
        if only_channel_name is not None:
            channels = self._store.list_channels()
            if not any(ch.get("name") == only_channel_name for ch in channels):
                raise UnknownChannelName(only_channel_name)

        with self._lock:
            if self._running_job_id is not None:
                raise RescanBusyError(self._running_job_id)

            job_id = uuid.uuid4().hex
            job = RescanJob(
                job_id=job_id,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                only_channel_name=only_channel_name,
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

        Loads the archive-wide dedup sets, fetches the domca priority
        ranking once (frozen for the duration of the job per
        ontwerp 0.2.6 §6.1), walks each ``*_rxlog.json[l]`` file
        (skipping whole files whose filename embeds a date outside
        the window), and feeds every line through :meth:`_handle_line`.
        Updates per-file progress and counters on the ``job`` so the
        GUI can render them.
        """
        job.status = RescanStatus.RUNNING
        job.started_at = datetime.now(timezone.utc).isoformat()

        # Re-validate the window.  ``RescanJobManager.submit`` already
        # validated it before queuing, but a job may also be
        # constructed and ``run()`` called from a test path.  Defending
        # here keeps the contract local to this method.
        try:
            window_start, window_end = validate_window(
                job.start_date, job.end_date,
            )
        except InvalidRescanWindow as exc:
            job.error = str(exc)
            job.status = RescanStatus.FAILED
            return

        if not self._source_dir.exists():
            job.error = f"source archive directory missing: {self._source_dir}"
            job.status = RescanStatus.FAILED
            return

        # Build channel-name → idx lookup (mirrors PacketPipeline) so
        # that newly-decoded messages get a current ``Message.channel``
        # idx for display.  ``channel_name`` is the identity.
        channels = self._store.list_channels()
        idx_by_name: Dict[str, int] = {
            ch.get("name", ""): ch.get("idx")
            for ch in channels
            if ch.get("name")
        }

        # Fetch the domca-API ranking once per job and freeze it
        # (ontwerp §6.1).  The frozen list is name-based; a watchlist
        # mutation during the job has no effect on this list — that
        # is precisely the property ADR-001 buys us.  An empty list
        # (network failure / malformed payload) means "use decoder
        # default order" — recorded as fallback in the job status so
        # the GUI surfaces the degraded mode.
        priority_name_order = fetch_priority_name_order(channels)
        if priority_name_order:
            job.priority_source = "domca"
        else:
            job.priority_source = "fallback"
        debug_print(
            f"ArchiveRescanner: priority list "
            f"({job.priority_source}, {len(priority_name_order)} entries)"
        )

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
            f"(only_channel_name={job.only_channel_name!r}, "
            f"window={job.start_date}..{job.end_date}, "
            f"{len(rxlog_hashes)} rxlog hashes, "
            f"{len(message_fps)} message fingerprints loaded)"
        )

        # Discover files; populate progress entries up-front so the
        # GUI knows the totals immediately.
        #
        # meshcore-gui keeps two parallel rxlog files per device:
        #   - ``*_rxlog.json``  : pretty-printed snapshot containing the
        #                          full retained history (~7-8 days, can
        #                          be 100+ MB)
        #   - ``*_rxlog.jsonl`` : append-only line file with the most
        #                          recent ~3 days
        # Both contain records with the same schema; the .jsonl is
        # typically a few minutes ahead of the .json (live writer vs
        # periodic flush).  We process .json first so the older history
        # lands first, then .jsonl picks up the recent records that the
        # .json snapshot does not yet contain.  Overlap between the two
        # is absorbed by the existing message_hash / fingerprint dedup
        # sets — duplicates are recognised as already-archived and
        # skipped without producing extra rows.
        files = sorted(self._source_dir.glob("*_rxlog.json"))
        files += sorted(self._source_dir.glob("*_rxlog.jsonl"))

        # Filename-skip layer (template 2, mechanism 2).  Whole files
        # whose name embeds a YYYY-MM-DD outside the window are
        # dropped before opening — dramatically faster than scanning
        # them line-by-line on a multi-day archive.  Files without an
        # unambiguous date in the name (notably the ``*_rxlog.json``
        # snapshot) fall through to record-level filtering.
        eligible: List[Path] = []
        for path in files:
            if _filename_window_skip(path.name, window_start, window_end):
                job.skipped_files += 1
                debug_print(
                    f"ArchiveRescanner: filename-skip {path.name} "
                    f"(outside {window_start}..{window_end})"
                )
                continue
            eligible.append(path)

        for path in eligible:
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
                    idx_by_name,
                    rxlog_hashes,
                    message_fps,
                    window_start,
                    window_end,
                    priority_name_order,
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
            f"(decoded_total={job.decoded_total}, "
            f"new_messages={job.new_messages}, "
            f"skipped_dup_message={job.skipped_dup_message}, "
            f"not_decryptable={job.not_decryptable}, "
            f"new_rxlog={job.new_rxlog}, "
            f"skipped_dup_rxlog={job.skipped_dup_rxlog}, "
            f"skipped_window={job.skipped_window}, "
            f"skipped_files={job.skipped_files}, "
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
        idx_by_name: Dict[str, int],
        rxlog_hashes: set,
        message_fps: set,
        window_start: _date,
        window_end: _date,
        priority_name_order: List[str],
    ) -> None:
        """Dispatch to the right parser based on file format.

        meshcore-gui writes two formats with the same record schema
        but different containers:

          - ``.jsonl``   one record per line, append-only
          - ``.json``    pretty-printed object with an ``entries``
                         array, periodically flushed snapshot

        Both must be read because the two files cover different
        windows (the .jsonl is typically minutes ahead of the .json,
        but the .json reaches further back).  Format is determined
        from the first non-whitespace byte rather than the extension
        alone, so a renamed or atypically-named file is still parsed
        correctly.
        """
        try:
            is_pretty = self._is_pretty_printed(path)
        except OSError as exc:
            debug_print(
                f"ArchiveRescanner: cannot peek {path.name}: {exc}"
            )
            progress.bytes_done = progress.bytes_total
            return

        if is_pretty:
            self._process_pretty_json_file(
                path, progress, job, idx_by_name,
                rxlog_hashes, message_fps,
                window_start, window_end, priority_name_order,
            )
        else:
            self._process_jsonl_file(
                path, progress, job, idx_by_name,
                rxlog_hashes, message_fps,
                window_start, window_end, priority_name_order,
            )

    @staticmethod
    def _is_pretty_printed(path: Path) -> bool:
        """Detect whether *path* is pretty-printed JSON or JSONL.

        Both formats start with ``{``, so the first byte alone is not
        sufficient.  Distinguishing feature: pretty-printed JSON has a
        newline immediately after the top-level opening brace
        (``"{\\n  ..."``), whereas JSONL records pack a full record
        onto one line (``'{"time":...}'``).

        Reads at most a handful of bytes — safe on multi-GB files.
        Returns ``False`` for empty files (defaults to JSONL handling,
        which is a safe no-op on an empty file).
        """
        with path.open("rb") as f:
            head = f.read(8)
        idx = head.find(b"{")
        if idx < 0 or idx + 1 >= len(head):
            return False
        next_byte = head[idx + 1:idx + 2]
        return next_byte in (b"\n", b"\r")

    def _process_jsonl_file(
        self,
        path: Path,
        progress: FileProgress,
        job: RescanJob,
        idx_by_name: Dict[str, int],
        rxlog_hashes: set,
        message_fps: set,
        window_start: _date,
        window_end: _date,
        priority_name_order: List[str],
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
                    path,
                    job,
                    idx_by_name,
                    rxlog_hashes,
                    message_fps,
                    window_start,
                    window_end,
                    priority_name_order,
                )

        # Pin progress to the recorded total in case the file grew
        # during the rescan (live tailer is still writing).  We do not
        # follow that growth — the live tailer will pick it up.
        progress.bytes_done = progress.bytes_total

    def _process_pretty_json_file(
        self,
        path: Path,
        progress: FileProgress,
        job: RescanJob,
        idx_by_name: Dict[str, int],
        rxlog_hashes: set,
        message_fps: set,
        window_start: _date,
        window_end: _date,
        priority_name_order: List[str],
    ) -> None:
        """Stream records from a meshcore-gui pretty-printed rxlog file.

        File structure (relevant excerpt)::

            {
              "version": 1,
              "address": "...",
              "last_updated": "...",
              "entries": [
                {
                  "time": "...",
                  "timestamp_utc": "...",
                  ...
                  "path_hashes": [...],
                  "path_names": [...],
                  ...
                },
                {
                  ...
                }
              ]
            }

        We could not use ``json.load()`` here because these files
        regularly exceed 150 MB, and loading the full DOM would peak
        at well over a gigabyte of resident memory on a Pi.

        Instead we lean on meshcore-gui's stable pretty-print
        indentation: every record begins with exactly ``"    {"``
        (four spaces + opening brace) and ends with ``"    }"`` or
        ``"    },"`` on its own line.  Lines between those markers
        are accumulated and parsed with a single ``json.loads`` call.
        Nested objects and arrays inside a record (e.g. ``path_hashes``)
        sit at deeper indents and never collide with the record
        delimiters.

        Failure modes handled:

          - Truncated trailing record (file is being written while we
            read): caught by the json.loads except branch, counted as
            a decode failure, rescan continues.
          - Indentation regression (meshcore-gui changes its writer):
            records are not detected and the file yields zero records.
            That manifests as a per-file decode_failures bump of zero
            and ``new_*`` deltas of zero — visible in the GUI's job
            summary, so the regression is observable.

        Byte progress is updated per line.
        """
        in_entries = False
        in_record = False
        buffer: List[str] = []

        with path.open("rb") as f:
            for raw_line in f:
                progress.bytes_done += len(raw_line)

                try:
                    line = raw_line.decode("utf-8")
                except UnicodeDecodeError:
                    # Pretty-print files should be ASCII-safe but be
                    # defensive against an occasional bad byte.
                    job.decode_failures += 1
                    continue

                stripped = line.rstrip("\n").rstrip("\r")

                if not in_entries:
                    # Skip past header until we hit the entries-array
                    # opener.  Test against the rstrip'ed line so a
                    # trailing CR on Windows-touched files doesn't
                    # break detection.
                    if stripped.endswith('"entries": ['):
                        in_entries = True
                    continue

                if not in_record:
                    if stripped == "    {":
                        in_record = True
                        buffer = ["{"]
                    # else: closing ']' of entries array, trailing
                    # post-entries keys (closing '}' of the outer
                    # object), or whitespace — all safely ignored.
                else:
                    if stripped == "    }" or stripped == "    },":
                        buffer.append("}")
                        try:
                            rec = json.loads("\n".join(buffer))
                        except json.JSONDecodeError:
                            job.decode_failures += 1
                            in_record = False
                            buffer = []
                            continue

                        in_record = False
                        buffer = []

                        self._handle_line(
                            rec,
                            path,
                            job,
                            idx_by_name,
                            rxlog_hashes,
                            message_fps,
                            window_start,
                            window_end,
                            priority_name_order,
                        )
                    else:
                        buffer.append(stripped)

        progress.bytes_done = progress.bytes_total

    # ------------------------------------------------------------------
    # Per-line
    # ------------------------------------------------------------------

    def _handle_line(
        self,
        rec: Dict,
        file_path: Path,
        job: RescanJob,
        idx_by_name: Dict[str, int],
        rxlog_hashes: set,
        message_fps: set,
        window_start: _date,
        window_end: _date,
        priority_name_order: List[str],
    ) -> None:
        """Process a single historical RX log record.

        Mirrors :meth:`PacketPipeline.handle_entry` but routes through
        the rescan-aware ingest methods so dedup is performed against
        the archive-wide sets rather than the small in-memory rings.

        Crucially, the original arrival timestamp is recovered from
        the record / filename / mtime via :func:`derive_record_timestamp_utc`
        and passed through to the ingest methods so the archive row
        carries the historical time, not the rescan moment.

        Window filtering (template 2, mechanism 2): records whose
        recovered timestamp falls outside ``[window_start,
        window_end]`` early-return *before* the decoder loop, which
        is the expensive part on a 426-channel watchlist.  The skip
        is counted on ``job.skipped_window`` so the operator can
        verify the filter actually fired.

        Counter logic (ontwerp 0.2.6 §5.4):

          - ``decoder.decode(...)`` returns ``None`` (structural
            failure or scoped to absent name) → ``decode_failures += 1``
          - Not GroupText                      → no counter
          - ``is_decrypted == False``          → ``not_decryptable += 1``
          - Decoded GroupText, dup fingerprint → ``decoded_total += 1``,
                                                 ``skipped_dup_message += 1``
          - Decoded GroupText, new fingerprint → ``decoded_total += 1``,
                                                 ``new_messages += 1``
        """
        raw_payload = rec.get("raw_payload") or ""

        # Recover the original arrival time once per record and reuse
        # it for both the rxlog and the message rows so they line up.
        ts_utc = derive_record_timestamp_utc(rec, file_path)

        # Recordniveau-window-filter (template 2, mechanism 2).
        # Early-return *before* dedup-set lookup, archive write, AND
        # the O(N_channels) decode loop.  That last point is what
        # makes this filter pay off: a record outside the window
        # avoids the multi-key trial decryption entirely.
        if not _record_in_window(ts_utc, window_start, window_end):
            job.skipped_window += 1
            return

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

        if self._shared.ingest_rescanned_rxlog(
            rx_entry, rxlog_hashes, timestamp_utc=ts_utc,
        ):
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
            allowed_name=job.only_channel_name,
            priority_name_order=priority_name_order or None,
        )
        if decoded is None:
            # Structural failure, or scoped to a name the decoder
            # doesn't have a key for (channel deleted between submit
            # and worker pickup).  Per §5.4 this counts as a decode
            # failure — the same bucket the live tailer uses for
            # malformed packets.
            job.decode_failures += 1
            return

        # Not-GroupText: no counter, just early-return.  The rxlog row
        # is already persisted; non-GroupText packets are not message
        # candidates by design.
        if decoded.payload_type != PayloadType.GroupText:
            return

        if not decoded.is_decrypted:
            job.not_decryptable += 1
            return

        # Successful GroupText decode — bump decoded_total then split
        # on dedup outcome.
        job.decoded_total += 1

        msg = Message.incoming(
            sender=decoded.sender,
            text=decoded.text,
            channel=idx_by_name.get(decoded.channel_name),
            time=rx_entry.time,
            snr=rx_entry.snr,
            path_len=decoded.path_length,
            path_hashes=decoded.path_hashes,
            path_names=rx_entry.path_names,
            message_hash=decoded.message_hash,
        )
        msg.channel_name = decoded.channel_name

        if self._shared.ingest_rescanned_message(
            msg, message_fps, timestamp_utc=ts_utc,
        ):
            job.new_messages += 1
        else:
            job.skipped_dup_message += 1
