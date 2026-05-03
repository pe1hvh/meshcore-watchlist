# ADR-002: Date and time format is ISO 8601 with YYYY-MM-DD for dates

| Field             | Value                                                         |
|-------------------|---------------------------------------------------------------|
| **Status**        | Accepted                                                      |
| **Date**          | 2026-05-01                                                    |
| **Author**        | PE1HVH (Hans)                                                 |
| **Scope**         | meshcore-watchlist, domca.nl, all PE1HVH projects             |
| **Supersedes**    | —                                                             |
| **Superseded by** | —                                                             |

---

## 1. Context

Dates and timestamps are written in IT projects in countless formats:
`01-05-2026` (Dutch notation), `5/1/2026` (US notation), `May 1, 2026`,
Unix epoch in seconds, milliseconds, or nanoseconds, and every variant
of ISO 8601 with or without time zone.

Without an agreement, every layer of the stack grows its own parsers,
formatters, and conversions, with these consequences:

- region bugs (a date called `01-05` may be 1 May or 5 January,
  depending on the parser),
- sort bugs (`5/1/2026` sorts lexicographically between `4/9/2026`
  and `5/2/2026`, so always wrong),
- time-zone confusion (log lines from a server in UTC and a client in
  CEST sitting next to each other without a visible offset),
- API incompatibility (two subsystems serialising the same field
  differently).

Within meshcore-watchlist, dates are already partly set on YYYY-MM-DD
(rescan window `start_date` / `end_date`); timestamps on records are
not yet consistently so. domca-API fields such as `first_received_at`
and `last_received_at` are not formally pinned to a format.

## 2. Decision

All dates, times, and timestamps — internal, in storage, in APIs, in
log output, and on screen — are serialised as **ISO 8601** in UTC:

- **Date (without time):** `YYYY-MM-DD`, for example `2026-05-01`.
- **Timestamp (date + time):** `YYYY-MM-DDTHH:MM:SSZ`, for example
  `2026-05-01T14:33:07Z`. The `T` as separator and the `Z` as
  UTC marker are mandatory.
- **Timestamp with sub-seconds** (only where needed, e.g. high-rate
  packet logging): `YYYY-MM-DDTHH:MM:SS.sssZ`.

Time zone is always UTC in storage and in API exchange. Local time is
permitted only in the display layer for the end user, and even there
UTC + offset (`2026-05-01T16:33:07+02:00`) is preferred over unmarked
local time.

## 3. Rationale

ISO 8601 is:

- **Lexicographically sortable** — string sorting yields chronological
  order, without a cast.
- **Unambiguous** — no confusion between day-first and month-first.
- **Internationally standardised** — natively supported by every common
  programming language, database, and tool (Python's
  `datetime.isoformat()`, SQLite, PostgreSQL, JavaScript `Date`, jq,
  and so on).
- **Human-readable** — no epoch numbers that are meaningless without
  conversion.
- **Compact** — fixed length, suitable for log lines and filenames.

UTC as storage time zone eliminates daylight-saving edge cases (a
timestamp on the Sunday of the smallest-hour transition otherwise has
two valid values or none).

## 4. Consequences

**What becomes easier:**

- A rescan window that lives as a string in a URL or field is directly
  usable without a parser choice.
- Log files are chronologically sortable with `sort` or `awk` on the
  timestamp field.
- API fields have one format; no "epoch in this endpoint, string in
  that endpoint".

**What becomes harder:**

- Existing domca fields (`first_received_at`, `last_received_at`) and
  archive records in another format require migration or a conversion
  layer at read time. One-time cost; new data is written conformant
  immediately.
- The user sees UTC times (two hours behind during summer time in NL).
  Acceptable for the operator audience of this package; for end-user
  display, conversion to local time can be added in the view layer,
  provided it carries an explicit offset.

**What must be enforced:**

- Code-review check: every new date/time field in models, API
  schemas, and archive records carries a docstring or comment naming
  the format: `# YYYY-MM-DD UTC, inclusive` or
  `# YYYY-MM-DDTHH:MM:SSZ (ISO 8601, UTC)`.
- Acceptance criterion for every release that adds a date/time field:
  a unit test verifying round-trip serialisation
  (string → datetime → string).
- Lint rule or grep check: no `strftime("%d-%m-%Y")`,
  `strftime("%m/%d/%Y")` or variants in code that writes persistent
  data.
- API documentation states explicitly "all times in UTC, ISO 8601" in
  the general section, and need not repeat it per endpoint.

## 5. Alternatives considered

**Alternative A — Unix epoch (seconds or milliseconds since
1970-01-01).** Rejected because it is unreadable without conversion,
seconds-vs-milliseconds errors are hard to spot, and the field is
opaque without context (`1746107587` — when is that?).

**Alternative B — local time with a time-zone suffix
(`2026-05-01 16:33:07 CEST`).** Rejected because textual time-zone
names are ambiguous (CEST in which year? Under which regulation?), and
parsers handle them inconsistently. A numeric offset (`+02:00`) is
acceptable for display, but for storage UTC is simpler.

**Alternative C — RFC 2822 / email-style
(`Fri, 01 May 2026 14:33:07 +0000`).** Rejected because it has variable
length, is not lexicographically sortable, and is not used anywhere in
this project's software chain.

**Alternative D — pick what is convenient per field.** Rejected
because that is exactly the situation this ADR resolves.

## 6. References

- ISO 8601:2019, *Date and time — Representations for information
  interchange*.
- RFC 3339, *Date and Time on the Internet: Timestamps* (the
  internet-profile version of ISO 8601).
