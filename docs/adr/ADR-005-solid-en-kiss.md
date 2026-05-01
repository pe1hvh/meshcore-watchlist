# ADR-005: SOLID en KISS als ontwerpprincipes

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

Code zonder ontwerpprincipes ontaardt in twee richtingen:

- **Onderontworpen** — alles in één grote klasse of één grote
  functie, geen scheiding van verantwoordelijkheden, elke wijziging
  raakt half de code.
- **Overontworpen** — abstracties op abstracties, factories voor
  factories, design patterns toegepast omdat ze bestaan en niet
  omdat het probleem erom vraagt. AI-gegenereerde code valt
  bijzonder vaak in deze tweede categorie.

Twee gangbare principes-sets dekken beide vallen af: **SOLID** als
disciplinerend kader voor de structuur, **KISS** als
overstijgend filter dat overontwerp afkapt. Geen exotische keuze;
beide zijn breed gedragen en goed gedocumenteerd.

## 2. Beslissing

PE1HVH-projecten volgen SOLID en KISS als expliciete
ontwerpprincipes, met KISS als tie-breaker wanneer een SOLID-
toepassing tot meer abstractie leidt dan het probleem rechtvaardigt.

## 3. Argumentatie

### SOLID, in eigen woorden

| Letter | Naam                         | Concrete check tijdens review                                          |
|--------|------------------------------|------------------------------------------------------------------------|
| **S**  | Single Responsibility         | Heeft deze klasse / module / functie meer dan één reden om te veranderen? Zo ja: splitsen. |
| **O**  | Open / Closed                 | Kan ik nieuw gedrag toevoegen zonder bestaande, werkende code aan te passen? |
| **L**  | Liskov Substitution           | Kan ik een subklasse overal waar de superklasse gebruikt wordt invullen, zonder verrassingen? |
| **I**  | Interface Segregation         | Hoeven afnemers alleen interfaces / methodes te kennen die ze ook werkelijk gebruiken? |
| **D**  | Dependency Inversion          | Hangt hoog-niveau code af van abstracties, niet van concrete implementaties? |

In de praktijk doen **S** (SRP) en **D** (DIP) het meeste werk; de
andere drie volgen vaak vanzelf als die twee goed zitten.

### KISS

*Keep It Simple, Stupid* — de simpelste oplossing die het probleem
correct oplost wint. "Simpel" betekent hier:

- minder code dan een alternatief, bij gelijke functionaliteit;
- minder lagen / abstracties / configuratie-opties;
- minder externe afhankelijkheden;
- minder mentale belasting bij lezen.

KISS staat boven SOLID: als een SOLID-toepassing de code complexer
maakt zonder dat een actueel of binnen-twaalf-maanden-verwacht
probleem dat nodig maakt, wint KISS.

## 4. Gevolgen

**Wat wordt makkelijker:**

- Code-review heeft een vast vragenrijtje (de vijf SRP-/DIP-/KISS-
  vragen hierboven). Geen smaak-discussie, gewoon afvinken.
- Bij het beoordelen van AI-gegenereerde code is "is dit het
  simpelste wat werkt?" een directe afkeur-grond, en daarmee een
  rem op overontwerp.
- Onderhoud van code over de jaren blijft hanteerbaar: een
  SRP-conforme klasse hoeft alleen aangepast te worden voor zijn
  ene reden tot wijziging.

**Wat wordt moeilijker:**

- Voor abstracties die niet direct nodig zijn maar "later misschien
  wel": expliciet motiveren waarom ze nu al moeten. KISS dwingt af
  dat speculatieve flexibiliteit niet wordt ingebouwd.
- "Quick hacks" zijn niet automatisch toegestaan onder de KISS-vlag.
  KISS = simpel, niet slordig. Een hack die SRP schendt is niet
  KISS-conform; hij verschuift het probleem naar later.

**Wat moet afgedwongen worden:**

- Pull-request-template (of het persoonlijke equivalent: een
  vaste-volgorde-check vóór commit) bevat:
  - SRP-vraag: noem de éne reden tot wijziging van de gewijzigde
    klasse / module.
  - KISS-vraag: is overwogen of dit simpeler kon? Welke simpelere
    versie is afgewezen en waarom?
- Bij elk nieuw design-document (zie ADR-template, hoofdstuk *Argumentatie*
  en *Overwogen alternatieven*): expliciete toetsing aan SOLID/KISS
  als onderdeel van de motivatie.
- Bij vermoeden van overontwerp tijdens review: doorvragen naar
  het concrete probleem dat de abstractie oplost. "Voor de
  flexibiliteit" is geen antwoord.

## 5. Overwogen alternatieven

**Alternatief A — Alleen KISS, geen SOLID.** Afgewezen omdat KISS
zonder structurele tegenkracht naar onderontwerp leidt: één grote
"simpele" functie van vijfhonderd regels is op KISS-criteria niet
af te keuren.

**Alternatief B — Domain-Driven Design (DDD) als overstijgend
kader.** Afgewezen voor PE1HVH-scope. DDD is sterk in grote
domeinen met meerdere teams en complexe business-logica. PE1HVH-
projecten zijn solo-werk met technische in plaats van zakelijke
domeinen; de DDD-overhead (bounded contexts, ubiquitous language,
aggregates) loont niet.

**Alternatief C — Geen expliciet ontwerpkader, code-stijl per
review-moment beoordelen op gevoel.** Afgewezen omdat dat exact de
toestand is die dit ADR oplost. "Op gevoel" werkt niet als de
gebruiker zelf de reviewer is en sinds vorige week een ander gevoel
heeft.

**Alternatief D — YAGNI als primair principe in plaats van KISS.**
*"You Aren't Gonna Need It"* overlapt sterk met KISS maar focust
specifiek op afwijzen van speculatieve features. Niet afgewezen,
maar als variant op KISS beschouwd; KISS dekt het en is breder.

## 6. Referenties

- Robert C. Martin, *Agile Software Development: Principles,
  Patterns, and Practices*, 2002 — de oorspronkelijke SOLID-
  formulering.
- *The Art of Unix Programming*, Eric S. Raymond — bron van het
  KISS-principe in de software-engineering-context.
- ADR-template, hoofdstuk 5 *Overwogen alternatieven*: dwingt
  KISS-toetsing af als onderdeel van besluitvorming.
- ADR-001 (channel_name als identiteit) — concreet voorbeeld van
  KISS in actie: secret_hex of UUID werd afgewezen ten gunste van
  de simpelste oplossing die werkt.
