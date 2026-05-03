# ADR-007: Channel-name length and charset are bounded by the MeshCore Companion Protocol

| Field             | Value                                                         |
|-------------------|---------------------------------------------------------------|
| **Status**        | Accepted                                                      |
| **Date**          | 2026-05-03                                                    |
| **Author**        | PE1HVH (Hans)                                                 |
| **Scope**         | meshcore-watchlist (all components that accept channel names) |
| **Supersedes**    | —                                                             |
| **Superseded by** | —                                                             |

---

## 1. Context

`meshcore-watchlist` accepts channel names from three sources:

- The **GUI** (Watchlist-tab "Add"-input), via `WatchlistStore.add()`.
- The on-disk **`watchlist.json`** at startup, via `WatchlistStore._load()`.
- The **REST API** `POST /api/v1/channels?name=...` (introduced in
  0.3.0), used by the out-of-process `tools.channel_injector` cron job
  and any other client that wants to seed the watchlist over HTTP.

Up to and including version 0.3.0 none of these paths enforced a
length limit or a charset constraint on the name. The only check was
that the name was non-empty and (in the POST path) free of
control-characters. In practice that is permissive enough to accept
names that the radio firmware can never use, and that opens two
concrete failure modes:

- A misconfigured or malicious upstream listing can push names of
  arbitrary length into `watchlist.json`. The SHA-256 key derivation
  still works on any byte length, so the live decoder appears to
  function — but the entry is **unusable** as a real channel because
  the firmware cannot accept it (see §3).
- Operator confusion: a name that the watchlist accepts but no
  MeshCore device can ever broadcast on is a silent data-quality
  bug. It is invisible until someone asks "why is `#weather` not
  receiving anything" and discovers the on-disk name is 47 bytes.

The MeshCore Companion Protocol itself (last documented 2026-03-08,
protocol version 1.12.0+) defines the wire format unambiguously:

- `CMD_SET_CHANNEL` (0x20): bytes 2-33 are the **Channel Name**,
  **32 bytes, UTF-8, null-padded**.
- `PACKET_CHANNEL_INFO` (0x12): same 32-byte slot, described as
  null-terminated in the response shape.
- No charset restriction beyond UTF-8 — the protocol accepts any
  Unicode codepoint that fits within the 32-byte field.
- The leading `#` that this project uses for hashtag channels is a
  community convention (it is the input to the SHA-256-based key
  derivation, `key = sha256(name)[:16]`), not a protocol-level
  requirement.

## 2. Decision

A channel name accepted by `meshcore-watchlist` is a UTF-8 string
whose **encoded length is at most 32 bytes**. Names longer than 32
UTF-8 bytes are rejected at every entry point. No further charset
restriction is imposed beyond rejecting ASCII control characters
(byte values `< 0x20` or `= 0x7F`), which serve no purpose in a
channel name and create a header / log-injection surface.

## 3. Rationale

The 32-byte limit is not an arbitrary design choice — it is the size
of the `Channel Name` field in `CMD_SET_CHANNEL` on the wire. A name
that does not fit in 32 UTF-8 bytes can never be activated on a
MeshCore device, so a watchlist entry exceeding that limit cannot
correspond to a real channel: it is data that looks valid, decodes
nothing, and is therefore noise.

The unit is **bytes**, not **characters**. Length-checking on
`len(name)` (Python `str` length, i.e. codepoints) would either be
too generous (32 codepoints can be 64+ bytes in UTF-8 with non-ASCII
input) or too strict (rejecting names that fit fine on the wire).
Encoding to UTF-8 first and counting bytes is the single rule that
matches the protocol exactly.

The charset stays liberal because the protocol is liberal. The
MeshCore community uses Unicode names in practice (regional
characters, emoji, etc.); imposing a `[A-Za-z0-9_-]`-style restriction
would be stricter than the firmware itself and would break legitimate
hashtag conventions like `#café` or `#groningen-müllerthal`. The
control-character exclusion is the minimum needed to prevent CR/LF in
log lines and HTTP headers from the POST endpoint — a security
hygiene rule, not a charset rule.

## 4. Consequences

- **What becomes easier**

  - Validation logic is uniform across GUI, file-load, and REST.
    One helper (`is_valid_channel_name`) decides accept / reject for
    all three paths.
  - "Why is this channel not receiving" diagnostics gain a clear
    rule: if the on-wire name does not fit, the channel cannot
    exist on a real device.
  - The `POST /api/v1/channels` endpoint can return a structured
    `name_too_long` error with the actual byte count, instead of
    accepting the name and silently producing dead state.

