"""
Channel-priority lookup for the rescan decode loop (template 2,
mechanism 1).

The packet decoder, by design, attempts every registered channel key
against every GroupText packet and stops at the first successful
decryption.  On a small watchlist (≤ 10 channels) the per-packet cost
is negligible.  On a 426-channel install the cost dominates the rescan:
~50 million AES attempts to rescan 114 K packets, hours of wall-clock
on a Pi 5.

The domca.nl collector knows, from historical traffic, *which* channels
account for the bulk of public messages.  Re-using that knowledge as a
priority order at the start of a rescan job lets the decoder hit its
break-on-first-match early on the dominant channels.  Top-three on the
current snapshot covers ~75 % of traffic; top-ten covers ~87 %.

This module owns the HTTP fetch and the watchlist-aware ordering.  It
returns a plain ``List[str]`` of channel names (per ADR-001 the stable
channel identity) that the rescanner hands to ``PacketDecoder.decode()``
as ``priority_name_order``.  All HTTP failure modes — timeout,
connection error, non-200, malformed JSON — are treated as a non-event
and produce an empty list, which the decoder interprets as "use
watchlist order".  The rescan therefore never blocks or fails on a
flaky domca.nl reachability.

Why ``urllib`` and not ``requests``: the watchlist service has no
``requests`` dependency today and we don't want to add one for a
single GET.

Bewust niet gebruikt:
~~~~~~~~~~~~~~~~~~~~~
The API also returns ``first_received_at`` and ``last_received_at``
per channel.  These look attractive for "skip channels that didn't
exist during the rescan window", but in domca's database those
timestamps were corrupted by an earlier truncate-and-faulty-rescan
incident: a number of channels show ``first_received_at = 2026-04-22``
while in reality they existed long before that.  Pre-filtering on a
field we know is wrong would silently drop matches.  The user picks
the rescan window explicitly via ``start_date`` / ``end_date``
(mechanism 2); the API is used purely for ranking.

A future session that considers re-introducing those fields: only do
so after confirming with the operator that domca's timestamps have
been recomputed from the archive.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Iterable, List

from meshcore_watchlist.config import VERSION, debug_print


# Default endpoint.  Overridable per call so tests can point at a
# fixture and ops can repoint at a staging mirror without touching code.
DEFAULT_PRIORITY_API_URL: str = (
    "https://www.domca.nl/api/meshcore/channel_statistics.php"
)

# Hard timeout: rescan must not block on a slow server.  The whole
# point is to fail fast and fall back to watchlist order.
_HTTP_TIMEOUT_SECONDS: float = 5.0


def fetch_priority_name_order(
    watchlist_channels: Iterable[dict],
    *,
    api_url: str = DEFAULT_PRIORITY_API_URL,
    timeout: float = _HTTP_TIMEOUT_SECONDS,
) -> List[str]:
    """Return a priority-ordered list of channel **names**.

    Two-tier ordering:

      1. Channels present in both the API response and the watchlist,
         sorted by the API's order (which the API documents as
         ``aantal_berichten`` descending).  These are the "high-prior"
         channels likely to match.
      2. All remaining watchlist channels in their existing watchlist
         order.  These are the "candidate" channels with no recent
         traffic on domca.

    Channels mentioned by the API but absent from the watchlist are
    ignored (we have no key for them).  Channels in the watchlist but
    absent from the API fall through to tier 2.

    Args:
        watchlist_channels: Iterable of channel dicts as returned by
            ``WatchlistStore.list_channels()``.  Each must carry
            ``name`` (str); ``idx`` is read for a stable tier-2
            ordering but does not appear in the output.
        api_url: HTTPS URL of the channel_statistics endpoint.
            Defaults to the production domca endpoint.
        timeout: Seconds before the HTTP request is aborted.  Defaults
            to ``_HTTP_TIMEOUT_SECONDS``.

    Returns:
        List of channel-name strings, possibly empty.  An empty list
        means "no priority signal available, use decoder default
        order" — the decoder treats that as the no-op case.

    Failure modes (all return an empty list):
        - URLError / timeout / non-2xx HTTP response
        - JSON parse error
        - Malformed payload (not a list, items missing ``name``)
        - Unexpected exception (logged at DEBUG, never re-raised)
    """
    # Build the watchlist name set first (with idx for tier-2 ordering)
    # before the HTTP call, so a hung request can't waste time when we
    # don't even have channels to prioritize.
    watchlist_names: set = set()
    watchlist_name_order: List[str] = []
    # Sort watchlist by idx so tier-2 ordering is deterministic across
    # calls regardless of the iterable's iteration order.
    sorted_channels = sorted(
        (ch for ch in watchlist_channels if ch.get("name")),
        key=lambda c: c.get("idx", 0),
    )
    for ch in sorted_channels:
        name = ch.get("name", "") or ""
        if not name or name in watchlist_names:
            continue
        watchlist_names.add(name)
        watchlist_name_order.append(name)

    if not watchlist_names:
        return []

    # Fetch and parse.  Any failure short-circuits to an empty list.
    api_names: List[str] = []
    try:
        req = urllib.request.Request(
            api_url,
            headers={"User-Agent": f"meshcore-watchlist/{VERSION}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            if status >= 400:
                debug_print(
                    f"channel_priority: HTTP {status} from {api_url}; "
                    f"falling back to watchlist order"
                )
                return []
            raw = resp.read()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            debug_print(
                f"channel_priority: malformed JSON from {api_url}: {exc}; "
                f"falling back to watchlist order"
            )
            return []
        if not isinstance(data, list):
            debug_print(
                f"channel_priority: unexpected payload shape "
                f"({type(data).__name__}); falling back to watchlist order"
            )
            return []
        for item in data:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str) and name:
                api_names.append(name)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        debug_print(
            f"channel_priority: HTTP error fetching {api_url}: {exc}; "
            f"falling back to watchlist order"
        )
        return []
    except Exception as exc:  # pragma: no cover - defensive
        debug_print(
            f"channel_priority: unexpected error fetching {api_url}: "
            f"{type(exc).__name__}: {exc}; "
            f"falling back to watchlist order"
        )
        return []

    # Tier 1: API-named channels that we have a key for, in API order.
    seen: set = set()
    priority: List[str] = []
    for name in api_names:
        if name not in watchlist_names or name in seen:
            continue
        priority.append(name)
        seen.add(name)

    # Tier 2: remaining watchlist channels in their existing order.
    for name in watchlist_name_order:
        if name in seen:
            continue
        priority.append(name)
        seen.add(name)

    return priority
