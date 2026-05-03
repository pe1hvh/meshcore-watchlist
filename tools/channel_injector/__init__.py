"""channel_injector — out-of-process watchlist seeder for meshcore-watchlist.

Fetches one or more remote channel-listings (JSON over HTTP) and asks
the running ``meshcore-watchlist`` daemon to add any hashtag channels
that are not yet on its watchlist, then triggers a single batched
rescan over the last 7 days covering all newly-added channels so
historical packets get decoded with the freshly-derived keys.

Designed to run as a cron job in the same virtualenv as the daemon.
See ``tools/channel_injector/README.md`` for installation and usage.
"""

__version__ = "0.3.5"
