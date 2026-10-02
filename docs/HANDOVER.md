# Handover: SimplyGreen-Lastgang-Integration

Stand: v0.1.0. Das Portal-Protokoll wurde am 02.10.2026 per Reverse
Engineering erfasst. Dieses Dokument enthält alles, was **nicht
direkt im Code steht**: woher das Wissen über das Portal kommt, welche Teile
beobachtet und welche angenommen sind, warum der Code so aufgebaut ist und was
als Nächstes ansteht.

---

## 1. Ausgangslage und Ziel

- **Tarif:** SimplyGreen (früher *ENTEGA Ökostrom dynamisch*). Dynamischer Tarif,
  stündlich bzw. viertelstündlich an den EPEX-Day-Ahead-Preis gekoppelt.
- **Datenquelle:** Der Messstellenbetreiber (Smart Meter / iMSys) meldet die
  Verbrauchswerte an den Versorger. Im Portal sind sie im 15-min-, 1-h- und
  1-Tag-Raster abrufbar.
- **Ziel:** Diese abrechnungsrelevanten Werte in Home Assistant bringen, um
  1. die eigene Messung (z. B. Lesekopf am Zähler) mit den Abrechnungsdaten abzugleichen und
  2. Verbrauch × EPEX-Preis auswerten zu können (später: tatsächliche Kosten).
- **Recherche:** Es gibt keine fertige HA-Integration (geprüft im HACS-Store und im
  Web unter „SimplyGreen“, „ENTEGA Ökostrom dynamisch“, „meineems.entega.de“). Die
  ENTEGA-App zum Tarif („ENTEGA-Cockpit“) stammt von der Tochter Energy Market
  Solutions. Eine öffentliche API ist nicht bekannt.

## 2. Portal-Protokoll (aus Browser-DevTools beobachtet)

Basis-URL: `https://mein.simplygreen.de/index.php`. Klassische PHP-App mit
jQuery-AJAX.

### 2.1 Login

1. `GET index.php?page=login` liefert HTML mit `<meta name="csrf-token" content="…">`.
2. `POST index.php?page=login&action=login&ajax=y`
   - Body: `multipart/form-data` mit den Feldern `form[username]` und `form[password]`
   - Header: `x-csrf-token: <token aus 1.>`, `x-requested-with: XMLHttpRequest`,
     `accept: application/json, text/javascript, */*; q=0.01`
   - Antwort: JSON (Inhalt **unbekannt**, siehe Abschnitt 3)
3. Session-Cookie: `auth_<32 hex>` (der Name enthält einen Hash, vermutlich pro
   Installation fix). Daneben gibt es `cc_cookie` (Cookie-Consent), das für die API
   nicht nötig scheint.

Der Code verlässt sich **nicht** auf den Inhalt der Login-Antwort. Er prüft den
Erfolg, indem er danach `?page=loadprofile` aufruft. Landet er auf `page=login`
oder `page=expired`, gilt der Login als gescheitert.

### 2.2 Lastgang abrufen

```
GET index.php?ajaxService=getLoadProfile&ajax=y
    &startDate=01.10.2026        (TT.MM.JJJJ, Beispielwerte)
    &endDate=02.10.2026
    &resOpt=ResOpt1              (Auflösung, s. u.)
    &mliids=["<mliid>"]          (JSON-Array, URL-kodiert)
    &_=<epoch ms>                (jQuery-Cache-Buster, wird nicht gesendet)
Header: x-csrf-token, x-requested-with: XMLHttpRequest, referer: …?page=loadprofile
```

| `resOpt` | Auflösung | Status |
|---|---|---|
| `ResOpt1` | 15 min | **beobachtet** (96 Werte pro Tag) |
| `ResOpt2` | 1 h | **beobachtet** (24 Werte) |
| `ResOpt3` | 1 Tag | Annahme, nicht geprüft |

Antwort: JSON-Array mit zwei Serien:

