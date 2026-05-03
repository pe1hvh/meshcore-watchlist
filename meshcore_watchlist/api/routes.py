"""
Public REST API route definitions for meshcore-watchlist.

Registers four read-only GET endpoints under ``/api/v1/`` on the
NiceGUI/FastAPI application instance — same surface as meshcore-gui.

    GET  /api/v1/stats
    GET  /api/v1/nodes      (always [])
    GET  /api/v1/messages
    GET  /api/v1/channels

Plus the rescan control-plane endpoints:

    POST /api/v1/rescan              → submit full rescan
    POST /api/v1/rescan/by-name      → submit per-channel rescan (0.2.6)
    GET  /api/v1/rescan/{job_id}     → job status

Plus the watchlist mutation endpoint (0.3.0, additive):

    POST /api/v1/channels            → add a hashtag channel to the watchlist

This is the single API entry point through which an out-of-process
client (e.g. ``tools.channel_injector``) can grow the watchlist without
violating the "WatchlistStore is the only mutator" invariant from
``CLAUDE.md``: the daemon still owns the store, the client merely
asks it to add a name.

Compared to 0.2.5 the per-channel rescan moved from
``POST /api/v1/rescan/{idx}`` to
``POST /api/v1/rescan/by-name?channel_name=...`` per ADR-001:
``channel_name`` is the stable channel identity, ``idx`` is a
vluchtige UI-positie that should never participate in API paths.
The old ``/rescan/{idx}`` endpoint is no longer registered;
clients calling it receive the FastAPI default 404.

Call :func:`register_routes` once from ``main.py`` after
:class:`SharedData` is constructed and before ``ui.run()`` is called.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from fastapi import HTTPException, Query
from fastapi.responses import JSONResponse
from nicegui import app as _nicegui_app

from meshcore_watchlist.config import debug_print, is_public_channel_name
from meshcore_watchlist.services.archive_rescanner import (
    InvalidRescanWindow,
    RescanBusyError,
    UnknownChannelName,
)
from meshcore_watchlist.services.public_api_service import (
    get_channels_payload,
    get_messages_payload,
    get_nodes_payload,
    get_stats_payload,
)
from meshcore_watchlist.services.watchlist_store import CHANNEL_NAME_MAX_BYTES

if TYPE_CHECKING:
    from meshcore_watchlist.core.shared_data import SharedData
    from meshcore_watchlist.services.archive_rescanner import RescanJobManager
    from meshcore_watchlist.services.watchlist_store import WatchlistStore


# CORS: same defaults as meshcore-gui.  Override via env if needed.
import os as _os
_CORS_ORIGINS = _os.environ.get(
    "MESHCORE_WATCHLIST_CORS_ORIGINS",
    "*",
)


def _cors_response(data: Any) -> JSONResponse:
    return JSONResponse(
        content=data,
        headers={
            "Access-Control-Allow-Origin": _CORS_ORIGINS,
            "Access-Control-Allow-Methods": "GET",
        },
    )


def register_routes(
    shared: "SharedData",
    rescan_manager: "RescanJobManager",
    store: "Optional[WatchlistStore]" = None,
) -> None:
    """Wire public API routes into the NiceGUI/FastAPI application.

    Args:
        shared: SharedData instance backing the read-only ``/api/v1/*``
            endpoints.
        rescan_manager: Rescan job manager backing the control-plane
            ``/api/v1/rescan*`` endpoints.
        store: Watchlist store backing ``POST /api/v1/channels``.  When
            omitted the channel-add endpoint is simply not registered —
            this preserves backward compatibility with any caller that
            still uses the 0.2.x two-argument signature.
    """

    @_nicegui_app.get(
        "/api/v1/stats",
        tags=["MeshCore Watchlist Public API"],
        summary="Network statistics for the last 72 hours",
        response_class=JSONResponse,
    )
    async def api_stats() -> JSONResponse:
        return _cors_response(get_stats_payload(shared))

    @_nicegui_app.get(
        "/api/v1/nodes",
        tags=["MeshCore Watchlist Public API"],
        summary="Known mesh nodes (always empty for watchlist)",
        response_class=JSONResponse,
    )
    async def api_nodes() -> JSONResponse:
        return _cors_response(get_nodes_payload(shared))

    @_nicegui_app.get(
        "/api/v1/messages",
        tags=["MeshCore Watchlist Public API"],
        summary="Paginated public and hashtag channel messages",
        response_class=JSONResponse,
    )
    async def api_messages(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> JSONResponse:
        return _cors_response(
            get_messages_payload(shared, limit=limit, offset=offset)
        )

    @_nicegui_app.get(
        "/api/v1/channels",
        tags=["MeshCore Watchlist Public API"],
        summary="Watchlist channel list",
        response_class=JSONResponse,
    )
    async def api_channels() -> JSONResponse:
        return _cors_response(get_channels_payload(shared))

    # ------------------------------------------------------------------
    # Rescan control plane
    # ------------------------------------------------------------------

    @_nicegui_app.post(
        "/api/v1/rescan",
        tags=["MeshCore Watchlist Rescan"],
        summary="Submit a full archive rescan over an explicit date window",
        response_class=JSONResponse,
    )
    async def api_rescan_full(
        start_date: str = Query(
            ...,
            description=(
                "Inclusive lower bound of the rescan window, "
                "ISO-8601 YYYY-MM-DD UTC day."
            ),
        ),
        end_date: str = Query(
            ...,
            description=(
                "Inclusive upper bound of the rescan window, "
                "ISO-8601 YYYY-MM-DD UTC day. Must be on or after "
                "start_date."
            ),
        ),
    ) -> JSONResponse:
        try:
            job = rescan_manager.submit(
                start_date=start_date,
                end_date=end_date,
                only_channel_name=None,
            )
        except InvalidRescanWindow as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_rescan_window",
                    "message": str(exc),
                },
            )
        except RescanBusyError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "rescan_busy",
                    "running_job_id": exc.running_job_id,
                },
            )
        return JSONResponse(status_code=202, content=job.to_dict())

    @_nicegui_app.post(
        "/api/v1/rescan/by-name",
        tags=["MeshCore Watchlist Rescan"],
        summary="Submit a per-channel archive rescan, scoped by channel name",
        response_class=JSONResponse,
    )
    async def api_rescan_by_name(
        channel_name: str = Query(
            default="",
            description=(
                "Channel name to scope the rescan to (e.g. \"#test\"). "
                "URL-encode '#' as '%23'.  Must be in the current "
                "watchlist; otherwise 404.  Empty / missing → 400."
            ),
        ),
        start_date: str = Query(
            ...,
            description=(
                "Inclusive lower bound of the rescan window, "
                "ISO-8601 YYYY-MM-DD UTC day."
            ),
        ),
        end_date: str = Query(
            ...,
            description=(
                "Inclusive upper bound of the rescan window, "
                "ISO-8601 YYYY-MM-DD UTC day. Must be on or after "
                "start_date."
            ),
        ),
    ) -> JSONResponse:
        # An empty / missing channel_name is a client error — surface
        # it as 400 *before* any other validation.  FastAPI's Query
        # default of ``""`` (rather than ``...``) lets us produce our
        # own message instead of FastAPI's generic
        # "field required" payload.
        if not channel_name:
            raise HTTPException(
                status_code=400,
                detail={"error": "missing_channel_name"},
            )
        try:
            job = rescan_manager.submit(
                start_date=start_date,
                end_date=end_date,
                only_channel_name=channel_name,
            )
        except InvalidRescanWindow as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_rescan_window",
                    "message": str(exc),
                },
            )
        except UnknownChannelName as exc:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "channel_name_not_in_watchlist",
                    "channel_name": exc.channel_name,
                },
            )
        except RescanBusyError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "rescan_busy",
                    "running_job_id": exc.running_job_id,
                },
            )
        return JSONResponse(status_code=202, content=job.to_dict())

    @_nicegui_app.get(
        "/api/v1/rescan/{job_id}",
        tags=["MeshCore Watchlist Rescan"],
        summary="Rescan job status",
        response_class=JSONResponse,
    )
    async def api_rescan_status(job_id: str) -> JSONResponse:
        job = rescan_manager.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "unknown_job", "job_id": job_id},
            )
        return _cors_response(job.to_dict())

    # ------------------------------------------------------------------
    # Watchlist mutation (additive, 0.3.0)
    # ------------------------------------------------------------------

    if store is not None:

        @_nicegui_app.post(
            "/api/v1/channels",
            tags=["MeshCore Watchlist Public API"],
            summary="Add a hashtag channel to the watchlist",
            response_class=JSONResponse,
        )
        async def api_channels_add(
            name: str = Query(
                default="",
                description=(
                    "Channel name to add (e.g. \"#test\"). URL-encode "
                    "'#' as '%23'.  A leading '#' is enforced server-"
                    "side if missing.  Public is system-managed: a "
                    "request for it returns 200 with added=false. "
                    "Empty / missing → 400."
                ),
            ),
        ) -> JSONResponse:
            # Reject empty / whitespace-only names with a structured
            # 400 before doing anything else.
            cleaned = name.strip() if name else ""
            if not cleaned:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "missing_name"},
                )

            # Defence against header / log injection: refuse any name
            # containing CR/LF or other control characters.  Channel
            # names are short printable strings; anything else is an
            # attack surface we don't need.
            if any(ord(c) < 0x20 or ord(c) == 0x7F for c in cleaned):
                raise HTTPException(
                    status_code=400,
                    detail={"error": "invalid_name"},
                )

            # Per ADR-007: enforce the MeshCore Companion Protocol's
            # 32-byte UTF-8 limit on the channel name field.  Length
            # is in bytes, not codepoints.  We check against the
            # name as the operator submitted it; WatchlistStore.add
            # may add a leading '#' on top, but that one byte is
            # accounted for by the strict-less-than-or-equal-to-32
            # boundary together with the post-prefix re-check inside
            # the store.
            cleaned_bytes = len(cleaned.encode("utf-8"))
            # Account for a possible '#' that the store will prepend
            # for non-Public, non-hashtag input — that costs 1 byte
            # on the wire.  Public is handled below before we get
            # here in the long-name path, so this check is
            # conservative for all other inputs.
            effective_bytes = cleaned_bytes + (
                0 if cleaned.startswith("#") or is_public_channel_name(cleaned)
                else 1
            )
            if effective_bytes > CHANNEL_NAME_MAX_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "name_too_long",
                        "max_bytes": CHANNEL_NAME_MAX_BYTES,
                        "got_bytes": effective_bytes,
                    },
                )

            # Public is system-managed and always present at idx 0.
            # Surface that as a no-op success rather than a duplicate
            # error: the client's intent ("make sure this name is on
            # the watchlist") is satisfied.
            if is_public_channel_name(cleaned):
                return JSONResponse(
                    status_code=200,
                    content={
                        "name": "Public",
                        "added": False,
                        "reason": "public_is_system_managed",
                    },
                )

            # WatchlistStore.add() enforces the leading '#', persists
            # the file, and notifies subscribers (decoder key map,
            # GUI, …) — exactly what we want.  Returns False only for
            # duplicates (already on the list).
            added = store.add(cleaned)
            normalised = cleaned if cleaned.startswith("#") else "#" + cleaned

            if added:
                return JSONResponse(
                    status_code=201,
                    content={"name": normalised, "added": True},
                )
            return JSONResponse(
                status_code=200,
                content={
                    "name": normalised,
                    "added": False,
                    "reason": "already_on_watchlist",
                },
            )

    debug_print(
        "Public API registered: /api/v1/stats, /api/v1/nodes, "
        "/api/v1/messages, /api/v1/channels (GET" +
        (" + POST" if store is not None else "") + "), "
        "/api/v1/rescan (POST full + by-name, GET status)"
    )
