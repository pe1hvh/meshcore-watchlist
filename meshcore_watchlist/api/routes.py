"""
Public REST API route definitions for meshcore-watchlist.

Registers four read-only GET endpoints under ``/api/v1/`` on the
NiceGUI/FastAPI application instance — same surface as meshcore-gui.

    GET /api/v1/stats
    GET /api/v1/nodes      (always [])
    GET /api/v1/messages
    GET /api/v1/channels

Call :func:`register_routes` once from ``main.py`` after
:class:`SharedData` is constructed and before ``ui.run()`` is called.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import Query
from fastapi.responses import JSONResponse
from nicegui import app as _nicegui_app

from meshcore_watchlist.config import debug_print
from meshcore_watchlist.services.public_api_service import (
    get_channels_payload,
    get_messages_payload,
    get_nodes_payload,
    get_stats_payload,
)

if TYPE_CHECKING:
    from meshcore_watchlist.core.shared_data import SharedData


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


def register_routes(shared: "SharedData") -> None:
    """Wire public API routes into the NiceGUI/FastAPI application."""

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

    debug_print(
        "Public API registered: /api/v1/stats, /api/v1/nodes, "
        "/api/v1/messages, /api/v1/channels"
    )
