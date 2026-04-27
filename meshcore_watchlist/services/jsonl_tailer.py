"""
JSONL tailer — reads new lines from meshcore-gui's append-only RX log.

Watches every ``*_rxlog.jsonl`` file under :data:`config.SOURCE_ARCHIVE_DIR`
and invokes a callback with each newly-parsed entry dict.  Uses a
byte-offset cursor per file, persisted to ``state.json``, so restarts
do not reprocess history.

Detection of file truncation / rotation: when the current file size is
smaller than the stored cursor, the cursor is reset to 0.  Downstream
deduplication (by ``message_hash``) absorbs the resulting one-time
re-emit of the new file's contents.
"""

import json
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

from meshcore_watchlist.config import (
    SOURCE_ARCHIVE_DIR,
    STATE_FILE,
    TAILER_POLL_SECONDS,
    WATCHLIST_HOME,
    debug_print,
)


class JsonlTailer:
    """Polls ``*_rxlog.jsonl`` files and emits new entries.

    Args:
        callback:   Invoked once per new JSON line.  Receives a dict.
        source_dir: Override directory to scan (default: config).
        state_path: Override cursor file (default: config).
        poll_sec:   Polling interval in seconds (default: config).
    """

    def __init__(
        self,
        callback: Callable[[Dict], None],
        source_dir: Optional[Path] = None,
        state_path: Optional[Path] = None,
        poll_sec: Optional[float] = None,
    ) -> None:
        self._callback = callback
        self._source_dir = source_dir or SOURCE_ARCHIVE_DIR
        self._state_path = state_path or STATE_FILE
        self._poll_sec = poll_sec if poll_sec is not None else TAILER_POLL_SECONDS

        self._cursors: Dict[str, int] = self._load_state()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> Dict[str, int]:
        if not self._state_path.exists():
            return {}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            cursors = data.get("cursors", {})
            # Cast values defensively to int.
            return {k: int(v) for k, v in cursors.items()}
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            debug_print(f"JsonlTailer: state load error: {exc}; starting fresh")
            return {}

    def _save_state(self) -> None:
        WATCHLIST_HOME.mkdir(parents=True, exist_ok=True)
        try:
            tmp = self._state_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"cursors": self._cursors}, indent=2),
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

    def _process_file(self, path: Path) -> bool:
        """Read new bytes from one file, dispatch entries.

        Returns True if the cursor advanced (state should be saved).
        """
        key = str(path)
        try:
            size = path.stat().st_size
        except OSError as exc:
            debug_print(f"JsonlTailer: stat error for {path}: {exc}")
            return False

        last_offset = self._cursors.get(key, 0)

        # Truncation / rotation detection.
        if size < last_offset:
            debug_print(
                f"JsonlTailer: truncation detected on {path.name} "
                f"(size={size} < offset={last_offset}); resetting cursor"
            )
            last_offset = 0

        if size == last_offset:
            return False

        try:
            with path.open("rb") as f:
                f.seek(last_offset)
                chunk = f.read()
        except OSError as exc:
            debug_print(f"JsonlTailer: read error for {path}: {exc}")
            return False

        # Split on newline; the last fragment may be incomplete (mid-write).
        try:
            text = chunk.decode("utf-8")
        except UnicodeDecodeError as exc:
            debug_print(f"JsonlTailer: decode error for {path}: {exc}")
            return False

        lines = text.split("\n")
        # If the chunk does not end on a newline, the final element is a
        # partial line — keep its bytes for the next tick.
        if not text.endswith("\n"):
            partial = lines.pop()
            consumed = len(chunk) - len(partial.encode("utf-8"))
        else:
            consumed = len(chunk)

        new_offset = last_offset + consumed
        self._cursors[key] = new_offset

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

        return new_offset != last_offset


def now() -> float:  # small helper kept for tests
    return time.time()
