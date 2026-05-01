# ADR-002: Datum- en tijdformaat is ISO 8601 met YYYY-MM-DD voor datums

| Veld              | Waarde                                                        |
|-------------------|---------------------------------------------------------------|
| **Status**        | Geaccepteerd                                                  |
| **Datum**         | 2026-05-01                                                    |
| **Auteur**        | PE1HVH (Hans)                                                 |
| **Scope**         | meshcore-watchlist, domca.nl, alle PE1HVH-projecten           |
| **Vervangt**      | —                                                             |
| **Vervangen door**| —                                                             |

---

## 1. Context

Datums en tijdstippen worden in IT-projecten in talloze formaten
genoteerd: `01-05-2026` (Nederlandse notatie), `5/1/2026` (US-notatie),
`May 1, 2026`, Unix-epoch in seconden, milliseconden, of nanoseconden,
en alle varianten van ISO 8601 met of zonder tijdzone.

Zonder afspraak ontstaan in elke laag van de stack opnieuw parsers,
formatters en conversies, met als gevolg:

- regio-bugs (een datum die `01-05` heet, kan 1 mei of 5 januari zijn,
  afhankelijk van de parser),
- sorteer-bugs (`5/1/2026` sorteert lexicografisch tussen `4/9/2026`
  en `5/2/2026`, dus altijd fout),
- tijdzone-verwarring (logregels van een server in UTC en een client in
  CEST naast elkaar zonder zichtbare offset),
- API-incompatibiliteit (twee subsystemen die hetzelfde veld anders
  serialiseren).

Binnen meshcore-watchlist zijn datums al deels op YYYY-MM-DD gezet
(rescan-venster `start_date` / `end_date`); timestamps op records zijn
dat nog niet consequent. Domca-API velden als `first_received_at` en
`last_received_at` zijn formattaal niet vastgelegd.

## 2. Beslissing

Alle datums, tijden en tijdstippen — intern, in opslag, in API's, in
log-output en op het scherm — worden geserialiseerd als **ISO 8601** in
UTC:

- **Datum (zonder tijd):** `YYYY-MM-DD`, bijvoorbeeld `2026-05-01`.
- **Tijdstip (datum + tijd):** `YYYY-MM-DDTHH:MM:SSZ`, bijvoorbeeld
  `2026-05-01T14:33:07Z`. De `T` als scheider en de `Z` als
  UTC-aanduiding zijn verplicht.
- **Tijdstip met sub-seconden** (alleen waar nodig, bijv. high-rate
  packet-logging): `YYYY-MM-DDTHH:MM:SS.sssZ`.

Tijdzone is altijd UTC bij opslag en bij API-uitwisseling. Lokale tijd
is uitsluitend toegestaan in de display-laag voor de eindgebruiker, en
zelfs daar verdient UTC + offset (`2026-05-01T16:33:07+02:00`) de
voorkeur boven onaangeduide lokale tijd.

## 3. Argumentatie

ISO 8601 is:

- **Lexicografisch sorteerbaar** — string-sortering geeft chronologische
  volgorde, zonder cast.
- **Ondubbelzinnig** — geen verwarring tussen dag-eerst en maand-eerst.
- **Internationaal gestandaardiseerd** — door alle gangbare
  programmeertalen, databases en tools native ondersteund (Python's
  `datetime.isoformat()`, SQLite, PostgreSQL, JavaScript `Date`, jq,
  enzovoort).
- **Door mensen leesbaar** — geen epoch-getallen die alleen na conversie
  betekenis hebben.
- **Compact** — vaste lengte, geschikt voor logregels en filenames.

UTC als opslag-tijdzone elimineert zomertijd-grensgevallen (een
tijdstip op de zondag van de kleinste-uurs-overgang heeft anders twee
geldige waarden of geen).

## 4. Gevolgen

**Wat wordt makkelijker:**

- Een rescan-venster dat als string in een URL of veld staat is direct
  bruikbaar zonder parser-keuze.
- Logbestanden zijn met `sort` of `awk` chronologisch te ordenen op
  het tijdstip-veld.
- API-velden hebben één formaat; geen "in dit endpoint epoch, in dat
  endpoint string".

**Wat wordt moeilijker:**

- Bestaande domca-velden (`first_received_at`, `last_received_at`) en
  archive-records die in een ander formaat staan, vragen om migratie of
  een conversielaag bij lezen. Eénmalige kost; nieuwe data wordt
  meteen conform geschreven.
- De gebruiker ziet UTC-tijden (twee uur achter zomertijd in NL).
  Acceptabel voor de operator-doelgroep van dit pakket; voor
  end-user-display kan een conversie naar lokale tijd in de view-laag
  toegevoegd worden, mits met expliciete offset.

**Wat moet afgedwongen worden:**

- Code-review-check: alle nieuwe datum-/tijd-velden in models,
  API-schema's en archive-records hebben docstring of comment dat het
  formaat noemt: `# YYYY-MM-DD UTC, inclusief` of
  `# YYYY-MM-DDTHH:MM:SSZ (ISO 8601, UTC)`.
- Acceptatiecriterium voor elke release waarin een datum-/tijd-veld
  wordt toegevoegd: een unit-test die round-trip serialisatie
  (string → datetime → string) controleert.
- Lint-regel of grep-check: geen `strftime("%d-%m-%Y")`,
  `strftime("%m/%d/%Y")` of varianten in code die persistente data
  schrijft.
- API-documentatie noemt expliciet "alle tijden in UTC, ISO 8601"
  in de algemene sectie en hoeft het dan niet per endpoint te
  herhalen.

## 5. Overwogen alternatieven

**Alternatief A — Unix-epoch (seconden of milliseconden sinds
1970-01-01).** Afgewezen omdat het niet leesbaar is zonder conversie,
fouten bij seconden-vs-milliseconden moeilijk te zien zijn, en het
veld zonder context onduidelijk is (`1746107587` — wanneer is dat?).

**Alternatief B — Lokale tijd met tijdzone-suffix
(`2026-05-01 16:33:07 CEST`).** Afgewezen omdat tekstuele
tijdzonenamen ambigu zijn (CEST in welk jaar? Welke regelgeving?), en
parsers er niet uniform mee omgaan. Een numerieke offset (`+02:00`)
is acceptabel voor display, maar voor opslag is UTC eenvoudiger.

**Alternatief C — RFC 2822 / e-mail-stijl
(`Fri, 01 May 2026 14:33:07 +0000`).** Afgewezen omdat het variabele
lengte heeft, niet lexicografisch sorteerbaar is, en in de
software-keten van dit project nergens al wordt gebruikt.

**Alternatief D — Per veld kiezen wat handig is.** Afgewezen omdat dit
exact de toestand is die dit ADR oplost.

## 6. Referenties

- ISO 8601:2019, *Date and time — Representations for information
  interchange*.
- RFC 3339, *Date and Time on the Internet: Timestamps* (de
  internet-profielversie van ISO 8601).
- Bestaande conforme velden in het project:
  `RescanJob.start_date`, `RescanJob.end_date` (zie
  `meshcore_watchlist/services/archive_rescanner.py`).
- Domca-API endpoint:
  `https://www.domca.nl/api/meshcore/channel_statistics.php` —
  velden `first_received_at`, `last_received_at` worden volgens dit
  ADR genormaliseerd op leesmoment.
