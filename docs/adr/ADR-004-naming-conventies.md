# ADR-004: Naming conventies — folders, namespaces, klassen, functies

| Veld              | Waarde                                                        |
|-------------------|---------------------------------------------------------------|
| **Status**        | Geaccepteerd                                                  |
| **Datum**         | 2026-05-01                                                    |
| **Auteur**        | PE1HVH (Hans)                                                 |
| **Scope**         | Alle PE1HVH-projecten                                         |
| **Vervangt**      | —                                                             |
| **Vervangen door**| —                                                             |

---

## 1. Context

Bij het opzetten van nieuwe projecten en bij het laten genereren van
code (door AI of door eigen hand op verschillende momenten) ontstaat
inconsistentie in naming: `getVariant` naast `get_variant` naast
`GetVariant` naast `variant_get`, namespaces in PascalCase versus
lowercase, folders met hoofdletters die op Linux wel werken maar op
Windows tot vreemde bugs leiden.

Naming-conventies vastleggen levert direct twee dingen op:

- code in alle PE1HVH-projecten leest hetzelfde,
- afwijkingen zijn met grep- of lint-regels te detecteren.

## 2. Beslissing

### Cross-language regels

| Element                  | Conventie                  | Voorbeeld                  |
|--------------------------|----------------------------|----------------------------|
| Folders                  | lowercase                  | `src/decoder/packet/`       |
| Namespaces / packages    | lowercase                  | `meshcore_watchlist.core`   |
| Klassen / objecten       | UpperCamelCase (PascalCase)| `PacketDecoder`             |
| Functies / methodes      | camelCase, werkwoord-onderwerp | `getVariant`, `loadConfig`, `parseHeader` |
| Constanten               | UPPER_SNAKE_CASE           | `MAX_PACKET_SIZE`           |
| Bestandsnamen klassen    | matchen aan klassenaam     | `PacketDecoder.php`         |

**Functies volgen verplicht het patroon `werkwoordOnderwerp`.** De
eerste term is een werkwoord dat zegt wat de functie *doet*; de tweede
is het object waarop ze opereert. Niet `variantGet`, niet `variant`,
niet `getter`. Wel `getVariant`, `setVariant`, `loadConfig`,
`parseHeader`, `validateInput`.

Folders en namespaces in lowercase voorkomt verschillen tussen
case-sensitive (Linux) en case-insensitive (Windows, macOS-default)
filesystems.

### Per-taal toevoegingen

**PHP:** volgt de PSR-1 + PSR-12 stijlnormen. Klassen in StudlyCaps
(= PascalCase), methoden in camelCase, constanten in UPPER_SNAKE_CASE.
Autoloading volgt PSR-4: namespace = directorypad. Dit is consistent
met de cross-language regels hierboven; PSR is in dit geval gewoon de
specifieke uitwerking.

**Python:** volgt PEP 8. Dat betekent dat de cross-language regel voor
functies (`camelCase`) hier **wijkt af**: Python-functies zijn
`snake_case` (`get_variant`, `load_config`). Klassen blijven
PascalCase, modules zijn lowercase met underscores (`packet_decoder.py`).
Deze afwijking is geaccepteerd omdat PEP 8 in Python door tooling
(black, flake8, ruff) wordt afgedwongen en consistentie binnen de
taal zwaarder weegt dan consistentie met PHP/JS.

**JavaScript / TypeScript:** klassen PascalCase, functies camelCase
(volgt cross-language). Bestandsnamen zijn lowercase met streepjes
(`packet-decoder.js`) of matchen de klassenaam (`PacketDecoder.ts`),
afhankelijk van project-conventie maar consistent binnen één project.

## 3. Argumentatie

**Werkwoord-onderwerp voor functies** maakt expliciet wat een functie
doet. Een naam als `variant` zegt niet of het een getter, setter,
factory, validator of converter is. `getVariant` zegt het wel. Dit
patroon stamt uit Java/SmallTalk en is sindsdien standaard in PHP, JS,
en C#. In Python wordt het patroon ook gebruikt maar dan in
`snake_case`.

**Lowercase folders en namespaces** is geen smaak-keuze maar een
duurzaamheids-keuze: een file `Foo.php` op Linux en een import-regel
`require_once 'foo.php'` werken op Linux niet, op Windows wel — een
crash die pas in productie zichtbaar wordt. Lowercase elimineert deze
klasse van bugs.

**Klassen in PascalCase** is in vrijwel alle moderne talen
(PHP, Python, Java, C#, JS, TS, Rust, Swift, Kotlin) de norm. Niet
volgen vraagt om uitleg.

## 4. Gevolgen

**Wat wordt makkelijker:**

- Bij het lezen van code direct herkennen wat een naam is (klasse?
  functie? constante?).
- Een grep-regel `grep -rn '^[a-z][a-zA-Z]* function\|function [A-Z]'`
  vindt afwijkingen.
- Cross-project copy-paste van naming-patronen werkt zonder
  aanpassing.

**Wat wordt moeilijker:**

- Bestaande projecten met afwijkende naming vragen om geleidelijke
  conformatie. Niet alles tegelijk; nieuwe code is conform, oude code
  wordt aangepast bij first-touch.

**Wat moet afgedwongen worden:**

- Per taal een lint-configuratie die de naming-regels controleert:
  - PHP: PHP_CodeSniffer met PSR-12 ruleset.
  - Python: ruff of flake8 met `pep8-naming` of equivalent.
  - JS/TS: ESLint met `naming-convention` rule.
- Code-review-check: bij elke nieuwe functie expliciet kijken naar
  het werkwoord-onderwerp-patroon. `processData` ja,
  `dataHandler` (zelfstandig naamwoord) nee — refactoren naar
  `handleData`.
- README van elk project bevat een sectie "Naming" die naar dit ADR
  verwijst en eventuele project-specifieke uitzonderingen noemt.

## 5. Overwogen alternatieven

**Alternatief A — `snake_case` voor alle talen.** Afgewezen omdat het
botst met de native conventie van PHP/Java/JS/C#, waardoor elk
auto-completion-suggestie en elke library-call eruit zou springen.
PEP 8 in Python is een geïsoleerde uitzondering die binnen Python
geaccepteerd is.

**Alternatief B — Geen verplicht werkwoord-onderwerp-patroon, alleen
camelCase.** Afgewezen omdat het de helft van de klacht oplost: vorm
is uniform, maar functienamen zonder werkwoord (`variant`,
`dataHandler`, `processor`) blijven mogelijk en blijven verwarrend.

**Alternatief C — Hongaarse notatie (type-prefix in de naam, zoals
`strName`, `iCount`).** Afgewezen — verouderd, voegt ruis toe in
moderne IDE's die types al tonen.

**Alternatief D — Per project zijn eigen conventies kiezen.**
Afgewezen, want dat is de toestand die dit ADR oplost.

## 6. Referenties

- PSR-1: *Basic Coding Standard*, PHP-FIG.
- PSR-4: *Autoloader*, PHP-FIG.
- PSR-12: *Extended Coding Style*, PHP-FIG.
- PEP 8: *Style Guide for Python Code*.
- ESLint rule: `@typescript-eslint/naming-convention`.
- ADR-003 (folder layout) — verwijst naar lowercase folder-namen.