```json
[
  {"period":1,"type":"M1","mliid":"<mliid>","mecsdesc":"DE00…<Marktlokations-ID>",
   "unisdesc":"kWh","LineDesc":"Menge","LineUnit":"kWh",
   "data":[[1790813700000,0.003],[1790814600000,0.003], …]},
  {"period":1,"type":"M1","mliid":"363501","mecsdesc":"EX0001",
   "unisdesc":"EUR/MWh","LineDesc":"EPEX","LineUnit":"Euro/MWh",
   "data":[[1790813700000,166.13], …]}
]
```

- Die Serien werden über **`LineDesc`** erkannt (`"Menge"` / `"EPEX"`). Die
  `mliid` der EPEX-Serie ist eine feste Börsen-ID und nicht die des Kunden.
- Im Repo liegt eine echte, anonymisierte Antwort: `tests/fixtures/load_profile_15min.json`.

### 2.3 Zeitstempel – wichtigste Erkenntnis

Die Zeitstempel sind **Intervall-Enden in lokaler Wanduhrzeit, kodiert als
UTC-Epoch** (typisch für Highcharts mit `useUTC`):

- Erster Wert eines Tages (Beispiel `1790813700000` = „2026-10-01 00:15 UTC“):
  gemeint ist das Intervall **00:00–00:15 Uhr Ortszeit**.
- Letzter Wert eines Tages (Beispiel „2026-10-02 00:00“): Intervall 23:45–24:00.
- Stundenwerte: erster Wert „01:00“ = Stunde 00:00–01:00.

Belegt durch den Abgleich mit einer eigenen Messung am Zähler (Portal und Lesekopf stimmen überein).
Umgesetzt in `api.to_intervals()`.

### 2.4 Session-Ablauf

- Ohne gültige Session leitet das Portal auf `?page=expired` um („Sitzung
  abgelaufen“, HTML mit Status 200). Ein AJAX-Aufruf liefert dann HTML statt JSON.
- Der Client erkennt das an der Ziel-URL oder an einer Antwort, die kein JSON ist.
  Er meldet sich **einmal** neu an und versucht es erneut.
- Wie lange eine Session hält, ist unbekannt. Wegen des 3-h-Intervalls ist
  wahrscheinlich bei jedem Lauf ein neuer Login fällig, was so gewollt ist.

## 3. Unverifizierte Annahmen (zuerst gegen das echte Portal prüfen)

| # | Annahme | Wo im Code | Prüfen durch |
|---|---|---|---|
| A1 | Login per Multipart-POST funktioniert auch ohne Browser-Header (`sec-ch-*`, User-Agent) | `api.login` | **Bestätigt 02.10.2026** (echter Lauf, ohne UA-Header). Erster echter Lauf; ggf. User-Agent setzen |
| A2 | Nach dem Login ist der CSRF-Token auf `?page=loadprofile` gültig (bzw. bleibt der Login-Token gültig, Fallback) | `api.login` | **Bestätigt 02.10.2026** (getLoadProfile und getMetLines liefen mit dem Token) |
| A3 | ~~mliid per Regex aus HTML~~ **Widerlegt 02.10.2026:** Der Regex fand nichts. Stattdessen liefert `ajaxService=getMetLines` die `MliId` (`Lines[]` mit `Type: "MLI"`) | `api.get_met_lines`, `api.parse_mliids` | Erledigt, Test mit anonymisierter Fixture |
| A4 | `endDate` ist inklusive bzw. unkritisch. Bei einer Abfrage „gestern bis heute“ kamen für den heutigen Tag keine Werte | `coordinator._async_update_data` | Abfrage mit älterem Zeitraum |
| A5 | Abfragen über 7 Tage pro Request sind erlaubt | `coordinator` (Blockgröße 6+1 Tage) | Längeren Zeitraum testen; ggf. Limit des Portals finden |
| A6 | Am 25.10.2026 (Ende der Sommerzeit) liefert das Portal 100 Werte und wiederholt die Wanduhr 02:15–03:00 | `api.to_intervals`, Test `test_dst_fall_back…` | **Am 26.10. die echte Antwort für den 25.10. prüfen** |
| A7 | Kein Rate-Limit / keine Sperre bei Abruf alle 3 h | – | Beobachten |
| A8 | Daten kommen spätestens nach 3 Tagen und werden danach nicht mehr korrigiert | `const.REFETCH_DAYS` | Beobachten |

