# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.2] - 2026-04-29

### Fixed

- **Rescan stamped historical messages with the rescan moment as
  their `timestamp_utc`.** `MessageArchive.add_message()` and
  `add_rx_log()` unconditionally wrote
  `timestamp_utc = datetime.now(timezone.utc)` on every insert.
  For the live tail this was approximately right ("now" ≈
  packet arrival time); for the rescanner — added in 0.2.0 to
  replay days of history through the same code path — it was
  catastrophically wrong: every newly-decoded historical row
  ended up clustered at the rescan moment, breaking
  `query_messages` time-sort, the `/api/v1/stats` 72-hour
  window, and any downstream consumer using `timestamp_utc` as
  a cursor or dedup key.

  Should have been caught when the rescanner was written. It
  wasn't. This release is the apology.

  `add_message()` and `add_rx_log()` now accept an optional
  `timestamp_utc` parameter; when supplied (only by the
  rescanner), it is used in place of `now()`. The rescanner
  derives the original arrival time from each record via
  `derive_record_timestamp_utc()` in
  `services/archive_rescanner.py`, which tries, in priority
  order: an ISO timestamp on the record itself
  (`timestamp_utc` / `timestamp` / `received_at` / `ts`); the
  record's `time` field combined with a date extracted from
  the rxlog filename (recognises `YYYY-MM-DD`, `YYYYMMDD`,
  `YYYY_MM_DD` patterns anywhere in the name); the rxlog
  file's mtime; and `now()` only as a final fallback, with a
  debug log marking which records failed all heuristics.

  Live-tail behaviour is unchanged.

  Affected files:
  `meshcore_watchlist/services/message_archive.py`,
  `meshcore_watchlist/core/shared_data.py`,
  `meshcore_watchlist/services/archive_rescanner.py`.

### Changed

- **API: `/api/v1/messages` items now include `message_hash`.**
  Previously the only stable per-message identifier in the
  response was the positional `id` (= `offset + i + 1`), which
  shifts whenever the underlying archive changes order — so
  any downstream collector using it as a dedup key or cursor
  would re-emit rows after every rescan. The packet's
  deterministic `message_hash` was already on every archived
  row but was not exposed. It is now the first identifier
  field after `id`. Existing consumers that don't read it are
  unaffected; new consumers should prefer it as the dedup key.

  Affected file:
  `meshcore_watchlist/services/public_api_service.py`.

### Notes

- **Cleanup of the in-place damage from 0.2.1 + first rescan**
  is the operator's responsibility — these fixes prevent
  recurrence but do not retroactively rewrite the
  ISO-timestamp column on rows already written. Identifying
  the affected rows in the on-disk archive (or in any
  downstream MariaDB / similar): they share an unusually
  uniform `timestamp_utc` clustered within a few seconds of
  the rescan job's start time (visible in the service log as
  `ArchiveRescanner: starting job <id>`). For exact recovery
  the rescan can be re-run after deleting both the affected
  rows and the corresponding fingerprint entries, since this
  release will then write the correct historical timestamps.

## [0.2.1] - 2026-04-29

### Fixed

- **Public-channel messages were never decoded.** Adding the
  Public channel via the Watchlist tab stored it as `#Public`
  because `WatchlistStore.add()` unconditionally force-prefixed
  the leading `#`, then derived its key as
  `SHA-256("#Public")[:16]`. The actual MeshCore Public channel
  uses a fixed well-known 16-byte secret that is not derivable
  from any name (the firmware's `SET_CHANNEL` slot represents it
  as 16 zero bytes — see Companion Protocol — but on-air
  encryption uses the real well-known value). No registered key
  matched, so packets arrived as undecoded `RxLogEntry` rows but
  no `Message` rows were ever produced, making the channel look
  silent.

  The well-known secret is now defined as
  `PUBLIC_CHANNEL_SECRET` in `config.py` along with the
  canonical name `"Public"` and an `is_public_channel_name()`
  helper that recognises `Public`, `public`, `PUBLIC`, with or
  without a leading `#`, with whitespace tolerance. The
  `PacketPipeline` watchlist subscriber now special-cases the
  Public name and registers the fixed secret directly via
  `PacketDecoder.add_channel_key()` instead of the
  name-derivation path used for hashtag channels. If the
  firmware ever rotates this value, change it in one place.

  Existing `#Public` watchlist entries from before the fix
  continue to work — the helper tolerates the stray `#` — so no
  migration of `watchlist.json` is required. Run a rescan after
  upgrading to retroactively decode any Public-channel packets
  that were ingested as undecoded rxlog rows during the bug
  window.

  Affected files: `meshcore_watchlist/config.py`,
  `meshcore_watchlist/main.py`,
  `meshcore_watchlist/services/watchlist_store.py`.

