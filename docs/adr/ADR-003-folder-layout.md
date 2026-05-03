# ADR-003: Standard project folder layout

| Field             | Value                                                         |
|-------------------|---------------------------------------------------------------|
| **Status**        | Accepted                                                      |
| **Date**          | 2026-05-01                                                    |
| **Author**        | PE1HVH (Hans)                                                 |
| **Scope**         | All PE1HVH projects (language-independent)                    |
| **Supersedes**    | —                                                             |
| **Superseded by** | —                                                             |

---

## 1. Context

PE1HVH projects exist in several languages (Python, PHP, JavaScript,
occasionally others). Without a fixed agreement on folder structure,
every new project ended up with its own layout: sometimes code in
`src/`, sometimes in a package directory directly under the root,
sometimes in `include/`, sometimes mixed with the web layer in the
same folder. The result:

- when resuming a project after a few months, first hunting for where
  the code lives;
- copy-paste of scripts or build steps between projects fails without
  adjustment;
- AI tooling tuned for one project breaks in the next because the
  assumptions about folder locations no longer hold.

A fixed layout costs little (agreed once, automatic afterwards) and
delivers immediate calm.

## 2. Decision

Every PE1HVH project uses this top-level structure:

```
<project-root>/
├── src/              # all application code (default)
│   └── …             # or: package directory directly under the root,
│                     #     for languages where that is the norm
│                     #     (Python flat-layout)
├── html/             # placeholders / web roots / thin entry points only
│                     # no application logic
├── docs/             # project documentation
│   └── adr/          # Architecture Decision Records (see ADR-template.md)
├── tests/            # all test code, parallel to src/
├── README.md         # what the project is and how to run it
├── CHANGELOG.md      # version history per Keep a Changelog
└── <language config> # composer.json, pyproject.toml, package.json, …
```

**Fixed rules:**

- **One place for code.** Default `src/`. In languages with a strong
  convention against a `src/` wrapper (Python flat-layout with the
  package directly under the root), that convention may be followed,
  provided the project does so consistently.
- **`include/` as an alternative to `src/`** is permitted for legacy
  or small PHP projects where `include/` was already historically in
  use. Not both side-by-side in the same project.
- **`html/` is exclusively for placeholders and the web front.** No
  business logic, no database queries, no utility functions. An
  `html/index.php` does no more than invoke an entry point from `src/`.
- **`docs/adr/`** is the fixed location for the ADR register.
- **`tests/`** runs structurally parallel to `src/`: `src/foo/bar.php`
  has its test in `tests/foo/bar.test.php` (or the language
  equivalent).

## 3. Rationale

One layout for all projects means:

- Starting a new project costs no time on structure choices.
- The user (Hans) or an AI assistant can find the right place in any
  project without explanation.
- Build and deploy scripts are portable.
- The separation `src/` vs `html/` enforces that application logic
  does not accidentally end up in the web root (and from there
  unintentionally publicly accessible).

`html/` as the name instead of `public/` or `www/` is a choice that
follows from existing PE1HVH projects (`pe1hvh.nl`, `domca.nl`); the
name matters less than the fact that it is fixed.

## 4. Consequences

**What becomes easier:**

- Every new project starts from a fixed skeleton.
- Cross-project tooling (linter configuration, CI scripts, deploy
  recipes) works without per-project adjustment.

**What becomes harder:**

- Existing projects that do not conform to this layout require a
  one-time clean-up. That need not happen all at once; it may be
  phased, provided every project is conformant at some point.

**What must be enforced:**

- On `git init` of a new project: start from a skeleton that has this
  structure. A `cookiecutter` or comparable template is a worthwhile
  follow-up step (worth its own ADR if and when).
- Code-review check: nothing in `html/` that is not a placeholder or
  thin entry point.
- The README.md of every project explicitly names which variant is in
  use (`src/`, package-flat, or `include/`) and why.

## 5. Alternatives considered

**Alternative A — choose layout per project.** Rejected, because that
is precisely the situation this ADR resolves.

**Alternative B — strict PSR-4 for everything, including non-PHP.**
Rejected, because PSR-4 is a PHP-specific standard and does not fit
without distortion onto Python flat-layout or JavaScript projects with
`package.json` conventions.

**Alternative C — separate top-level folder for configuration
(`config/`).** Rejected for now — adds complexity without clear gain
in projects of this size. May be reconsidered in its own ADR if
practice demands it.

## 6. References

- ADR-001 (channel_name as identity) — unrelated, but illustrates the
  format for a comparable scope statement.
- PSR-4: PHP autoloading standard that proposes `src/` as default.
- Python Packaging User Guide, *Src layout vs flat layout*.
- Existing PE1HVH projects with this layout: `domca.nl`, `pe1hvh.nl`.
