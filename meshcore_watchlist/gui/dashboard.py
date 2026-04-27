"""
NiceGUI dashboard for meshcore-watchlist.

Three tabs:
    * Watchlist  — CRUD over watchlist.json (channel_panel)
    * Messages   — decoded GroupText messages, optionally filtered
    * RX Log     — every raw packet seen on the air

Read-only with respect to the radio (no TX).  Writes only happen
to the local watchlist.json via WatchlistStore.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from nicegui import ui

from meshcore_watchlist.config import VERSION
from meshcore_watchlist.core.shared_data import SharedData
from meshcore_watchlist.services.watchlist_store import WatchlistStore


def build_dashboard(shared: SharedData, store: WatchlistStore) -> None:
    """Mount the dashboard UI on the root NiceGUI page."""

    @ui.page("/")
    def index() -> None:
        _render_index(shared, store)


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------

def _render_index(shared: SharedData, store: WatchlistStore) -> None:
    ui.add_head_html(
        "<style>body{background:#101418;color:#e0e0e0;font-family:sans-serif;}</style>"
    )

    with ui.header().classes("items-center justify-between"):
        ui.label(f"meshcore-watchlist v{VERSION}").classes("text-lg font-bold")
        status_label = ui.label(shared.status).classes("text-sm opacity-75")

    with ui.tabs().classes("w-full") as tabs:
        tab_watchlist = ui.tab("Watchlist", icon="list")
        tab_messages = ui.tab("Messages", icon="chat")
        tab_rxlog = ui.tab("RX Log", icon="radio")

    with ui.tab_panels(tabs, value=tab_watchlist).classes("w-full"):
        with ui.tab_panel(tab_watchlist):
            wl_table = _build_watchlist_panel(store)

        with ui.tab_panel(tab_messages):
            msg_table = _build_messages_panel()

        with ui.tab_panel(tab_rxlog):
            rx_table = _build_rxlog_panel()

    # Initial render — populate every table from the current snapshot
    # before the timer starts.  Required because SharedData's *_updated
    # flags are process-global: an earlier browser session may already
    # have ticked and cleared them, leaving freshly-mounted tables empty
    # until the next mutation flips a flag back on.  Each new page
    # session must therefore prime its own tables explicitly.
    initial = shared.get_snapshot()
    wl_table.rows = [
        {"idx": c["idx"], "name": c["name"]} for c in initial["channels"]
    ]
    wl_table.update()
    msg_table.rows = [_msg_to_row(m) for m in reversed(initial["messages"])][:200]
    msg_table.update()
    rx_table.rows = [_rx_to_row(r) for r in reversed(initial["rx_log"])][:50]
    rx_table.update()

    # Render loop — refresh every second while the page is open.
    def _refresh() -> None:
        snap = shared.get_snapshot()
        status_label.text = snap["status"]

        if snap["channels_updated"]:
            wl_table.rows = [
                {"idx": c["idx"], "name": c["name"]} for c in snap["channels"]
            ]
            wl_table.update()

        if snap["messages_updated"]:
            msg_table.rows = [_msg_to_row(m) for m in reversed(snap["messages"])][:200]
            msg_table.update()

        if snap["rxlog_updated"]:
            rx_table.rows = [_rx_to_row(r) for r in reversed(snap["rx_log"])][:50]
            rx_table.update()

        shared.clear_update_flags()

    ui.timer(1.0, _refresh)


# ---------------------------------------------------------------------------
# Tab panels
# ---------------------------------------------------------------------------

def _build_watchlist_panel(store: WatchlistStore):
    """Watchlist CRUD: add hashtag, list, remove."""
    with ui.row().classes("w-full items-end gap-2"):
        name_input = ui.input(
            label="Hashtag channel",
            placeholder="#mc-radar",
        ).classes("flex-grow")

        def _on_add() -> None:
            value = name_input.value or ""
            if store.add(value):
                name_input.value = ""
                ui.notify(f"Added {value}", color="positive")
            else:
                ui.notify("Empty or duplicate", color="warning")

        ui.button("Add", icon="add", on_click=_on_add).props("color=primary")

    columns = [
        {"name": "idx", "label": "Idx", "field": "idx", "align": "left"},
        {"name": "name", "label": "Name", "field": "name", "align": "left"},
        {"name": "actions", "label": "", "field": "actions"},
    ]
    table = ui.table(columns=columns, rows=[], row_key="idx").classes("w-full")

    table.add_slot(
        "body-cell-actions",
        r"""
        <q-td :props="props">
            <q-btn dense flat icon="delete" color="negative"
                   @click="$parent.$emit('remove', props.row)" />
        </q-td>
        """,
    )

    def _on_remove(e) -> None:
        idx = e.args.get("idx")
        if idx is not None and store.remove(int(idx)):
            ui.notify("Removed", color="positive")

    table.on("remove", _on_remove)
    return table


def _build_messages_panel():
    """Decoded GroupText messages — read-only table."""
    columns = [
        {"name": "time", "label": "Time", "field": "time", "align": "left"},
        {"name": "channel_name", "label": "Channel", "field": "channel_name"},
        {"name": "sender", "label": "Sender", "field": "sender"},
        {"name": "text", "label": "Message", "field": "text", "align": "left"},
        {"name": "snr", "label": "SNR", "field": "snr"},
        {"name": "path_len", "label": "Hops", "field": "path_len"},
    ]
    return ui.table(columns=columns, rows=[], row_key="message_hash").classes("w-full")


def _build_rxlog_panel():
    """Raw RX log — every packet, decoded or not."""
    columns = [
        {"name": "time", "label": "Time", "field": "time"},
        {"name": "payload_type", "label": "Type", "field": "payload_type"},
        {"name": "hops", "label": "Hops", "field": "hops"},
        {"name": "snr", "label": "SNR", "field": "snr"},
        {"name": "rssi", "label": "RSSI", "field": "rssi"},
        {"name": "sender", "label": "Sender", "field": "sender"},
        {"name": "path", "label": "Path", "field": "path", "align": "left"},
    ]
    return ui.table(columns=columns, rows=[], row_key="time").classes("w-full")


# ---------------------------------------------------------------------------
# Row mappers
# ---------------------------------------------------------------------------

def _msg_to_row(m) -> Dict:
    return {
        "time": getattr(m, "time", ""),
        "channel_name": getattr(m, "channel_name", "") or f"ch{getattr(m, 'channel', '?')}",
        "sender": getattr(m, "sender", ""),
        "text": getattr(m, "text", ""),
        "snr": getattr(m, "snr", None),
        "path_len": getattr(m, "path_len", 0),
        "message_hash": getattr(m, "message_hash", ""),
    }


def _rx_to_row(r) -> Dict:
    return {
        "time": getattr(r, "time", ""),
        "payload_type": getattr(r, "payload_type", "?"),
        "hops": getattr(r, "hops", 0),
        "snr": getattr(r, "snr", 0),
        "rssi": getattr(r, "rssi", 0),
        "sender": getattr(r, "sender", ""),
        "path": " → ".join(getattr(r, "path_names", []) or []),
    }