- **API: Public channel mis-classified when not first in list.**
  `is_public_channel(idx, name)` in
  `services/public_api_service.py` returned `True` for `idx == 0`
  as a meshcore-gui legacy assumption. In the watchlist `idx` is
  just zero-based list position, not a device-channel slot, so a
  user who added `#weather` before Public would get Public's
  messages and stats filtered out of `/api/v1/messages` and
  `/api/v1/stats`. Classification is now name-based via
  `is_public_channel_name()`, independent of list position.

  Affected file:
  `meshcore_watchlist/services/public_api_service.py`.

### Changed

- **Dashboard: confirmation dialog before removing a watchlist
  channel.** The per-row delete icon now opens an English
  confirmation dialog ("Remove `<name>` from the watchlist?")
  with Cancel / Remove buttons before calling
  `WatchlistStore.remove()`. Public has no delete icon (slot
  template `v-if="props.row.idx !== 0"`) and is therefore
  unaffected.

  Affected file: `meshcore_watchlist/gui/dashboard.py`.

### Notes

- The dashboard slot template comments referencing
  `WatchlistStore._ensure_public_invariant_locked` are stale —
  no such method exists, and Public's position at idx 0 is a
  consequence of add-order rather than an enforced invariant.
  Left untouched in this release; either correcting the comment
  or implementing the invariant is left for a future change.

## [0.2.0] - 2026-04-28

### Added

- **Retroactive archive rescan.** When a new hashtag channel is added
  to the watchlist, packets that arrived *before* the channel was added
  were ingested as undecoded `RxLogEntry` rows but never decrypted with
  the freshly-derived key. The live `JsonlTailer` cursor only advances
  forward, so without manual intervention those packets stayed
  undecoded for the rest of the retention window. This release adds an
  on-demand rescan that reopens every `*_rxlog.jsonl` from byte 0,
  reruns each line through the current `PacketDecoder`, and writes any
  newly-decoded `Message` to the archive.

  Three REST endpoints are exposed:

  - `POST /api/v1/rescan` — full rescan, returns `202` with a job-id.
  - `POST /api/v1/rescan/{idx}` — rescan scoped to one watchlist
    channel index. Decode trial is restricted to that channel's key
    via the new `allowed_idx` parameter on `PacketDecoder.decode()`.
  - `GET /api/v1/rescan/{job_id}` — poll job status, including
    per-file byte progress and counts of new messages, new rxlog
    rows, and dedup-skipped rxlog rows.

  Submitting a second job while one is running returns `409 Conflict`
  with the running job-id; this is intentional (single-user
  deployment, no queue).

- **Dashboard: "Rescan archive" button on the Watchlist tab.** Calls
  the same `RescanJobManager` directly (in-process, no HTTP loopback)
  and renders a progress bar plus running counters until the job
  completes. Buttons disable themselves for the duration of the job
  and pick up an already-running job if a second browser tab is
  opened mid-run.

  Each watchlist row also gets a per-channel refresh icon next to
  the delete icon, which submits a rescan scoped to that single
  channel index — useful right after adding a new hashtag, when
  only that channel needs retroactive decoding.

  Affected files: `meshcore_watchlist/api/routes.py`,
  `meshcore_watchlist/gui/dashboard.py`,
  `meshcore_watchlist/main.py`, and the new
  `meshcore_watchlist/services/archive_rescanner.py`.

