"""
Public REST API business logic for meshcore-watchlist.

Returns the same JSON shapes as meshcore-gui's public_api_service so
that domca.nl and other downstream consumers work without changes.

Key differences from meshcore-gui:
    * /api/v1/nodes returns an empty list — the watchlist service does
      not maintain a contact list (no advert reception, no device).
    * /api/v1/stats returns zero for ``active_clients``,
      ``active_repeaters`` and ``active_room_servers`` — same reason.
    * /api/v1/channels reflects the watchlist contents; ``is_private``
      is always ``False`` because watchlist entries are by construction
      public/hashtag channels.
    * /api/v1/messages serves the watchlist's own decoded messages.

Channel-type rules (same as meshcore-gui — for forward compatibility):
    idx == 0               → Public  — always expose
    name.startswith('#')   → Hashtag — always expose
    anything else          → Private — NEVER expose
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from meshcore_watchlist.core.shared_data import SharedData


STATS_PERIOD_HOURS: int = 72
_STATS_FETCH_LIMIT: int = 50_000


# ---------------------------------------------------------------------------
# Channel classification
# ---------------------------------------------------------------------------

def is_public_channel(idx: Optional[int], name: str) -> bool:
    """Return True for public (idx 0) or hashtag (#-prefixed) channels."""
    if idx == 0:
        return True
    if name and name.startswith("#"):
        return True
    return False


def is_private_channel(idx: Optional[int], name: str) -> bool:
    return not is_public_channel(idx, name)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def get_stats_payload(shared: "SharedData") -> Dict[str, Any]:
    """Network statistics over the last STATS_PERIOD_HOURS hours."""
    archive = shared.archive
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=STATS_PERIOD_HOURS)

    messages: List[Dict[str, Any]] = []
    if archive is not None:
        raw, _ = archive.query_messages(
            after=cutoff,
            limit=_STATS_FETCH_LIMIT,
            offset=0,
        )
        messages = [
            m for m in raw
            if is_public_channel(m.get("channel"), m.get("channel_name", ""))
        ]

    unique_senders: set = set()
    hops_values: List[int] = []
    hour_counter: Counter = Counter()

    for msg in messages:
        sender = msg.get("sender") or msg.get("sender_pubkey", "")
        if sender:
            unique_senders.add(sender)

        path_len = msg.get("path_len", 0) or 0
        if path_len > 0:
            hops_values.append(path_len)

        ts_str = msg.get("timestamp_utc", "")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str)
                hour_counter[ts.hour] += 1
            except (ValueError, TypeError):
                pass

    avg_hops = round(sum(hops_values) / len(hops_values), 2) if hops_values else 0.0
    peak_hour = hour_counter.most_common(1)[0][0] if hour_counter else None

    return {
        "generated_at": now_utc.isoformat(),
        "period_hours": STATS_PERIOD_HOURS,
        "total_messages": len(messages),
        "unique_senders": len(unique_senders),
        # Watchlist does not track contacts — fixed at zero.
        "active_clients": 0,
        "active_repeaters": 0,
        "active_room_servers": 0,
        "avg_hops": avg_hops,
        "peak_hour": peak_hour,
    }


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def get_nodes_payload(_shared: "SharedData") -> List[Dict[str, Any]]:
    """Return an empty list — watchlist does not track contacts."""
    return []


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def get_messages_payload(
    shared: "SharedData",
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """Paginated public + hashtag messages from the watchlist archive."""
    limit = min(max(1, limit), 500)
    offset = max(0, offset)

    archive = shared.archive
    if archive is None:
        return {"total": 0, "limit": limit, "offset": offset, "items": []}

    fetch_limit = offset + limit + 1000
    raw, _ = archive.query_messages(limit=fetch_limit, offset=0)

    public_msgs = [
        m for m in raw
        if is_public_channel(m.get("channel"), m.get("channel_name", ""))
    ]

    total = len(public_msgs)
    page = public_msgs[offset: offset + limit]

    items: List[Dict[str, Any]] = []
    for i, msg in enumerate(page):
        items.append({
            "id":            offset + i + 1,
            "channel_idx":   msg.get("channel"),
            "channel_name":  msg.get("channel_name", ""),
            "sender":        msg.get("sender", ""),
            "sender_pubkey": msg.get("sender_pubkey", "") or "",
            "text":          msg.get("text", ""),
            "timestamp":     msg.get("timestamp_utc"),
            "hops":          msg.get("path_len", 0) or 0,
            "path_hashes":   msg.get("path_hashes") or [],
            "path_names":    msg.get("path_names")  or [],
        })

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------

def get_channels_payload(shared: "SharedData") -> List[Dict[str, Any]]:
    """Watchlist channels — always public (idx >= 0, hashtag names)."""
    channels: List[Dict[str, Any]] = []

    with shared.lock:
        for ch in shared.channels:
            idx = ch.get("idx")
            name = ch.get("name", "")
            channels.append({
                "idx": idx,
                "name": name,
                "is_private": is_private_channel(idx, name),
            })

    return channels
