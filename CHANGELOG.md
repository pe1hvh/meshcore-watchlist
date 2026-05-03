# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.1] - 2026-05-03

Hardening of the 0.3.0 channel-injection path.  The injector and the
``POST /api/v1/channels`` endpoint now enforce the protocol-bounded
length of a MeshCore channel name and add two pragmatic safety caps
against a misbehaving external source.  No breaking changes for
well-formed clients.

### Added

- **ADR-007** — *Channel-name length and charset are bounded by the
  MeshCore Companion Protocol* (Accepted, 2026-05-03).  Establishes
  32 UTF-8 bytes as the universal limit, with rejection of ASCII
  control characters as the only charset rule.  See
  ``docs/adr/ADR-007-channel-name-length-and-charset.md``.
- **Daemon error ``name_too_long``** — ``POST /api/v1/channels``
  returns HTTP 400 with
  ``{"error": "name_too_long", "max_bytes": 32, "got_bytes": N}``
  for names whose UTF-8 encoding exceeds 32 bytes (after server-side
  ``#``-prefix where applicable).
- **Constant ``CHANNEL_NAME_MAX_BYTES = 32``** in
  ``services/watchlist_store.py`` (and re-imported in ``api/routes.py``
  and ``tools/channel_injector/injector.py``) — single source of
  truth for the protocol-bounded limit, citing ADR-007 inline.
- **Injector flags ``--max-source-bytes`` and
  ``--max-adds-per-run``** — two pragmatic safety caps with
  defaults 1 MiB and 50 respectively.  Both configurable per run.
- **Injector field ``InjectorResult.max_adds_reached``** plus
  ``max_adds_reached=yes/no`` in the one-line summary, so cron
  output makes it visible when a run hit the per-run cap.
- **Injector exception ``ResponseTooLarge``** — raised when a source
  response exceeds the byte cap; reported as a source error, run
  continues with other URLs.

### Changed

- **``WatchlistStore.add()``** now rejects names whose UTF-8 length
  exceeds 32 bytes (returning ``False``, same convention as duplicate
  rejection).  This affects every entry path, not just the API: GUI
  add and direct callers also see the cap.  Pre-existing entries in
  ``watchlist.json`` that exceed the limit are loaded as-is — only
  *new* mutations validate.  See ADR-007 §4 for the migration note.
- **``tools/channel_injector/injector.py``** — ``_http_get_json``
  gained a ``max_bytes`` parameter; ``_is_safe_channel_name`` is now
  paired with a separate ``_is_within_protocol_length`` so the two
  concerns are documented orthogonally.  Source fetches use the
  configured ``max_source_bytes``; the daemon-side
  ``GET /api/v1/channels`` uses a generous internal ceiling because
  it is a trusted local endpoint.
- **Version bump** ``0.3.0`` → ``0.3.1`` (PATCH — additive
  hardening, no breaking changes for well-formed clients).

### IMPACT

- A channel name that cannot fit on the wire can no longer enter the
  watchlist via API or GUI.  Existing on-disk entries that violate
  the rule remain in place to avoid surprising operators; they are
  effectively no-ops since no real device can produce matching
  traffic.
- A misbehaving or compromised external source that sends a
  multi-megabyte payload no longer drains memory: the injector
  stops reading at the configured ceiling.
- An external source that suddenly produces hundreds of new names
  per run no longer floods the watchlist; the injector stops adding
  past ``--max-adds-per-run`` and surfaces the situation in the
  summary.
- No changes to JSONL archive schema, no changes to existing REST
  shapes other than the new ``name_too_long`` error variant on the
  already-additive ``POST /api/v1/channels`` endpoint.  Downstream
  consumers (``meshcore-gui`` clients, ``domca.nl``) are unaffected.

### RATIONALE

The injector was discussed against a threat model where the source
URL is on the public internet — that puts the bus between an
untrusted actor and the daemon's ``WatchlistStore``.  Three concrete
gaps existed against that model:

1. No length cap — a malformed source could write arbitrarily long
   strings into ``watchlist.json`` even though the protocol clearly
   states the field is 32 bytes UTF-8.
2. No response-size cap — a bug or attack could force the cron
   process to allocate hundreds of MB.
3. No per-run add cap — a transient upstream glitch could
   single-shot hundreds of entries into the watchlist.

The 32-byte limit is grounded in the MeshCore Companion Protocol
(`CMD_SET_CHANNEL`, bytes 2-33 = "32 bytes, UTF-8, null-padded"),
not chosen arbitrarily; it is captured in ADR-007 so future work has
a stable reference.  The two operational caps (1 MiB / 50) are
configurable defaults, not invariants — operators with different
needs can tune them per cron entry.

## [0.3.0] - 2026-05-03

Adds an out-of-process pathway for seeding the watchlist from an
upstream channel-listing source, without breaking the
"``WatchlistStore`` is the only mutator" invariant.  See
``tools/channel_injector/README.md`` for the operational story.

### Added

- **``POST /api/v1/channels?name=...``** — additive endpoint that
  forwards a channel-add request to ``WatchlistStore.add()``.  Returns
  ``201`` on add, ``200`` if the name was already on the watchlist or
  refers to ``Public`` (system-managed), ``400`` on empty or
  control-character names.  Existing GET stays byte-for-byte
  compatible; downstream consumers (e.g. ``meshcore-gui`` clients,
  ``domca.nl``) are unaffected.
- **``tools/channel_injector``** — standalone CLI (stdlib-only) that
  fetches one or more remote channel listings, computes the diff
  against the current watchlist, and submits the missing channels via
  the new POST endpoint, followed by a per-channel rescan over the
  last 7 UTC days.  Exit codes ``0`` / ``1`` / ``2``; one-line summary
  at WARNING level for cron-friendly logs.
- **``install_script/channel_injector.cron.example``** — drop-in cron
  entry showing the explicit ``.venv/bin/python`` invocation and a
  reasonable default schedule.
- **``install_script/install.sh``** — now also copies ``tools/`` to
  the install dir when present, so the cron entry can ``cd`` there
  and invoke ``python -m tools.channel_injector``.  Conditional on
  the directory existing, so installing from an older tree without
  ``tools/`` is unchanged.
- **Documentation refresh** — ``docs/architecture.md`` (§3.3 folder
  layout, §9 REST API endpoint table + new §9.4 watchlist-mutation
  control-plane, new §12.5 out-of-process helper pattern in
  ``tools/``), ``docs/fto.md`` (new UC-13 cron-driven seeding, new
  §6.3 watchlist-mutation contract, §8.1 reference to the cron
  example), ``docs/datadictionary.md`` (new §5.8 ``POST /api/v1/channels``
  request and response shapes).  The 0.2.6 release-specific document
  ``docs/ontwerp/ontwerp-0.2.6.md`` is intentionally left untouched.

### Changed

- **``register_routes(shared, rescan_manager, store=None)``** —
  signature gained an optional trailing ``store`` keyword argument so
  the new POST endpoint can reach the watchlist store without
  detouring through ``SharedData``.  Default ``None`` keeps the
  0.2.x two-argument callers working: when ``store`` is omitted the
  POST endpoint is simply not registered.  ``main.py`` now passes
  ``store=store``.
- **Version bump** ``0.2.6`` → ``0.3.0`` (MINOR — additive
  functionality, no breaking changes).

### IMPACT

- Operators can now point a cron job at an upstream listing and have
  the watchlist self-update.  Adding the channel triggers
  ``WatchlistStore``'s normal subscriber chain, so the decoder key
  registry, the GUI, and ``state.json`` all pick up the new entry
  without a service restart.
- No changes to JSONL archive schema, no changes to existing REST
  shapes, no new third-party dependencies.

### RATIONALE

The watchlist was UI-managed only.  Clients needing programmatic
seeding (e.g. fleet syncing across multiple watchlist instances,
discovery-driven channel rotation) had no clean entry point and were
forced to either edit ``watchlist.json`` directly (race with the live
``WatchlistStore``, no decoder key refresh) or re-implement the GUI
flow.  An additive ``POST /api/v1/channels`` is the smallest possible
hook that preserves CLAUDE.md's "single mutator" rule: the daemon is
still the only writer, the new endpoint just exposes its existing
``add()`` capability over HTTP.

## [0.2.6] - 2026-05-01

Closes the half-refactor that 0.2.5 left behind: ``channel_name`` is
now the stable channel identity end-to-end (decoder, rescan, REST,
GUI), per ADR-001.  See `docs/ontwerp/ontwerp-0.2.6.md` for the full
sequence diagrams and design rationale; this entry summarises what
shipped.

### Design invariant (made literal across the codebase)

> ``channel_name`` is the stable channel identity.  ``idx`` is a
> vluchtige UI-positie and never participates in identity, scope,
> priority, routing, or dedup.  This was already true in 0.2.5 for
> the dedup fingerprints (template 1).  In 0.2.6 it is now also true
> for the decoder, the rescan job state, the priority ranking, the
> per-channel REST endpoint, and the GUI per-row rescan button.

### Fixed

- **Watchlist mutation tijdens een lopende rescan brak het
  decode-pad.**  In 0.2.5 the rescan job froze a
  ``priority_idx_order`` list at job-start while the decoder's
  ``_secret_to_idx`` was kept live by the GUI thread (clear+rebuild
  on every mutation).  An add / remove / reorder during a running
  rescan therefore made the frozen idx values point at the wrong
  channel — silently miss-decoding the rest of the job.  Same root
  cause for ``RescanJob.only_channel_idx``: a delete shifted every
  higher idx down by one and the scope drifted to a neighbour.

  In 0.2.6 the priority list is bevroren on **names**, the rescan
  job is scoped on **name**, and the decoder's key registry is
  keyed on **name** and delta-updated by the GUI thread (instead of
  clear+rebuild) so a record decoded in the middle of a watchlist
  mutation never sees an empty registry.  Reorder of the watchlist
  during a rescan now has no observable effect — that is the
  property ADR-001 buys.

  Affected files: ``decoder/packet_decoder.py``, ``main.py``,
  ``services/archive_rescanner.py``,
  ``services/channel_priority.py``, ``api/routes.py``,
  ``gui/dashboard.py``.

### Added

- **Three new rescan counters: ``decoded_total``,
  ``not_decryptable``, ``skipped_dup_message``.**  In 0.2.5 a "+0
  new messages" report was ambiguous: it could mean "decoder ran on
  every record and produced zero matches" or "every record was
  already in the archive".  The new counters split the outcomes:

    - ``decoded_total``       — GroupText successfully decrypted
    - ``new_messages``        — those of which were freshly archived
    - ``skipped_dup_message`` — those of which were dedup hits
    - ``not_decryptable``     — GroupText with no matching key

  Exposed in ``RescanJob.to_dict()`` under ``counts`` and rendered
  inline in the GUI progress label
  (`+N dec (+M new, K dup), L undec, +R rx`).

- **``PacketDecoder.remove_channel_key(name)``.**  Specified in
  ontwerp 0.2.6 §7 to support delta-updates from the watchlist
  subscriber.  Idempotent — silently no-ops when the name is not
  registered.

- **REST endpoint ``POST /api/v1/rescan/by-name``.**  Replaces
  ``POST /api/v1/rescan/{idx}``.  ``channel_name`` is supplied as a
  query parameter (URL-encode ``#`` as ``%23``).  New error paths:
  400 ``missing_channel_name``, 404
  ``channel_name_not_in_watchlist``.  Existing error paths
  (400 invalid_rescan_window, 409 rescan_busy) preserved.

- **``UnknownChannelName`` exception (`services/archive_rescanner.py`).**
  Validated in ``RescanJobManager.submit`` against the live
  watchlist on submit-time per ontwerp 0.2.6 §9.2 (a delete *after*
  submit but *before* worker pickup is by design tolerated and
  surfaces as ``not_decryptable`` in the counters, not as a job
  failure).

### Changed

- **``DecodedPacket.channel_idx: Optional[int]`` →
  ``DecodedPacket.channel_name: str``.**  Default ``""`` when
  ``is_decrypted`` is False.  The decoder no longer carries idx
  values across its output boundary.

- **``PacketDecoder._secret_to_idx`` →
  ``PacketDecoder._secret_to_name``.**  Same dict shape, name-keyed
  values.  ``add_channel_key(name, secret_bytes, source)``,
  ``add_channel_key_from_name(name)`` (no longer takes idx).

- **``PacketDecoder.decode(...)`` parameters.**
  ``allowed_idx`` → ``allowed_name``,
  ``priority_idx_order`` → ``priority_name_order``.  When
  ``allowed_name`` is given but is not registered (e.g. channel
  deleted between job-submit and worker pickup), ``decode()``
  returns ``None`` after the structural decode — the rescan loop
  counts that as a decode failure and continues.

- **``RescanJob.only_channel_idx: Optional[int]`` →
  ``RescanJob.only_channel_name: Optional[str]``.**  Same JSON
  shape on the wire under a new field name, surfaced in
  ``to_dict()``.

- **``fetch_priority_idx_order(...)`` → ``fetch_priority_name_order(...)``
  returning ``List[str]``.**  Two-tier ordering identical to 0.2.5:
  watchlist∩API names in API order, then remaining watchlist names
  in idx order (sorted defensively in this version so callers don't
  have to).  HTTP failure modes unchanged: every error collapses to
  an empty list and ``priority_source = "fallback"`` on the job.

- **``PacketPipeline._on_watchlist_changed`` does delta updates.**
  Replaces the 0.2.5 clear+rebuild.  Computes
  ``new_names - registered_names`` for adds and
  ``registered_names - new_names`` for removes; calls
  ``add_channel_key`` / ``add_channel_key_from_name`` and
  ``remove_channel_key`` accordingly.  The decoder's key registry
  is therefore never empty between mutations — a rescan worker
  never lands in a transient zero-key window.

- **``RescanJobManager.__init__`` takes ``store: WatchlistStore``.**
  Needed for the submit-time validation of ``only_channel_name``.
  Wiring update in ``main.py``.

- **GUI per-row rescan button sends ``channel_name``, not ``idx``.**
  ``_start_job(only_channel_name=..., label=name)``.  The Quasar
  emit and the row dict are unchanged; the Python handler just
  reads ``e.args.get("name")`` instead of ``"idx"``.

- **GUI progress label exposes the new counters** (compactere
  variant per ontwerp 0.2.6 §9.3).

### Removed

- **``POST /api/v1/rescan/{idx}``.**  Replaced by
  ``/api/v1/rescan/by-name``.  Clients calling the old path now
  receive the FastAPI default 404 (not registered).  Per ontwerp
  0.2.6 §5.5 this is intentional — there is no soft-deprecate
  alias.  Migration path is to switch to ``/rescan/by-name`` with
  ``?channel_name=`` (URL-encode ``#`` as ``%23``).

- **``DecodedPacket.channel_idx``.** Replaced by ``channel_name``.

- **``RescanJob.only_channel_idx``.** Replaced by
  ``only_channel_name``.

- **``fetch_priority_idx_order``.**  Replaced by
  ``fetch_priority_name_order`` returning ``List[str]``.

### Impact

- **Wire format.**  ``RescanJob.to_dict()`` exposes
  ``only_channel_name`` (was ``only_channel_idx``) and four
  additional fields under ``counts``:  ``decoded_total``,
  ``not_decryptable``, ``skipped_dup_message`` are new;
  ``new_rxlog``, ``skipped_dup_rxlog``, ``skipped_window``,
  ``skipped_files``, ``decode_failures`` and ``new_messages`` are
  unchanged.  Consumers that destructured ``only_channel_idx``
  must read ``only_channel_name`` instead.

- **REST.**  Per-channel rescan moved from ``/rescan/{idx}`` to
  ``/rescan/by-name?channel_name=``.  Other endpoints unchanged.
  ``GET /api/v1/messages`` still emits ``channel_idx`` in each
  item (an alias for ``Message.channel``, the display idx) for
  backward-compat with downstream consumers; identity continues
  to flow through ``channel_name`` in the same item.

- **Archive schema.**  No change.  ``Message.channel_name`` was
  already verplicht-aanwezig sinds 0.2.0; ``Message.channel``
  remains an optional integer for display.

- **Watchlist mutations during a running rescan.**  Previously
  silently miss-decoded; now have no effect on the job's scope or
  priority order.  Add of a new channel is reflected in the
  decoder for records processed thereafter; remove drops the key
  immediately.

### Rationale

ADR-001 was already settled at the design level in 0.2.4 ("naam is
identiteit, idx is positie") but only made literal in the dedup
path in 0.2.5 (template 1).  The decoder, rescan-job state, and
per-channel REST endpoint were left on idx.  That coupling
re-introduced the same problem ADR-001 was meant to prevent — just
in a different sub-system: a watchlist mutation tijdens een lopende
rescan brak de mapping tussen frozen state en live decoder.  0.2.6
finishes the application of ADR-001 to the remaining sub-systems.
KISS (ADR-005): no new abstractions, no new locks; the change is a
field-rename refactor with two new explicit primitives
(``remove_channel_key``, ``UnknownChannelName``) where §7 / §5.2 of
the design called them out.

## [0.2.5] - 2026-05-01

This release folds two work-tracks into one drop-in package:

- **template 1** — make ``channel_name`` the leidende dedup-identity
  and remove the API ``fetch_limit`` cap.
- **template 2** — make the rescan usable on a 426-channel watchlist:
  data-driven key priority and a verplicht tijdvenster per rescan-job.

### Design invariant (made explicit)

> ``channel_name`` is the stable channel identity.  The integer
> ``channel`` is a vluchtige UI-positie (the index in the user's
> watchlist) and never participates in identity keys.

### Fixed (template 1)

- **Dedup-fingerprints used the integer idx as identity, not the
  channel name.**  When the user reordered the watchlist, every
  channel got a new idx and every historical message looked
  "new" again.  Diagnosed on a live archive: 16 % of packet
  hashes (979 / 6144) appeared on multiple idx values with
  identical sender, text and ``channel_name`` — the same logical
  message ingested twice because of an idx shift.

  All four fingerprint construction sites now key off
  ``channel_name`` instead of ``channel``:

  - ``SharedData.add_message()`` (live tail dedup)
  - ``SharedData.ingest_rescanned_message()`` (rescan dedup)
  - ``SharedData._load_from_archive()`` (startup replay seed)
  - ``MessageArchive.load_all_message_fingerprints()`` (rescan
    pre-load)

  The integer ``channel`` field stays in the JSONL archive and the
  API response — useful for display — but no longer participates
  in identity keys.

  Affected files: ``meshcore_watchlist/core/shared_data.py``,
  ``meshcore_watchlist/services/message_archive.py``.

- **API ``/api/v1/messages`` reported a moving ``total`` and
  truncated paginators at ~1000 rows.**  ``get_messages_payload``
  used ``fetch_limit = offset + limit + 1000`` as a cap on records
  read from the archive before filtering.  Two failure modes:

  1. ``total`` varied per call as a function of ``limit`` /
     ``offset``, breaking the pagination contract.
  2. Downstream paginators (e.g. domca's PHP collector) were
     silently truncated at the cap, so archives larger than ~1000
     rows were unreachable past that point.

  The cap is replaced with a generous full-scan ceiling
  (``_MESSAGES_FETCH_LIMIT = 10_000_000``).  ``total`` now reflects
  the true filtered dataset size; paginators reach the end.  Cost
  on archives in the tens of thousands of rows is a few hundred
  milliseconds — acceptable for this endpoint's call rate.

  Affected file:
  ``meshcore_watchlist/services/public_api_service.py``.

### Added (template 2 — mechanism 1: data-driven key priority)

- **``meshcore_watchlist/services/channel_priority.py`` (new).**
  At rescan-job start, fetches
  ``https://www.domca.nl/api/meshcore/channel_statistics.php`` and
  produces a ``List[int]`` of channel-idx values ordered by
  expected match-rate.  Two-tier ordering:

  1. Channels present in *both* the API response and the watchlist,
     in API-order (which the API documents as
     ``aantal_berichten`` desc).
  2. Remaining watchlist channels in their existing order.

  Channels named by the API but absent from the watchlist are
  ignored (no key for them).  Watchlist channels absent from the
  API fall through to tier 2 — never silently skipped.

  All HTTP failure modes (URLError, timeout, non-2xx, malformed
  JSON, unexpected payload shape) collapse to "return an empty
  list", which the rescanner treats as the no-op case
  (``priority_source = "fallback"`` on the job status).  The
  rescan therefore never fails on domca being unreachable.

  Hard 5-second HTTP timeout.  No ``requests`` dependency added —
  uses ``urllib.request`` from the standard library.

- **``PacketDecoder.decode()`` accepts an optional
  ``priority_idx_order: Optional[List[int]] = None``.**  When
  ``None`` (default), iteration over registered keys is bit-for-bit
  identical to 0.2.4 — the live tail is unaffected.  When a list
  is supplied, those idx values are tried first in the given
  order; remaining registered keys are then tried in
  dict-iteration order so a freshly-added watchlist channel is
  never silently skipped.

  ``allowed_idx`` (per-channel rescan scope) and
  ``priority_idx_order`` compose: ``allowed_idx`` filters the
  iteration, ``priority_idx_order`` orders it.

  Why the API's ``first_received_at`` / ``last_received_at`` are
  **not** consulted here: those fields in domca's database were
  corrupted by an earlier truncate-and-faulty-rescan incident and
  show artificially-recent ``first_received_at`` for channels
  that existed long before that date.  Pre-filtering on a field
  we know is wrong would silently drop matches.  The user picks
  the rescan window explicitly via ``start_date`` / ``end_date``;
  the API supplies ranking only.  A future session that
  reconsiders these fields should first confirm with the
  operator that domca's timestamps have been recomputed.

  Affected file:
  ``meshcore_watchlist/decoder/packet_decoder.py``.

### Added (template 2 — mechanism 2: verplicht tijdvenster)

- **Every rescan-job carries a mandatory inclusive UTC-day
  window.**  ``RescanJob`` gets ``start_date`` and ``end_date``
  fields; ``RescanJobManager.submit()`` requires them as the
  first two arguments and validates them via the new
  ``validate_window()`` helper.  ``InvalidRescanWindow`` is
  raised when either date is missing, not a ``YYYY-MM-DD``
  string, or when ``start_date > end_date``.

  Both endpoints
  (``POST /api/v1/rescan``, ``POST /api/v1/rescan/{idx}``)
  expose ``start_date`` and ``end_date`` as **required** query
  parameters.  A missing or malformed value returns 400 with
  ``error: invalid_rescan_window`` and a human-readable message.

  No implicit defaults.  An explicit window forces the operator
  to think about scope and prevents an accidental full-history
  rescan on a 426-channel watchlist.

  Affected files:
  ``meshcore_watchlist/services/archive_rescanner.py``,
  ``meshcore_watchlist/api/routes.py``,
  ``meshcore_watchlist/gui/dashboard.py``.

- **Two-level filtering on the rescan window.**

  *Filename-skip.*  Source files whose name embeds an unambiguous
  ``YYYY-MM-DD`` substring outside the window are dropped before
  opening — dramatically faster than line-level filtering on a
  multi-day archive.  Skipped count surfaces as
  ``counts.skipped_files`` on the job status so the operator can
  see the layer fired.  The unbounded ``*_rxlog.json`` snapshot
  has no date in its name and falls through to record-level
  filtering, which is correct.

  *Record-level filter.*  For records that survive the
  filename-skip, ``derive_record_timestamp_utc`` recovers the
  record's original UTC arrival time and ``_handle_line``
  early-returns *before* the decode loop if it falls outside
  the window.  That avoids both the O(N_channels) trial-decrypt
  and the archive write for out-of-window records.  Skipped
  count surfaces as ``counts.skipped_window``.

  Records with an unparseable timestamp default to
  "inside the window" — better to fall through to the existing
  dedup pipeline than silently drop a row over a clock-recovery
  edge case.

  Affected file:
  ``meshcore_watchlist/services/archive_rescanner.py``.

- **GUI: two date inputs next to the rescan button.**  Both the
  full-archive button and the per-channel rescan buttons in the
  watchlist table now read ``start_date`` / ``end_date`` from
  the date inputs in the rescan row.  Empty inputs surface a
  warning notify; server-side ``InvalidRescanWindow`` surfaces
  a negative notify with the validation message verbatim.

  Affected file:
  ``meshcore_watchlist/gui/dashboard.py``.

### Changed

- ``RescanJob.to_dict`` exposes ``start_date``, ``end_date``,
  ``priority_source`` and adds ``counts.skipped_window`` /
  ``counts.skipped_files``.  Existing fields are unchanged so
  GUI clients that don't read the new fields still work.

### Performance baseline & measured improvement

**Not yet measured on the live install.**  This release is
intended to enable the measurement.  A representative rescan
on the live install (≈114K rxlog records, 426+ watchlist
channels) under 0.2.4 ran for several hours; the expectation
for 0.2.5, with mechanism 1 + mechanism 2 active, is minutes
for a matching-traffic-heavy window.  The acceptance criterion
in the template is "measure and record in this CHANGELOG".

When the measurement is available, append below this paragraph:

```
0.2.4 baseline — N rxlog records, M watchlist channels,
window YYYY-MM-DD..YYYY-MM-DD: <wallclock>
0.2.5 measured — same input: <wallclock>, speedup <factor>x
```

### Operational notes

- **No archive migration.**  Existing JSONL records already carry
  ``channel_name``; the new fingerprint scheme reads it directly.
  Historical collisions in the archive (the 979 rows) are not
  rewritten — they remain visible but no longer grow.
- **Live tail unchanged.**  ``priority_idx_order`` is rescan-only;
  the live-tail decode path passes ``None`` and behaves
  bit-for-bit as 0.2.4.
- **No new third-party dependencies.**  ``urllib.request`` is
  standard library.
- **Watchlist mutation during a rescan.**  The priority list is
  frozen at job start; a channel added mid-rescan is tried after
  the priority list (never silently skipped), and the next
  rescan-job picks it up at its proper position.

### Deploy

Drop-in replacement.  Restart the systemd unit after copying the
package into place:

```bash
sudo systemctl restart meshcore-watchlist
```

No archive on-disk format changes.  Existing ``*_messages.jsonl``
and ``*_rxlog.jsonl`` are read as-is.

## [0.2.4] - 2026-04-30

### Fixed

- **Archive writes were O(N²) per flush.**
  ``MessageArchive._flush_messages()`` and ``_flush_rxlog()`` in
  the previous storage format read the entire archive file from
  disk, appended the buffered records, re-serialised everything
  to JSON, wrote it to a temp file, and atomic-renamed over the
  original — every flush.  With ``_batch_size = 10`` (intended
  to keep per-flush rewrite cost down), every 10 inserts
  triggered a full-file rewrite.

  At small archive sizes the cost was barely noticeable.  When
  the rescanner in 0.2.3 began ingesting the full
  ``*_rxlog.json`` history (~114 K records over 8 days), the
  cost compounded into a multi-hour stall: the first 1 K
  records flushed in 0.7 s, the 5 K-record mark in 15 s, and
  the 10 K-record mark in 60 s — quadratic, with the trend
  pointing at multiple hours for the full 100 K+ records of a
  fresh 0.2.3 rescan.

  The archive is now stored as JSON-Lines (``.jsonl``) — one
  record per line, written append-only with
  ``open(path, "a")`` + ``write`` + ``fsync``.  Every flush is
  O(buffer-size) regardless of total archive size.  Measured
  against the same 100 K-record synthetic workload that
  previously timed out: now completes in **1.4 s end-to-end**
  (~73 K records/sec, with a flat throughput curve from start
  to finish).

  Format version is encoded in the filename suffix:

  - ``*.json``  — format 1 (legacy, read-merge-rewrite),
  - ``*.jsonl`` — format 2 (current, append-only).

  All readers (``query_messages``, ``get_message_by_hash``,
  ``load_all_message_fingerprints``,
  ``load_all_rxlog_hashes``, ``get_distinct_channel_names``,
  ``get_messages_by_sender_pubkey``) and the retention-cleanup
  path were updated to stream the new format.  Retention
  cleanup itself is now a single one-shot rewrite (read .jsonl,
  filter, write to .jsonl.tmp, atomic rename) instead of being
  baked into every flush.

  ``SharedData._load_from_archive()`` reads the ``.jsonl`` via
  the same streaming iterator, with a sliding-window trim so
  loading a multi-GB archive does not balloon memory: only the
  trailing ``MAX_MESSAGES`` / ``MAX_RX_LOG`` records are kept
  in the in-memory caches.

  Affected files:
  ``meshcore_watchlist/services/message_archive.py``,
  ``meshcore_watchlist/core/shared_data.py``.

### Migration

- **Automatic, one-shot, on first start of 0.2.4.** When a
  legacy ``*_messages.json`` or ``*_rxlog.json`` (format 1) is
  found and the corresponding ``.jsonl`` does not yet exist,
  the archive constructor reads the legacy file, writes its
  ``messages`` / ``entries`` array as one record per line into
  a new ``.jsonl`` (atomically via ``.jsonl.tmp`` + rename),
  and then renames the legacy file to
  ``*.json.migrated-v1`` — kept on disk for recovery, *not*
  deleted.

  Subsequent starts notice that ``.jsonl`` already exists and
  skip the migration.  A failure during migration leaves the
  legacy file untouched so the next start retries.

  No manual intervention is required, but operators may want
  to verify after the first 0.2.4 start by listing
  ``~/.meshcore-watchlist/archive/``: there should be a fresh
  ``*.jsonl`` next to a ``*.json.migrated-v1`` per device, and
  the line count of the ``.jsonl`` should match the
  ``messages`` / ``entries`` array length of the legacy file.

  The ``*.json.migrated-v1`` files can be deleted manually
  once the operator is satisfied with the migration.

## [0.2.3] - 2026-04-30

### Fixed

- **Rescan only saw the last ~3 days of history.** The rescanner
  globbed exclusively for ``*_rxlog.jsonl`` files, but
  meshcore-gui keeps two parallel rxlog files per device:

  - ``*_rxlog.jsonl`` — append-only line file with the most
    recent few days (~32 MB observed),
  - ``*_rxlog.json``  — pretty-printed snapshot containing the
    full retained history (~150+ MB observed, 8 days of records).

  Both contain identically-shaped records.  The pretty-printed
  ``.json`` was simply ignored, so any rescan reached back only
  as far as the ``.jsonl`` window — typically 3 days, sometimes
  less depending on meshcore-gui's flush schedule.

  The rescanner now picks up ``.json`` files in addition to
  ``.jsonl``.  Format is detected per-file: an opening brace
  followed by a newline is treated as pretty-printed JSON
  (single-record-per-file with an ``entries`` array, parsed via
  a streaming reader that walks the file line-by-line and
  reconstructs records by matching the ``"    {"`` /
  ``"    }"`` indent markers used by meshcore-gui's pretty
  writer); anything else is treated as JSONL and parsed
  line-per-record as before.  Files are processed
  ``.json``-first, then ``.jsonl``, and overlap between the two
  is absorbed by the existing ``message_hash`` and message
  fingerprint dedup sets — duplicates are recognised as
  already-archived and skipped.

  The pretty-printed parser deliberately does *not* call
  ``json.load()`` on the whole file (that peaks at over a
  gigabyte of resident memory on a 153 MB input).  It streams
  one record at a time, runs ``json.loads`` per record, and
  gracefully skips any trailing record that is incomplete
  because meshcore-gui is still writing to the file — that
  record is picked up on the next rescan or by the live tail.

  Affected file:
  ``meshcore_watchlist/services/archive_rescanner.py``.

### Notes

- The live ``JsonlTailer`` is **not** changed.  It continues to
  follow ``*_rxlog.jsonl`` only, which is the right behaviour:
  the ``.jsonl`` is always at-or-ahead of the ``.json`` for new
  records, so anchoring the live cursor on the ``.json`` would
  introduce latency without benefit.  Pretty-printed ``.json``
  is therefore treated as a rescan-only data source.

- Operational note: a rescan that includes a 150+ MB pretty
  ``.json`` will produce significantly more on-disk archive
  writes than previous versions, since up to ~8 days of
  previously-unseen history may now decode successfully.
  Expect the first post-upgrade rescan on an active mesh to
  add tens of thousands of message rows; subsequent rescans
  are quick because the dedup sets cover the same ground.

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

[0.2.5]: #025---2026-05-01
[0.2.4]: #024---2026-04-30
[0.2.3]: #023---2026-04-30
[0.2.2]: #022---2026-04-29
[0.2.1]: #021---2026-04-29
[0.2.0]: #020---2026-04-28
[0.1.1]: #011---2026-04-27
[0.1.0]: #010---initial-release
