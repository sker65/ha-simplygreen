# SimplyGreen Lastgang für Home Assistant

Custom Integration, die die **vom Messstellenbetreiber gemeldeten Verbrauchswerte**
(15-Minuten-Lastgang) und die zugehörigen **EPEX-Spotpreise** aus dem Kundenportal
[mein.simplygreen.de](https://mein.simplygreen.de) abruft und in Home Assistant als
Langzeitstatistik ablegt.

SimplyGreen ist der neue Name von **ENTEGA Ökostrom dynamisch** (früheres Portal
`meineems.entega.de`). Eine offizielle API gibt es nicht. Die Integration nutzt
dieselben AJAX-Aufrufe wie die Weboberfläche des Portals.

> **Status: Entwurf / v0.1.0.** Login, Abruf und Parsing sind gegen einen Mock
> getestet, der das im Browser beobachtete Verhalten nachbildet. Gegen das
> echte Portal ist die Integration noch nicht gelaufen. Siehe
> [docs/HANDOVER.md](docs/HANDOVER.md), Abschnitt „Unverifizierte Annahmen“.

## Was die Integration liefert

| Was | ID | Inhalt |
|---|---|---|
| Statistik | `simplygreen:netzbezug_<mliid>` | Netzbezug in kWh, stündlich, mit fortlaufender Summe (`has_sum`). Nutzbar im Statistik-Diagramm und als Netzbezugsquelle im Energie-Dashboard. |
| Statistik | `simplygreen:epex_<mliid>` | EPEX-Day-Ahead-Preis in ct/kWh, Stundenmittel/-min/-max |
| Sensor | `sensor.simplygreen_netzbezug_letzter_tag` | kWh des letzten **vollständigen** Tages; Attribute siehe unten |
| Sensor | `sensor.simplygreen_letzter_messwert` | Zeitpunkt (Intervallende) des jüngsten gelieferten Werts |

Attribute von *Netzbezug letzter Tag*:

- `day`: Datum des Tages
- `intervals`: `[[start_iso_lokal, kWh], …]`, 96 Werte (92/100 an Tagen mit Zeitumstellung)
- `epex_ct`: `[[start_iso_lokal, ct/kWh], …]`
- `avg_price_ct`: verbrauchsgewichteter EPEX-Durchschnitt des Tages

`intervals` und `epex_ct` werden **nicht** in die Recorder-Datenbank geschrieben
(`_unrecorded_attributes`).

**Warum stündlich?** Home Assistant speichert Langzeitstatistiken nur stündlich.
Die 15-Minuten-Auflösung gibt es deshalb nur über die Sensor-Attribute, z. B. für
ein `custom:plotly-graph`-Diagramm (Beispiel unten).

## Installation

**Manuell:** `custom_components/simplygreen` nach `/config/custom_components/`
kopieren, Home Assistant neu starten.

**HACS (benutzerdefiniertes Repository):** HACS → ⋮ → Benutzerdefinierte Repositories
→ URL dieses Repos, Kategorie *Integration*.

Danach: *Einstellungen → Geräte & Dienste → Integration hinzufügen → SimplyGreen Lastgang*

| Feld | Bedeutung |
|---|---|
| Benutzername / Passwort | Zugangsdaten für mein.simplygreen.de |
| Messlokations-ID (`mliid`) | optional; wird nach dem Login über den Portal-Aufruf `getMetLines` ermittelt. Falls das fehlschlägt: im Browser Netzwerk-Tab → Request `getLoadProfile` → Parameter `mliids`. |
| Historie | Tage, die beim ersten Start nachgeladen werden (Standard 30) |

## Funktionsweise (Kurzfassung)

- Abruf alle **3 Stunden**. Die Werte kommen verzögert, meist am Folgetag. Deshalb
  werden bei jedem Lauf die **letzten 3 Tage neu importiert**. Bereits vorhandene
  Stunden werden dabei überschrieben, nachgelieferte Korrekturen landen so automatisch
  in der Statistik.
- Beim ersten Lauf werden die eingestellten Tage in 7-Tages-Blöcken nachgeladen.
- Die fortlaufende Summe setzt an der letzten vorhandenen Stunde vor dem Importfenster an.
- Läuft die Portal-Session ab, meldet sich die Integration einmal neu an. Bei
  abgelehntem Passwort startet der Re-Auth-Flow von HA.

Die Entwurfsentscheidungen, das beobachtete Portal-Protokoll und offene Punkte
stehen in **[docs/HANDOVER.md](docs/HANDOVER.md)**.

## Dashboard-Beispiele

Stündlicher Vergleich der eigenen Messung mit den Versorgerdaten (HA-Bordmittel):

```yaml
type: statistics-graph
title: Netzbezug – HA vs. Versorger
chart_type: bar
period: hour
days_to_show: 3
stat_types: [change]
entities:
  - entity: sensor.strom_energy          # eigener Zähler-Lesekopf (Beispiel)
    name: HA-Messung
  - entity: simplygreen:netzbezug_<mliid>
    name: Versorger
```

15-Minuten-Lastgang des letzten Tages mit EPEX-Preis (benötigt
[plotly-graph](https://github.com/dbuezas/lovelace-plotly-graph-card)):

```yaml
type: custom:plotly-graph
title: Lastgang letzter Tag (15 min)
config:
  scrollZoom: false
entities:
  - entity: sensor.simplygreen_netzbezug_letzter_tag
    name: Netzbezug
    type: bar
    unit_of_measurement: kWh
    filters:
      - fn: |
          ({ meta }) => ({
            xs: meta.intervals.map(r => new Date(r[0])),
            ys: meta.intervals.map(r => r[1]),
          })
  - entity: sensor.simplygreen_netzbezug_letzter_tag
    name: EPEX
    yaxis: y2
    unit_of_measurement: ct/kWh
    line: { shape: hv }
    filters:
      - fn: |
          ({ meta }) => ({
            xs: meta.epex_ct.map(r => new Date(r[0])),
            ys: meta.epex_ct.map(r => r[1]),
          })
hours_to_show: 48
```

> Das plotly-graph-Beispiel ist ungetestet. Die `fn`-Filter-API (`meta` =
> Attribute des aktuellen Zustands) bitte gegen die plotly-graph-Doku prüfen.

## Entwicklung

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

`api.py` enthält bewusst **keine** Home-Assistant-Imports. Die Tests laden es
direkt und brauchen daher kein HA. CI (`.github/workflows/validate.yml`) führt
`hassfest`, die HACS-Validierung und die Tests aus.

Vor dem ersten Push in `custom_components/simplygreen/manifest.json` den Platzhalter
`OWNER` durch den GitHub-Namen ersetzen (`documentation`, `issue_tracker`, `codeowners`).

## Hinweise

- Inoffizielle Integration, keine Verbindung zu SimplyGreen/ENTEGA. Änderungen am
  Portal können sie jederzeit brechen.
- Die Zugangsdaten liegen im Config Entry von Home Assistant (`.storage/core.config_entries`).
