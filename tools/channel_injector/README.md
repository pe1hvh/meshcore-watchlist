# channel_injector

Standalone hulpscript dat één of meer remote channel-listings (JSON
over HTTP) ophaalt en de daaruit ontbrekende hashtag-channels
automatisch toevoegt aan een draaiende `meshcore-watchlist`-daemon,
gevolgd door een per-channel rescan over de afgelopen 7 dagen.

Dit script staat **los van** `meshcore_watchlist.py` (de daemon zelf):
het is een korte HTTP-client die de publieke `/api/v1/`-endpoints
gebruikt. Mutaties op de watchlist gaan dus alsnog via de
`WatchlistStore` van de daemon (zoals `CLAUDE.md` voorschrijft) — de
client vraagt het, de daemon doet het.

## Werking

1. Voor elke `--source-url`: `GET <url>` → JSON met `channels[]`.
2. `GET <api-base>/api/v1/channels` → huidige watchlist.
3. Per ontbrekend channel:
   - `POST <api-base>/api/v1/channels?name=...` (toevoegen, idempotent)
   - `POST <api-base>/api/v1/rescan/by-name?...` (rescan-window: 7 UTC-dagen)
4. Bestaande channels: skip (geen rescan).
5. `Public` wordt nooit toegevoegd of gerescand (system-managed).

Channel-namen uit de bron komen al met `#`-prefix; entries zonder `#`
worden geskipt met reden `missing_hashtag_prefix` (geen impliciete
correctie). Control-characters (CR/LF, …) worden geweigerd.

## Installatie

`channel_injector` heeft **geen** extra dependencies — alleen Python's
stdlib (`urllib`, `json`, `argparse`, `logging`, `dataclasses`,
`datetime`). Het draait in dezelfde virtualenv als de daemon.

Plaats de `tools/`-map naast `meshcore_watchlist/` in dezelfde
installatiemap (de `install.sh` van de daemon zorgt al voor `.venv`).

## Gebruik

Uitvoeren als module:

```bash
/opt/meshcore-watchlist/.venv/bin/python -m tools.channel_injector \
    --source-url https://example.org/channels.json
```

### Argumenten

| Argument | Verplicht | Default | Beschrijving |
|---|---|---|---|
| `--source-url URL` | ✅ (≥1) | — | Upstream JSON-URL. Mag herhaald worden voor meerdere bronnen. |
| `--api-base URL` | nee | `http://localhost:8083` | Basis-URL van de daemon. |
| `--rescan-days N` | nee | `7` | Aantal UTC-dagen voor de rescan-window. |
| `--timeout SEC` | nee | `10.0` | Timeout per HTTP-call. |
| `--max-source-bytes BYTES` | nee | `1048576` (1 MiB) | Hard plafond op grootte van één source-response. Sluit een misbehavende of gecompromitteerde upstream uit. |
| `--max-adds-per-run N` | nee | `50` | Hard plafond op het aantal toevoegingen per run. Sluit een burst van honderden namen uit als een bron gek doet. |
| `--dry-run` | nee | uit | Vergelijk alleen; geen POSTs. |
| `-v` / `-vv` | nee | WARNING | INFO / DEBUG-loggen. |

### Exit codes

| Code | Betekenis |
|---|---|
| `0` | Succes — alle bronnen opgehaald, daemon bereikbaar. |
| `1` | Argument-fout (bv. geen `--source-url`). |
| `2` | Runtime-fout: daemon onbereikbaar, of ≥1 bron faalde. |

Per run wordt op WARNING-niveau één samenvattings-regel gelogd zoals:

```
channel_injector: run complete: added=2 skipped_existing=5 skipped_invalid=0 rescans=2 source_errors=0 daemon_error=no
```

## Cron versus systemd-service

**Aanbeveling: cron.** Het script is idempotent, korte runs (paar
seconden), geen state, geen lange polling-loop nodig. Een systemd-
service zou onnodig complex zijn voor wat in feite een periodieke
batch-job is. Een `systemd-timer` zou ook kunnen, maar voegt boven
cron niets functioneels toe.

### Cron-entry

Zie `install_script/channel_injector.cron.example`. Belangrijk: gebruik
het **expliciete pad** naar `.venv/bin/python` — cron heeft een
minimale environment en kent geen `source activate`-achtige magie.
Dezelfde `.venv` als de daemon = consistente Python en libraries.

```cron
# Elk kwartier de upstream channel-lijst syncen
*/15 * * * * cd /opt/meshcore-watchlist && \
    /opt/meshcore-watchlist/.venv/bin/python -m tools.channel_injector \
    --source-url https://example.org/channels.json \
    >> /var/log/channel_injector.log 2>&1
```

De `cd` zorgt dat Python `tools.channel_injector` als pakket vindt
(de installatiemap moet op `sys.path` of de cwd staan; `cd` is de
eenvoudigste route).

### Logrotate

Voor `/var/log/channel_injector.log` is een eenvoudige `logrotate.d`
entry aan te raden — de injector zelf rouleert niet.

## Foutpaden

| Situatie | Gedrag |
|---|---|
| Daemon onbereikbaar | Run aborteert direct, exit 2, watchlist onaangetast. |
| Eén bron-URL faalt | Andere bronnen worden alsnog verwerkt; exit 2. |
| Bron-response > `--max-source-bytes` | Bron geweigerd (`ResponseTooLarge`); andere bronnen verwerken door, exit 2. |
| Naam > 32 UTF-8 bytes (ADR-007) | Skip client-side met reden `name_exceeds_32_bytes`; geen POST. |
| Max-adds-per-run cap bereikt | Resterende kandidaten skip met reden `max_adds_reached`; reeds toegevoegde channels behouden hun rescan; vlag `max_adds_reached=yes` in summary. |
| `409 rescan_busy` | Channel is wél toegevoegd; rescan opnieuw bij volgende run. |
| Channel al aanwezig | Skip (geen mutatie, geen rescan). |
| Entry zonder `#` | Skip met reden `missing_hashtag_prefix`. |
| Naam = `Public` (case-insensitive) | Skip met reden `public_is_system_managed`. |
| Naam met CR/LF / control-chars | Skip lokaal én daemon weigert met 400 `invalid_name`. |
