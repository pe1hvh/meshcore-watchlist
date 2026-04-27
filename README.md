# meshcore-watchlist

Local hashtag-channel monitor for MeshCore mesh radio networks.

Runs as a separate service alongside [meshcore-gui](https://github.com/pe1hvh/meshcore-gui).
Monitors hashtag channels of which the name (and therefore the
SHA-256-derived key) is known, without requiring those channels to
occupy a slot on the connected MeshCore device.

## How it works

`meshcore-gui` writes every received LoRa packet to an append-only
JSONL file under `~/.meshcore-gui/archive/_dev_*_rxlog.jsonl`.
`meshcore-watchlist` tails this file with a byte-offset cursor,
attempts to decrypt each `GroupText` packet using locally-derived
hashtag keys, and presents the decoded messages in its own UI and
REST API.

Output format (Message, RxLogEntry, REST API responses) is identical
to meshcore-gui, so downstream consumers such as `domca.nl` work
without changes.

## Installation

Requires meshcore-gui v1.22.1 or newer (provides the JSONL stream).

```bash
sudo ./install_script/install.sh --port 8083
```

Optional flags:

```
--user USER         Run-as user (default: current user)
--install-dir DIR   Install location (default: /opt/meshcore-watchlist)
```

The UI becomes available at `http://localhost:<port>/`.

## Uninstall

```bash
sudo ./install_script/uninstall.sh
```

Stops and disables the service, removes the systemd unit and the install
directory, but **keeps** your watchlist and archive in
`~/.meshcore-watchlist/` so a reinstall picks them up again.

To also wipe user data and start completely fresh:

```bash
sudo ./install_script/uninstall.sh --purge
```

Other flags: `--user USER`, `--install-dir DIR` (must match what was used
at install time), `--yes` to skip the confirmation prompt.

## REST API

The service exposes the same read-only REST API as `meshcore-gui` under
`/api/v1/`, with identical JSON shapes. Downstream consumers such as
`domca.nl` can therefore pull from this service using exactly the
polling logic they already use for `meshcore-gui` — only the host/port
needs to change. CORS is enabled (`*` by default; override with
`MESHCORE_WATCHLIST_CORS_ORIGINS`). There is no authentication, same
as `meshcore-gui`.

The service binds to `0.0.0.0:<port>`, so the API is reachable from
other hosts on the LAN. Restrict with a firewall if undesired.

Base URL: `http://<host>:<port>/api/v1`

| Endpoint    | Description                                              |
|-------------|----------------------------------------------------------|
| `/channels` | Channels currently being monitored (the watchlist)       |
| `/messages` | Decoded public + hashtag messages, paginated             |
| `/stats`    | Aggregated counts over the last 72 hours                 |
| `/nodes`    | Always `[]` — watchlist has no contact list of its own   |

### `GET /channels`

Returns the channels this instance monitors. Watchlist entries are
hashtag channels by construction, so `is_private` is always `false`.

```json
[
  { "idx": 1, "name": "#mc-radar", "is_private": false },
  { "idx": 2, "name": "#nl-mesh",  "is_private": false }
]
```

### `GET /messages`

Paginated list of decoded public + hashtag messages.

| Param    | Type | Default | Range  |
|----------|------|---------|--------|
| `limit`  | int  | 100     | 1–500  |
| `offset` | int  | 0       | ≥ 0    |

Response:

```json
{
  "total": 1234,
  "limit": 100,
  "offset": 0,
  "items": [
    {
      "id": 1,
      "channel_idx": 1,
      "channel_name": "#mc-radar",
      "sender": "PE1ABC",
      "sender_pubkey": "abc123…",
      "text": "Hallo allemaal",
      "timestamp": "2026-04-27T07:44:41+00:00",
      "hops": 2,
      "path_hashes": ["a1b2", "c3d4"],
      "path_names":  ["pe1xyz-rep", "pe1xyz-home"]
    }
  ]
}
```

Field names and types match `meshcore-gui`'s `/api/v1/messages` exactly.

### `GET /stats`

Counts and aggregates over the last 72 hours of public + hashtag
traffic. The fields `active_clients`, `active_repeaters` and
`active_room_servers` are always `0` for the watchlist (no contact list
or radio of its own); they remain in the response for shape
compatibility with `meshcore-gui`.

### Mirroring data to a downstream consumer

The intent is that channels and messages are pulled out of this service
and processed elsewhere — e.g. for ingestion into `domca.nl` — in
exactly the same manner as the existing `meshcore-gui ↔ domca.nl`
flow. Because the response shapes are identical, no changes to the
downstream code are needed; configure it with this service's URL as an
additional source.

Practical examples with curl:

```bash
HOST=raspberrypi5nas.local
PORT=8083

# All monitored channels
curl -s "http://$HOST:$PORT/api/v1/channels" | jq

# Latest 100 messages
curl -s "http://$HOST:$PORT/api/v1/messages?limit=100" | jq

# Page through older messages
curl -s "http://$HOST:$PORT/api/v1/messages?limit=100&offset=100" | jq
```

When persisting messages downstream, dedupe on a content key such as
`(timestamp, sender_pubkey, text)` rather than on `id` — `id` is a
positional index within a single response, not a stable primary key.

## Configuration

The watchlist is stored in `~/.meshcore-watchlist/watchlist.json` and
managed via the **Watchlist** tab in the UI.  Each entry is a hashtag
channel name (e.g. `#mc-radar`).  The `idx` is the position in the
file.

## Layout

```
~/.meshcore-watchlist/
├── watchlist.json          # CRUD via UI
├── state.json              # tailer cursors per source file
└── archive/                # decoded messages + raw rx-log
```

## License

Same as meshcore-gui.
