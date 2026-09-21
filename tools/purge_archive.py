#!/usr/bin/env python3
"""
Offline retention purge for the meshcore-watchlist archive.

Run this with the daemon stopped.  It does the job that
``MessageArchive.cleanup_old_data()`` does, but out of process, with a
progress report, a free-space check up front, and one deliberate
difference in policy (see ``--drop-undated``).

Usage
~~~~~
    python3 purge_archive.py --report
    python3 purge_archive.py --dry-run
    python3 purge_archive.py
    python3 purge_archive.py --days 3

``--report`` only counts and prints; it opens nothing for writing and
is safe to run while the daemon is up.  ``--dry-run`` does the full
retention pass and reports what would be kept and dropped, still
without writing.  Without either flag the files are rewritten via a
temporary file and an atomic rename, exactly like the daemon does.

Differences from ``MessageArchive._cleanup_jsonl``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
1. Rows whose ``timestamp_utc`` is missing or unparseable are KEPT by
   default.  The daemon drops them, because ``_is_newer_than`` returns
   False for both cases and the caller treats False as expired.  For a
   one-shot purge of months of history that is the wrong way round to
   fail, so the default is inverted here; pass ``--drop-undated`` to
   match the daemon's behaviour.
2. Free space is checked before anything is written.  The rewrite needs
   room for a full second copy of the retained data alongside the
   original.  The daemon's OSError path is silent unless
   MESHCORE_WATCHLIST_DEBUG=1.
3. Progress is printed per million lines, so a 600 MB rxlog does not
   look like a hang.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

DEFAULT_ARCHIVE_DIR = Path.home() / ".meshcore-watchlist" / "archive"
PROGRESS_EVERY = 1_000_000


def human(n: float) -> str:
    """Format a byte count."""
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(n) < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TiB"


def parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp, or return None if unusable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def report(path: Path) -> None:
    """Count rows and print the observed timestamp range."""
    if not path.exists():
        print(f"  {path.name}: absent")
        return

    total = 0
    undated = 0
    malformed = 0
    hashes: set = set()
    hashed_rows = 0
    oldest: Optional[datetime] = None
    newest: Optional[datetime] = None

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total += 1
            if total % PROGRESS_EVERY == 0:
                print(f"    ... {total:,} rows", flush=True)
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            key = rec.get("message_hash") or ""
            if key:
                hashed_rows += 1
                hashes.add(key)
            ts = parse_ts(rec.get("timestamp_utc"))
            if ts is None:
                undated += 1
                continue
            if oldest is None or ts < oldest:
                oldest = ts
            if newest is None or ts > newest:
                newest = ts

    size = path.stat().st_size
    print(f"  {path.name}: {total:,} rows, {human(size)}")
    if oldest and newest:
        span = (newest - oldest).total_seconds() / 86400.0
        print(f"    oldest {oldest.isoformat()}")
        print(f"    newest {newest.isoformat()}")
        print(f"    span   {span:.1f} days")
    if undated:
        print(f"    rows without a usable timestamp_utc: {undated:,}")
    if malformed:
        print(f"    unparseable lines: {malformed:,}")
    dupes = hashed_rows - len(hashes)
    if dupes:
        pct = dupes / hashed_rows * 100.0
        print(
            f"    duplicate rows by message_hash: {dupes:,} "
            f"of {hashed_rows:,} ({pct:.1f}%) — see --dedupe"
        )


def purge(
    path: Path,
    cutoff: datetime,
    drop_undated: bool,
    dry_run: bool,
) -> Tuple[int, int]:
    """Rewrite *path* keeping only rows newer than *cutoff*.

    Returns ``(kept, dropped)``.
    """
    if not path.exists():
        print(f"  {path.name}: absent, skipping")
        return (0, 0)

    tmp = path.with_suffix(path.suffix + ".purge-tmp")
    kept = 0
    dropped = 0
    total = 0

    # Size at the moment the pass starts.  The archive is append-only,
    # so anything beyond this offset when the pass finishes was written
    # by the running daemon while we worked, and must be carried over
    # verbatim before the rename — otherwise those rows are destroyed by
    # it.  This is what makes the script safe to run against a live
    # service; without it a cron job silently ate the few minutes of
    # traffic that arrived during the rewrite.
    start_size = path.stat().st_size

    out = None
    try:
        if not dry_run:
            out = tmp.open("w", encoding="utf-8")
        with path.open("r", encoding="utf-8", errors="replace") as f:
            # Iterating a text handle disables tell(), so the byte
            # position is tracked by hand.  Only lines written before
            # the pass started are filtered; anything after start_size
            # is carried over verbatim at the end.
            consumed = 0
            for line in f:
                consumed += len(line.encode("utf-8"))
                if consumed > start_size:
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                total += 1
                if total % PROGRESS_EVERY == 0:
                    print(
                        f"    ... {total:,} rows "
                        f"(kept {kept:,}, dropped {dropped:,})",
                        flush=True,
                    )
                try:
                    rec = json.loads(stripped)
                except json.JSONDecodeError:
                    # Unparseable line: keep it unless undated rows are
                    # being dropped.  Never silently discard data the
                    # script cannot interpret.
                    if drop_undated:
                        dropped += 1
                        continue
                    kept += 1
                    if out:
                        out.write(stripped + "\n")
                    continue

                ts = parse_ts(rec.get("timestamp_utc"))
                if ts is None:
                    if drop_undated:
                        dropped += 1
                        continue
                    kept += 1
                    if out:
                        out.write(stripped + "\n")
                    continue

                if ts > cutoff:
                    kept += 1
                    if out:
                        out.write(stripped + "\n")
                else:
                    dropped += 1

        if out:
            end_size = path.stat().st_size
            out.flush()
            os.fsync(out.fileno())
            out.close()
            out = None
            if end_size > start_size:
                with tmp.open("ab") as dst, path.open("rb") as f:
                    f.seek(start_size)
                    while True:
                        block = f.read(1 << 20)
                        if not block:
                            break
                        dst.write(block)
                    dst.flush()
                    os.fsync(dst.fileno())
                print(
                    f"    carried over {human(end_size - start_size)} "
                    f"appended during the pass"
                )
            tmp.replace(path)
    except OSError as exc:
        print(f"  {path.name}: FAILED — {exc}", file=sys.stderr)
        if out:
            out.close()
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
    finally:
        if out:
            out.close()

    verb = "would keep" if dry_run else "kept"
    print(f"  {path.name}: {verb} {kept:,}, dropped {dropped:,}")
    return (kept, dropped)



def dedupe(path: Path, key_field: str, dry_run: bool) -> Tuple[int, int]:
    """Rewrite *path* keeping only the first row per *key_field* value.

    Order is preserved and the first occurrence wins, so the earliest
    ``timestamp_utc`` for a packet survives — replayed copies carry the
    replay instant and are the ones worth losing.

    Rows with an empty or missing key are always kept: without a key
    there is no evidence they are duplicates of anything.

    Returns ``(kept, dropped)``.
    """
    if not path.exists():
        print(f"  {path.name}: absent, skipping")
        return (0, 0)

    tmp = path.with_suffix(path.suffix + ".dedupe-tmp")
    seen: set = set()
    kept = 0
    dropped = 0
    total = 0
    start_size = path.stat().st_size

    out = None
    try:
        if not dry_run:
            out = tmp.open("w", encoding="utf-8")
        with path.open("r", encoding="utf-8", errors="replace") as f:
            # Iterating a text handle disables tell(), so the byte
            # position is tracked by hand.  Only lines written before
            # the pass started are filtered; anything after start_size
            # is carried over verbatim at the end.
            consumed = 0
            for line in f:
                consumed += len(line.encode("utf-8"))
                if consumed > start_size:
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                total += 1
                if total % PROGRESS_EVERY == 0:
                    print(
                        f"    ... {total:,} rows "
                        f"(kept {kept:,}, dropped {dropped:,})",
                        flush=True,
                    )
                try:
                    rec = json.loads(stripped)
                except json.JSONDecodeError:
                    kept += 1
                    if out:
                        out.write(stripped + "\n")
                    continue

                key = rec.get(key_field) or ""
                if not key:
                    kept += 1
                    if out:
                        out.write(stripped + "\n")
                    continue
                if key in seen:
                    dropped += 1
                    continue
                seen.add(key)
                kept += 1
                if out:
                    out.write(stripped + "\n")

        if out:
            end_size = path.stat().st_size
            out.flush()
            os.fsync(out.fileno())
            out.close()
            out = None
            if end_size > start_size:
                with tmp.open("ab") as dst, path.open("rb") as f:
                    f.seek(start_size)
                    while True:
                        block = f.read(1 << 20)
                        if not block:
                            break
                        dst.write(block)
                    dst.flush()
                    os.fsync(dst.fileno())
                print(
                    f"    carried over {human(end_size - start_size)} "
                    f"appended during the pass"
                )
            tmp.replace(path)
    except OSError as exc:
        print(f"  {path.name}: FAILED — {exc}", file=sys.stderr)
        if out:
            out.close()
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
    finally:
        if out:
            out.close()

    verb = "would keep" if dry_run else "kept"
    pct = (dropped / total * 100.0) if total else 0.0
    print(
        f"  {path.name}: {verb} {kept:,}, dropped {dropped:,} "
        f"duplicates ({pct:.1f}%)"
    )
    return (kept, dropped)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline retention purge for the meshcore-watchlist archive.",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=DEFAULT_ARCHIVE_DIR,
        help=f"Archive directory (default: {DEFAULT_ARCHIVE_DIR})",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Retention window in days (default: 7, matching config.py)",
    )
    parser.add_argument(
        "--dedupe",
        action="store_true",
        help=(
            "Remove duplicate rows instead of applying retention. Keeps "
            "the first occurrence of each message_hash. Use when the "
            "archive holds more rows than the source ever produced."
        ),
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Only count rows and print the timestamp range; write nothing.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do the retention pass but write nothing.",
    )
    parser.add_argument(
        "--drop-undated",
        action="store_true",
        help=(
            "Drop rows with a missing or unparseable timestamp_utc, "
            "matching the daemon's behaviour. Default is to keep them."
        ),
    )
    args = parser.parse_args()

    archive_dir: Path = args.archive_dir
    if not archive_dir.is_dir():
        print(f"No such archive directory: {archive_dir}", file=sys.stderr)
        return 2

    messages = archive_dir / "watchlist_messages.jsonl"
    rxlog = archive_dir / "watchlist_rxlog.jsonl"

    if args.report:
        print(f"Archive: {archive_dir}")
        report(messages)
        report(rxlog)
        return 0

    if args.dedupe:
        print(f"Archive: {archive_dir}")
        print("Mode:    de-duplicate on message_hash (retention not applied)")
        print()
        if not args.dry_run:
            needed = sum(
                p.stat().st_size for p in (messages, rxlog) if p.exists()
            )
            free = shutil.disk_usage(archive_dir).free
            print(f"Free space: {human(free)}, worst-case need: {human(needed)}")
            if free < needed:
                print(
                    "Refusing to run: not enough free space for the "
                    "temporary copy.",
                    file=sys.stderr,
                )
                return 1
            print()
        before = sum(p.stat().st_size for p in (messages, rxlog) if p.exists())
        dedupe(messages, "message_hash", args.dry_run)
        dedupe(rxlog, "message_hash", args.dry_run)
        if not args.dry_run:
            after = sum(
                p.stat().st_size for p in (messages, rxlog) if p.exists()
            )
            print()
            print(f"Archive size: {human(before)} -> {human(after)}")
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    print(f"Archive: {archive_dir}")
    print(f"Cutoff:  {cutoff.isoformat()}  (retaining {args.days} days)")
    print(
        "Undated rows: "
        + ("dropped" if args.drop_undated else "kept")
    )
    print()

    if not args.dry_run:
        # The rewrite needs room for a second copy alongside the
        # original.  Check before touching anything.
        needed = sum(
            p.stat().st_size for p in (messages, rxlog) if p.exists()
        )
        free = shutil.disk_usage(archive_dir).free
        print(f"Free space: {human(free)}, worst-case need: {human(needed)}")
        if free < needed:
            print(
                "Refusing to run: not enough free space for the temporary "
                "copy. Use --dry-run, free up space, or purge one file at "
                "a time with --archive-dir pointing at a copy.",
                file=sys.stderr,
            )
            return 1
        print()

    before = sum(p.stat().st_size for p in (messages, rxlog) if p.exists())
    purge(messages, cutoff, args.drop_undated, args.dry_run)
    purge(rxlog, cutoff, args.drop_undated, args.dry_run)

    if not args.dry_run:
        after = sum(p.stat().st_size for p in (messages, rxlog) if p.exists())
        print()
        print(f"Archive size: {human(before)} -> {human(after)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
