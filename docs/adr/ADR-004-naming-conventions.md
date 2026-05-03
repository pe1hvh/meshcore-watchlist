# ADR-004: Naming conventions — folders, namespaces, classes, functions

| Field             | Value                                                         |
|-------------------|---------------------------------------------------------------|
| **Status**        | Accepted                                                      |
| **Date**          | 2026-05-01                                                    |
| **Author**        | PE1HVH (Hans)                                                 |
| **Scope**         | All PE1HVH projects                                           |
| **Supersedes**    | —                                                             |
| **Superseded by** | —                                                             |

---

## 1. Context

When setting up new projects and when generating code (by AI or by
hand at different moments), inconsistency creeps into naming:
`getVariant` next to `get_variant` next to `GetVariant` next to
`variant_get`, namespaces in PascalCase versus lowercase, folders with
capitals that work on Linux but lead to strange bugs on Windows.

Pinning naming conventions delivers two things at once:

- code in all PE1HVH projects reads the same way,
- deviations are detectable with grep or lint rules.

## 2. Decision

### Cross-language rules

| Element                  | Convention                  | Example                    |
|--------------------------|-----------------------------|----------------------------|
| Folders                  | lowercase                   | `src/decoder/packet/`       |
| Namespaces / packages    | lowercase                   | `meshcore_watchlist.core`   |
| Classes / objects        | UpperCamelCase (PascalCase) | `PacketDecoder`             |
| Functions / methods      | camelCase, verb-object      | `getVariant`, `loadConfig`, `parseHeader` |
| Constants                | UPPER_SNAKE_CASE            | `MAX_PACKET_SIZE`           |
| Class file names         | match the class name        | `PacketDecoder.php`         |

**Functions are required to follow the `verbObject` pattern.** The
first term is a verb saying what the function *does*; the second is
the object on which it operates. Not `variantGet`, not `variant`, not
`getter`. Yes `getVariant`, `setVariant`, `loadConfig`, `parseHeader`,
`validateInput`.

Lowercase folders and namespaces prevent differences between
case-sensitive (Linux) and case-insensitive (Windows, macOS-default)
filesystems.

### Per-language additions

**PHP:** follows the PSR-1 + PSR-12 style standards. Classes in
StudlyCaps (= PascalCase), methods in camelCase, constants in
UPPER_SNAKE_CASE. Autoloading follows PSR-4: namespace = directory
path. This is consistent with the cross-language rules above; PSR is,
in this case, simply the specific instantiation.

**Python:** follows PEP 8. That means the cross-language rule for
functions (`camelCase`) **deviates here**: Python functions are
`snake_case` (`get_variant`, `load_config`). Classes remain
PascalCase, modules are lowercase with underscores
(`packet_decoder.py`). This deviation is accepted because PEP 8 in
Python is enforced by tooling (black, flake8, ruff) and consistency
within the language outweighs consistency with PHP/JS.

**JavaScript / TypeScript:** classes PascalCase, functions camelCase
(follows cross-language). File names are lowercase with dashes
(`packet-decoder.js`) or match the class name (`PacketDecoder.ts`),
depending on project convention but consistent within one project.

## 3. Rationale

**Verb-object for functions** makes explicit what a function does. A
name like `variant` does not say whether it is a getter, setter,
factory, validator, or converter. `getVariant` does. This pattern
comes from Java/SmallTalk and has since been standard in PHP, JS, and
C#. In Python the pattern is also used, but in `snake_case`.

**Lowercase folders and namespaces** is not a taste choice but a
durability choice: a file `Foo.php` on Linux with an import line
`require_once 'foo.php'` does not work on Linux but does on Windows —
a crash that only surfaces in production. Lowercase eliminates this
class of bugs.

**Classes in PascalCase** is the norm in virtually all modern
languages (PHP, Python, Java, C#, JS, TS, Rust, Swift, Kotlin). Not
following it requires explanation.

## 4. Consequences

**What becomes easier:**

- When reading code, recognising directly what a name is (class?
  function? constant?).
- A grep rule `grep -rn '^[a-z][a-zA-Z]* function\|function [A-Z]'`
  finds deviations.
- Cross-project copy-paste of naming patterns works without
  adjustment.

**What becomes harder:**

- Existing projects with deviating naming require gradual conformance.
  Not everything at once; new code is conformant, old code is adjusted
  on first touch.

**What must be enforced:**

- Per language, a lint configuration that checks the naming rules:
  - PHP: PHP_CodeSniffer with PSR-12 ruleset.
  - Python: ruff or flake8 with `pep8-naming` or equivalent.
  - JS/TS: ESLint with `naming-convention` rule.
- Code-review check: for every new function, look explicitly at the
  verb-object pattern. `processData` yes, `dataHandler` (a noun) no —
  refactor to `handleData`.
- The README of every project contains a section "Naming" referring to
  this ADR and naming any project-specific exceptions.

## 5. Alternatives considered

**Alternative A — `snake_case` for all languages.** Rejected because
it conflicts with the native convention of PHP/Java/JS/C#, making
every auto-completion suggestion and every library call stand out.
PEP 8 in Python is an isolated exception that is accepted within
Python.

**Alternative B — no required verb-object pattern, only camelCase.**
Rejected because it solves half of the complaint: form is uniform,
but function names without a verb (`variant`, `dataHandler`,
`processor`) remain possible and remain confusing.

**Alternative C — Hungarian notation (type prefix in the name, like
`strName`, `iCount`).** Rejected — outdated, adds noise in modern
IDEs that already display types.

**Alternative D — let every project pick its own conventions.**
Rejected, because that is the situation this ADR resolves.

## 6. References

- PSR-1: *Basic Coding Standard*, PHP-FIG.
- PSR-4: *Autoloader*, PHP-FIG.
- PSR-12: *Extended Coding Style*, PHP-FIG.
- PEP 8: *Style Guide for Python Code*.
- ESLint rule: `@typescript-eslint/naming-convention`.
- ADR-003 (folder layout) — refers to lowercase folder names.
