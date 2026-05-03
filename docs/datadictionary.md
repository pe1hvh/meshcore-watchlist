# Data dictionary — meshcore-watchlist

> **Status**: v1.0.0 · 2026-05-02
> **Doelgroep**: Python-developers die het project overnemen of uitbreiden,
> en AI-assistenten (Claude) die in latere sessies features bouwen of bugs
> verhelpen tegen deze codebase.

---

## Inhoudsopgave

1.  [Inleiding & leeswijzer](#1-inleiding--leeswijzer)
2.  [Centrale invariant](#2-centrale-invariant)
3.  [Domeintypes (Python dataclasses)](#3-domeintypes-python-dataclasses)
4.  [Bestandsformaten](#4-bestandsformaten)
5.  [REST API — payload-shapes](#5-rest-api--payload-shapes)
6.  [Externe payload-shapes](#6-externe-payload-shapes)
7.  [Exception-types](#7-exception-types)
8.  [Verwijzingen](#8-verwijzingen)

---

## 1. Inleiding & leeswijzer

### 1.1 Doel van dit document

Dit document bevat **alle datavelden** die `meshcore-watchlist` intern
gebruikt of extern uitwisselt: Python-dataclasses, JSONL-archiefformaten,
REST-payload-shapes en de externe Domca-API-respons. Het is bedoeld als
referentie bij het schrijven of debuggen van code: *"welke velden heeft
een `Message` ook alweer, welke zijn verplicht, welke compatibiliteit
moet ik bewaken?"*.

Voor het *gedrag* dat met deze data verbonden is, zie `docs/fto.md`.
Voor de *implementatie* die de data verwerkt, zie `docs/architecture.md`.

### 1.2 Leesconventies

Elke tabel heeft dezelfde kolommen:

| Kolom         | Betekenis                                                                 |
|---------------|---------------------------------------------------------------------------|
| **Veld**      | Naam van het veld zoals het in de code of payload heet.                  |
| **Type**      | Python-type (intern) of JSON-type (REST/JSONL).                          |
| **Verplicht** | Of het veld bij een nieuwe instantie aanwezig moet zijn.                 |
| **Rol**       | Waarvoor het veld bestaat (identiteit, sleutel, dedup, display, …).      |
| **Beschrijving** | Inhoud, herkomst en eventuele bijzonderheden.                          |

**Optioneel-conventie**: een `Optional[T]`-veld kan `None` zijn; een veld
zonder `Optional` mag niet `None` zijn. JSON-equivalent: `null` is
toegestaan voor de eerste, niet voor de tweede.

### 1.3 Wat is het integerveld dat de UI-positie weergeeft

Sommige types en payloads bevatten een integerveld dat een rij-positie
in de UI weergeeft. Dat veld is uitsluitend bedoeld voor weergave en
compatibility met de externe shape van `meshcore-gui`. Het is:

- ❌ **niet** een sleutel of identiteit van een kanaal — dat is `channel_name`.
- ❌ **niet** een component van een dedup-fingerprint.
- ❌ **niet** een scope-parameter voor een rescan.
- ❌ **niet** een element van een priority-volgorde.
- ❌ **niet** een onderdeel van een REST-pad.

Bij elk veld waar deze waarschuwing relevant is, noteert de tabel
expliciet *display* in de Rol-kolom. Zie ADR-001 en `docs/architecture.md`
§7.5 voor de volledige uitwerking.

---

## 2. Centrale invariant

> **`channel_name` is overal in het systeem de stabiele identiteit van
> een kanaal.** Alle dedup-fingerprints, scope-parameters,
> priority-lijsten, decoder-key-tabellen en rescan-API-paden werken met
> deze naam. Een mutatie aan de watchlist (toevoegen, verwijderen,
> herordenen) wijzigt geen historische data zolang namen ongewijzigd
> blijven. Velden die een UI-positie weergeven zijn afgeleid en dienen
> uitsluitend voor display.

---

## 3. Domeintypes (Python dataclasses)

### 3.1 `Message` — `core/models.py`

Eén `GroupText`-bericht of DM, na succesvol decoden.

| Veld           | Type                | Verplicht | Rol                  | Beschrijving                                                                                                          |
|----------------|---------------------|-----------|----------------------|-----------------------------------------------------------------------------------------------------------------------|
| `time`         | `str`               | ja        | weergave             | HH:MM:SS lokaal moment. Voor UTC ISO-8601 zie `timestamp_utc` in archive en REST-payload (apart, niet op deze dataclass). |
| `sender`       | `str`               | ja        | weergave             | Display-naam van de afzender, zoals uit de decrypted GroupText-payload.                                                |
| `text`         | `str`               | ja        | weergave / dedup     | De berichttekst.                                                                                                       |
| `channel`      | `Optional[int]`     | ja        | display              | UI-positie van het kanaal in de watchlist op moment van ingest. Mag `None` zijn (kanaal verwijderd tussen decode en ingest). Geen sleutel. |
| `direction`    | `str`               | ja        | weergave             | `'in'` voor ontvangen, `'out'` voor verzonden. In deze service in praktijk altijd `'in'`.                              |
| `snr`          | `Optional[float]`   | nee       | diagnose             | Signal-to-noise ratio in dB.                                                                                          |
| `path_len`     | `int`               | nee       | diagnose             | Hops uit de LoRa frame-header. Default `0`.                                                                           |
| `sender_pubkey`| `str`               | nee       | dedup-content        | Volledige publieke sleutel van de afzender (hex). Default `""`.                                                        |
| `path_hashes`  | `List[str]`         | nee       | diagnose             | 2-char hex repeater-hashes uit het decoded packet. Default `[]`.                                                       |
| `path_names`   | `List[str]`         | nee       | weergave             | Display-namen voor elke hash, opgehaald op ontvangtijd. Default `[]`.                                                  |
| `message_hash` | `str`               | nee       | dedup                | Deterministische packet-identifier (hex). Component van de fingerprint. Default `""`.                                  |
| `channel_name` | `str`               | nee*      | **identiteit**       | Naam van het kanaal waarmee het packet ontcijferd werd. Default `""`. Component van de fingerprint. **Stabiele identiteit per ADR-001.** |

*Default `""`, maar in de praktijk altijd populated voor decoded
GroupText-berichten — `PacketPipeline` zet hem direct na bouw vanuit
`DecodedPacket.channel_name`.

**Fingerprint-tuple** voor dedup in `SharedData` en het archive:

```python
(message_hash, sender, text, channel_name)
```

— met `channel_name` als laatste component, **expliciet géén `channel`**.

### 3.2 `RxLogEntry` — `core/models.py`

Eén rauw rx-log-record, ongeacht of het ontcijferbaar bleek.

| Veld              | Type             | Verplicht | Rol               | Beschrijving                                                  |
|-------------------|------------------|-----------|-------------------|---------------------------------------------------------------|
| `time`            | `str`            | ja        | weergave          | HH:MM:SS lokaal moment.                                       |
| `snr`             | `float`          | nee       | diagnose          | Default `0.0`.                                                |
| `rssi`            | `float`          | nee       | diagnose          | Received signal strength (dBm). Default `0.0`.                |
| `payload_type`    | `str`            | nee       | classificatie     | Packet type identifier (string-name). Default `"?"`.          |
| `hops`            | `int`            | nee       | diagnose          | `path_len` uit de frame-header. Default `0`.                  |
| `message_hash`    | `str`            | nee       | dedup             | Default `""`. Sleutel voor `_rxlog_hashes`-set.               |
| `path_hashes`     | `List[str]`      | nee       | diagnose          | 2-char hex repeater-hashes. Default `[]`.                     |
| `path_names`      | `List[str]`      | nee       | weergave          | Display-namen per hash. Default `[]`.                         |
| `sender`          | `str`            | nee       | diagnose          | Default `""`.                                                 |
| `receiver`        | `str`            | nee       | diagnose          | Default `""`.                                                 |
| `raw_payload`     | `str`            | nee       | input voor decode | Hex string van het rauwe packet. Default `""`.                |
| `packet_len`      | `int`            | nee       | diagnose          | Totale packet-lengte in bytes. Default `0`.                   |
| `payload_len`     | `int`            | nee       | diagnose          | Payload-lengte in bytes. Default `0`.                         |
| `route_type`      | `str`            | nee       | diagnose          | `"F"` (flood) of `"D"` (direct). Default `""`.                |
| `packet_type_num` | `int`            | nee       | diagnose          | Numerieke packet-type. Default `-1`.                          |

**Dedup-sleutel** in `SharedData._rxlog_hashes`: alleen `message_hash`.

### 3.3 `DecodedPacket` — `decoder/packet_decoder.py`

Structuur die `PacketDecoder.decode()` retourneert. Beschrijft een
binnenkomend packet na structurele decode plus eventuele decryptie.

| Veld           | Type           | Verplicht | Rol             | Beschrijving                                                                |
|----------------|----------------|-----------|-----------------|-----------------------------------------------------------------------------|
| `message_hash` | `str`          | ja        | dedup           | Deterministische packet-identifier (hex). Uit de unencrypted header.        |
| `payload_type` | `PayloadType`  | ja        | classificatie   | Enum uit `meshcoredecoder`. Alleen `GroupText` triggert Message-aanmaak.    |
| `path_length`  | `int`          | ja        | diagnose        | Aantal repeater-hashes in het pad.                                          |
| `path_hashes`  | `List[str]`    | nee       | diagnose        | 2-char hex strings, één per repeater. Default `[]`.                         |
| `sender`       | `str`          | nee       | weergave        | GroupText only, na decryptie. Default `""`.                                 |
| `text`         | `str`          | nee       | weergave        | GroupText only, na decryptie. Default `""`.                                 |
| `channel_name` | `str`          | ja*       | **identiteit**  | Naam van het kanaal waarvan de sleutel matchde. Default `""` als niet-decrypted. **Stabiele identiteit per ADR-001.** |
| `timestamp`    | `int`          | nee       | weergave        | GroupText only, uit decrypted payload. Default `0`.                         |
| `is_decrypted` | `bool`         | ja        | gedrag          | `True` als payload succesvol ontcijferd. Bij `False` is `channel_name == ""`. |

*Default `""`, maar populated bij elke succesvolle decryptie.

### 3.4 `RescanJob` — `services/archive_rescanner.py`

Server-side state voor één ingediende rescan-job. Wordt geserialiseerd
naar JSON via `to_dict()` en als zodanig door de REST API teruggegeven.

| Veld                   | Type                  | Verplicht | Rol             | Beschrijving                                                                                            |
|------------------------|-----------------------|-----------|-----------------|---------------------------------------------------------------------------------------------------------|
| `job_id`               | `str`                 | ja        | identiteit      | Opaque hex string, toegekend bij submit. Sleutel voor `GET /api/v1/rescan/{job_id}`.                    |
| `start_date`           | `str`                 | ja        | scope           | Inclusieve onderkant van het rescan-venster, `YYYY-MM-DD` UTC dag.                                       |
| `end_date`             | `str`                 | ja        | scope           | Inclusieve bovenkant. Moet ≥ `start_date`.                                                              |
| `status`               | `RescanStatus`        | ja        | gedrag          | Enum-string: `"queued"` / `"running"` / `"done"` / `"failed"`.                                          |
| `only_channel_name`    | `Optional[str]`       | nee       | scope           | `None` voor volledige rescan; kanaalnaam voor per-channel rescan. **Stabiele identiteit per ADR-001.**   |
| `started_at`           | `Optional[str]`       | nee       | tijd-stamp      | ISO-8601 UTC moment waarop de worker de job pakte. `None` zolang `status == "queued"`.                  |
| `finished_at`          | `Optional[str]`       | nee       | tijd-stamp      | ISO-8601 UTC moment waarop de job `"done"` of `"failed"` werd. `None` zolang nog actief.                |
| `files`                | `List[FileProgress]`  | nee       | voortgang       | Per-file voortgang in de volgorde waarin ze verwerkt worden. Default `[]`.                              |
| `new_messages`         | `int`                 | ja        | teller          | GroupText-berichten nieuw geschreven naar archive deze run.                                             |
| `skipped_dup_message`  | `int`                 | ja        | teller          | GroupText-berichten succesvol decoded waarvan de fingerprint al in archive zat.                         |
| `new_rxlog`            | `int`                 | ja        | teller          | RxLogEntry-records nieuw geschreven naar archive.                                                       |
| `skipped_dup_rxlog`    | `int`                 | ja        | teller          | RxLogEntry-records waarvan `message_hash` al in archive zat.                                            |
| `decoded_total`        | `int`                 | ja        | teller          | GroupText succesvol ontcijferd door decoder = `new_messages + skipped_dup_message`.                    |
| `not_decryptable`      | `int`                 | ja        | teller          | GroupText waarvoor geen sleutel matchte (geen scope-match of gewoon geen sleutel).                       |
| `skipped_window`       | `int`                 | ja        | teller          | Records waarvan timestamp buiten het venster viel.                                                       |
| `skipped_files`        | `int`                 | ja        | teller          | Files die de filename-skip-laag wegfilterde.                                                            |
| `decode_failures`      | `int`                 | ja        | teller          | Structureel ongeldige packets.                                                                          |
| `priority_source`      | `str`                 | ja        | gedrag          | `"domca"` als priority-lijst van de Domca-API kwam, `"fallback"` bij uitval / lege lijst.               |
| `error`                | `Optional[str]`       | nee       | diagnose        | Human-readable foutmelding wanneer `status == "failed"`. `None` anders.                                  |

**Tellerrelatie**: `decoded_total == new_messages + skipped_dup_message`
geldt op elk moment binnen de run.

### 3.5 `FileProgress` — `services/archive_rescanner.py`

Per-file byte-voortgang voor een rescan-job.

| Veld          | Type   | Verplicht | Rol      | Beschrijving                                       |
|---------------|--------|-----------|----------|----------------------------------------------------|
| `path`        | `str`  | ja        | identiteit | Filesystem-pad van de source-file.                  |
| `bytes_total` | `int`  | ja        | voortgang  | Totale bytes in het bestand bij job-start.          |
| `bytes_done`  | `int`  | nee       | voortgang  | Reeds verwerkte bytes. Default `0`. Loopt op tijdens de run. |

### 3.6 `RescanStatus` — enum

```python
class RescanStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
```

| Waarde      | Betekenis                                                                                        |
|-------------|--------------------------------------------------------------------------------------------------|
| `"queued"`  | Job aangemaakt, nog niet door een worker opgepakt.                                               |
| `"running"` | Worker is bezig met records verwerken.                                                           |
| `"done"`    | Volledig afgerond zonder fatale fout. Tellers reflecteren het eindresultaat.                     |
| `"failed"`  | Afgebroken met fatale fout (ontbrekende source-archive, geen archive op SharedData, etc.). `error` veld is gezet. |

### 3.7 Overige types in `core/models.py`

`Contact`, `DeviceInfo` en `RouteNode` staan ook in `core/models.py` voor
shape-compatibility met `meshcore-gui`-helpers. Ze worden in de
watchlist-service zelf niet actief gebruikt (de watchlist heeft geen
contact-list of eigen device-info). Voor de exacte velden zie de source
of de overeenkomstige documentatie van `meshcore-gui`.

---

## 4. Bestandsformaten

### 4.1 `~/.meshcore-watchlist/watchlist.json`

Persistente representatie van de gemonitorde kanalen. Beheerd door
`WatchlistStore`.

```json
{
    "version": 1,
    "channels": [
        {"idx": 0, "name": "Public"},
        {"idx": 1, "name": "#mc-radar"},
        {"idx": 2, "name": "#weather"}
    ]
}
```

| Veld             | Type           | Verplicht | Rol            | Beschrijving                                                                  |
|------------------|----------------|-----------|----------------|-------------------------------------------------------------------------------|
| `version`        | `int`          | ja        | schema-versie  | Op dit moment `1`.                                                            |
| `channels`       | `List[Object]` | ja        | inhoud         | Lijst van kanaal-objecten in UI-volgorde.                                     |
| `channels[].idx` | `int`          | ja        | display        | UI-rij-positie. Geen sleutel. Eerste rij is `0`. Public hoort altijd op `0`.  |
| `channels[].name`| `str`          | ja        | **identiteit** | Kanaalnaam. Hashtag-kanalen beginnen met `#`. `Public` is canoniek.          |

**Invariant**: er is exact één entry met `name` gelijk aan
`PUBLIC_CHANNEL_CANONICAL_NAME` (case-insensitive). Verwijderen van de
Public-entry wordt door `WatchlistStore` afgewezen.

### 4.2 `~/.meshcore-watchlist/state.json`

Tailer-cursors per source-file. Beheerd door `JsonlTailer`.

```json
{
    "/home/pi/.meshcore-gui/archive/_dev_<addr>_rxlog.jsonl": 12345678
}
```

| Veld                     | Type        | Verplicht | Rol      | Beschrijving                                                              |
|--------------------------|-------------|-----------|----------|---------------------------------------------------------------------------|
| (key) absolute file-path | `str`       | ja        | sleutel  | Volledige pad van een source-file die de tailer monitort.                  |
| (value) byte-offset      | `int`       | ja        | cursor   | Aantal bytes dat van het bestand al verwerkt is.                           |

**Reset-gedrag**: als bij volgende poll het bestand kleiner is dan de
opgeslagen offset (truncate of rotate), wordt de cursor naar `0` gereset.
De fingerprint-dedup absorbeert de eenmalige re-emit.

### 4.3 `~/.meshcore-watchlist/archive/<device-id>_messages.jsonl`

Append-only JSONL met decoded berichten. Eén `Message`-record per regel,
geserialiseerd via `dataclasses.asdict(msg)`. De record-shape is dus
gelijk aan de `Message`-dataclass uit §3.1, plus één extra veld dat
`MessageArchive` toevoegt:

| Veld extra      | Type   | Verplicht | Rol         | Beschrijving                                                              |
|-----------------|--------|-----------|-------------|---------------------------------------------------------------------------|
| `timestamp_utc` | `str`  | nee       | tijd-stamp  | ISO-8601 UTC moment van schrijven. Bij rescan-ingest het *originele* packet-moment, niet het rescan-moment. |

**Append-only-discipline**: bestaande regels worden nooit overschreven.
Edits gebeuren door een nieuw record met dezelfde fingerprint en latere
`timestamp_utc` toe te voegen — consumers nemen het laatste.

**Retentie**: dagelijks worden records ouder dan
`MESSAGE_RETENTION_DAYS` (default `7`) verwijderd door een one-shot
rewrite (lees → filter → schrijf naar `.tmp` → atomic rename).

### 4.4 `~/.meshcore-watchlist/archive/<device-id>_rxlog.jsonl`

Append-only JSONL met rauwe rx-log entries. Shape gelijk aan
`RxLogEntry` uit §3.2, plus dezelfde extra `timestamp_utc` als in §4.3.

**Retentie**: `RXLOG_RETENTION_DAYS` (default `7`).

### 4.5 Legacy-files na 0.2.4-migratie

Bij upgrade van een 0.2.3-archive worden de oude
`<device-id>_messages.json` en `<device-id>_rxlog.json` (formaat-versie
1, read-merge-rewrite) automatisch geconverteerd naar JSONL en hernoemd
naar `<device-id>_messages.json.migrated-v1` resp.
`<device-id>_rxlog.json.migrated-v1`. Deze bestanden zijn voor recovery
en worden door de service zelf niet meer gelezen.

---

## 5. REST API — payload-shapes

Alle endpoints zitten onder `/api/v1/`. Response-shapes zijn
byte-voor-byte compatible met die van `meshcore-gui` (zie
`docs/architecture.md` §2.4).

### 5.1 `GET /api/v1/channels`

Retourneert de huidige watchlist.

**Response** (HTTP 200, JSON-array):

```json
[
    {"idx": 0, "name": "Public",    "is_private": false},
    {"idx": 1, "name": "#mc-radar", "is_private": false}
]
```

| Veld         | Type   | Verplicht | Rol            | Beschrijving                                                                 |
|--------------|--------|-----------|----------------|------------------------------------------------------------------------------|
| `idx`        | `int`  | ja        | display        | UI-rij-positie uit `watchlist.json`.                                         |
| `name`       | `str`  | ja        | **identiteit** | Kanaalnaam.                                                                  |
| `is_private` | `bool` | ja        | classificatie  | Watchlist-entries zijn altijd public/hashtag, dus altijd `false`.            |

### 5.2 `GET /api/v1/messages`

Geretourneert decoded berichten paginaat.

**Query-parameters**:

| Parameter | Type | Default | Range  | Beschrijving               |
|-----------|------|---------|--------|----------------------------|
| `limit`   | int  | 100     | 1-500  | Maximum aantal items per response. |
| `offset`  | int  | 0       | ≥0     | Aantal items om over te slaan vanaf het begin van het filtered dataset. |

**Response** (HTTP 200):

```json
{
    "total": 1234,
    "limit": 100,
    "offset": 0,
    "items": [
        {
            "id": 1,
            "message_hash": "abc123...",
            "channel_idx": 1,
            "channel_name": "#mc-radar",
            "sender": "PE1ABC",
            "sender_pubkey": "abc123...",
            "text": "Hallo allemaal",
            "timestamp": "2026-04-27T07:44:41+00:00",
            "hops": 2,
            "path_hashes": ["a1", "b2"],
            "path_names":  ["pe1xyz-rep", "pe1xyz-home"]
        }
    ]
}
```

| Veld        | Type           | Verplicht | Rol      | Beschrijving                                                                                |
|-------------|----------------|-----------|----------|---------------------------------------------------------------------------------------------|
| `total`     | `int`          | ja        | metadata | Totale aantal items dat aan de filter voldoet, onafhankelijk van `limit`/`offset`.          |
| `limit`     | `int`          | ja        | echo     | De `limit` die de server effectief toepaste (geclampt op 1-500).                            |
| `offset`    | `int`          | ja        | echo     | De `offset` die de server effectief toepaste (geclampt ≥0).                                 |
| `items`     | `List[Object]` | ja        | inhoud   | De page met items.                                                                          |

**Item-velden** (per object in `items`):

| Veld            | Type            | Verplicht | Rol             | Beschrijving                                                                  |
|-----------------|-----------------|-----------|-----------------|-------------------------------------------------------------------------------|
| `id`            | `int`           | ja        | response-lokaal | Lokaal nummer per response (start `offset+1`). **Geen stable primary key**, geen relatie met `channel_name`. Downstream dedup op content-key. |
| `message_hash`  | `str`           | ja        | dedup-content   | Hex hash uit packet header.                                                   |
| `channel_idx`   | `Optional[int]` | ja        | display         | UI-positie van het kanaal op moment van ingest. Mag `null` zijn. Geen sleutel. |
| `channel_name`  | `str`           | ja        | **identiteit**  | Naam van het kanaal waarmee het packet ontcijferd werd.                       |
| `sender`        | `str`           | ja        | weergave        | Display-naam afzender.                                                        |
| `sender_pubkey` | `str`           | ja        | dedup-content   | Volledige public-key (hex).                                                   |
| `text`          | `str`           | ja        | weergave        | Bericht-tekst.                                                                |
| `timestamp`     | `Optional[str]` | ja        | tijd-stamp      | ISO-8601 UTC. Default `null` als niet beschikbaar.                            |
| `hops`          | `int`           | ja        | diagnose        | Repeater-hop-count.                                                           |
| `path_hashes`   | `List[str]`     | ja        | diagnose        | 2-char hex repeater-hashes.                                                   |
| `path_names`    | `List[str]`     | ja        | weergave        | Display-namen per hash.                                                       |

**Compatibiliteits-eis**: deze shape is byte-voor-byte gelijk aan
`meshcore-gui`. Toevoegen van velden mag (additieve evolutie); hernoemen
of weglaten breekt downstream consumers.

### 5.3 `GET /api/v1/stats`

Aggregaten over de laatste 72 uur public/hashtag-traffic.

**Response** (HTTP 200):

```json
{
    "generated_at": "2026-05-02T10:00:00+00:00",
    "period_hours": 72,
    "total_messages": 1234,
    "unique_senders": 56,
    "active_clients": 0,
    "active_repeaters": 0,
    "active_room_servers": 0,
    "avg_hops": 1.85,
    "peak_hour": 19
}
```

| Veld                  | Type            | Verplicht | Rol         | Beschrijving                                                                                                |
|-----------------------|-----------------|-----------|-------------|-------------------------------------------------------------------------------------------------------------|
| `generated_at`        | `str`           | ja        | tijd-stamp  | ISO-8601 UTC moment waarop de stats berekend werden.                                                         |
| `period_hours`        | `int`           | ja        | metadata    | Het venster waarover geaggregeerd werd. Vast op `72`.                                                       |
| `total_messages`      | `int`           | ja        | aggregaat   | Aantal berichten in het venster.                                                                            |
| `unique_senders`      | `int`           | ja        | aggregaat   | Unieke afzenders (op `sender_pubkey` of `sender`).                                                          |
| `active_clients`      | `int`           | ja        | compat      | Altijd `0` — watchlist heeft geen contact-list. Aanwezig voor shape-compatibility.                          |
| `active_repeaters`    | `int`           | ja        | compat      | Altijd `0`. Idem.                                                                                           |
| `active_room_servers` | `int`           | ja        | compat      | Altijd `0`. Idem.                                                                                           |
| `avg_hops`            | `float`         | ja        | aggregaat   | Gemiddelde hop-count over berichten met `path_len > 0`. `0.0` als geen data.                                |
| `peak_hour`           | `Optional[int]` | ja        | aggregaat   | Uur (0-23) met de meeste berichten in het venster. `null` als geen data.                                    |

### 5.4 `GET /api/v1/nodes`

Altijd lege array.

**Response** (HTTP 200):

```json
[]
```

Aanwezig voor shape-compatibility met `meshcore-gui`. De watchlist
heeft geen eigen contact-list.

### 5.5 `POST /api/v1/rescan` — submit volledige rescan

**Query-parameters**:

| Parameter    | Type | Verplicht | Beschrijving                                                |
|--------------|------|-----------|-------------------------------------------------------------|
| `start_date` | str  | ja        | ISO-8601 `YYYY-MM-DD` UTC dag. Inclusief.                  |
| `end_date`   | str  | ja        | ISO-8601 `YYYY-MM-DD` UTC dag. Inclusief. Moet ≥ `start_date`. |

**Response op succes** (HTTP 202): `RescanJob.to_dict()` zoals beschreven
in §5.7. `only_channel_name` is `null`. `status` is `"queued"`.

**Foutresponses**:

| Status | Body-shape                                              | Conditie                          |
|--------|---------------------------------------------------------|-----------------------------------|
| 400    | `{"error": "invalid_rescan_window", "message": "..."}`  | Datums ontbreken of zijn ongeldig.|
| 409    | `{"error": "rescan_busy", "running_job_id": "..."}`     | Andere rescan-job draait al.      |

### 5.6 `POST /api/v1/rescan/by-name` — submit per-channel rescan

**Query-parameters**:

| Parameter      | Type | Verplicht | Beschrijving                                                       |
|----------------|------|-----------|--------------------------------------------------------------------|
| `channel_name` | str  | ja        | Kanaalnaam (URL-encoded; `#` als `%23`). Moet in huidige watchlist. |
| `start_date`   | str  | ja        | Idem als bij volledige rescan.                                     |
| `end_date`     | str  | ja        | Idem.                                                               |

**Response op succes** (HTTP 202): `RescanJob.to_dict()`. `only_channel_name`
is gezet op de gevraagde naam.

**Foutresponses**:

| Status | Body-shape                                                              | Conditie                                  |
|--------|-------------------------------------------------------------------------|-------------------------------------------|
| 400    | `{"error": "missing_channel_name"}`                                     | Geen `channel_name` opgegeven of leeg.    |
| 400    | `{"error": "invalid_rescan_window", "message": "..."}`                  | Datums ontbreken of ongeldig.             |
| 404    | `{"error": "channel_name_not_in_watchlist", "channel_name": "..."}`     | Naam niet in huidige watchlist op submit. |
| 409    | `{"error": "rescan_busy", "running_job_id": "..."}`                     | Andere rescan-job draait al.              |

### 5.7 `GET /api/v1/rescan/{job_id}` — status van rescan-job

**Response op succes** (HTTP 200):

```json
{
    "job_id": "f3a9b2...",
    "status": "running",
    "only_channel_name": "#mc-radar",
    "start_date": "2026-04-25",
    "end_date": "2026-04-30",
    "priority_source": "domca",
    "started_at": "2026-05-02T10:00:00+00:00",
    "finished_at": null,
    "progress": {
        "bytes_done": 1048576,
        "bytes_total": 4194304,
        "percent": 25.0,
        "files": [
            {"path": "/home/pi/.meshcore-gui/archive/...", "bytes_done": 1048576, "bytes_total": 4194304}
        ]
    },
    "counts": {
        "new_messages": 12,
        "skipped_dup_message": 100,
        "new_rxlog": 0,
        "skipped_dup_rxlog": 200,
        "decoded_total": 112,
        "not_decryptable": 5,
        "skipped_window": 50,
        "skipped_files": 2,
        "decode_failures": 0
    },
    "error": null
}
```

| Veld                  | Type            | Verplicht | Beschrijving                                                                |
|-----------------------|-----------------|-----------|-----------------------------------------------------------------------------|
| `job_id`              | `str`           | ja        | Echo van submit.                                                            |
| `status`              | `str`           | ja        | `"queued"` / `"running"` / `"done"` / `"failed"`.                           |
| `only_channel_name`   | `Optional[str]` | ja        | Echo. `null` voor volledige rescan.                                         |
| `start_date`          | `str`           | ja        | Echo.                                                                       |
| `end_date`            | `str`           | ja        | Echo.                                                                       |
| `priority_source`     | `str`           | ja        | `"domca"` of `"fallback"`.                                                  |
| `started_at`          | `Optional[str]` | ja        | ISO-8601 UTC. `null` zolang queued.                                         |
| `finished_at`         | `Optional[str]` | ja        | ISO-8601 UTC. `null` zolang nog actief.                                     |
| `progress`            | `Object`        | ja        | Aggregate + per-file voortgang. Zie hieronder.                              |
| `counts`              | `Object`        | ja        | Tellers gespiegeld op `RescanJob`-velden uit §3.4.                          |
| `error`               | `Optional[str]` | ja        | Foutmelding als `status == "failed"`. `null` anders.                        |

**`progress`-object**:

| Veld          | Type           | Beschrijving                                                          |
|---------------|----------------|-----------------------------------------------------------------------|
| `bytes_done`  | `int`          | Som van `bytes_done` over alle files.                                 |
| `bytes_total` | `int`          | Som van `bytes_total` over alle files. Minimum `1` om deelbaar te blijven. |
| `percent`     | `float`        | `100.0 * bytes_done / bytes_total`, afgerond op 1 decimaal.           |
| `files`       | `List[Object]` | Lijst van `{path, bytes_done, bytes_total}` per file.                 |

**`counts`-object**: alle tellers uit `RescanJob` (§3.4) één-op-één.

**Foutresponse**:

| Status | Body-shape                                  | Conditie               |
|--------|---------------------------------------------|------------------------|
| 404    | `{"error": "unknown_job", "job_id": "..."}` | `job_id` onbekend.    |

### 5.8 `POST /api/v1/channels` — voeg kanaal toe aan watchlist

Additieve endpoint (sinds 0.3.0) voor out-of-process clients die de
watchlist programmatisch willen seeden. Forward intern naar
`WatchlistStore.add()`.

**Query-parameters**:

| Parameter | Type | Verplicht | Beschrijving                                                                                              |
|-----------|------|-----------|-----------------------------------------------------------------------------------------------------------|
| `name`    | str  | ja        | Kanaalnaam (URL-encoded; `#` als `%23`). Een ontbrekend `#`-prefix wordt server-side toegevoegd, behalve voor de canonieke naam `Public`. |

**Response op succes — nieuwe naam toegevoegd** (HTTP 201):

```json
{
    "name": "#weather",
    "added": true
}
```

**Response op succes — naam al aanwezig** (HTTP 200):

```json
{
    "name": "#weather",
    "added": false,
    "reason": "already_on_watchlist"
}
```

**Response op succes — naam = `Public`** (HTTP 200):

```json
{
    "name": "Public",
    "added": false,
    "reason": "public_is_system_managed"
}
```

`Public` is system-managed en altijd aanwezig op `idx == 0` (zie
ADR-001 en architectuurhoofdstuk 7.3). Een verzoek voor deze naam
levert daarom 200 met `added=false`, niet een fout — de
client-intentie ("zorg dat deze naam op de watchlist staat") is
voldaan.

**Foutresponses**:

| Status | Body-shape                                                              | Conditie                                                              |
|--------|-------------------------------------------------------------------------|-----------------------------------------------------------------------|
| 400    | `{"error": "missing_name"}`                                             | `name`-parameter ontbreekt of is leeg / whitespace-only.              |
| 400    | `{"error": "invalid_name"}`                                             | `name` bevat control-characters (CR, LF, of een ander byte < 0x20 / = 0x7F). |
| 400    | `{"error": "name_too_long", "max_bytes": 32, "got_bytes": N}`           | `name` is langer dan 32 UTF-8 bytes (zie ADR-007).                    |

**Lengte-eenheid is bytes, niet codepoints** (zie ADR-007). Bijvoorbeeld
`#café` is 6 bytes (`#`=1, `c`=1, `a`=1, `f`=1, `é`=2), niet 5. Een
naam als `#` + 16× `é` is 33 bytes en wordt geweigerd, ook al zijn het
"maar" 17 codepoints. De `got_bytes`-waarde in de foutresponse is de
gemeten UTF-8-bytelengte ná eventuele server-side `#`-prefix.

| Veld         | Type    | Verplicht | Beschrijving                                                                       |
|--------------|---------|-----------|------------------------------------------------------------------------------------|
| `name`       | `str`   | ja        | De canonieke naam zoals opgeslagen (met `#`-prefix voor hashtag-kanalen).          |
| `added`      | `bool`  | ja        | `true` iff een nieuwe rij is aangemaakt; `false` voor "al aanwezig" en `Public`.   |
| `reason`     | `str`   | nee       | Aanwezig wanneer `added=false`. Waarden: `already_on_watchlist`, `public_is_system_managed`. |

---

## 6. Externe payload-shapes

### 6.1 Domca-API — `GET https://www.domca.nl/api/meshcore/channel_statistics.php`

Door `services/channel_priority.fetch_priority_name_order` aangeroepen
met 5 seconden timeout. De respons is een JSON-array van objecten.

| Veld                  | Type   | Door watchlist gebruikt? | Beschrijving                                                              |
|-----------------------|--------|--------------------------|---------------------------------------------------------------------------|
| `name`                | `str`  | **ja**                   | Kanaalnaam, basis van prioriteit.                                         |
| `aantal_berichten`    | `int`  | **ja**                   | Aantal berichten op dit kanaal in de Domca-database. Sorteersleutel (descending). |
| `first_received_at`   | `str`  | nee                      | Server-side cumulatief timestamp; bekend onbetrouwbaar in de huidige Domca-database. |
| `last_received_at`    | `str`  | nee                      | Idem.                                                                     |

**Failure-modus**: timeout, HTTP 4xx/5xx, malformed JSON, of een
JSON-array waarin een element geen `name`-veld heeft, leiden tot een
lege resultaatlijst (`[]`). De rescan-worker zet dan
`priority_source = "fallback"` en gebruikt watchlist-volgorde. Geen
exception bubbelt op.

### 6.2 meshcore-gui rx-log records

Door `JsonlTailer` ingelezen uit `*_rxlog.jsonl` onder
`MESHCORE_GUI_ARCHIVE`. De record-shape is meshcore-gui-eigendom maar
moet voor deze service de velden bevatten die `RxLogEntry` vult (§3.2)
plus minstens:

- `time` — string-formattering van ontvangstijd (HH:MM:SS).
- `raw_payload` — hex string van het rauwe LoRa-packet (input voor decoder).
- `payload_type` — packet type identifier.
- `message_hash` — voor dedup.

Velden die ontbreken worden door `PacketPipeline.handle_entry`
defensief afgevangen met defaults; een record met onleesbare structuur
wordt geskipt en gelogd, niet gecrashed.

---

## 7. Exception-types

Gedefinieerd in `services/archive_rescanner.py` en geconverteerd door
`api/routes.py` naar HTTP-statuscodes.

| Exception              | HTTP-status | API error-string                | Trigger                                                              |
|------------------------|-------------|---------------------------------|----------------------------------------------------------------------|
| `InvalidRescanWindow`  | 400         | `invalid_rescan_window`         | `start_date` of `end_date` ontbreekt, ongeldig formaat, of `end < start`. |
| `UnknownChannelName`   | 404         | `channel_name_not_in_watchlist` | `only_channel_name` opgegeven maar niet in huidige watchlist op submit.  |
| `RescanBusyError`      | 409         | `rescan_busy`                   | Andere rescan-job draait al; `running_job_id` is op de exception gezet. |

`UnknownChannelName.channel_name` houdt de afgewezen naam vast voor in
de error-body. `RescanBusyError.running_job_id` houdt de `job_id` vast
van de actieve job.

---

## 8. Verwijzingen

- **`CLAUDE.md`** (repo-root) — bindende regels en conventies.
- **`docs/architecture.md`** — technisch ontwerp: lagen, threading,
  decode-pad, lock-strategie.
- **`docs/fto.md`** — functioneel ontwerp: gedrag vanuit
  gebruikersperspectief, use cases, foutpaden.
- **`docs/ontwerp/ontwerp-0.2.6.md`** — release-specifiek ontwerp van de
  naam-leidende refactor.
- **`docs/adr/ADR-001-channel-name-als-identiteit.md`** — motivatie en
  alternatieven achter de naam-leidende identiteit.
- **`docs/adr/ADR-002-datum-en-tijdformaat.md`** — ISO 8601 + UTC.
- **`README.md`** — installatie en eerste gebruik.
- **`CHANGELOG.md`** — wijzigingen per release.
