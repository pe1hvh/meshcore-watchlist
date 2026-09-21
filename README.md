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

| Endpoint           | Method | Description                                              |
|--------------------|--------|----------------------------------------------------------|
| `/channels`        | GET    | Channels currently being monitored (the watchlist)       |
| `/channels`        | POST   | Add a hashtag channel (used by `tools/channel_injector`) |
| `/messages`        | GET    | Decoded public + hashtag messages, paginated             |
| `/stats`           | GET    | Aggregated counts over the last 72 hours                 |
| `/nodes`           | GET    | Always `[]` — watchlist has no contact list of its own   |
| `/rescan/by-name`  | POST   | Submit a per-channel rescan over an explicit date window |

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

### `POST /channels`

Adds a hashtag channel to the watchlist. Additive endpoint introduced
in 0.3.0 so out-of-process clients (notably `tools/channel_injector`)
can grow the watchlist without violating the "`WatchlistStore` is the
only mutator" invariant: the daemon still owns the store, the client
merely asks it to add a name.

| Param  | Type   | Required | Notes                                       |
|--------|--------|----------|---------------------------------------------|
| `name` | string | yes      | Channel name. URL-encode `#` as `%23`.      |

Status codes:

| Code | Meaning                                                            |
|------|--------------------------------------------------------------------|
| 201  | Channel added.                                                     |
| 200  | Already on the watchlist (or `Public`, which is system-managed).   |
| 400  | Empty / control-character / over-32-byte UTF-8 name.               |

Channel names are limited to 32 UTF-8 bytes per the MeshCore Companion
Protocol; see ADR-007 for the rationale. Length is in **bytes**, not
codepoints (`#café` is 6 bytes, not 5).

```bash
curl -X POST "http://localhost:8083/api/v1/channels?name=%23weather"
# → 201 {"name": "#weather", "added": true}
```

## Channel injector (cron-driven seeder)

`tools/channel_injector` is a small standalone script that fetches one
or more upstream channel listings (JSON over HTTP) and seeds any
missing hashtag channels into the running daemon, then triggers a
per-channel rescan over the last 7 days. It is intended to run
periodically from cron, in the daemon's own venv. No extra
dependencies.

See [`tools/channel_injector/README.md`](tools/channel_injector/README.md)
for the full reference and [`install_script/channel_injector.cron.example`](install_script/channel_injector.cron.example)
for a sample crontab entry. Quick start:

```bash
/opt/meshcore-watchlist/.venv/bin/python -m tools.channel_injector \
    --source-url https://example.org/channels.json
```

## Archive purge (`tools/purge_archive.py`)

`tools/purge_archive.py` is a standalone, out-of-process retention purge
for the archive in `~/.meshcore-watchlist/archive/`. It does the same
job as the daemon's `MessageArchive.cleanup_old_data()`, but with a
progress report, a free-space check before anything is written, and an
optional de-duplication pass. Stdlib only; it runs in the daemon's own
venv.

It works on two files:

```
~/.meshcore-watchlist/archive/watchlist_messages.jsonl
~/.meshcore-watchlist/archive/watchlist_rxlog.jsonl
```

### How it works

The script has four modes. Only the last two write anything.

| Mode | Command | Writes | Daemon |
|------|---------|--------|--------|
| Report | `--report` | no | may be running |
| Dry run | `--dry-run` (optionally with `--dedupe`) | no | may be running |
| Retention purge | *(no mode flag)* | yes | **stop first** |
| De-duplication | `--dedupe` | yes | **stop first** |

1. **Report** counts rows per file and prints the oldest/newest
   `timestamp_utc`, the span in days, rows without a usable timestamp,
   unparseable lines, and the number of duplicate rows by
   `message_hash`.
2. **Dry run** performs the full pass and prints what would be kept and
   dropped.
3. **Retention purge** keeps rows whose `timestamp_utc` is newer than
   *now − `--days`* (UTC). Before writing, it checks that free space is
   at least the combined size of both files (worst case: a full second
   copy). If not, it refuses with exit code 1 and touches nothing. Each
   file is then rewritten to `<file>.purge-tmp`, fsynced, and moved over
   the original with an atomic rename.
