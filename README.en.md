# SimplyGreen Load Profile for Home Assistant

Custom integration that fetches the **consumption values reported by the metering
point operator** (15-minute load profile) and the matching **EPEX spot prices** from the
customer portal [mein.simplygreen.de](https://mein.simplygreen.de) and stores them in
Home Assistant as long-term statistics.

SimplyGreen is the new name of **ENTEGA Ökostrom dynamisch** (formerly the portal
`meineems.entega.de`). There is no official API. The integration uses the same AJAX
calls as the portal's web interface.

> **Status: draft / v0.1.0.** Login, fetching and parsing are tested against a mock
> that replicates the behavior observed in the browser. The integration has not yet
> been run against the real portal. See [docs/HANDOVER.md](docs/HANDOVER.md),
> section "Unverifizierte Annahmen" (unverified assumptions).

## What the integration provides

| What | ID | Content |
|---|---|---|
| Statistic | `simplygreen:netzbezug_<mliid>` | Grid consumption in kWh, hourly, with a running sum (`has_sum`). Usable in the statistics graph and as a grid consumption source in the Energy dashboard. |
| Statistic | `simplygreen:epex_<mliid>` | EPEX day-ahead price in ct/kWh, hourly mean/min/max |
| Sensor | `sensor.simplygreen_netzbezug_letzter_tag` | kWh of the last **complete** day; attributes see below |
| Sensor | `sensor.simplygreen_letzter_messwert` | Timestamp (interval end) of the most recent value delivered |

Attributes of *Netzbezug letzter Tag* (grid consumption, last day):

- `day`: date of the day
- `intervals`: `[[start_iso_local, kWh], …]`, 96 values (92/100 on days with a DST change)
- `epex_ct`: `[[start_iso_local, ct/kWh], …]`
- `avg_price_ct`: consumption-weighted EPEX average of the day

`intervals` and `epex_ct` are **not** written to the recorder database
(`_unrecorded_attributes`).

**Why hourly?** Home Assistant stores long-term statistics only at hourly resolution.
The 15-minute resolution is therefore only available through the sensor attributes,
e.g. for a `custom:plotly-graph` chart (example below).

## Installation

**Manual:** Copy `custom_components/simplygreen` to `/config/custom_components/` and
restart Home Assistant.

**HACS (custom repository):** HACS → ⋮ → Custom repositories → URL of this repo,
category *Integration*.

Then: *Settings → Devices & Services → Add Integration → SimplyGreen Lastgang*

| Field | Meaning |
|---|---|
| Username / Password | Credentials for mein.simplygreen.de |
| Metering location ID (`mliid`) | Optional; determined after login via the portal call `getMetLines`. If that fails: in the browser's network tab → request `getLoadProfile` → parameter `mliids`. |
| History | Days to backfill on first start (default 30) |

## How it works (short version)

- Fetches every **3 hours**. Values arrive with a delay, usually on the following day.
  Therefore each run **re-imports the last 3 days**. Hours that already exist are
  overwritten, so corrections delivered later end up in the statistics automatically.
- On the first run, the configured number of days is backfilled in blocks of 7 days.
- The running sum continues from the last existing hour before the import window.
- If the portal session expires, the integration logs in again once. If the password
  is rejected, HA's re-auth flow starts.

The design decisions, the observed portal protocol and open items are documented in
**[docs/HANDOVER.md](docs/HANDOVER.md)**.

## Dashboard examples

Hourly comparison of your own measurement with the utility's data (built-in HA cards):

```yaml
type: statistics-graph
title: Netzbezug – HA vs. Versorger
chart_type: bar
period: hour
days_to_show: 3
stat_types: [change]
entities:
  - entity: sensor.strom_energy          # own meter reading head (example)
    name: HA measurement
  - entity: simplygreen:netzbezug_<mliid>
    name: Utility
```

15-minute load profile of the last day with EPEX price (requires
[plotly-graph](https://github.com/dbuezas/lovelace-plotly-graph-card)):

```yaml
type: custom:plotly-graph
title: Load profile last day (15 min)
config:
  scrollZoom: false
entities:
  - entity: sensor.simplygreen_netzbezug_letzter_tag
    name: Grid consumption
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

> The plotly-graph example is untested. Please check the `fn` filter API (`meta` =
> attributes of the current state) against the plotly-graph documentation.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

`api.py` deliberately contains **no** Home Assistant imports. The tests load it
directly and therefore don't need HA. CI (`.github/workflows/validate.yml`) runs
`hassfest`, the HACS validation and the tests.

Before the first push, replace the placeholder `OWNER` in
`custom_components/simplygreen/manifest.json` with your GitHub name (`documentation`,
`issue_tracker`, `codeowners`).

## Notes

- Unofficial integration, not affiliated with SimplyGreen/ENTEGA. Changes to the
  portal can break it at any time.
- Credentials are stored in Home Assistant's config entry (`.storage/core.config_entries`).
