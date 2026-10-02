# CLAUDE.md

Home-Assistant-Custom-Integration `simplygreen`: holt den 15-min-Lastgang und die
EPEX-Preise aus dem inoffiziellen Kundenportal mein.simplygreen.de und importiert sie
als External Statistics.

**Vor jeder Änderung `docs/HANDOVER.md` lesen.** Dort stehen das beobachtete
Portal-Protokoll, die unverifizierten Annahmen (A1–A8), die Entwurfsentscheidungen
(E1–E10) und die Roadmap.

## Befehle

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Regeln

- `custom_components/simplygreen/api.py` bleibt frei von `homeassistant`-Imports (E1);
  Protokoll- und Parse-Logik gehört dorthin und wird in `tests/` ohne HA getestet.
- Zeitstempel des Portals = Intervall-ENDE in lokaler Wanduhrzeit als UTC-Epoch (HANDOVER 2.3).
  Änderungen an `to_intervals` nur mit Test, inkl. Zeitumstellungstag.
- Statistik-Import nur volle Stunden, tz-aware UTC; Metadaten mit `mean_type` und `unit_class` (E7).
- Statistik-IDs `simplygreen:netzbezug_<mliid>` / `simplygreen:epex_<mliid>` nicht umbenennen –
  das verwaist die Historie der Nutzer.
- Niemals Zugangsdaten, Cookies, CSRF-Tokens, echte mliid/Marktlokations-IDs loggen oder
  committen. Neue Fixtures anonymisieren.
- Ziel-HA-Version: 2026.9+. API-Fragen gegen den HA-Quellcode des passenden Tags prüfen,
  nicht aus dem Gedächtnis.
- Nach einer bestätigten Annahme (A1–A8) den Status in `docs/HANDOVER.md` aktualisieren.