### Fixed

- **Archive-wide dedup for rescan ingest.** `SharedData._rxlog_hashes`
  and `SharedData._message_fingerprints` are seeded from only the
  most recent `MAX_RX_LOG` (50) and `MAX_MESSAGES` (500) archive
  entries on startup. That window is large enough for live-tail
  dedup against truncation/rotation re-emits, but several orders of
  magnitude too small for a rescan over a 7-day rxlog file. The
  rescan path therefore loads the **full** archive hash and
  fingerprint sets once at job start (via the new
  `MessageArchive.load_all_rxlog_hashes()` and
  `load_all_message_fingerprints()` helpers) and dedupes against
  those instead. Live-tail behaviour is unchanged.

### Notes

- The live tailer is **not** paused or rewound during a rescan.
  The rescanner reads from byte 0 to whatever EOF was at the moment
  the file was opened; bytes appended after that are picked up by
  the live tailer on its next poll cycle. State.json cursors are
  never written by the rescan path.
- Rescan ingest writes only to the on-disk archive, not to the
  in-memory 50/500-entry rings; flooding the "live" Messages and
  RX Log tabs with reprocessed history would defeat their purpose.
  At end-of-job `SharedData.reload_caches_from_archive()` clears
  and re-populates those rings from disk so the GUI reflects
  newly-decoded messages without a service restart.
- The `POST /api/v1/rescan/{idx}` endpoint is mainly a scoping
  device, not a CPU optimisation. With fewer than ten channels the
  per-key trial cost in `PacketDecoder.decode()` is already
  negligible (per its module docstring); the restriction simply
  expresses the user intent "I just added #foo, only emit messages
  for #foo this time".

## [0.1.1] - 2026-04-27

### Fixed

- **GUI: empty channel and message lists after closing and reopening the
  browser tab.** The dashboard's per-page render loop relied on the
  `*_updated` flags exposed by `SharedData.get_snapshot()` to decide
  whether to populate the Watchlist, Messages, and RX Log tables. Those
  flags are *process-global*: the first browser session to tick after a
  mutation would render the tables and then call
  `shared.clear_update_flags()`, leaving any subsequently-mounted page
  session looking at flags that read `False`. Freshly-built (empty)
  tables therefore stayed empty until the next inbound packet flipped a
  flag back on — typically only triggered by sending a message on a
  watched channel, or by enough service restarts that a page mount
  happened to coincide with the post-`__init__` window before any tick
  had cleared the flags.

  The render loop now performs an unconditional initial render from the
  current snapshot in `_render_index()` before starting the
  one-second timer. Each new browser session is primed with the current
  state regardless of the flag history, so the workaround of restarting
  the service to "pop" the lists back is no longer needed.

  Affected file: `meshcore_watchlist/gui/dashboard.py`.

### Notes

- The flag-based gating in the timer's `_refresh()` is preserved as an
  efficiency optimisation for the single-session case, and the existing
  `clear_update_flags()` call is left intact. With the initial-render
  fix in place this is correct for the typical single-browser usage.
  Concurrent multi-browser sessions can still race on the global flags
  (one session clearing them before another ticks); a per-session
  generation counter would be the proper structural fix and is left for
  a future change.

## [0.1.0] - initial release

- Tail meshcore-gui's `*_rxlog.jsonl` files, decode GroupText packets
  matching watchlist channel keys, persist messages and rx-log entries
  under `~/.meshcore-watchlist/archive/`, expose a NiceGUI dashboard
  and a public REST API.

[0.2.2]: #022---2026-04-29
[0.2.1]: #021---2026-04-29
[0.2.0]: #020---2026-04-28
[0.1.1]: #011---2026-04-27
[0.1.0]: #010---initial-release
