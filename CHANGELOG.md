# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.1]: #011---2026-04-27
[0.1.0]: #010---initial-release
