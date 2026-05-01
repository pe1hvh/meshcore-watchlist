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

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Query
from fastapi.responses import JSONResponse
from nicegui import app as _nicegui_app

from meshcore_watchlist.config import debug_print
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

if TYPE_CHECKING:
    from meshcore_watchlist.core.shared_data import SharedData
    from meshcore_watchlist.services.archive_rescanner import RescanJobManager


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
) -> None:
    """Wire public API routes into the NiceGUI/FastAPI application.

    Args:
        shared: SharedData instance backing the read-only ``/api/v1/*``
            endpoints.
        rescan_manager: Rescan job manager backing the control-plane
            ``/api/v1/rescan*`` endpoints.
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

    debug_print(
        "Public API registered: /api/v1/stats, /api/v1/nodes, "
        "/api/v1/messages, /api/v1/channels, "
        "/api/v1/rescan (POST full + by-name, GET status)"
    )