4. **De-duplication** (`--dedupe`) keeps the first row per
   `message_hash` and drops later copies; retention is *not* applied in
   this mode. Rows without a `message_hash` are always kept. Use it when
   `--report` shows duplicate rows. Temporary file: `<file>.dedupe-tmp`.

Differences from the daemon's own cleanup:

- Rows with a missing or unparseable `timestamp_utc` (and unparseable
  lines) are **kept** by default; the daemon drops them. Pass
  `--drop-undated` to match the daemon's behaviour.
- Free space is checked up front; the daemon's `OSError` path is silent
  unless `MESHCORE_WATCHLIST_DEBUG=1`.
- Progress is printed every million lines, so a large rx-log does not
  look like a hang.

Bytes appended to a file while a pass is running are carried over
verbatim before the rename. That narrows the window, but the rewrite
still replaces the file underneath the daemon, and the daemon's own
cleanup rewrites the same files. Writing modes are therefore run with
the service stopped.

### Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `--archive-dir DIR` | `~/.meshcore-watchlist/archive` | Archive directory. Resolved from `$HOME` — pass it explicitly when running from cron or as another user. |
| `--days N` | `7` | Retention window in days. Keep in line with `config.py`. |
| `--report` | off | Count and print only. |
| `--dry-run` | off | Full pass, no writes. |
| `--dedupe` | off | De-duplicate on `message_hash` instead of applying retention. |
| `--drop-undated` | off | Drop rows without a usable `timestamp_utc`. |

Exit codes: `0` success, `1` not enough free space for the temporary
copy, `2` archive directory does not exist.

### Manual run

```bash
cd /opt/meshcore-watchlist
PY=/opt/meshcore-watchlist/.venv/bin/python

$PY tools/purge_archive.py --report          # safe while running
$PY tools/purge_archive.py --dry-run         # safe while running

sudo systemctl stop meshcore-watchlist.service
$PY tools/purge_archive.py                   # or: --days 3, --dedupe
sudo systemctl start meshcore-watchlist.service
```

Run the writing modes as the service user (not as root): the atomic
rename leaves the new file owned by whoever ran the script, and a
root-owned archive is no longer writable by the daemon.

### Scheduling via cron (with service stop/start)

Stopping and starting the service requires root, while the purge itself
must run as the service user. Use a system cron file, which carries a
user field, and drop privileges with `runuser` for the purge step.

`/etc/cron.d/meshcore-watchlist-purge` (as root, mode `0644`, file ends
with a newline):

```cron
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Daily at 03:43: stop service, purge archive as <user>, start service again
43 3 * * * root systemctl stop meshcore-watchlist.service && runuser -u <user> -- /opt/meshcore-watchlist/.venv/bin/python /opt/meshcore-watchlist/tools/purge_archive.py --archive-dir /home/<user>/.meshcore-watchlist/archive --days 7 >> /var/log/meshcore/purge_archive.log 2>&1; systemctl start meshcore-watchlist.service
```

How the line behaves:

- `stop … && purge` — the purge only runs once the service has actually
  stopped. If `stop` fails, nothing is rewritten.
- `; start` — the service is started again **regardless** of the purge
  result (including exit code 1 for insufficient space), so a failed
  purge never leaves the daemon down.
- `runuser -u <user> --` — the purge runs as the service user, so file
  ownership stays correct. `--archive-dir` is passed explicitly because
  `$HOME` in root's cron environment is `/root`.
- Cron does not support line continuation with `\`; keep the entry on a
  single line.

Choice of time: pick a minute that does not coincide with the channel
injector (e.g. `17 * * * *`) or other `*/30` jobs. While the service is
down, the injector gets no answer (`daemon_error=yes`) and any rescan
in progress is interrupted; the next injector run picks things up
again. The tailer resumes from its cursor in `state.json`, so traffic
written by `meshcore-gui` during the stop is processed after the
restart.

For a periodic de-duplication pass, use the same line with `--dedupe`
in place of `--days 7`, on a different schedule (e.g. weekly).

Alternative without `/etc/cron.d`: put the line (without the `root`
field) in root's crontab via `sudo crontab -e`.

Check the result:

```bash
tail -n 20 /var/log/meshcore/purge_archive.log
systemctl status meshcore-watchlist.service
```

For `/var/log/meshcore/purge_archive.log` a simple `logrotate.d` entry
is advisable; the script does not rotate its own log.

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
