"""
meshcore-watchlist — main entrypoint.

Wires the watchlist store, packet decoder, JSONL tailer, shared data,
public REST API and NiceGUI dashboard together.

Pipeline::

    [meshcore-gui *_rxlog.jsonl]
              │
        JsonlTailer (poll, byte-offset)
              │  raw entry dict (incl. raw_payload, hops, snr, ...)
              ▼
        PacketPipeline.handle_entry()
              │
              ├─ Always: persist as RxLogEntry to SharedData/archive
              │
              └─ If GroupText decrypts with a watchlist key:
                  build Message → SharedData/archive
"""

from __future__ import annotations

from typing import Dict, List

from nicegui import ui

from meshcore_watchlist.config import (
    HOST,
    PORT,
    VERSION,
    debug_print,
)
from meshcore_watchlist.api.routes import register_routes
from meshcore_watchlist.core.models import Message, RxLogEntry
from meshcore_watchlist.core.shared_data import SharedData
from meshcore_watchlist.decoder.packet_decoder import PacketDecoder
from meshcore_watchlist.gui.dashboard import build_dashboard
from meshcore_watchlist.services.jsonl_tailer import JsonlTailer
from meshcore_watchlist.services.watchlist_store import WatchlistStore


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class PacketPipeline:
    """Bridges JsonlTailer → PacketDecoder → SharedData."""

    def __init__(
        self,
        shared: SharedData,
        decoder: PacketDecoder,
        store: WatchlistStore,
    ) -> None:
        self._shared = shared
        self._decoder = decoder
        self._store = store
        self._channel_name_by_idx: Dict[int, str] = {}

        # Subscribe to watchlist changes: refresh decoder keys + cached
        # idx-to-name map, and propagate channel list to SharedData.
        store.subscribe(self._on_watchlist_changed)

    # ------------------------------------------------------------------
    # Subscriber callback
    # ------------------------------------------------------------------

    def _on_watchlist_changed(self, channels: List[Dict]) -> None:
        # Reset and repopulate decoder keys.
        self._decoder._secret_to_idx.clear()  # noqa: SLF001
        self._channel_name_by_idx.clear()
        for ch in channels:
            idx = ch["idx"]
            name = ch["name"]
            self._decoder.add_channel_key_from_name(idx, name)
            self._channel_name_by_idx[idx] = name

        # Push channel list into SharedData so the GUI tabs render.
        self._shared.set_channels(channels)

    # ------------------------------------------------------------------
    # Entry handler (called by JsonlTailer)
    # ------------------------------------------------------------------

    def handle_entry(self, rec: Dict) -> None:
        """Process one JSONL record from meshcore-gui's RX stream."""
        raw_payload = rec.get("raw_payload") or ""

        # Build the RxLogEntry first — always stored, regardless of
        # decode success.  Field set mirrors meshcore-gui::on_rx_log.
        rx_entry = RxLogEntry(
            time=rec.get("time", ""),
            snr=float(rec.get("snr", 0) or 0),
            rssi=float(rec.get("rssi", 0) or 0),
            payload_type=rec.get("payload_type", "?"),
            hops=int(rec.get("hops", 0) or 0),
            message_hash=rec.get("message_hash", "") or "",
            path_hashes=list(rec.get("path_hashes") or []),
            path_names=list(rec.get("path_names") or []),
            sender=rec.get("sender", "") or "",
            receiver=rec.get("receiver", "") or "",
            raw_payload=raw_payload,
            packet_len=int(rec.get("packet_len", 0) or 0),
            payload_len=int(rec.get("payload_len", 0) or 0),
            route_type=rec.get("route_type", "") or "",
            packet_type_num=int(rec.get("packet_type_num", -1) or -1),
        )
        self._shared.add_rx_log(rx_entry)

        # If we have keys, attempt decryption.  Successful decrypt of
        # a GroupText packet → store as Message.
        if not raw_payload or not self._decoder.has_keys:
            return

        decoded = self._decoder.decode(raw_payload)
        if decoded is None or not decoded.is_decrypted:
            return
        if decoded.channel_idx is None:
            return

        msg = Message.incoming(
            sender=decoded.sender,
            text=decoded.text,
            channel=decoded.channel_idx,
            time=rx_entry.time,
            snr=rx_entry.snr,
            path_len=decoded.path_length,
            path_hashes=decoded.path_hashes,
            path_names=rx_entry.path_names,
            message_hash=decoded.message_hash,
        )
        # Channel name attribution (mirrors meshcore-gui Message handling).
        ch_name = self._channel_name_by_idx.get(decoded.channel_idx, "")
        if ch_name:
            msg.channel_name = ch_name

        self._shared.add_message(msg)
        debug_print(
            f"Decoded GroupText: ch={decoded.channel_idx} ({ch_name}), "
            f"sender={decoded.sender!r}, text={decoded.text[:40]!r}"
        )


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def main() -> None:
    debug_print(f"meshcore-watchlist v{VERSION} starting on {HOST}:{PORT}")

    # Core services
    store = WatchlistStore()
    shared = SharedData()
    decoder = PacketDecoder()
    pipeline = PacketPipeline(shared, decoder, store)

    # Tailer
    tailer = JsonlTailer(callback=pipeline.handle_entry)
    tailer.start()

    # GUI
    build_dashboard(shared=shared, store=store)

    # Public REST API (/api/v1/...)
    register_routes(shared)

    # NiceGUI run.  ``reload=False`` because we manage long-lived
    # background threads (the tailer) and reload would orphan them.
    ui.run(
        host=HOST,
        port=PORT,
        title=f"meshcore-watchlist v{VERSION}",
        reload=False,
        show=False,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
