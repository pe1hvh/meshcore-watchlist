# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.2.0]: #020---2026-04-28
[0.1.1]: #011---2026-04-27
[0.1.0]: #010---initial-release
