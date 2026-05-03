# Functioneel ontwerp — meshcore-watchlist

> **Status**: v1.0.0 · 2026-05-02
> **Doelgroep**: Python-developers die het project overnemen of uitbreiden,
> en AI-assistenten (Claude) die in latere sessies features bouwen of bugs
> verhelpen tegen deze codebase.

---

## Inhoudsopgave

1.  [Inleiding & leeswijzer](#1-inleiding--leeswijzer)
2.  [Begrippenlijst](#2-begrippenlijst)
3.  [Stakeholders](#3-stakeholders)
4.  [Use cases](#4-use-cases)
5.  [Schermen](#5-schermen)
6.  [REST API — gedragscontracten](#6-rest-api--gedragscontracten)
7.  [Foutpaden samengevat](#7-foutpaden-samengevat)
8.  [Configuratie- en runtime-scenarios](#8-configuratie--en-runtime-scenarios)
9.  [Bewuste niet-functies](#9-bewuste-niet-functies)
10. [Verwijzingen](#10-verwijzingen)

---

## 1. Inleiding & leeswijzer

### 1.1 Doel van dit document

Dit document beschrijft `meshcore-watchlist` op **gedragsniveau**:
*welk waarneembaar resultaat produceert het systeem bij welke gebeurtenis*.
Het beantwoordt vragen als "wat gebeurt er als de gebruiker een kanaal
verwijdert tijdens een lopende rescan?" of "welke statuscode geeft de
REST API als de Domca-server onbereikbaar is?".

Het beschrijft **niet** hoe het systeem die uitkomsten technisch
realiseert — daarvoor is `docs/architecture.md`. Het beschrijft **niet**
welke datavelden er zijn — daarvoor is `docs/datadictionary.md`. Het
beschrijft **niet** de bindende regels voor wie aan de codebase werkt —
daarvoor is `CLAUDE.md`.

Een developer die werkt aan een feature of bug heeft aan dit document
genoeg om te bepalen of een wijziging een gedragsregressie introduceert.
Een AI-assistent gebruikt het als contract: alle hier vastgelegde
gedragingen moeten na een refactor nog steeds gelden.

### 1.2 Wat is `meshcore-watchlist`

`meshcore-watchlist` is een Python-daemon plus webdashboard die een door
de gebruiker gekozen lijst van hashtag-kanalen op een
MeshCore mesh-radio-netwerk monitort. De service draait náást
`meshcore-gui` op dezelfde host (of een andere host op het LAN), leest
diens append-only rx-log, ontcijfert binnenkomende berichten met
lokaal-afgeleide kanaalkeys, en presenteert het resultaat in een eigen
GUI plus een REST API.

Het systeem heeft geen eigen radio-aansluiting; het is volledig een
*lezer*. Berichten versturen kan niet (zie §9).

### 1.3 Leesvolgorde

- **Eerste kennismaking**: §2 (begrippen) → §3 (stakeholders) →
  §4 (use cases).
- **API-integratie bouwen**: §6 (REST API-contracten).
- **Bug onderzoeken**: §4 (use case van het bugscenario) → §7
  (foutpaden) → architecture-doc voor implementatie.
- **Een feature toevoegen die UI raakt**: §5 (schermen) → architecture
  hoofdstuk 12 (uitbreidingspunten).

### 1.4 Een centrale invariant

Eén invariant raakt elke use case in dit document:

> **Een kanaal wordt overal in het systeem geïdentificeerd via zijn
> naam.** De positie van een kanaal in de watchlist-UI is een puur
> visuele eigenschap; mutaties aan de watchlist (toevoegen, verwijderen,
> herordenen) tasten geen historische gegevens, dedup-state,
> rescan-resultaten of API-output aan zolang de namen ongewijzigd blijven.

Wie deze invariant in zijn hoofd houdt kan de meeste use cases zelf
voorspellen. Zie ADR-001 voor de volledige motivatie.

---

## 2. Begrippenlijst

| Term                     | Betekenis                                                                                       |
|--------------------------|--------------------------------------------------------------------------------------------------|
| **Kanaal**               | Een MeshCore-broadcast-groep, geïdentificeerd door een naam. Bijvoorbeeld `#mc-radar` of `Public`. |
| **Hashtag-kanaal**       | Een kanaal waarvan de naam met `#` begint en waarvan de decryptie-sleutel afgeleid is van de naam. |
| **Public-kanaal**        | Het reserved kanaal `Public`. Iedereen op het mesh kan meelezen; gebruikt een vaste, well-known sleutel. |
| **Watchlist**            | De door de gebruiker gekozen lijst van kanalen die deze service monitort.                       |
| **Live-tail**            | Het continu binnenlezen van nieuwe regels uit het meshcore-gui-rx-log.                          |
| **Rescan**               | Een eenmalige pas over de historische rx-log om historische packets opnieuw te ontcijferen.     |
| **Rescan-job**           | Eén ingediende rescan met status `QUEUED` → `RUNNING` → `DONE` of `FAILED`.                     |
| **Archive**              | De append-only JSONL-bestanden onder `~/.meshcore-watchlist/archive/` waarin alle decoded berichten en rauwe rx-log entries persistent zijn opgeslagen. |
| **Fingerprint**          | De sleutel waarop dedupliceren gebeurt: `(message_hash, sender, text, channel_name)` voor berichten. |
| **Domca-API**            | De externe statistiek-bron op `domca.nl` die per kanaal het aantal berichten in de afgelopen tijd terugmeldt. Wordt gebruikt voor rescan-prioritering. |
| **Downstream consumer**  | Een externe applicatie die de REST API van deze service pollt. `domca.nl` is op dit moment de enige bekende. |

---

## 3. Stakeholders

| Stakeholder                     | Belang                                                                                |
|---------------------------------|----------------------------------------------------------------------------------------|
| **Operator**                    | Installeert de service op een Pi, beheert de systemd-unit, configureert poort en bron-pad. |
| **Eindgebruiker**               | Beheert de watchlist, leest binnenkomende berichten, start rescans bij behoefte.       |
| **Downstream consumer**         | Pollt de REST API; verwacht een shape die identiek is aan die van `meshcore-gui`.      |
| **Maintainer van `meshcore-gui`** | Heeft een schema-contract met deze service: format-wijzigingen aan de rx-log raken deze service direct. |
| **Maintainer van `domca.nl`**   | Heeft een shape-contract met de REST API. Statistiek-aanvragen vanuit deze service moeten gracieus uitvallen. |
| **Developer / Claude**          | Voegt features toe of verhelpt bugs. Doelgroep van dit document.                       |

In de typische setup is de operator dezelfde persoon als de eindgebruiker
(Pi-thuisgebruik). Dit document maakt het onderscheid om te markeren
welke acties root-rechten of host-toegang vereisen versus welke alleen
een browser-tabblad nodig hebben.

---

## 4. Use cases

Elke use case heeft dezelfde structuur: trigger, voorwaarden, verwacht
gedrag (observable), foutpaden. Dit zijn **gedragscontracten** —
een refactor die een hier beschreven gedrag wijzigt is per definitie
een gedragsregressie en vereist explicieten afstemming.

### UC-01 — Operator installeert de service

**Trigger**: operator voert `sudo ./install_script/install.sh --port 8083`
uit op een Linux-host (typisch Raspberry Pi).

**Voorwaarden**:

- `meshcore-gui` v1.22.1 of nieuwer draait op dezelfde host (of de
  rx-log staat op een gemonteerd pad).
- Python 3.10+ aanwezig.

**Verwacht gedrag**:

- Een systemd-unit `meshcore-watchlist.service` wordt aangemaakt en
  geactiveerd.
- De service luistert binnen seconden op `http://<host>:<port>/`.
- De data-root `~/.meshcore-watchlist/` wordt op eerste start aangemaakt
  als hij niet bestaat, met daarin een lege `watchlist.json` met alleen
  de `Public`-entry, een lege `state.json` en een lege `archive/`-map.
- Een browser die naar `http://<host>:<port>/` navigeert ziet het
  dashboard met drie tabs: *Watchlist*, *Messages*, *RX Log*. De
  Watchlist-tab toont de Public-entry.

**Foutpaden**:

- Port al in gebruik → service start niet, `systemctl status` toont
  bind-error. Operator kiest andere port.
- `meshcore-gui`-archive niet aanwezig → service start, maar de tailer
  logt elke poll dat hij geen `*_rxlog.jsonl` ziet. Geen crash.
- Read-only home-directory → service start niet (kan
  `~/.meshcore-watchlist/` niet maken). Logmelding richting operator.

### UC-02 — Eindgebruiker bekijkt live berichten

**Trigger**: gebruiker opent `http://<host>:<port>/` en navigeert naar
de **Messages**-tab.

**Voorwaarden**:

- Watchlist bevat tenminste één kanaal (de Public-entry telt mee).
- `meshcore-gui` schrijft actief packets naar zijn rx-log.

**Verwacht gedrag**:

- Bij paginalad zijn de laatste 500 berichten zichtbaar (uit het archive
  gerepliceerd bij service-start; zie UC-09).
- Een nieuw binnenkomend GroupText-packet op een gemonitord kanaal
  verschijnt binnen ~1 seconde in de tabel (de tailer pollt elke seconde
  op nieuwe regels).
- Berichten op kanalen waarvan de naam **niet** in de watchlist staat,
  verschijnen niet in deze tab — ze worden niet eens gedecodeerd.
- De RX Log-tab toont parallel de laatste 50 rauwe rx-log entries
  (decoded én niet-decodeerbaar), zonder hashtag-filtering.

**Foutpaden**:

- Watchlist leeg → de decoder heeft geen sleutels; binnenkomende packets
  verschijnen alleen in RX Log, niet in Messages.
- Een hashtag-kanaal-naam in de watchlist die niet matcht met de
  netwerkrealiteit (typo) → packets worden voor dat kanaal niet
  gedecodeerd. Geen foutmelding op het scherm; symptoom is "ik krijg
  geen berichten".

### UC-03 — Eindgebruiker voegt een kanaal toe aan de watchlist

**Trigger**: in de **Watchlist**-tab vult de gebruiker een kanaalnaam
in en drukt op *"Add"*.

**Voorwaarden**:

- De naam is een hashtag-naam (start met `#` of het systeem voegt het
  prefix toe), of de canonieke `Public`-naam.
- De naam is nog niet in de watchlist aanwezig.

**Verwacht gedrag**:

- De watchlist-tabel toont de nieuwe rij direct.
- `~/.meshcore-watchlist/watchlist.json` is op disk geüpdatet (atomic).
- De decoder begint binnen één render-tick met het ontcijferen van
  binnenkomende packets met de afgeleide sleutel van dit kanaal —
  inclusief packets in een lopende rescan-job (zie UC-08).
- Toekomstige binnenkomende GroupText-packets die met deze sleutel
  ontcijferen verschijnen als bericht in de Messages-tab.

**Foutpaden**:

- Naam al aanwezig → toevoeging wordt afgewezen, stil of met
  visuele feedback. Geen duplicate-rij.
- Lege naam → toevoeging wordt afgewezen.
- Naam zonder `#` voor een hashtag → systeem prefixt
  automatisch `#`, behalve voor de canonieke `Public`.

### UC-04 — Eindgebruiker verwijdert een kanaal uit de watchlist

**Trigger**: in de **Watchlist**-tab klikt de gebruiker op de
verwijder-knop in een rij.

**Voorwaarden**:

- Het kanaal is een hashtag-kanaal — `Public` kan niet worden verwijderd.

**Verwacht gedrag**:

- De rij verdwijnt direct uit de tabel.
- `watchlist.json` is op disk geüpdatet.
- De decoder verwijdert direct de bijbehorende sleutel.
- Toekomstige packets op dit kanaal worden niet meer gedecodeerd; ze
  blijven wél in de RX Log verschijnen als rauwe rx-log entries.
- **Reeds gedecodeerde berichten in de Messages-tab en in het archive
  blijven staan.** Verwijderen van een kanaal is *niet* een
  geschiedenis-wis-actie.
- In bestaande, reeds opgeslagen berichten van dit kanaal verandert
  het integerveld `Message.channel` niet retroactief; de naam-attributie
  in `Message.channel_name` blijft de waarheid.

**Foutpaden**:

- Poging tot verwijderen van `Public` → afgewezen door de
  `WatchlistStore`-invariant. Geen rij-mutatie.
- Verwijderen tijdens lopende rescan → toegestaan; de rescan ziet vanaf
  het volgende verwerkte record geen sleutel meer voor dit kanaal,
  records vallen onder de `not_decryptable`-teller.

### UC-05 — Eindgebruiker herordent kanalen in de watchlist

**Trigger**: gebruiker sleept een rij naar een andere positie in de
Watchlist-tabel (of gebruikt eventuele up/down-knoppen).

**Voorwaarden**: geen.

**Verwacht gedrag**:

- De rij-volgorde in de tabel verandert.
- `watchlist.json` reflecteert de nieuwe volgorde op disk.
- **Geen historische berichten verschuiven**, **geen dedup-state
  verandert**, **geen rescan-resultaat wijzigt**. De namen van de
  kanalen zijn niet meegegaan.
- Een lopende rescan-job draait onverstoord door — herordening is
  voor het rescan-resultaat een no-op.

**Foutpaden**: geen domeingebonden foutpaden. Filesystem-fouten bij het
schrijven worden gelogd; de in-memory volgorde wordt teruggedraaid bij
falen.

### UC-06 — Eindgebruiker start een volledige rescan

**Trigger**: gebruiker kiest een datumvenster (start- en einddatum)
in de Watchlist-tab en drukt op *"Rescan"* (de algemene rescan-knop,
niet per rij).

**Voorwaarden**:

- Geen andere rescan-job draait op dit moment.
- Beide datums zijn geldige `YYYY-MM-DD`-strings, met `start_date ≤ end_date`.

**Verwacht gedrag**:

- Een rescan-job wordt aangemaakt met status `QUEUED`, daarna direct
  `RUNNING`.
- De voortgangs-widget verschijnt en toont per-file en per-record-tellers
  die tijdens de rescan oplopen.
- Bestaande berichten in het archive worden niet gedupliceerd — de
  fingerprint-dedup absorbeert reeds-gearchiveerde records.
- Berichten die de rescan nieuw ontcijfert worden naar het archive
  geschreven en verschijnen na voltooiing in de Messages-tab.
- Een externe Domca-API-call wordt aan het begin van de job gedaan voor
  een prioriteits-volgorde van kanaalnamen (om de decoder snel te laten
  matchen op de meest-actieve kanalen). Deze prioriteits-volgorde is
  voor de duur van de job bevroren.
- Job eindigt met status `DONE` zodra alle records in het venster
  verwerkt zijn.

**Foutpaden**:

- Datums ongeldig → 400 `invalid_rescan_window`. Geen job aangemaakt.
- Job loopt al → 409 `rescan_busy`. Bestaande job draait door.
- Source-archive directory ontbreekt → job → `FAILED` direct, met
  `error` gezet; geen records verwerkt.
- Domca-API onbereikbaar/5xx/malformed → de job gebruikt een
  fallback-volgorde (eigen watchlist-volgorde) en zet
  `priority_source = "fallback"` in zijn status. **De job faalt niet**.
- Watchlist leeg op moment van job-pickup → alle records vallen onder
  `not_decryptable`. Job eindigt succesvol met 0 nieuwe berichten.

### UC-07 — Eindgebruiker start een per-kanaal rescan

**Trigger**: gebruiker drukt op een rescan-knop in de rij van een
specifiek kanaal in de Watchlist-tab.

**Voorwaarden**:

- Geen andere rescan-job draait.
- De datumvelden zijn ingevuld en geldig.
- Het gekozen kanaal staat in de huidige watchlist (validatie op
  submit-tijd).

**Verwacht gedrag**:

- Identiek aan UC-06, met als verschil dat de decoder alleen probeert
  te ontcijferen met de sleutel van het gekozen kanaal. Records die
  daarmee niet matchen vallen onder `not_decryptable`.
- De prioriteits-volgorde wordt nog steeds opgehaald, maar speelt geen
  rol omdat slechts één sleutel relevant is.

**Foutpaden**:

- Naam ontbreekt in de request → 400 `missing_channel_name`.
- Naam niet in de watchlist op submit-moment → 404
  `channel_name_not_in_watchlist`.
- Naam wordt verwijderd uit de watchlist *na* job-submit, *vóór*
  job-pickup → de job draait wel, maar alle records vallen onder
  `not_decryptable`. De job eindigt succesvol met 0 nieuwe berichten.

### UC-08 — Watchlist-mutatie tijdens een lopende rescan

**Trigger**: gebruiker voegt een kanaal toe, verwijdert een kanaal,
of herordent rijen, terwijl een rescan-job loopt.

**Voorwaarden**: een rescan-job is in status `RUNNING`.

**Verwacht gedrag**:

- **Toevoegen**: de rescan ziet vanaf het volgende record dat het
  verwerkt de nieuwe decryptie-sleutel. Records die daarmee matchen
  worden opgenomen.
- **Verwijderen**: de rescan ziet vanaf het volgende record geen
  sleutel meer voor het verwijderde kanaal. Records die alleen daarmee
  zouden matchen vallen onder `not_decryptable`.
- **Herordenen**: geen effect op de rescan-uitkomst.
- De prioriteits-volgorde van de lopende job wijzigt **niet** op een
  watchlist-mutatie. Een kanaal dat tijdens de rescan wordt toegevoegd
  staat dus achter aan de prioriteits-volgorde voor *deze* job; de
  decoder probeert het wel, alleen niet bij voorrang.

Dit gedrag is rechtstreeks de waarde die de naam-leidende identiteit
oplevert (zie §1.4 en ADR-001). Een gebruiker kan zonder zorgen zijn
watchlist herzien terwijl een uren durende rescan over historische data
draait.

**Foutpaden**: geen — alle drie mutatietypen zijn toegestaan tijdens
rescan.

### UC-09 — Service-herstart

**Trigger**: `systemctl restart meshcore-watchlist`, een crash-en-respawn,
of een hardware-reboot.

**Voorwaarden**: data-root `~/.meshcore-watchlist/` is intact.

**Verwacht gedrag**:

- De watchlist wordt geladen uit `watchlist.json`.
- De tailer-cursors worden geladen uit `state.json`. De live-tail begint
  voort waar hij gebleven was — geen herverwerking van historische
  packets.
- De Messages- en RX Log-tabs worden bij paginalad gepopuleerd uit het
  archive: laatste 500 berichten en laatste 50 rx-log entries. De
  gebruiker ziet dus geen lege tabs na een herstart.
- De fingerprint-dedup-set in geheugen wordt herbouwd uit dezelfde 500
  berichten en 50 rx-log entries; voor langer-historische records
  vertrouwt het systeem op de archive-brede dedup die de rescan-paden
  gebruiken.
- Een op het moment van de herstart lopende rescan-job is **niet**
  recoverable. Hij verdwijnt; status `RUNNING` blijft niet hangen.
  De gebruiker kan een nieuwe rescan starten met dezelfde parameters —
  reeds-gearchiveerde records worden gededupeerd.

**Foutpaden**:

- `watchlist.json` corrupt of unparseable → fallback naar lege
  watchlist met alleen Public; corrupt bestand wordt hernoemd voor
  recovery, niet verwijderd.
- `state.json` corrupt → tailer-cursors worden naar 0 gereset; de
  fingerprint-dedup absorbeert de eenmalige re-emit van de huidige files.
- Archive-files corrupt → individuele unparseable lijnen worden
  geskipt-en-gelogd, de service start door.

### UC-10 — Downstream consumer pollt de REST API

**Trigger**: een externe applicatie (bv. `domca.nl`) doet periodieke
HTTP GETs op `/api/v1/messages`, `/api/v1/channels`, `/api/v1/stats`.

**Voorwaarden**: de service draait, netwerkpad bestaat.

**Verwacht gedrag**:

- Response-shape is byte-voor-byte identiek aan die van `meshcore-gui`.
  Een downstream-consumer die voor `meshcore-gui` is gebouwd werkt
  zonder code-wijziging.
- `/api/v1/messages` paginiert via `limit` (1-500, default 100) en
  `offset` (≥0, default 0). De response bevat `total`, `limit`, `offset`
  en `items`.
- `/api/v1/nodes` retourneert altijd `[]`. Deze service heeft geen eigen
  contact-list.
- `/api/v1/stats` retourneert aggregaten over de laatste 72 uur. Velden
  die voor een radio-loze service betekenisloos zijn (zoals
  `active_clients`) komen mee als `0` voor shape-compatibiliteit.
- Geen authenticatie-headers vereist. CORS toegestaan vanaf alle
  origins per default.

**Foutpaden**:

- Onbekende endpoint → 404 (FastAPI default).
- `limit` buiten 1-500 → 422 (FastAPI parameter-validatie).
- `offset` negatief → 422.
- Service onbereikbaar → consumer ziet network-error; service-zijde geen
  state-effect.

### UC-11 — `meshcore-gui` wordt geüpgraded

**Trigger**: operator update `meshcore-gui` naar een nieuwere versie.

**Voorwaarden**: de nieuwere `meshcore-gui` blijft het JSONL-rxlog-format
schrijven.

**Verwacht gedrag**:

- Als het record-schema (veldnamen, types) **gelijk blijft**:
  `meshcore-watchlist` draait door zonder onderbreking. Tailer-cursors
  blijven geldig.
- Als het record-schema **wijzigt** (veldnaam hernoemd, type gewijzigd):
  de tailer logt parse-errors per regel, skipt de records, draait door.
  Geen crash; wel zichtbare incompleetheid in de Messages-tab.
- Als de directory-locatie verandert: de operator past `MESHCORE_GUI_ARCHIVE`
  aan in de systemd-unit en herstart.

**Foutpaden**: zie boven. De service is gebouwd om gracieus uit te
vallen op extern-format-wijzigingen, niet om ze automatisch te detecteren
en een upgrade te doen.

### UC-12 — Domca-API onbereikbaar tijdens rescan

**Trigger**: een rescan-job start, doet een HTTP GET naar
`https://www.domca.nl/api/meshcore/channel_statistics.php`, en de call
faalt (timeout, 5xx, malformed JSON, of geen netwerk).

**Voorwaarden**: een rescan-job is gesubmit.

**Verwacht gedrag**:

- De HTTP-call valt na maximaal ~5 seconden uit.
- De rescan-worker logt de uitval en zet `priority_source = "fallback"`
  in de job-status.
- De rescan **draait verder** met de eigen watchlist-volgorde als
  decoder-iteratie-volgorde. Geen verschil in eindresultaat — alleen
  de optimalisatie van "probeer dominante kanalen eerst" verdwijnt.
- De gebruiker ziet in de voortgangs-widget dat de prioriteits-bron
  fallback is, zodat duidelijk is dat de rescan in gedegradeerde-modus
  draait.

**Foutpaden**: deze use case **is** het foutpad voor de
priority-fetch. Geen geneste foutpaden.

### UC-13 — Operator zaait de watchlist vanuit een externe channel-listing (cron)

**Trigger**: een cron-entry op de host roept periodiek het
`tools.channel_injector`-script aan met één of meer
`--source-url`-argumenten. Typische frequentie: elke 15 minuten.

**Voorwaarden**:

- De daemon draait en is bereikbaar op `--api-base`
  (default `http://localhost:8083`).
- De ge-cronde gebruiker heeft executie-rechten op `.venv/bin/python`.
- De externe URL(s) leveren JSON met een `channels[]`-veld waarin
  elk item minimaal een `hash` óf `name` heeft. Namen komen
  hashtag-prefixed binnen.

**Verwacht gedrag**:

- Voor elke source-URL: één HTTP `GET`. Daarna één `GET` op
  `/api/v1/channels` om de huidige watchlist op te halen.
- Per kanaal in de gemerge'de input dat **niet** in de watchlist
  staat:
  1. `POST /api/v1/channels?name=...` — daemon voegt toe via
     `WatchlistStore.add()`, decoder ziet de sleutel direct (zelfde
     notify-pad als UC-03).
  2. `POST /api/v1/rescan/by-name?...` over de laatste 7 UTC-dagen —
     historische packets voor het nieuwe kanaal worden alsnog
     gedecodeerd (zelfde mechanisme als UC-07).
- Kanalen die al op de watchlist staan: skip. Geen rescan, geen
  log-spam.
- `Public` en namen zonder `#`-prefix worden geskipt met een reden
  in het log.
- Eén samenvattingsregel op stdout/stderr per run, zodat de
  cron-logfile per run één auditregel bevat.

**Foutpaden**:

- Daemon onbereikbaar → run abort direct (exit 2), watchlist niet
  aangetast. Volgende cron-tik probeert opnieuw.
- Eén van meerdere source-URLs faalt (timeout, 5xx, malformed JSON)
  → andere bronnen worden alsnog verwerkt; exit 2 zodat de
  monitoring de gefaalde fetch oppikt.
- Source-URL response groter dan `--max-source-bytes` (default
  1 MiB) → bron wordt gemeld als `ResponseTooLarge` source-error;
  andere bronnen verwerken door, exit 2.
- Naam > 32 UTF-8 bytes (ADR-007) → injector skipt client-side met
  reden `name_exceeds_32_bytes`; geen POST naar de daemon. (Mocht
  een lokale of ander client wél zo'n naam POSTen, weigert de
  daemon zelf met 400 `name_too_long`.)
- Maximum aantal toevoegingen per run bereikt
  (`--max-adds-per-run`, default 50) → resterende kandidaten in
  deze run worden geskipt met reden `max_adds_reached`; reeds
  toegevoegde kanalen behouden hun rescan. Vlag `max_adds_reached`
  in summary-regel.
- `409 rescan_busy` bij stap 2 → niet-fataal: het kanaal is wél
  toegevoegd, de rescan kan bij de volgende cron-tik opnieuw
  geprobeerd worden (de injector forceert dat niet zelf — een
  manuele rescan-knop in de GUI werkt ook).
- Naam met control-chars (CR/LF) in de bron → daemon weigert met
  400, injector logt en gaat door met de volgende.
- Bron levert lege lijst → no-op, exit 0.

**Niet in scope van deze UC**:

- Verwijderen van kanalen die *niet meer* in de bron staan. Dat zou
  Public en handmatig toegevoegde kanalen kunnen kapotmaken — niet
  ondersteund.
- Bewaken/forceren van een specifieke rescan-volgorde tussen
  meerdere injector-runs.

---

## 5. Schermen

De webapplicatie heeft één pagina met **drie tabs**:

### 5.1 Watchlist-tab

**Doel**: CRUD over de gemonitorde kanalen, plus rescan-bediening.

**Inhoud**:

- Een tabel met de huidige kanalen, één rij per kanaal. Kolommen tonen
  minimaal de positie in de lijst en de naam.
- Een input-veld + *"Add"*-knop voor het toevoegen van een kanaal (UC-03).
- Een verwijder-knop per rij — afwezig of disabled voor de Public-rij
  (UC-04).
- Twee datum-inputs (start- en einddatum) voor het rescan-venster.
- Een algemene *"Rescan"*-knop voor een volledige rescan (UC-06).
- Een rescan-knop per rij voor een per-kanaal rescan (UC-07).
- Een voortgangs-widget die zichtbaar wordt zodra een rescan-job loopt;
  toont per-file voortgang en de rescan-tellers (UC-06).

**Interactie-gedrag**:

- Mutaties in de tabel zijn direct (geen *"Save"*-knop).
- Tijdens een lopende rescan blijven CRUD-acties op de watchlist gewoon
  beschikbaar; gevolg op de rescan zoals beschreven in UC-08.

### 5.2 Messages-tab

**Doel**: live-overzicht van decoded berichten op de watchlist-kanalen.

**Inhoud**:

- Een tabel van de laatste 500 decoded berichten, oplopend in tijd.
- Per bericht: tijd, kanaal-naam, sender, tekst, eventueel pad-informatie
  (hops, repeater-namen).

**Interactie-gedrag**:

- Bij paginalad gevuld uit het archive (laatste 500).
- Updates verschijnen automatisch zodra de tailer-thread nieuwe records
  verwerkt heeft. Geen polling vanuit de browser nodig.
- Berichten die ouder worden dan de retentie (default 7 dagen) verdwijnen
  zowel uit het archive als uit deze tab tijdens de dagelijkse cleanup.

### 5.3 RX Log-tab

**Doel**: rauwe rx-log voor diagnose — toont *alles* wat van de radio
binnenkomt, ongeacht decoding-status.

**Inhoud**:

- Een tabel van de laatste 50 rx-log entries.
- Per entry: tijd, payload-type, hops, signaalsterkte (SNR/RSSI) en
  message-hash. Geen tekst-veld — als het bericht decoded werd is dat
  zichtbaar in de Messages-tab.

**Interactie-gedrag**: zoals Messages-tab, maar dan met de RX Log-stroom
en cap van 50.

### 5.4 Wat ontbreekt bewust

- Geen *"Send"*-knop of compose-veld. De service is read-only (zie §9).
- Geen authenticatie-overlay of login-prompt (zie §9).
- Geen contact-list-tab. De service heeft geen contact-state (zie §9).
- Geen settings-tab voor port, host, of source-pad. Configuratie loopt
  via env-variabelen / systemd-unit (zie §8).

---

## 6. REST API — gedragscontracten

De API is read-only voor data-endpoints en heeft een control-plane voor
rescan-jobs. Alle endpoints zitten onder `/api/v1/`.

### 6.1 Data-endpoints

| Endpoint                 | Methode | Gedrag                                                    |
|--------------------------|---------|-----------------------------------------------------------|
| `/api/v1/channels`       | GET     | Geeft de huidige watchlist als lijst van kanaal-objecten. |
| `/api/v1/messages`       | GET     | Geeft decoded berichten paginaat (`limit`, `offset`).     |
| `/api/v1/stats`          | GET     | Geeft aggregaten over de laatste 72 uur.                  |
| `/api/v1/nodes`          | GET     | Geeft altijd `[]`.                                         |

**Compatibiliteits-eis**: response-shapes zijn byte-voor-byte identiek
aan die van `meshcore-gui`. Toevoegen van velden mag (additieve
evolutie); hernoemen of weglaten breekt downstream consumers en mag
alleen na coordinatie. Zie `docs/datadictionary.md` voor de exacte
shapes.

**Pagination-gedrag**:

- `limit` clamped op `[1, 500]` (default 100). Buiten range → 422.
- `offset` ≥ 0 (default 0). Negatief → 422.
- `total` in de response is het totaal aantal records dat op dit moment
  beschikbaar is voor de query — niet het totaal in het archive.

**Het `id`-veld in `/messages`-items**: een lokaal nummer per response,
geen stable primary key, geen relatie met `channel_name` of
kanaal-identiteit. Downstream consumers dedupliceren op een content-key
(typisch `(timestamp, sender_pubkey, text)`).

### 6.2 Rescan-control-plane

| Endpoint                       | Methode | Gedrag                                                        |
|--------------------------------|---------|---------------------------------------------------------------|
| `/api/v1/rescan`               | POST    | Submit volledige rescan over een datumvenster.                |
| `/api/v1/rescan/by-name`       | POST    | Submit per-channel rescan, gescoped op `channel_name`.        |
| `/api/v1/rescan/{job_id}`      | GET     | Geeft status van een eerder gesubmitteerde rescan-job.        |

**Submit-gedrag**:

- Beide `POST`-endpoints zijn synchroon in de zin dat ze direct retour
  geven: de job wordt aangemaakt en gequeued, de worker pakt hem op.
- Response op succes: HTTP 202 met de job-representatie (job_id, status,
  parameters).
- Slechts één job draait tegelijk. Submit terwijl een job loopt → 409.

**Status-codes**:

| Endpoint                       | Conditie                              | Status |
|--------------------------------|---------------------------------------|--------|
| `POST /rescan`                 | dates ontbreken / ongeldig            | 400    |
| `POST /rescan/by-name`         | `channel_name` ontbreekt              | 400    |
| `POST /rescan/by-name`         | naam niet in watchlist (op submit)    | 404    |
| `POST /rescan*`                | job loopt al                          | 409    |
| `GET  /rescan/{job_id}`        | onbekend job_id                       | 404    |

**Status-polling**: een client die een rescan submit kan via
`GET /rescan/{job_id}` de voortgang volgen. Velden in de response
omvatten status (`QUEUED` / `RUNNING` / `DONE` / `FAILED`),
per-file voortgang, en de tellers (zie `docs/datadictionary.md`).

### 6.3 Watchlist-mutation control-plane

| Endpoint              | Methode | Gedrag                                                  |
|-----------------------|---------|---------------------------------------------------------|
| `/api/v1/channels`    | POST    | Voeg een hashtag-kanaal toe aan de watchlist.           |

**Bedoeld voor**: out-of-process clients die de watchlist programmatisch
willen seeden — typisch de `tools.channel_injector` cron-job uit UC-13.
De UI gebruikt deze endpoint **niet**; die roept `WatchlistStore.add()`
direct aan binnen het daemon-proces.

**Submit-gedrag**:

- Synchroon: de daemon voegt toe (of stelt vast dat de naam er al was)
  en geeft direct antwoord. Geen queue, geen background-werk.
- Notify-pad uit §7.2 van `docs/architecture.md` wordt gewoon
  doorlopen — decoder, GUI en `state.json` zien de wijziging
  binnen één render-tick.
- **Idempotent**: dezelfde naam tweemaal toevoegen levert
  201-dan-200, niet een fout.

**Status-codes**:

| Endpoint              | Conditie                                                  | Status |
|-----------------------|-----------------------------------------------------------|--------|
| `POST /channels`      | nieuwe naam toegevoegd                                    | 201    |
| `POST /channels`      | naam al aanwezig                                          | 200    |
| `POST /channels`      | naam = `Public` (system-managed, al op idx 0 aanwezig)    | 200    |
| `POST /channels`      | `name` ontbreekt of leeg                                  | 400    |
| `POST /channels`      | `name` bevat control-chars (CR/LF, …)                     | 400    |
| `POST /channels`      | `name` > 32 UTF-8 bytes (zie ADR-007)                     | 400    |

Zie `docs/datadictionary.md` §5.8 voor de exacte response-shapes.

### 6.4 CORS, authenticatie, binding

- **CORS**: default `*` op alle endpoints. Override via
  `MESHCORE_WATCHLIST_CORS_ORIGINS`-env.
- **Authenticatie**: geen. Bewuste keuze, gelijk aan `meshcore-gui`.
- **Binding**: default `0.0.0.0:8083`. Bewust LAN-bereikbaar in de
  typische Pi-deployment. Toegangsbeperking is operator-zorg
  (firewall, reverse-proxy).

---

## 7. Foutpaden samengevat

Een gecondenseerd overzicht van foutpaden uit de use cases hierboven.
De hoofdregel: **het systeem faalt graceful**. Externe afhankelijkheden
mogen falen zonder dat de service crasht of een job in `FAILED` belandt
zonder reden.

| Categorie                        | Voorbeeld                                            | Reactie                                                           |
|----------------------------------|------------------------------------------------------|-------------------------------------------------------------------|
| **Externe service onbereikbaar** | Domca-API timeout                                    | Gracieus vallen naar fallback-volgorde; `priority_source = "fallback"` in jobstatus. Job draait door. |
| **Externe input corrupt**        | Malformed JSON-regel in meshcore-gui-rxlog           | Regel skippen, log naar stderr, doorgaan.                         |
| **Eigen state corrupt**          | `watchlist.json` unparseable                         | Hernoemen voor recovery; doorstart met lege watchlist + Public.   |
| **Eigen state corrupt**          | `state.json` unparseable                             | Cursors resetten naar 0; dedup absorbeert eenmalige re-emit.      |
| **Disk vol**                     | Append naar archive faalt                            | Logmelding; in-memory ringbuffer blijft werken; bericht is verloren bij volgende restart als hij niet in archive staat. |
| **Cliënt-fout**                  | Onbekend `channel_name` in rescan-request            | 404 met machine-leesbare error-string in de body.                 |
| **Cliënt-fout**                  | Onbekend `job_id` in status-call                     | 404.                                                               |
| **State-conflict**               | Tweede rescan submit terwijl eerste loopt            | 409 met `running_job_id` van de actieve job.                      |
| **Ontbrekende voorwaarde**       | `meshcore-gui`-archive directory niet aanwezig       | Service draait, tailer logt elke poll. Rescan-job → `FAILED` met error-veld als de directory bij job-start ontbreekt. |
| **Lege watchlist**               | Geen kanalen, alleen rauwe rx-log                    | Geen GroupText-decoding; berichten verschijnen alleen in RX Log-tab. Geen foutmelding op het scherm. |
| **Sleutel-mismatch**             | Hashtag-naam in watchlist die niet in netwerk bestaat | Decoder probeert maar matcht nooit; symptoom is "geen berichten". Geen foutmelding. |

Zie `docs/architecture.md` hoofdstukken 5.3 en 6.5 voor de
implementatie-details per foutpad.

---

## 8. Configuratie- en runtime-scenarios

### 8.1 Configuratie-mogelijkheden

Configuratie loopt uitsluitend via omgevingsvariabelen, gezet in de
systemd-unit (of de shell-omgeving van de operator). Geen
config-bestand, geen settings-pagina in de UI.

| Env-variabele                       | Default                         | Effect                                                      |
|-------------------------------------|---------------------------------|-------------------------------------------------------------|
| `MESHCORE_WATCHLIST_PORT`           | `8083`                          | TCP-port waarop de service luistert.                        |
| `MESHCORE_WATCHLIST_HOST`           | `0.0.0.0`                       | Bind-adres.                                                 |
| `MESHCORE_GUI_ARCHIVE`              | `~/.meshcore-gui/archive`       | Source-directory waar de tailer rx-log-files leest.         |
| `MESHCORE_WATCHLIST_CORS_ORIGINS`   | `*`                             | CORS Origin-header voor `/api/v1/*`.                        |
| `MESHCORE_WATCHLIST_DEBUG`          | `0`                             | Aan met `1`: stderr-debug-prints.                           |

**Wat retentie betreft**: 7 dagen voor zowel berichten als rx-log-entries,
dezelfde defaults als `meshcore-gui`. Niet runtime-configureerbaar; te
wijzigen via constanten in `config.py` (vereist deploy).

**Wat de channel injector betreft (UC-13)**: configuratie loopt niet via
env-variabelen maar via de cron-entry zelf — `--source-url`,
`--api-base`, `--rescan-days`. Een drop-in voorbeeld staat in
`install_script/channel_injector.cron.example`; de install-stap is een
crontab-edit, geen systemd-unit. Zie `tools/channel_injector/README.md`
voor de volledige referentie.

### 8.2 Runtime-scenario's

**Twee instances tegelijk**: ondersteund mits ze verschillende ports
gebruiken en (idealiter) verschillende source-archive-paden. Ze hebben
afzonderlijke data-roots als ze als verschillende OS-users draaien;
draaien als dezelfde user betekent dat ze dezelfde
`~/.meshcore-watchlist/`-directory delen — dat is **niet ondersteund**
en leidt tot data-corruptie.

**Dezelfde host, andere user**: ondersteund. Elke user heeft zijn eigen
`~/.meshcore-watchlist/`. Ports moeten verschillen.

**meshcore-gui op andere host**: ondersteund mits de rx-log-directory
via NFS/SMB beschikbaar is. Performance-overweging: de tailer pollt elke
seconde; over een trage mount kan dat merkbaar zijn.

**Crash + auto-restart door systemd**: zie UC-09. Geen state-verlies
afgezien van eventuele in-flight rescan-jobs.

---

## 9. Bewuste niet-functies

Functionaliteit die **niet** in `meshcore-watchlist` zit en er ook niet
in komt zonder expliciet akkoord van de maintainer:

| Niet-functie                                       | Reden                                                                                          |
|----------------------------------------------------|------------------------------------------------------------------------------------------------|
| Berichten versturen                                | Out of scope: de service is een lezer, geen radio-driver. Versturen vereist sleutelbeheer en breekt het read-only-contract met domca. |
| Eigen radio-aansluiting (BLE / serial / dbus)      | De service leest de rx-log van `meshcore-gui` — die heeft de radio.                            |
| Authenticatie of autorisatie                       | Gelijk aan `meshcore-gui`. Toegangsbeperking is operator-zorg via firewall/reverse-proxy.      |
| Multi-tenant (meerdere watchlists per instance)    | Eén instance = één watchlist. Een tweede watchlist = een tweede instance op een andere port.   |
| Eigen contact-list / nodes-tracking                | Geen radio = geen contact-state. `/api/v1/nodes` is dus altijd `[]`.                          |
| Real-time push naar consumers (WebSocket / SSE)    | Polling-based. Polling-interval is consumer-keuze.                                             |
| End-to-end-encrypted DMs                           | Public/hashtag-decoding alleen. DM-decryptie zou per-user-keys vereisen.                       |
| Browser-notificaties                               | Niet aanwezig. Een browser-tab moet open blijven om updates te zien.                           |
| Mobiele app                                        | Niet aanwezig. De webapplicatie is mobile-friendly via NiceGUI maar er is geen native app.     |
| Archive-search via UI                              | De Messages-tab toont laatste 500. Voor oudere berichten gebruiken consumers de REST API of `jq` op de JSONL-files. |

Wie een van deze functies overweegt: lees eerst ADR-005 (KISS) en pleeg
afstemming voor implementatie.

---

## 10. Verwijzingen

- **`CLAUDE.md`** (repo-root) — bindende regels en conventies voor
  developer- en AI-sessies.
- **`docs/architecture.md`** — technisch ontwerp: lagen, threading,
  decode-pad, lock-strategie, externe afhankelijkheden.
- **`docs/datadictionary.md`** — alle types, velden en JSONL-/REST-shapes
  in tabelvorm.
- **`docs/ontwerp/ontwerp-0.2.6.md`** — release-specifiek ontwerp van
  de naam-leidende refactor (0.2.4 → 0.2.6).
- **`docs/adr/ADR-001-channel-name-als-identiteit.md`** — motivatie en
  alternatieven achter de naam-leidende identiteit.
- **`docs/adr/ADR-002-datum-en-tijdformaat.md`** — ISO 8601 + UTC.
- **`docs/adr/ADR-003-folder-layout.md`** — flat-layout voor Python-packages.
- **`docs/adr/ADR-004-naming-conventies.md`** — PEP 8 als geaccepteerde
  Python-uitzondering.
- **`docs/adr/ADR-005-solid-en-kiss.md`** — KISS wint van SOLID waar
  SOLID overshoot.
- **`README.md`** — installatie, REST-API-curl-voorbeelden, layout van
  de data root.
- **`CHANGELOG.md`** — wijzigingen per release.
