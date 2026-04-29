"""
NiceGUI dashboard for meshcore-watchlist.

Three tabs:
    * Watchlist  — CRUD over watchlist.json (channel_panel) + rescan
    * Messages   — decoded GroupText messages, optionally filtered
    * RX Log     — every raw packet seen on the air

Read-only with respect to the radio (no TX).  Writes only happen
to the local watchlist.json via WatchlistStore, and to the message /
rxlog archive via the rescan job (when triggered).
"""

from __future__ import annotations

from typing import Dict, List, Optional, TYPE_CHECKING

from nicegui import ui

from meshcore_watchlist.config import VERSION, debug_print
from meshcore_watchlist.core.shared_data import SharedData
from meshcore_watchlist.services.archive_rescanner import RescanBusyError
from meshcore_watchlist.services.watchlist_store import WatchlistStore

if TYPE_CHECKING:
    from meshcore_watchlist.services.archive_rescanner import RescanJobManager


def build_dashboard(
    shared: SharedData,
    store: WatchlistStore,
    rescan_manager: "RescanJobManager",
) -> None:
    """Mount the dashboard UI on the root NiceGUI page."""

    @ui.page("/")
    def index() -> None:
        _render_index(shared, store, rescan_manager)


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------

def _render_index(
    shared: SharedData,
    store: WatchlistStore,
    rescan_manager: "RescanJobManager",
) -> None:
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
            wl_table = _build_watchlist_panel(store, rescan_manager)

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