## 4. Architektur und Entwurfsentscheidungen

```
custom_components/simplygreen/
  api.py          Portal-Client + reine Parse-Funktionen (ohne HA-Imports)
  coordinator.py  DataUpdateCoordinator: Abruf, Statistik-Import, Tageszusammenfassung
  sensor.py       2 Sensoren auf Basis der Tageszusammenfassung
  config_flow.py  Setup (User/Pass/mliid/Historie) + Re-Auth
  __init__.py     Setup des Config Entry, eigene aiohttp-Session mit Cookie-Jar
```

**E1 – `api.py` ohne HA-Abhängigkeit.** Damit lassen sich Parsing und Protokoll
ohne HA-Testumgebung testen (`tests/conftest.py` lädt die Datei direkt). Diese
Trennung bitte beibehalten.

**E2 – Eigene ClientSession mit `aiohttp.CookieJar()`.** Die geteilte HA-Session
eignet sich nicht für Cookie-basierte Logins. Deshalb
`async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())`.

**E3 – External Statistics statt Sensor mit `state_class`.** Die Werte gehören zu
vergangenen Zeitpunkten und kommen verzögert. Ein Sensor-Zustand würde sie dem
Abrufzeitpunkt zuordnen. Deshalb `async_add_external_statistics` mit den echten
Zeitstempeln. Statistik-IDs: `simplygreen:netzbezug_<mliid>` und
`simplygreen:epex_<mliid>` (die Quelle muss der Domain entsprechen).

**E4 – Nur stündliche Statistiken.** HA importiert ausschließlich volle Stunden
(Start mit Minute 0). Die 15-min-Werte werden deshalb zu Stunden summiert,
Preise gemittelt. Die volle Auflösung gibt es nur in den Sensor-Attributen.

**E5 – Re-Import der letzten 3 Tage.** HA aktualisiert beim Import vorhandene
Stunden (`_statistics_exists` → Update). Der Lauf beginnt 3 Tage vor der letzten
importierten Stunde. Die Summe setzt an `_sum_before(erste Stunde)` an, der
letzten Statistikzeile davor (Suchfenster 90 Tage). Folge: Korrekturen des
Versorgers werden übernommen, und die Summe bleibt durchgängig.

> **Bekannte Schwäche:** Eine Korrektur in Stunde X ändert die Summe aller späteren
> Stunden. Das ist korrekt, weil alle späteren Stunden im selben Lauf neu geschrieben
> werden. Lücken älter als 90 Tage vor dem Fenster setzen die Summe aber auf 0
> zurück. Das ist im Normalbetrieb irrelevant.

**E6 – Zeitumstellung über die Tageslänge.** Ist ein lokaler Tag vollständig
(92/96/100 Werte), werden die Intervalle **fortlaufend ab lokaler Mitternacht (UTC)**
vergeben. Das ist eindeutig, unabhängig davon, wie das Portal die doppelte Stunde
kodiert. Unvollständige Tage (z. B. heute) werden einfach per Wanduhrzeit
umgerechnet. Ein erster Ansatz mit `fold`-Erkennung war fehlerhaft, weil 03:00 nicht
mehrdeutig ist und dadurch falsch zugeordnet wurde. Der Test deckt das ab.

