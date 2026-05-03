"""Core logic for the channel injector.

This module is deliberately framework-free and stdlib-only: it makes
HTTP calls with :mod:`urllib`, parses JSON with :mod:`json`, and uses
no third-party dependencies.  That keeps the cron entry simple — the
same ``.venv`` that the daemon uses already has everything it needs,
no extra ``pip install`` required.

Flow per source URL:

1. ``GET <source-url>``  →  list of channels (the JSON shape produced
   by upstream listings, see :func:`extract_channel_names`).  The
   response is read with a hard byte cap; over-large responses are
   rejected as a source error rather than read into memory in full.
2. ``GET <api-base>/api/v1/channels``  →  current watchlist.
3. For each name in (1) that is not in (2) and that fits the
   protocol-bounded length (ADR-007, 32 UTF-8 bytes):

   a. ``POST <api-base>/api/v1/channels?name=...``  →  add it.
   b. ``POST <api-base>/api/v1/rescan/by-name?...``  →  rescan the
      last *N* days so existing archive packets for the new channel
      get decoded.

A safety cap on adds per run prevents a misbehaving source from
seeding the watchlist with hundreds of names in one go: once the
cap is reached the run stops trying to add more, but already-added
channels keep their rescan submission.

Names already on the watchlist are skipped (no rescan).  The Public
channel is always skipped — it is system-managed and a fixed entry on
the daemon.

Errors on a single source URL are logged and the next URL is still
processed; the script exits non-zero only if at least one URL failed
or if the daemon could not be reached at all.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger("channel_injector")


# ---------------------------------------------------------------------------
# Constants (ADR-007 + safety caps)
# ---------------------------------------------------------------------------

#: Per ADR-007 / MeshCore Companion Protocol CMD_SET_CHANNEL: a channel
#: name on the wire is at most 32 bytes UTF-8.  Length is in bytes,
#: not codepoints — ``#café`` is 6 bytes, not 5.
CHANNEL_NAME_MAX_BYTES = 32

#: Default upper bound on the size of a single ``GET <source-url>``
#: response body, in bytes.  Pragmatic safety cap against a
#: misbehaving or compromised upstream that returns a multi-megabyte
#: payload.  Override per run via the CLI.
DEFAULT_MAX_SOURCE_BYTES = 1 * 1024 * 1024  # 1 MiB

#: Default upper bound on the number of channels added in a single
#: injector run.  An upstream that suddenly produces hundreds of new
#: names is suspicious; this cap keeps an unintended burst from
#: turning into hundreds of mutations.  Override via CLI.
DEFAULT_MAX_ADDS_PER_RUN = 50


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class InjectorResult:
    """Aggregate outcome of a full injector run.

    Attributes:
        added: Channel names that were newly added to the watchlist.
        skipped_existing: Channel names that were already on the
            watchlist and therefore not added or rescanned.
        skipped_invalid: Channel names from the source that were
            rejected (empty, non-hashtag-shaped, Public, control
            characters, …).
        rescans_submitted: Channel names for which a rescan job was
            successfully submitted.
        source_errors: ``(url, message)`` pairs for source URLs that
            could not be fetched or parsed.
        daemon_error: Set when the daemon itself was unreachable; in
            that case the entire run is aborted.
    """

    added: List[str] = field(default_factory=list)
    skipped_existing: List[str] = field(default_factory=list)
    skipped_invalid: List[Tuple[str, str]] = field(default_factory=list)
    rescans_submitted: List[str] = field(default_factory=list)
    source_errors: List[Tuple[str, str]] = field(default_factory=list)
    daemon_error: Optional[str] = None
    max_adds_reached: bool = False

    def has_failures(self) -> bool:
        """True iff the caller should exit non-zero."""
        return self.daemon_error is not None or bool(self.source_errors)

    def summary_line(self) -> str:
        """One-line, cron-friendly summary."""
        return (
            f"added={len(self.added)} "
            f"skipped_existing={len(self.skipped_existing)} "
            f"skipped_invalid={len(self.skipped_invalid)} "
            f"rescans={len(self.rescans_submitted)} "
            f"source_errors={len(self.source_errors)} "
            f"daemon_error={'yes' if self.daemon_error else 'no'} "
            f"max_adds_reached={'yes' if self.max_adds_reached else 'no'}"
        )


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only)
# ---------------------------------------------------------------------------


def _http_get_json(
    url: str,
    timeout: float,
    max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> object:
    """GET ``url`` and return parsed JSON.  Raises on any failure.

    The response body is read in chunks up to ``max_bytes``; if the
    server is still sending after that, :class:`ResponseTooLarge` is
    raised and the connection is closed.  This prevents a runaway or
    misbehaving upstream from forcing us to allocate hundreds of MB.
    """
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        # read(n) returns up to n bytes.  We ask for max_bytes + 1 to
        # detect overflow: if the response is exactly max_bytes long
        # the next read() returns b"" and we accept it; if it's
        # longer, we get max_bytes + 1 bytes back and reject.
        raw = resp.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ResponseTooLarge(
            f"response from {url} exceeds {max_bytes} bytes"
        )
    return json.loads(raw.decode("utf-8"))


class ResponseTooLarge(Exception):
    """Raised when a source response exceeds the configured byte cap."""


def _http_post(url: str, timeout: float) -> Tuple[int, object]:
    """POST to ``url`` (no body) and return ``(status, parsed_json_or_text)``.

    Treats 2xx as success and returns the parsed JSON body if the
    response declares ``application/json`` or the body parses as JSON.
    Non-2xx responses raise :class:`urllib.error.HTTPError`, which the
    caller is expected to catch — except when the API uses 200 vs 201
    to distinguish "added" from "already present", which both arrive
    here as success.
    """
    req = urllib.request.Request(url, method="POST")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = resp.getcode()
        raw = resp.read()
    body: object
    try:
        body = json.loads(raw.decode("utf-8")) if raw else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = raw.decode("utf-8", errors="replace")
    return status, body


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _is_safe_channel_name(name: str) -> bool:
    """True iff ``name`` is safe to forward to the daemon API.

    Rejects empty strings and names containing CR/LF or other control
    characters (header / log injection).  The daemon does its own
    validation as well; this is defence in depth.

    See :func:`_is_within_protocol_length` for the separate
    protocol-bound length check (ADR-007).
    """
    if not name:
        return False
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in name):
        return False
    return True


def _is_within_protocol_length(name: str) -> bool:
    """True iff ``name`` fits in the on-wire 32-byte UTF-8 channel field.

    Per ADR-007 / MeshCore Companion Protocol CMD_SET_CHANNEL.  The
    measure is **bytes**, not codepoints.  We do not synthesise the
    leading ``#`` here — input that lacks ``#`` is rejected upstream
    by :func:`run_injector` before reaching this check, so what we
    see is always the operator-visible name.
    """
    return len(name.encode("utf-8")) <= CHANNEL_NAME_MAX_BYTES


def extract_channel_names(payload: object) -> List[str]:
    """Pull channel names out of an upstream listing payload.

    Accepts the shape used by the upstream channel-listing service::

        {"channels": [{"hash": "#ruche", "name": "#ruche", ...}, ...]}

    Per agreed contract (Iteratie A, keuze 4) the source already
    delivers names with a leading ``#``; we do not synthesise one.

    Falls back to a top-level list of objects if no ``channels`` key
    is present, so a service that returns a bare list still works.
    The first non-empty of ``hash`` / ``name`` is used per entry.

    Returns:
        A de-duplicated list of names in input order.  Names that
        fail :func:`_is_safe_channel_name` are dropped silently here;
        the caller sees them as "missing from input" rather than as
        explicit invalid entries (the caller logs invalids it sees
        elsewhere — extraction errors stay quiet by design).
    """
    if isinstance(payload, dict) and isinstance(payload.get("channels"), list):
        items = payload["channels"]
    elif isinstance(payload, list):
        items = payload
    else:
        return []

    seen: Set[str] = set()
    out: List[str] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("hash") or entry.get("name") or ""
        if not isinstance(raw, str):
            continue
        name = raw.strip()
        if not _is_safe_channel_name(name):
            continue
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


# ---------------------------------------------------------------------------
# Daemon client
# ---------------------------------------------------------------------------


class WatchlistClient:
    """Thin HTTP client wrapping the daemon's ``/api/v1/`` surface."""

    def __init__(self, api_base: str, timeout: float = 10.0) -> None:
        self._base = api_base.rstrip("/")
        self._timeout = timeout

    def list_channels(self) -> List[str]:
        """Return current watchlist channel names (incl. ``Public``).

        No byte cap on the response: this is our own daemon and the
        watchlist size is bounded by what an operator manages — there
        is no untrusted upstream to defend against here.
        """
        url = f"{self._base}/api/v1/channels"
        # 16 MiB ceiling is generous beyond any realistic watchlist.
        payload = _http_get_json(url, timeout=self._timeout, max_bytes=16 * 1024 * 1024)
        if not isinstance(payload, list):
            raise ValueError(
                f"unexpected /api/v1/channels payload type: {type(payload).__name__}"
            )
        names: List[str] = []
        for ch in payload:
            if isinstance(ch, dict):
                name = ch.get("name", "")
                if isinstance(name, str) and name:
                    names.append(name)
        return names

    def add_channel(self, name: str) -> Tuple[int, object]:
        """POST to ``/api/v1/channels?name=...``.

        Returns ``(status, body)`` where status is 201 (newly added),
        200 (already present / Public), or raises on 4xx/5xx.
        """
        qs = urllib.parse.urlencode({"name": name})
        url = f"{self._base}/api/v1/channels?{qs}"
        return _http_post(url, timeout=self._timeout)

    def rescan_by_name(
        self,
        name: str,
        start_date: str,
        end_date: str,
    ) -> Tuple[int, object]:
        """POST to ``/api/v1/rescan/by-name?...``.

        The dates are ISO ``YYYY-MM-DD`` UTC days, per the existing
        endpoint contract.  Returns ``(status, body)``; the daemon
        uses 202 for "accepted".
        """
        qs = urllib.parse.urlencode({
            "channel_name": name,
            "start_date": start_date,
            "end_date": end_date,
        })
        url = f"{self._base}/api/v1/rescan/by-name?{qs}"
        return _http_post(url, timeout=self._timeout)


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------