def _build_watchlist_panel(
    store: WatchlistStore,
    rescan_manager: "RescanJobManager",
):
    """Watchlist CRUD: add hashtag, list, remove — plus the rescan
    archive trigger and a progress widget."""

    # ── Add row ───────────────────────────────────────────────────────
    # Public is system-managed (always present at idx=0, see
    # WatchlistStore._ensure_public_invariant_locked) so this input is
    # only for user-managed hashtag channels.
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

    # ── Rescan row + progress widget ─────────────────────────────────
    # Per-page state: which job_id this browser session is watching.
    # Stored as a single-element list so the closure can mutate it.
    watched_job: List[Optional[str]] = [None]

    with ui.row().classes("w-full items-center gap-2 q-mt-md"):
        rescan_btn = ui.button(
            "Rescan archive",
            icon="refresh",
        ).props("color=secondary")
        progress_label = ui.label("").classes("text-sm opacity-75")
        progress_bar = ui.linear_progress(value=0).classes("flex-grow")
        progress_bar.visible = False

    def _start_job(only_channel_idx: Optional[int], label: str) -> None:
        """Submit a rescan job and wire the GUI to watch its progress.

        Shared by the full-rescan button and the per-channel buttons
        in the table action column.  *label* is a short human string
        ("full archive", "#mc-radar") used in toast notifications.
        """
        try:
            job = rescan_manager.submit(only_channel_idx=only_channel_idx)
        except RescanBusyError as exc:
            ui.notify(
                f"Rescan already running (job {exc.running_job_id[:8]})",
                color="warning",
            )
            # Even though we couldn't submit, attach the GUI to the
            # already-running job so the progress bar shows reality.
            watched_job[0] = exc.running_job_id
            rescan_btn.disable()
            progress_bar.visible = True
            return
        except Exception as exc:  # pragma: no cover - defensive
            debug_print(f"GUI rescan submit error: {exc}")
            ui.notify(f"Rescan failed to start: {exc}", color="negative")
            return
        ui.notify(
            f"Rescanning {label} (job {job.job_id[:8]})",
            color="positive",
        )
        watched_job[0] = job.job_id
        rescan_btn.disable()
        progress_bar.visible = True
        progress_bar.value = 0
        progress_label.text = f"starting ({label})…"

    def _on_rescan() -> None:
        _start_job(only_channel_idx=None, label="full archive")

    rescan_btn.on("click", _on_rescan)

    def _poll_progress() -> None:
        """Tick once per second while a job is being watched.

        Updates the progress bar and label, then re-enables the
        button when the job reaches a terminal state.
        """
        jid = watched_job[0]
        if jid is None:
            return
        job = rescan_manager.get(jid)
        if job is None:
            # Job evicted (very unlikely while running) — just clear.
            watched_job[0] = None
            rescan_btn.enable()
            progress_bar.visible = False
            progress_label.text = ""
            return

        d = job.to_dict()
        prog = d["progress"]
        counts = d["counts"]
        total = prog["bytes_total"] or 1
        progress_bar.value = prog["bytes_done"] / total
        progress_label.text = (
            f'{d["status"]} — {prog["bytes_done"]:,}/{prog["bytes_total"]:,} B '
            f'({prog["percent"]}%) · '
            f'+{counts["new_messages"]} msgs, '
            f'+{counts["new_rxlog"]} rxlog, '
            f'{counts["skipped_dup_rxlog"]} dup-skipped'
        )

        if d["status"] in ("done", "failed"):
            watched_job[0] = None
            rescan_btn.enable()
            if d["status"] == "done":
                ui.notify(
                    f'Rescan done: +{counts["new_messages"]} new messages',
                    color="positive",
                )
            else:
                ui.notify(
                    f'Rescan failed: {d.get("error") or "unknown error"}',
                    color="negative",
                )

    ui.timer(1.0, _poll_progress)

    # If a rescan was already running when this page mounted (e.g. the
    # user opened a second browser tab mid-job), pick it up so the
    # progress bar reflects reality rather than a stale "idle" state.
    pre_existing = rescan_manager.running_job_id()
    if pre_existing is not None:
        watched_job[0] = pre_existing
        rescan_btn.disable()
        progress_bar.visible = True

    # ── Channel table ────────────────────────────────────────────────
    columns = [
        {"name": "idx", "label": "Idx", "field": "idx", "align": "left"},
        {"name": "name", "label": "Name", "field": "name", "align": "left"},
        {"name": "actions", "label": "", "field": "actions"},
    ]
    table = ui.table(columns=columns, rows=[], row_key="idx").classes("w-full")

    # Per-row action buttons:
    #   refresh → rescan archive scoped to this channel only
    #             (POST /api/v1/rescan/{idx} equivalent, in-process)
    #   delete  → remove channel from watchlist (hashtag channels only;
    #             hidden for Public at idx=0, which is system-managed)
    # The Quasar emits travel up to the parent table component, where
    # the Python handlers below (table.on(...)) catch them.
    table.add_slot(
        "body-cell-actions",
        r"""
        <q-td :props="props">
            <q-btn dense flat icon="refresh" color="primary"
                   @click="$parent.$emit('rescan_channel', props.row)">
                <q-tooltip>Rescan archive for this channel</q-tooltip>
            </q-btn>
            <q-btn v-if="props.row.idx !== 0" dense flat icon="delete" color="negative"
                   @click="$parent.$emit('remove', props.row)">
                <q-tooltip>Remove channel from watchlist</q-tooltip>
            </q-btn>
        </q-td>
        """,
    )

    def _on_remove(e) -> None:
        idx = e.args.get("idx")
        name = e.args.get("name", "") or f"ch{idx}"
        if idx is None:
            return

        # Confirmation dialog — built on demand so each click gets a
        # fresh dialog scoped to that row, and we don't have to track
        # which row a long-lived dialog refers to.
        with ui.dialog() as confirm, ui.card().classes("min-w-80"):
            ui.label(f"Remove {name} from the watchlist?").classes(
                "text-base font-medium"
            )
            ui.label(
                "New messages on this channel will no longer be decoded. "
                "Messages already in the archive are not affected."
            ).classes("text-sm opacity-75")
            with ui.row().classes("justify-end w-full gap-2 q-mt-sm"):
                ui.button("Cancel", on_click=confirm.close).props("flat")

                def _do_remove() -> None:
                    confirm.close()
                    if store.remove(int(idx)):
                        ui.notify(f"Removed {name}", color="positive")
                    else:
                        ui.notify("Remove failed", color="warning")

                ui.button("Remove", on_click=_do_remove).props("color=negative")

        confirm.open()

    def _on_rescan_channel(e) -> None:
        idx = e.args.get("idx")
        name = e.args.get("name") or f"ch{idx}"
        if idx is None:
            return
        _start_job(only_channel_idx=int(idx), label=name)

    table.on("remove", _on_remove)
    table.on("rescan_channel", _on_rescan_channel)
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
