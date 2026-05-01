# Architecture Decision Records — PE1HVH-projecten

Deze folder bevat de Architecture Decision Records (ADRs) voor de
projecten van PE1HVH. Een ADR legt één architectuur- of ontwerpbesluit
vast: wat is besloten, waarom, en wat de gevolgen zijn. Wie later in de
code een vreemde keuze tegenkomt, leest hier waarom die keuze indertijd
zo is gemaakt.

## Conventies

### Bestandsnamen

`ADR-NNN-korte-titel-met-streepjes.md`, met `NNN` een doorlopend
volgnummer met drie cijfers, beginnend bij `001`. Nummers worden niet
hergebruikt, ook niet als een ADR wordt vervangen of vervalt.

### Statuswaarden

| Status              | Betekenis                                                                                          |
|---------------------|----------------------------------------------------------------------------------------------------|
| **Voorgesteld**     | Concept, nog niet besloten. Kan vrij gewijzigd worden.                                            |
| **Geaccepteerd**    | Besluit staat. Code en review houden zich eraan.                                                  |
| **Vervangen**       | Door een nieuwer ADR vervangen. Bestand blijft staan, met verwijzing naar het opvolgende ADR.     |
| **Vervallen**       | Niet vervangen, maar niet meer geldig (bijv. doordat een feature is geschrapt).                   |

Eenmaal *Geaccepteerd* wordt de inhoud niet meer aangepast. Wijziging
van het besluit gebeurt via een nieuw ADR dat het oude *Vervangt*.

### Datums

Alle datums in ADRs (en breder, in alle PE1HVH-projecten) staan in
ISO 8601-formaat: `YYYY-MM-DD`. Zie ADR-002.

### Auteur

Default-auteur is **PE1HVH (Hans)**. Als een derde een ADR voorstelt,
staat de naam daar; het besluit blijft een PE1HVH-besluit en wordt
expliciet als *Geaccepteerd* gemarkeerd voor het ingaat.

### Template

Nieuwe ADRs starten vanuit `ADR-template.md`. Kopieer het bestand,
geef het volgende beschikbare nummer, en vul de zes hoofdstukken in.
Een ADR met onvolledige hoofdstukken wordt niet *Geaccepteerd*.

## Index

| ID      | Titel                                                        | Scope                  | Status         |
|---------|--------------------------------------------------------------|------------------------|----------------|
| ADR-001 | `channel_name` is de stabiele identiteit van een kanaal      | meshcore-watchlist     | Geaccepteerd   |
| ADR-002 | Datum- en tijdformaat is ISO 8601 met YYYY-MM-DD voor datums | Alle PE1HVH-projecten  | Geaccepteerd   |
| ADR-003 | Standaard project folder layout                              | Alle PE1HVH-projecten  | Geaccepteerd   |
| ADR-004 | Naming conventies — folders, namespaces, klassen, functies   | Alle PE1HVH-projecten  | Geaccepteerd   |
| ADR-005 | SOLID en KISS als ontwerpprincipes                           | Alle PE1HVH-projecten  | Geaccepteerd   |

## Begrippenlijst

Termen die in ADRs voorkomen en niet vanzelfsprekend zijn:

- **Invariant** — een eigenschap die altijd waar moet blijven, ongeacht
  welke operatie wordt uitgevoerd. ADR-001 stelt zo'n invariant vast:
  `channel_name` is altijd de identiteit, op elk moment, in elk pad.
- **Drop-in vervanging** — een nieuwe versie van een package die de
  oude een-op-een vervangt: zelfde importpaden, zelfde installatieroute,
  geen aanpassingen nodig in code die de package gebruikt.
- **Shape (van een datastructuur of API-respons)** — de structuur:
  welke velden, met welke types, op welke plek in de boom.
- **Live tail** — het pad waarbij een binnenkomend packet (van de
  radio, via meshcore-gui) direct door de pijplijn gaat: tailer leest
  de JSONL-regel zoals `tail -f` dat doet, decoder ontcijfert,
  shared-data slaat op.
- **Rescan** — het opnieuw verwerken van eerder ontvangen, gearchiveerde
  packets, bijvoorbeeld nadat een nieuwe key is toegevoegd of een
  watchlist-wijziging een eerder onleesbaar bericht alsnog leesbaar
  maakt.
- **Fingerprint (van een bericht)** — een tuple van velden die samen
  een logisch bericht uniek identificeren binnen het archief, gebruikt
  voor deduplicatie tussen live-tail en rescan.
- **idx** — de positie van een kanaal in de huidige watchlist-array,
  een geheel getal beginnend bij 0. Vluchtig: verandert bij elke
  watchlist-mutatie. Geen identiteit; zie ADR-001.
- **PSR (PHP Standard Recommendations)** — door PHP-FIG vastgestelde
  stijlnormen. Relevant in dit register: PSR-1 (basis-stijl), PSR-4
  (autoloading), PSR-12 (uitgebreide stijl). Zie ADR-004.
- **PEP 8** — de Python-stijlgids, *Style Guide for Python Code*.
  Wijkt op functienaam-stijl af van de cross-language regel uit
  ADR-004 (snake_case in plaats van camelCase). Geaccepteerde
  uitzondering binnen Python.
- **SOLID** — vijf objectgeoriënteerde ontwerpprincipes (Single
  Responsibility, Open/Closed, Liskov Substitution, Interface
  Segregation, Dependency Inversion). Zie ADR-005.
- **KISS** — *Keep It Simple, Stupid.* De simpelste oplossing die het
  probleem correct oplost wint. Zie ADR-005.
- **YAGNI** — *You Aren't Gonna Need It.* Variant op KISS specifiek
  gericht op het afwijzen van speculatieve features. Niet als eigen
  principe vastgelegd; wordt door KISS gedekt.
