# ADR-001: `channel_name` is the stable identity of a channel

| Field             | Value                                                         |
|-------------------|---------------------------------------------------------------|
| **Status**        | Accepted                                                      |
| **Date**          | 2026-05-01                                                    |
| **Author**        | PE1HVH (Hans)                                                 |
| **Scope**         | meshcore-watchlist (all components)                           |
| **Supersedes**    | —                                                             |
| **Superseded by** | —                                                             |

---

## 1. Context

In the watchlist, every channel has two designations:

- **`channel_name`** — the name the user sees (e.g. `#test`,
  `#zwolle`). Stable: changes only when the user explicitly renames
  the channel.
- **`idx`** — the position in the watchlist array, an integer starting
  at 0. Volatile: changes every time the user adds, removes, or
  reorders a channel.

Up to and including version 0.2.4, `idx` was used as a key or identity
in several places:

- in dedup fingerprints of both the live-tail path and the rescan,
- in the archive replay at startup,
- in the pre-load of fingerprints from the archive,
- in the decoder (`_secret_to_idx`, `DecodedPacket.channel_idx`),
- in the rescan job (`only_channel_idx`),
- in the REST API (`POST /api/v1/rescan/{idx}`),
- in the GUI button for per-channel rescan.

**Measured impact on a live archive (1 May 2026):**
979 of 6144 packet hashes (16 %) appeared on multiple `idx` values
with identical `sender`, `text`, and `channel_name` — the same logical
message was stored multiple times in the archive because the user had
shifted the watchlist in between, and every shift made all historical
messages "new" to the dedup layer.

## 2. Decision

`channel_name` is the identity of a channel. `idx` is purely a position
in the current watchlist render and plays no role in identity, key,
scope, priority, routing, or dedup.

## 3. Rationale

Identity must be stable over time, otherwise it is not identity. `idx`
changes on every watchlist mutation; `channel_name` changes only on an
explicit user action (rename). That makes `channel_name` the only
sensible choice for:

- dedup between live reception and rescan,
- the scope of a per-channel rescan,
- the priority order of decryption keys,
- the external REST API.

`idx` continues to exist as a field on `Message` and as a column in
the JSONL archive — solely for display and backwards compatibility with
the existing payload shape of meshcore-gui. At ingest time, `idx` is
derived from `channel_name` via a lookup in the current watchlist;
after that, it is a snapshot without identity.

Elsewhere in the documentation this principle is referred to as
**name-led identity**.

## 4. Consequences

**What becomes easier:**

- The user can reorder the watchlist at any time without corrupting
  the archive or causing the rescan to pick the wrong channel.
- One single rule to test against during code review: "is `idx` being
  used as identity here?" If yes, refuse.

**What becomes harder:**

- A breaking change in the internal decoder API: parameters and fields
  must be renamed. One-time cost.
- The REST endpoint `POST /api/v1/rescan/{idx}` is dropped; replaced by
  `POST /api/v1/rescan/by-name?channel_name=...`. External consumers
  must switch over once. See CHANGELOG.

**What must be enforced:**

- Acceptance criterion: `grep -rn "_idx\|channel_idx\|only_channel_idx"
  meshcore_watchlist/` must yield no hits in new code outside the
  display layer and comments that explicitly disavow the pattern.
- When designing every new parameter, field, or endpoint: explicit
  test against this ADR before implementation.
- The data dictionary of every shared data structure includes the
  column **Identity?** with one of three values: *yes — primary*,
  *yes — derived*, *no*.

## 5. Alternatives considered

**Alternative A — `secret_hex` as identity (the decryption key
itself).** Rejected because the key can roll (e.g. on a fresh
deployment of a mesh node) while the channel stays the same. In
addition, a hex blob is not display-friendly and not human-recognisable.

**Alternative B — keep `idx`, make the watchlist immutable during
rescan.** Rejected because it imposes a behavioural restriction on the
user for a problem that belongs in the design. The user must be free
to modify the watchlist at any moment; it is up to the system to
handle that correctly.

**Alternative C — generate a separate UUID per channel on creation.**
Rejected because `channel_name` is already stable and unique within a
single watchlist, intended by the user as identifying, and directly
recognisable in display. Adding a UUID layer introduces indirection
without extra certainty.

## 6. References

- Bug analysis and initial fix: CHANGELOG `[0.2.5] - 2026-05-01`,
  section *Fixed (template 1)*.
- Code locations that must be conformant with this ADR:
  - `meshcore_watchlist/core/shared_data.py`
    (`add_message`, `ingest_rescanned_message`, `_load_from_archive`)
  - `meshcore_watchlist/services/message_archive.py`
    (`load_all_message_fingerprints`)
  - `meshcore_watchlist/decoder/packet_decoder.py`
    (`_secret_to_name`, `decode(..., priority_name_order=...)`)
  - `meshcore_watchlist/services/archive_rescanner.py`
    (`RescanJob.only_channel_name`)
  - `meshcore_watchlist/api/` (route `/api/v1/rescan/by-name`)
- Working document with the full bug-fix trajectory:
  `template_3_naam_leidend_rescan.md`.
