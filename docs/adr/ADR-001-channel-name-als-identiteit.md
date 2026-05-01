# ADR-001: `channel_name` is de stabiele identiteit van een kanaal

| Veld              | Waarde                                                        |
|-------------------|---------------------------------------------------------------|
| **Status**        | Geaccepteerd                                                  |
| **Datum**         | 2026-05-01                                                    |
| **Auteur**        | PE1HVH (Hans)                                                 |
| **Scope**         | meshcore-watchlist (alle componenten)                         |
| **Vervangt**      | —                                                             |
| **Vervangen door**| —                                                             |

---

## 1. Context

In de watchlist heeft elk kanaal twee aanduidingen:

- **`channel_name`** — de naam zoals de gebruiker hem ziet (bijv.
  `#test`, `#zwolle`). Stabiel: verandert alleen als de gebruiker het
  kanaal expliciet hernoemt.
- **`idx`** — de positie in de watchlist-array, een geheel getal
  beginnend bij 0. Vluchtig: verandert elke keer dat de gebruiker een
  kanaal toevoegt, verwijdert of verplaatst.

Tot en met versie 0.2.4 werd `idx` op meerdere plekken gebruikt als
sleutel of identiteit:

- in dedup-fingerprints van zowel het live-tail-pad als de rescan,
- in de archive-replay bij opstart,
- in de pre-load van fingerprints uit het archief,
- in de decoder (`_secret_to_idx`, `DecodedPacket.channel_idx`),
- in de rescan-job (`only_channel_idx`),
- in de REST-API (`POST /api/v1/rescan/{idx}`),
- in de GUI-knop voor per-kanaal-rescan.

**Gemeten gevolg op een live archive (1 mei 2026):**
979 van 6144 packet-hashes (16 %) verschenen op meerdere `idx`-waarden
met identieke `sender`, `text` en `channel_name` — hetzelfde logische
bericht stond meerdere keren in de archive omdat de gebruiker tussentijds
de watchlist had verschoven en elke verschuiving alle historische
berichten "nieuw" maakte voor de dedup-laag.

## 2. Beslissing

`channel_name` is de identiteit van een kanaal. `idx` is uitsluitend een
positie in de huidige watchlist-render en speelt geen rol in identiteit,
sleutel, scope, prioriteit, routering of dedup.

## 3. Argumentatie

Identiteit moet stabiel zijn over de tijd, anders is het geen
identiteit. `idx` verandert bij elke watchlist-mutatie; `channel_name`
verandert alleen bij een expliciete actie van de gebruiker
(hernoemen). Daarmee is `channel_name` de enige zinvolle keuze voor:

- dedup tussen live-ontvangst en rescan,
- scope van een per-kanaal-rescan,
- prioriteits-volgorde van decryptiesleutels,
- de externe REST-API.

`idx` blijft bestaan als veld op `Message` en als kolom in de
JSONL-archive — uitsluitend voor display en backwards-compatibiliteit
met de bestaande payload-vorm van meshcore-gui. Op het moment van
ingest wordt `idx` uit `channel_name` afgeleid via een lookup in de
huidige watchlist; daarna is hij een momentopname zonder identiteit.

## 4. Gevolgen

**Wat wordt makkelijker:**

- De gebruiker kan zijn watchlist op elk moment herordenen zonder dat
  de archive corrupteert of de rescan het verkeerde kanaal pakt.
- Eén regel om naar te toetsen tijdens code-review: "wordt `idx`
  hier gebruikt als identiteit?" Zo ja, weigeren.

**Wat wordt moeilijker:**

- Een breaking change in de interne decoder-API: parameters en velden
  hernoemen. Eenmalige kost.
- De REST-endpoint `POST /api/v1/rescan/{idx}` vervalt; vervangen door
  `POST /api/v1/rescan/by-name?channel_name=...`. Externe consumers
  moeten één keer omschakelen. Zie CHANGELOG.

**Wat moet afgedwongen worden:**

- Acceptatiecriterium: `grep -rn "_idx\|channel_idx\|only_channel_idx"
  meshcore_watchlist/` mag in nieuwe code geen treffers hebben buiten
  het display-laagje en commentaren die het patroon afzweren.
- Bij ontwerp van elke nieuwe parameter, veld of endpoint: expliciete
  toets tegen dit ADR vóór implementatie.
- Datadictionary van elke gedeelde datastructuur bevat de kolom
  **Identiteit?** met één van drie waarden: *ja — primair*, *ja — afgeleid*,
  *nee*.

## 5. Overwogen alternatieven

**Alternatief A — `secret_hex` als identiteit (de decryptiesleutel zelf).**
Afgewezen omdat de sleutel kan rollen (bijv. nieuwe deployment van een
mesh-knooppunt) terwijl het kanaal hetzelfde blijft. Bovendien is een
hex-blob niet display-vriendelijk en niet door mensen te herkennen.

**Alternatief B — `idx` houden, watchlist immuteerbaar maken tijdens
rescan.** Afgewezen omdat het de gebruiker een gedragsbeperking oplegt
voor een probleem dat in het ontwerp thuishoort. De gebruiker mag op elk
moment zijn watchlist mogen aanpassen; het is aan het systeem om daar
correct mee om te gaan.

**Alternatief C — een aparte UUID per kanaal genereren bij toevoeging.**
Afgewezen omdat `channel_name` al stabiel en uniek is binnen één
watchlist, door de gebruiker bedoeld als identificerend, en in het
display direct herkenbaar. Een UUID-laag erbij voegt indirectie zonder
extra zekerheid.

## 6. Referenties

- Bug-analyse en initiële fix: CHANGELOG `[0.2.5] - 2026-05-01`,
  sectie *Fixed (template 1)*.
- Code-locaties die conform dit ADR moeten zijn:
  - `meshcore_watchlist/core/shared_data.py`
    (`add_message`, `ingest_rescanned_message`, `_load_from_archive`)
  - `meshcore_watchlist/services/message_archive.py`
    (`load_all_message_fingerprints`)
  - `meshcore_watchlist/decoder/packet_decoder.py`
    (`_secret_to_name`, `decode(..., priority_name_order=...)`)
  - `meshcore_watchlist/services/archive_rescanner.py`
    (`RescanJob.only_channel_name`)
  - `meshcore_watchlist/api/` (route `/api/v1/rescan/by-name`)
- Werkdocument met het volledige bugfix-traject:
  `template_3_naam_leidend_rescan.md`.