- **What becomes harder**

  - A pre-existing `watchlist.json` written by 0.3.0 or earlier may
    contain entries whose names exceed 32 bytes. They are loaded
    as-is (the file format does not change and re-validation on load
    would surprise operators). Only **new** mutations are checked.
    See §6 for the migration note.
  - Length is in bytes, not characters — operators with non-ASCII
    names need to be aware that 32 codepoints is not always 32 bytes.
    The error message includes both numbers to make this concrete.

- **What must be enforced**

  - `WatchlistStore.add()` rejects names whose UTF-8 encoding
    exceeds 32 bytes, returning `False` — same convention it uses
    for duplicates.
  - `POST /api/v1/channels` returns HTTP 400 with
    `{"error": "name_too_long", "max_bytes": 32, "got_bytes": N}`
    for names exceeding the limit.
  - `tools.channel_injector` validates client-side and skips
    over-long names with reason `"name_exceeds_32_bytes"` before
    calling the API, to keep cron logs informative.
  - The bytes-not-codepoints subtlety is documented inline in the
    code (one comment per check) and in `docs/datadictionary.md`
    §5.8 alongside the API shape.

## 5. Alternatives considered

**Cap on codepoints (`len(name) > 32`).** Rejected: produces a
different boundary than the wire format and would let names like
`#`+30 emoji through (~120 UTF-8 bytes) while rejecting an ASCII
name of 33 chars (33 bytes). The two are not the same problem.

**Cap at 31 bytes to leave room for a null terminator.** Rejected:
the protocol docs describe the field as "null-padded" — the field is
32 bytes wide; a 32-byte name fills the field and needs no
terminator. The `PACKET_CHANNEL_INFO` response uses the wording
"null-terminated", but inspection of the existing
`meshcoredecoder` and reference clients shows the firmware does not
require a trailing null inside the 32-byte slot. Going to 31 would
cap one byte below the actual protocol limit and be silently wrong
in the other direction.

**Restricted charset, e.g. `[A-Za-z0-9_-]`.** Rejected: stricter
than the firmware. The MeshCore community uses Unicode names; this
project should not be the layer that breaks them. Operators who want
a stricter local convention can enforce it at the edge (cron source,
GUI input filter) without it being a protocol-level rule.

**Validate on file-load as well, rewrite or skip non-conforming
entries.** Rejected: the JSONL invariant "the file is the source of
truth" means a load-time rewrite would alter operator-visible state
without an explicit action. Loading is permissive by design; only
mutations validate.

**Capture this in `config.py` as `CHANNEL_NAME_MAX_BYTES = 32` for
later configurability.** Rejected per CLAUDE.md (no overengineering):
the value is fixed by the protocol, not a deployment-tunable. Making
it configurable would invite divergence from the wire format.

## 6. References

- **Protocol source**:
  [MeshCore Companion Protocol — Set Channel command](https://docs.meshcore.io/companion_protocol/#4-set-channel)
  (`CMD_SET_CHANNEL`, 0x20; bytes 2-33 are the Channel Name field,
  32 bytes UTF-8 null-padded). Last reviewed: 2026-05-03.
- **Affected code locations**:
  - `meshcore_watchlist/services/watchlist_store.py::WatchlistStore.add`
  - `meshcore_watchlist/api/routes.py::api_channels_add`
  - `tools/channel_injector/injector.py::_is_safe_channel_name` and
    `_is_valid_channel_name`
- **Related ADRs**:
  - ADR-001 (channel_name is the stable identity) — establishes that
    the *name* is the identity, this ADR sets the *bounds* of that
    identity.
  - ADR-005 (SOLID and KISS) — supports the choice not to make this
    configurable.
- **Documents updated by this ADR**:
  - `docs/architecture.md` §9.4 (status code table)
  - `docs/fto.md` §6.3 (status code table) and UC-13 (foutpaden)
  - `docs/datadictionary.md` §5.8 (`name_too_long` row in error
    responses)
  - `tools/channel_injector/README.md` (foutpaden table)
- **Migration**: pre-existing `watchlist.json` entries that exceed
  32 UTF-8 bytes are not removed automatically; they remain on the
  watchlist until an operator explicitly removes them. The decoder
  still attempts to use them, but no real device can produce
  matching traffic, so they are effectively no-ops.

---

*Conventions for this ADR register are in `README.md` in this folder.*
