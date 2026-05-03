# Architecture Decision Records — PE1HVH projects

This folder contains the Architecture Decision Records (ADRs) for the
projects of PE1HVH. An ADR records one architecture or design decision:
what was decided, why, and what the consequences are. Anyone who later
encounters an unusual choice in the code reads here why that choice was
made at the time.

## Conventions

### File names

`ADR-NNN-short-title-with-dashes.md`, with `NNN` a running serial
number of three digits, starting at `001`. Numbers are not reused, not
even when an ADR is superseded or withdrawn.

### Status values

| Status           | Meaning                                                                                            |
|------------------|----------------------------------------------------------------------------------------------------|
| **Proposed**     | Draft, not yet decided. May be modified freely.                                                    |
| **Accepted**     | Decision stands. Code and review hold to it.                                                       |
| **Superseded**   | Replaced by a newer ADR. The file remains in place, with a reference to the succeeding ADR.        |
| **Withdrawn**    | Not superseded, but no longer valid (e.g. because a feature has been removed).                     |

Once **Accepted**, the content is no longer modified. Changing the
decision happens via a new ADR that **Supersedes** the old one.

### Dates

All dates in ADRs (and more broadly, in all PE1HVH projects) are in
ISO 8601 format: `YYYY-MM-DD`. See ADR-002.

### Author

The default author is **PE1HVH (Hans)**. If a third party proposes an
ADR, that name appears there; the decision remains a PE1HVH decision
and is explicitly marked **Accepted** before it takes effect.

### Template

New ADRs start from `ADR-template.md`. Copy the file, give it the next
available number, and fill in the six chapters. An ADR with incomplete
chapters is not **Accepted**.

## Index

| ID      | Title                                                          | Scope                  | Status     |
|---------|----------------------------------------------------------------|------------------------|------------|
| ADR-001 | `channel_name` is the stable identity of a channel             | meshcore-watchlist     | Accepted   |
| ADR-002 | Date and time format is ISO 8601 with YYYY-MM-DD for dates     | All PE1HVH projects    | Accepted   |
| ADR-003 | Standard project folder layout                                 | All PE1HVH projects    | Accepted   |
| ADR-004 | Naming conventions — folders, namespaces, classes, functions   | All PE1HVH projects    | Accepted   |
| ADR-005 | SOLID and KISS as design principles                            | All PE1HVH projects    | Accepted   |
| ADR-006 | Language policy for documentation and project communication    | meshcore-watchlist     | Accepted   |
| ADR-007 | Channel-name length and charset bounded by Companion Protocol  | meshcore-watchlist     | Accepted   |

## Glossary

Terms appearing in ADRs that are not self-evident:

- **Invariant** — a property that must always hold true, regardless of
  the operation performed. ADR-001 establishes such an invariant:
  `channel_name` is always the identity, at every moment, in every
  path.
- **Drop-in replacement** — a new version of a package that replaces
  the old one-for-one: same import paths, same install route, no
  changes needed in code that uses the package.
- **Shape (of a data structure or API response)** — the structure:
  which fields, with which types, in which place in the tree.
- **Live tail** — the path on which an incoming packet (from the
  radio, via meshcore-gui) goes directly through the pipeline: tailer
  reads the JSONL line as `tail -f` would, decoder decrypts,
  shared-data stores.
- **Rescan** — reprocessing previously received, archived packets,
  for example after a new key has been added or after a watchlist
  change makes a previously unreadable message readable.
- **Fingerprint (of a message)** — a tuple of fields that together
  uniquely identify a logical message within the archive, used for
  deduplication between live tail and rescan.
- **idx** — the position of a channel in the current watchlist
  array, an integer starting at 0. Volatile: changes on every
  watchlist mutation. Not identity; see ADR-001.
- **PSR (PHP Standard Recommendations)** — style standards set by
  PHP-FIG. Relevant in this register: PSR-1 (basic style), PSR-4
  (autoloading), PSR-12 (extended style). See ADR-004.
- **PEP 8** — the Python style guide, *Style Guide for Python Code*.
  Deviates on function-name style from the cross-language rule of
  ADR-004 (snake_case instead of camelCase). Accepted exception
  within Python.
- **SOLID** — five object-oriented design principles (Single
  Responsibility, Open/Closed, Liskov Substitution, Interface
  Segregation, Dependency Inversion). See ADR-005.
- **KISS** — *Keep It Simple, Stupid.* The simplest solution that
  solves the problem correctly wins. See ADR-005.
- **YAGNI** — *You Aren't Gonna Need It.* A variant of KISS aimed
  specifically at rejecting speculative features. Not recorded as its
  own principle; covered by KISS.