def _is_public(name: str) -> bool:
    """Loose Public-channel match — mirrors daemon's tolerance."""
    return name.lstrip("#").strip().lower() == "public"


def _rescan_window_utc(rescan_days: int) -> Tuple[str, str]:
    """Return ``(start_date, end_date)`` ISO UTC for the rescan call.

    ``end_date`` is today (UTC), ``start_date`` is ``rescan_days - 1``
    days earlier so a request for "7 days" covers exactly 7 inclusive
    UTC days.
    """
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=max(rescan_days - 1, 0))
    return start.isoformat(), today.isoformat()


def run_injector(
    source_urls: Sequence[str],
    api_base: str,
    rescan_days: int = 7,
    timeout: float = 10.0,
    dry_run: bool = False,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    max_adds_per_run: int = DEFAULT_MAX_ADDS_PER_RUN,
) -> InjectorResult:
    """Execute one full injector pass.

    Args:
        source_urls: One or more upstream channel-listing URLs.  Must
            be non-empty.
        api_base: Base URL of the running daemon (e.g.
            ``http://localhost:8083``).
        rescan_days: How many UTC days the per-channel rescan should
            cover (default 7).
        timeout: Per-request HTTP timeout in seconds.
        dry_run: When True, no POSTs are sent to the daemon — only the
            comparison is reported.  The current watchlist is still
            fetched.
        max_source_bytes: Hard ceiling on the size of each individual
            source-URL response, in bytes.  Defends against a
            misbehaving / compromised upstream.
        max_adds_per_run: Hard ceiling on the number of channels
            actually added in this run.  Once reached, further
            ``--source-url`` candidates are skipped with reason
            ``max_adds_reached``.  Already-added channels still get
            their rescan submission.

    Returns:
        :class:`InjectorResult` describing what happened.
    """
    if not source_urls:
        raise ValueError("at least one source URL is required")

    result = InjectorResult()
    client = WatchlistClient(api_base=api_base, timeout=timeout)

    # 1. Snapshot the current watchlist once, up front.  If the
    # daemon is down there is nothing useful we can do — abort.
    try:
        current = client.list_channels()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        msg = f"could not reach daemon at {api_base}: {exc}"
        logger.error(msg)
        result.daemon_error = msg
        return result
    current_set: Set[str] = set(current)
    logger.info("daemon watchlist has %d channel(s)", len(current_set))

    # 2. Build the union of all "wanted" channels across every source
    # URL.  Source-level errors are recorded but do not abort: the
    # other URLs may still contribute usable channels.
    wanted: List[str] = []
    wanted_set: Set[str] = set()
    for url in source_urls:
        try:
            payload = _http_get_json(url, timeout=timeout, max_bytes=max_source_bytes)
        except ResponseTooLarge as exc:
            logger.warning("source %s rejected: %s", url, exc)
            result.source_errors.append((url, f"ResponseTooLarge: {exc}"))
            continue
        except (urllib.error.URLError, urllib.error.HTTPError,
                OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            msg = f"{type(exc).__name__}: {exc}"
            logger.warning("source %s failed: %s", url, msg)
            result.source_errors.append((url, msg))
            continue

        names = extract_channel_names(payload)
        logger.info("source %s yielded %d channel(s)", url, len(names))
        for n in names:
            if n not in wanted_set:
                wanted_set.add(n)
                wanted.append(n)

    # 3. Decide what to do per name.  Validation here matches the
    # daemon's acceptance rules so we don't fire pointless POSTs.
    start_date, end_date = _rescan_window_utc(rescan_days)
    logger.info("rescan window: %s … %s (UTC)", start_date, end_date)

    for name in wanted:
        if _is_public(name):
            result.skipped_invalid.append((name, "public_is_system_managed"))
            logger.debug("skip %r: Public is system-managed", name)
            continue
        if not name.startswith("#"):
            # Per agreed contract the source delivers hashtag-prefixed
            # names.  An entry without '#' is suspicious enough to
            # surface as invalid rather than silently fixing it.
            result.skipped_invalid.append((name, "missing_hashtag_prefix"))
            logger.debug("skip %r: missing leading '#'", name)
            continue
        if not _is_within_protocol_length(name):
            # ADR-007: a name that does not fit in 32 UTF-8 bytes can
            # never correspond to a real MeshCore channel.  Skip
            # client-side so the cron log says exactly why, and so we
            # don't waste a POST on something the daemon would reject
            # with 400 anyway.
            result.skipped_invalid.append((
                name,
                f"name_exceeds_{CHANNEL_NAME_MAX_BYTES}_bytes"
                f": got {len(name.encode('utf-8'))}",
            ))
            logger.debug(
                "skip %r: %d UTF-8 bytes > %d (ADR-007)",
                name, len(name.encode('utf-8')), CHANNEL_NAME_MAX_BYTES,
            )
            continue
        if name in current_set:
            result.skipped_existing.append(name)
            logger.debug("skip %r: already on watchlist", name)
            continue

        # Safety cap: stop adding once we hit the per-run ceiling.
        # Channels we've already added on this run keep their state;
        # we just stop *trying to add more*.  The flag in the result
        # makes this visible to the operator without raising.
        if len(result.added) >= max_adds_per_run:
            if not result.max_adds_reached:
                logger.warning(
                    "max-adds-per-run cap reached (%d); skipping remaining "
                    "candidates from this run",
                    max_adds_per_run,
                )
            result.max_adds_reached = True
            result.skipped_invalid.append((name, "max_adds_reached"))
            continue

        if dry_run:
            logger.info("[dry-run] would add %r and rescan %s..%s",
                        name, start_date, end_date)
            result.added.append(name)
            result.rescans_submitted.append(name)
            continue

        # 3a. Add channel.
        try:
            status, body = client.add_channel(name)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            logger.error("add %r failed: %s", name, exc)
            result.skipped_invalid.append((name, f"add_failed: {exc}"))
            continue

        if status == 201:
            logger.info("added %r", name)
            result.added.append(name)
        elif status == 200:
            # Daemon told us it was already there (race with another
            # injector run, GUI add, …) — treat as "existing".
            logger.info("daemon reports %r already present", name)
            result.skipped_existing.append(name)
            continue
        else:
            logger.warning("unexpected status %s for add %r: %s",
                           status, name, body)
            result.skipped_invalid.append((name, f"unexpected_status: {status}"))
            continue

        # 3b. Rescan only if we just added it.
        try:
            status, body = client.rescan_by_name(name, start_date, end_date)
        except urllib.error.HTTPError as exc:
            # 409 = rescan_busy.  That's not fatal — the channel is
            # added, the rescan can be re-issued later.  Log and move
            # on to the next channel (no point queueing more 409s).
            logger.warning("rescan %r got HTTP %s: %s",
                           name, exc.code, exc.reason)
        except (urllib.error.URLError, OSError) as exc:
            logger.warning("rescan %r failed: %s", name, exc)
        else:
            if status == 202:
                logger.info("rescan submitted for %r", name)
                result.rescans_submitted.append(name)
            else:
                logger.warning("unexpected status %s for rescan %r: %s",
                               status, name, body)

    return result


def fetch_and_inject(
    source_urls: Iterable[str],
    api_base: str = "http://localhost:8083",
    rescan_days: int = 7,
    timeout: float = 10.0,
    dry_run: bool = False,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    max_adds_per_run: int = DEFAULT_MAX_ADDS_PER_RUN,
) -> InjectorResult:
    """Public entry point — see :func:`run_injector`."""
    return run_injector(
        source_urls=list(source_urls),
        api_base=api_base,
        rescan_days=rescan_days,
        timeout=timeout,
        dry_run=dry_run,
        max_source_bytes=max_source_bytes,
        max_adds_per_run=max_adds_per_run,
    )
