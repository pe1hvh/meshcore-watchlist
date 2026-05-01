# ADR-003: Standaard project folder layout

| Veld              | Waarde                                                        |
|-------------------|---------------------------------------------------------------|
| **Status**        | Geaccepteerd                                                  |
| **Datum**         | 2026-05-01                                                    |
| **Auteur**        | PE1HVH (Hans)                                                 |
| **Scope**         | Alle PE1HVH-projecten (taal-onafhankelijk)                    |
| **Vervangt**      | —                                                             |
| **Vervangen door**| —                                                             |

---

## 1. Context

PE1HVH-projecten bestaan in meerdere talen (Python, PHP, JavaScript,
incidenteel anders). Zonder een vaste afspraak over folderstructuur
kreeg elk nieuw project zijn eigen layout: soms code in `src/`, soms
in een package-directory direct onder de root, soms in `include/`,
soms gemengd met de web-laag in dezelfde folder. Resultaat:

- bij hervatten van een project na een paar maanden eerst zoeken
  waar de code staat;
- copy-paste van scripts of build-stappen tussen projecten werkt
  niet zonder aanpassing;
- AI-tooling die in het ene project getuned is, faalt in het volgende
  doordat de aannames over folder-locaties niet kloppen.

Een vaste layout kost weinig (één keer afspreken, daarna automatisch)
en levert direct rust op.

## 2. Beslissing

Elk PE1HVH-project gebruikt deze top-level structuur:

```
<project-root>/
├── src/              # alle applicatie-code (default)
│   └── …             # of: package-directory direct onder root,
│                     #     voor talen waar dat de norm is (Python flat-layout)
├── html/             # alleen placeholders / web-roots / thin entry-points
│                     # geen applicatielogica
├── docs/             # projectdocumentatie
│   └── adr/          # Architecture Decision Records (zie ADR-template.md)
├── tests/            # alle testcode, parallel aan src/
├── README.md         # wat het project is en hoe je het draait
├── CHANGELOG.md      # versiehistorie volgens Keep a Changelog
└── <taal-config>     # composer.json, pyproject.toml, package.json, …
```

**Vaste regels:**

- **Eén plek voor code.** Default `src/`. In talen met een sterke
  conventie tegen een `src/`-wrapper (Python flat-layout met de
  package direct onder de root) mag die conventie gevolgd worden,
  mits het project dat consistent doet.
- **`include/` als alternatief voor `src/`** is toegestaan voor
  legacy- of klein-PHP-projecten waar `include/` historisch al in
  gebruik was. Niet beide naast elkaar in hetzelfde project.
- **`html/` is uitsluitend voor placeholders en de web-front.** Geen
  business logic, geen database-queries, geen utility-functies. Een
  `html/index.php` doet niets meer dan een entry-point uit `src/`
  aanroepen.
- **`docs/adr/`** is de vaste plek voor het ADR-register.
- **`tests/`** loopt structureel parallel aan `src/`: `src/foo/bar.php`
  heeft zijn test in `tests/foo/bar.test.php` (of de
  taal-equivalent).

## 3. Argumentatie

Eén layout voor alle projecten betekent:

- Een nieuw project starten kost geen tijd aan structuur-keuzes.
- De gebruiker (Hans) of een AI-assistent kan zonder uitleg in elk
  project de juiste plek vinden.
- Build- en deploy-scripts zijn portabel.
- De scheiding `src/` vs `html/` dwingt af dat applicatielogica niet
  per ongeluk in de web-root komt te staan (en daarmee onbedoeld
  publiek toegankelijk).

`html/` als naam in plaats van `public/` of `www/` is een keuze die
volgt uit bestaande PE1HVH-projecten (`pe1hvh.nl`, `domca.nl`); de
naam is minder belangrijk dan dat hij vast staat.

## 4. Gevolgen

**Wat wordt makkelijker:**

- Elk nieuw project begint vanuit een vast skelet.
- Cross-project tooling (linter-configuratie, CI-scripts, deploy-
  recepten) werkt zonder per-project aanpassing.

**Wat wordt moeilijker:**

- Bestaande projecten die niet aan deze layout voldoen, vragen om
  een eenmalige opruim-actie. Dat hoeft niet in één keer; het mag
  gefaseerd, mits elk project op een gegeven moment conform is.

**Wat moet afgedwongen worden:**

- Bij `git init` van een nieuw project: starten met een skelet dat
  deze structuur heeft. Een `cookiecutter`- of vergelijkbaar
  template is een zinvolle vervolgstap (eigen ADR waard als en
  wanneer).
- Code-review-check: niets in `html/` dat geen placeholder of
  thin entry-point is.
- README.md van elk project benoemt expliciet welke variant
  gebruikt wordt (`src/`, package-flat, of `include/`) en waarom.

## 5. Overwogen alternatieven

**Alternatief A — Per project zelf de layout kiezen.** Afgewezen,
want dat is precies de toestand die dit ADR oplost.

**Alternatief B — Strikt PSR-4 voor alles, ook niet-PHP.** Afgewezen,
want PSR-4 is een PHP-specifieke standaard en past niet zonder
verwringing op Python-flat-layout of op JavaScript-projecten met
`package.json`-conventies.

**Alternatief C — Aparte top-level folder voor configuratie
(`config/`).** Afgewezen voor nu — voegt complexiteit toe zonder
duidelijk gewin in projecten van deze omvang. Kan in een eigen ADR
heroverwogen worden als de praktijk daarom vraagt.

## 6. Referenties

- ADR-001 (channel_name als identiteit) — niet gerelateerd, maar
  toont format voor een vergelijkbare scope-uitspraak.
- PSR-4: PHP autoloading-standaard die `src/` als default oppert.
- Python Packaging User Guide, *Src layout vs flat layout*.
- Bestaande PE1HVH-projecten met deze layout: `domca.nl`,
  `pe1hvh.nl`.
