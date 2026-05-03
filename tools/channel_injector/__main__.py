"""CLI entry point for the channel injector.

Usage::

    python -m tools.channel_injector \\
        --source-url https://example.org/channels.json \\
        [--source-url https://other.example/list.json ...] \\
        [--api-base http://localhost:8083] \\
        [--rescan-days 7] \\
        [--timeout 10] \\
        [--dry-run] [-v]

Exit codes:

    0 — success: every source URL fetched, daemon reachable.  Some
        channels may have been skipped (already present, invalid).
    1 — argument / configuration error.
    2 — runtime failure: daemon unreachable or at least one source
        URL failed.  Stderr will contain the details; the run still
        attempted as much as it could.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional, Sequence

from tools.channel_injector import __version__
from tools.channel_injector.injector import (
    DEFAULT_MAX_ADDS_PER_RUN,
    DEFAULT_MAX_SOURCE_BYTES,
    fetch_and_inject,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="channel_injector",
        description=(
            "Fetch channel listings over HTTP and seed missing "
            "hashtag channels into a running meshcore-watchlist "
            "daemon, then trigger a per-channel rescan."
        ),
    )
    parser.add_argument(
        "--source-url",
        action="append",
        metavar="URL",
        help=(
            "Upstream channel-listing URL.  May be passed multiple "
            "times to merge several sources.  At least one is "
            "required (no default — explicit is safer than implicit "
            "for a job that mutates the watchlist)."
        ),
    )
    parser.add_argument(
        "--api-base",
        default="http://localhost:8083",
        metavar="URL",
        help=(
            "Base URL of the meshcore-watchlist daemon "
            "(default: %(default)s)."
        ),
    )
    parser.add_argument(
        "--rescan-days",
        type=int,
        default=7,
        metavar="N",
        help=(
            "Number of UTC days the per-channel rescan should cover "
            "(default: %(default)s).  Window is "
            "[today - (N - 1)d ... today], inclusive."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        metavar="SECONDS",
        help="Per-request HTTP timeout (default: %(default)s).",
    )
    parser.add_argument(
        "--max-source-bytes",
        type=int,
        default=DEFAULT_MAX_SOURCE_BYTES,
        metavar="BYTES",
        help=(
            "Hard ceiling on the size of a single source-URL "
            "response body (default: %(default)s, i.e. 1 MiB).  "
            "Defends against a misbehaving or compromised upstream."
        ),
    )
    parser.add_argument(
        "--max-adds-per-run",
        type=int,
        default=DEFAULT_MAX_ADDS_PER_RUN,
        metavar="N",
        help=(
            "Hard ceiling on the number of channels added in a "
            "single run (default: %(default)s).  Once reached, "
            "remaining new candidates are skipped with reason "
            "'max_adds_reached' in the summary."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compare only; do not POST anything to the daemon.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help=(
            "Increase log verbosity.  -v = INFO (default is WARNING), "
            "-vv = DEBUG."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def _configure_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)
    log = logging.getLogger("channel_injector.cli")

    source_urls: List[str] = list(args.source_url or [])
    if not source_urls:
        # argparse can't enforce "at least one of an action='append'
        # flag" natively without forcing a default — we do it here to
        # keep the contract explicit (Iteratie A keuze 5).
        parser.error("at least one --source-url is required")
        return 1  # unreachable, parser.error exits

    if args.rescan_days < 1:
        parser.error("--rescan-days must be >= 1")
        return 1

    if args.max_source_bytes < 1024:
        parser.error("--max-source-bytes must be >= 1024")
        return 1

    if args.max_adds_per_run < 1:
        parser.error("--max-adds-per-run must be >= 1")
        return 1

    log.info("channel_injector v%s starting (%d source(s), api=%s, dry_run=%s, "
             "max_source_bytes=%d, max_adds_per_run=%d)",
             __version__, len(source_urls), args.api_base, args.dry_run,
             args.max_source_bytes, args.max_adds_per_run)

    result = fetch_and_inject(
        source_urls=source_urls,
        api_base=args.api_base,
        rescan_days=args.rescan_days,
        timeout=args.timeout,
        dry_run=args.dry_run,
        max_source_bytes=args.max_source_bytes,
        max_adds_per_run=args.max_adds_per_run,
    )

    # Always emit a one-line summary at WARNING level so a quiet cron
    # entry still leaves a single audit line per run.
    logging.getLogger("channel_injector").warning(
        "run complete: %s", result.summary_line()
    )

    if result.added:
        log.info("added: %s", ", ".join(result.added))
    if result.skipped_existing:
        log.info("already present: %s", ", ".join(result.skipped_existing))
    if result.skipped_invalid:
        for name, reason in result.skipped_invalid:
            log.info("skipped %r: %s", name, reason)
    if result.source_errors:
        for url, msg in result.source_errors:
            log.warning("source %s: %s", url, msg)
    if result.daemon_error:
        log.error("daemon: %s", result.daemon_error)

    return 2 if result.has_failures() else 0


if __name__ == "__main__":
    sys.exit(main())
