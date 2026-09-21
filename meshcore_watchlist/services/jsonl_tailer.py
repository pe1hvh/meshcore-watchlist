"""
JSONL tailer — reads new lines from meshcore-gui's append-only RX log.

Watches every ``*_rxlog.jsonl`` file under :data:`config.SOURCE_ARCHIVE_DIR`
and invokes a callback with each newly-parsed entry dict.  Uses a
byte-offset cursor per file, persisted to ``state.json``, so restarts
do not reprocess history.

Surviving a rewrite of the source file
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
meshcore-gui applies its own retention to the files this tailer reads,
so the file can be rewritten underneath us with old records dropped
from the front.  A byte offset alone cannot survive that: the same
offset now points at unrelated content.

Up to 0.3.6 the only defence was ``size < offset``, and on a match the
cursor was reset to 0 and the whole file re-emitted, relying on
downstream deduplication to absorb it.  That had two defects:

1. It missed the common case.  When a day of old records is dropped and
   roughly a day of new records has arrived, the file is the same size
   or larger, the comparison is False, and the cursor silently points
   into shifted content.
2. The recovery was expensive out of all proportion — a full re-emit,
   and (in 0.3.6) a full-archive dedup set loaded at every startup to
   make that re-emit safe.

The cursor now carries a SHA-1 of the last line consumed.  At every
tick the bytes immediately preceding the offset are checked against it:

* match     → the file is intact, read forward as usual;
* mismatch  → the file was rewritten.  Scan backwards from EOF for that
  same line and resume just after it.  Because the tailer is normally
  caught up, the line sits within the last few kilobytes and the search
  costs one or two block reads.  Nothing is re-emitted.
* not found → the line has genuinely rolled out of the source retention
  window.  Only then is the cursor reset to 0 and the file re-emitted,
  and ``on_reset`` fires first so the caller can arm whatever
  deduplication that replay needs.

The hash is taken over the raw line bytes, so none of this depends on
the record schema, the field names, or how meshcore-gui chooses to
rotate.

State file format
~~~~~~~~~~~~~~~~~
``{"version": 2, "cursors": {"<path>": {"offset": int, "last_line": str}}}``

Version 1 (``{"cursors": {"<path>": int}}``) is read transparently: a
bare int becomes ``{"offset": n, "last_line": ""}``, and an empty
``last_line`` falls back to the 0.3.6 size check for that one file
until the next line is consumed.  No migration step, no rewrite on
read.
"""

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from meshcore_watchlist.config import (
    SOURCE_ARCHIVE_DIR,
    STATE_FILE,
    TAILER_POLL_SECONDS,
    TAILER_RESYNC_MAX_BYTES,
    WATCHLIST_HOME,
    debug_print,
)

STATE_VERSION = 2

# Block size for the backwards searches.  256 KiB covers a few hundred
# typical rxlog rows, so the expected case — the tailer is caught up and
# its last line is near EOF — is satisfied by a single read.
RESYNC_BLOCK_BYTES = 262144


def line_fingerprint(line: str) -> str:
    """Return the SHA-1 of a raw JSONL line.

    Taken over the line as written, with the trailing newline stripped
    but nothing else normalised, so two files agree on the fingerprint
    of a line iff they hold the same bytes for it.
    """
    return hashlib.sha1(line.encode("utf-8", errors="replace")).hexdigest()