**E7 – Metadaten für HA ≥ 2025.x.** `mean_type` (`StatisticMeanType`) und
`unit_class` werden gesetzt. Ohne diese Felder bricht der Import laut
`report_usage` in **HA 2026.11**. Fallback auf `has_mean` bei älteren Versionen.
Abgeglichen mit dem Quellcode von HA **2026.9.2**
(`recorder/statistics.py`, `models/statistics.py`).

**E8 – Preise in ct/kWh** (Portal: EUR/MWh, ÷10). Das ist näher an Tarif und
Rechnung.

**E9 – Große Attribute nicht aufzeichnen.** `intervals`/`epex_ct` stehen in
`_unrecorded_attributes`. Sonst würde die Datenbank bei jeder Aktualisierung rund
200 Werte zusätzlich speichern.

**E10 – Login-Erfolg indirekt prüfen** (Abschnitt 2.1). Das Antwortformat ist
unbekannt, und so ist es robuster.

## 5. Tests

| Datei | Prüft |
|---|---|
| `tests/test_parsing.py` | Zeitstempel-Semantik an echten Daten, Stundensumme (0,308 kWh), Zeitumstellung (100 Intervalle, lückenlos), unvollständiger Tag, CSRF-Regex |
| `tests/test_client.py` | Login, abgelehntes Passwort, `mliid`-Erkennung, Abruf, Neu-Login nach abgelaufener Session, gegen einen lokalen aiohttp-Mock |

**Nicht getestet:** `coordinator.py`, `sensor.py`, `config_flow.py`. Das bräuchte
`pytest-homeassistant-custom-component`. Kompiliert sind sie, die API-Signaturen
sind von Hand gegen HA 2026.9.2 geprüft. Zusätzlich lief die Integration in einer
Docker-HA (`docker-compose.yml`): Setup, Abruf und Statistik-Import ohne Fehler im Log.

## 6. Roadmap / offene Punkte (priorisiert)

1. **Kosten-Statistik:** `simplygreen:kosten_<mliid>` in € je Stunde =
   Σ(kWh × (EPEX + Aufschlag)). Der Aufschlag (Netzentgelte, Umlagen, Steuern,
   Marge) kommt per **Options Flow** in ct/kWh, ggf. mit MwSt.-Faktor. Damit lässt
   sich die Statistik als `stat_cost` im Energie-Dashboard nutzen.
2. **Options Flow** für Historie, Aufschlag und Abrufintervall (derzeit nur über
   die Daten des Config Entry).
3. **ResOpt3 (Tag) prüfen** und gegebenenfalls zur Plausibilisierung nutzen (Summe
   der Tageswerte gegen die Summe der 15-min-Werte).
4. **Zeitumstellung 25.10.2026 verifizieren** (A6) und die echte Antwort als
   Fixture ablegen.
5. **Tests für den Coordinator** mit `pytest-homeassistant-custom-component`
   (Summenfortschreibung, Re-Import, Tageszusammenfassung).
6. **`diagnostics.py`** mit geschwärzten Zugangsdaten und letzter Rohantwort,
   hilfreich für Issues.
7. Optional: Preisvorschau. Das Portal liefert nur vergangene EPEX-Werte. Für die
   Zukunft gibt es eigene Integrationen (z. B. EPEX Spot, Nordpool), das bleibt
   außerhalb des Umfangs.
8. Optional: HACS-Default-Repository und Brands-Eintrag (`ignore: brands` in CI
   entfernen).

## 7. Sicherheit

- Beim Reverse Engineering wurden curl-Befehle mit **gültigen Session-Cookies und
  CSRF-Tokens** kopiert. Diese Sessions sollten per Logout ungültig gemacht sein.
  **Niemals HAR-Dateien oder curl-Dumps committen** (`*.har` steht in `.gitignore`).
- Die Fixture ist anonymisiert (`mliid`, Marktlokations-ID). Echte Antworten vor dem
  Einchecken genauso behandeln.
- Benutzername/E-Mail und Passwort nie loggen.
