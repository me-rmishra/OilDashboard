# Downstream Oil Dashboard
Crude, crack spreads, GRM proxy, EIA inventories/refinery utilisation and energy headlines.
- Local: run `run.bat` -> http://localhost:8765 (live data, standard-library Python only).
- Hosted: GitHub Actions refreshes `docs/data/*.json` every 30 min on weekdays; GitHub Pages serves `docs/`.
- Optional: add repo secret `EIA_API_KEY` (free at eia.gov/opendata) instead of the shared DEMO_KEY.
Prices are delayed futures (Yahoo Finance); the GRM is a simple proxy, not Singapore/company GRM.