class JsonlTailer:
    """Polls ``*_rxlog.jsonl`` files and emits new entries.

    Args:
        callback:   Invoked once per new JSON line.  Receives a dict.
        source_dir: Override directory to scan (default: config).
        state_path: Override cursor file (default: config).
        poll_sec:   Polling interval in seconds (default: config).
        on_reset:   Invoked with the file path immediately before a file
            is re-emitted from byte 0 because its last known line could
            not be recovered.  Called from the tailer thread, before any
            callback for that file's replayed lines, so the handler can
            load whatever deduplication state the replay needs.
            Exceptions are logged and the replay proceeds.
    """

    def __init__(
        self,
        callback: Callable[[Dict], None],
        source_dir: Optional[Path] = None,
        state_path: Optional[Path] = None,
        poll_sec: Optional[float] = None,
        on_reset: Optional[Callable[[Path], None]] = None,
    ) -> None:
        self._callback = callback
        self._on_reset = on_reset
        self._source_dir = source_dir or SOURCE_ARCHIVE_DIR
        self._state_path = state_path or STATE_FILE
        self._poll_sec = poll_sec if poll_sec is not None else TAILER_POLL_SECONDS

        self._cursors: Dict[str, Dict] = self._load_state()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_cursor(value) -> Optional[Dict]:
        """Accept both state formats; return ``None`` if unusable."""
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            # Version 1: bare byte offset, no integrity information.
            return {"offset": int(value), "last_line": ""}
        if isinstance(value, dict):
            try:
                return {
                    "offset": int(value.get("offset", 0)),
                    "last_line": str(value.get("last_line", "") or ""),
                }
            except (TypeError, ValueError):
                return None
        return None

    def _load_state(self) -> Dict[str, Dict]:
        if not self._state_path.exists():
            return {}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            raw = data.get("cursors", {})
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            debug_print(f"JsonlTailer: state load error: {exc}; starting fresh")
            return {}

        cursors: Dict[str, Dict] = {}
        for key, value in raw.items():
            cursor = self._normalise_cursor(value)
            if cursor is not None:
                cursors[key] = cursor
        debug_print(
            f"JsonlTailer: loaded {len(cursors)} cursor(s) from state "
            f"version {data.get('version', 1)}"
        )
        return cursors

    def _save_state(self) -> None:
        WATCHLIST_HOME.mkdir(parents=True, exist_ok=True)
        try:
            tmp = self._state_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(
                    {"version": STATE_VERSION, "cursors": self._cursors},
                    indent=2,
                ),
                encoding="utf-8",
            )
            tmp.replace(self._state_path)
        except OSError as exc:
            debug_print(f"JsonlTailer: state save error: {exc}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the tailer thread (idempotent)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="jsonl-tailer",
            daemon=True,
        )
        self._thread.start()
        debug_print(
            f"JsonlTailer: started, source={self._source_dir}, "
            f"poll={self._poll_sec}s"
        )

    def stop(self) -> None:
        """Signal the tailer to stop; joins on next poll cycle."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:
                debug_print(f"JsonlTailer: tick error: {exc}")
            self._stop_event.wait(self._poll_sec)

    def _tick(self) -> None:
        if not self._source_dir.exists():
            return

        any_progress = False
        for path in sorted(self._source_dir.glob("*_rxlog.jsonl")):
            if self._process_file(path):
                any_progress = True

        if any_progress:
            self._save_state()

    # ------------------------------------------------------------------
    # Integrity check and resynchronisation
    # ------------------------------------------------------------------

    @staticmethod
    def _line_ending_at(path: Path, offset: int) -> Optional[str]:
        """Return the complete line that ends at *offset*.

        *offset* is expected to sit immediately after a newline.  Reads
        backwards from there until the preceding newline is found or the
        start of the file is reached.  Returns ``None`` on a read error.
        """
        if offset <= 0:
            return None
        chunk = b""
        try:
            with path.open("rb") as f:
                read_to = offset
                while read_to > 0:
                    read_size = min(RESYNC_BLOCK_BYTES, read_to)
                    read_to -= read_size
                    f.seek(read_to)
                    chunk = f.read(read_size) + chunk
                    # chunk now ends at *offset*.  Drop the trailing
                    # newline and look for the one before it.
                    body = chunk[:-1] if chunk.endswith(b"\n") else chunk
                    idx = body.rfind(b"\n")
                    if idx != -1:
                        return body[idx + 1:].decode("utf-8", errors="replace")
                    if read_to == 0:
                        return body.decode("utf-8", errors="replace")
        except OSError as exc:
            debug_print(f"JsonlTailer: back-read error on {path.name}: {exc}")
            return None
        return None

    def _find_line_from_end(
        self,
        path: Path,
        fingerprint: str,
        size: int,
    ) -> Optional[int]:
        """Search backwards from EOF for the line matching *fingerprint*.

        Returns the byte offset just after that line, or ``None`` if it
        was not found within :data:`TAILER_RESYNC_MAX_BYTES`.

        The search runs backwards because the tailer is normally caught
        up when a rewrite happens, which puts its last consumed line
        within a block or two of EOF.  A forward scan would read the
        whole file in that same case.
        """
        scanned = 0
        read_to = size
        tail = b""
        while read_to > 0 and scanned < TAILER_RESYNC_MAX_BYTES:
            read_size = min(RESYNC_BLOCK_BYTES, read_to)
            read_to -= read_size
            scanned += read_size
            try:
                with path.open("rb") as f:
                    f.seek(read_to)
                    block = f.read(read_size)
            except OSError as exc:
                debug_print(
                    f"JsonlTailer: resync read error on {path.name}: {exc}"
                )
                return None
            tail = block + tail

            # Only whole lines can be fingerprinted.  While the scan has
            # not reached byte 0 the first fragment is incomplete, so it
            # stays in the buffer for the next iteration.
            text = tail.decode("utf-8", errors="replace")
            parts = text.split("\n")
            complete = parts[1:] if read_to > 0 else parts

            # Walk from the end: the match is expected to be near EOF.
            for i in range(len(complete) - 1, -1, -1):
                line = complete[i]
                if not line.strip():
                    continue
                if line_fingerprint(line) != fingerprint:
                    continue
                trailing = "\n".join(complete[i + 1:])
                return size - len(trailing.encode("utf-8"))

        return None

    # ------------------------------------------------------------------
    # Per-file processing
    # ------------------------------------------------------------------

    def _resolve_offset(self, path: Path, size: int) -> Tuple[int, bool]:
        """Determine where to resume reading *path*.

        Returns ``(offset, replay)``.  ``replay`` is True when the stored
        position could not be recovered and the file is about to be
        re-emitted from byte 0.
        """
        key = str(path)
        cursor = self._cursors.get(key)
        if cursor is None:
            return (0, False)

        offset = cursor["offset"]
        fingerprint = cursor["last_line"]

        if offset <= 0:
            return (0, False)

        # A version-1 cursor carries no fingerprint, so the only check
        # available is the file length.  An offset past EOF means the
        # cursor was written against a different file than the one on
        # disk now — a source that was replaced, or a cursor left by an
        # earlier version that counted differently.
        #
        # Resume at EOF rather than replaying from 0.  Without a
        # fingerprint there is no evidence that any part of the current
        # file is unprocessed, so a replay is a guess that costs a full
        # re-ingest of the retention window; and until the tick that
        # replay belongs to completes, no state is saved, so an
        # interrupted replay leaves the bad cursor in place and the
        # daemon ingests nothing at all.  That is exactly what happened
        # in production on 2026-09-19: cursor 136,659,036 against a
        # 130,176,333-byte source, and every restart began the same
        # doomed replay.  Resuming at EOF costs at most the records
        # written between the two files diverging and now; the cursor
        # gains a fingerprint on the first tick that reads a line, and
        # every rewrite after that is handled properly.
        if not fingerprint:
            if size < offset:
                debug_print(
                    f"JsonlTailer: {path.name} is shorter than a legacy "
                    f"cursor (size={size} < offset={offset}); resuming at "
                    f"EOF — a replay cannot be justified without a line "
                    f"fingerprint"
                )
                return (size, False)
            return (offset, False)

        if size >= offset:
            actual = self._line_ending_at(path, offset)
            if actual is not None and line_fingerprint(actual) == fingerprint:
                return (offset, False)

        # The file was rewritten.  Recover the position by finding the
        # last line we consumed.
        debug_print(
            f"JsonlTailer: {path.name} was rewritten "
            f"(size={size}, cursor={offset}); resynchronising"
        )
        recovered = self._find_line_from_end(path, fingerprint, size)
        if recovered is not None:
            debug_print(
                f"JsonlTailer: {path.name} resynchronised "
                f"{offset} -> {recovered}, nothing re-emitted"
            )
            return (recovered, False)

        debug_print(
            f"JsonlTailer: {path.name} last known line has rolled out of the "
            f"source retention window; replaying from 0"
        )
        return (0, True)

    def _process_file(self, path: Path) -> bool:
        """Read new bytes from one file, dispatch entries.

        Returns True if the cursor changed (state should be saved).
        """
        key = str(path)
        try:
            size = path.stat().st_size
        except OSError as exc:
            debug_print(f"JsonlTailer: stat error for {path}: {exc}")
            return False

        previous = self._cursors.get(key)
        last_offset, replay = self._resolve_offset(path, size)

        if replay and self._on_reset is not None:
            try:
                self._on_reset(path)
            except Exception as exc:
                debug_print(f"JsonlTailer: on_reset handler error: {exc}")

        if size == last_offset:
            # Record a recovered offset even when there is nothing new,
            # so the next tick does not repeat the resync.
            if previous is not None and previous["offset"] != last_offset:
                self._cursors[key] = {
                    "offset": last_offset,
                    "last_line": previous["last_line"],
                }
                return True
            return False

        try:
            with path.open("rb") as f:
                f.seek(last_offset)
                chunk = f.read()
        except OSError as exc:
            debug_print(f"JsonlTailer: read error for {path}: {exc}")
            return False

        try:
            text = chunk.decode("utf-8")
        except UnicodeDecodeError as exc:
            debug_print(f"JsonlTailer: decode error for {path}: {exc}")
            return False

        lines: List[str] = text.split("\n")
        # If the chunk does not end on a newline, the final element is a
        # partial line — keep its bytes for the next tick.
        if not text.endswith("\n"):
            partial = lines.pop()
            consumed = len(chunk) - len(partial.encode("utf-8"))
        else:
            consumed = len(chunk)

        new_offset = last_offset + consumed

        # Remember the last complete line so the next tick can verify the
        # file has not been rewritten underneath us.
        last_complete = ""
        for line in reversed(lines):
            if line.strip():
                last_complete = line
                break

        self._cursors[key] = {
            "offset": new_offset,
            "last_line": (
                line_fingerprint(last_complete)
                if last_complete
                else (previous or {}).get("last_line", "")
            ),
        }

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                debug_print(
                    f"JsonlTailer: bad JSON on {path.name} "
                    f"(skipping): {exc}"
                )
                continue
            try:
                self._callback(rec)
            except Exception as exc:
                debug_print(f"JsonlTailer: callback error: {exc}")

        return self._cursors[key] != previous


def now() -> float:  # small helper kept for tests
    return time.time()
