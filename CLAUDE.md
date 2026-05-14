# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup
python -m venv .venv
.venv\Scripts\activate          # Windows PowerShell
pip install -r requirements.txt

# Run apps
streamlit run main.py           # Staff version (with map and case list)
streamlit run app_simple.py     # Customer-facing version (no map/case list)

# Utility scripts
python filter_3years.py                        # Filter CSV data to recent 5 years in-place
python scripts/geocode_seiyaku_full.py         # Batch geocode all addresses in the CSV
python clean_csv.py                            # Clean/normalize the CSV data
```

## Architecture Overview

Two Streamlit entry points share a common business logic module:

```
main.py          ← Staff internal tool (map, case list, sidebar filters)
app_simple.py    ← Customer-facing HP tool (compact UI, no map/case list)
valuation_core.py ← All shared business logic (imported by both)
app.py           ← Legacy prototype using the national API (deprecated)
```

**valuation_core.py** is the core module. It contains:
- Geocoding (GSI API primary → geopy Nominatim fallback)
- CSV loading and parsing (`load_data`, `_load_case_from_row`)
- Distance filtering (`filter_csv_by_distance`, `haversine_distance`)
- Valuation computation (`compute_valuation`, `_compute_valuation_detached`)
- Outlier-robust averaging (`_compute_robust_average` — IQR-based)
- Price trend chart generation (Plotly)
- PDF report generation (ReportLab + `ipaexg.ttf`)
- Webhook dispatch (`send_inquiry_to_webhook`)

Both `main.py` and `app_simple.py` duplicate much of the code from `valuation_core.py` (they were developed independently). `valuation_core.py` is the canonical, most up-to-date version of that logic.

## Data

The primary data source is a local CSV:  
`data/seiyaku_20260321_10year_date.csv`

CSV schema (key columns): `address`, `contract_date`, `type`, `contract_price`, `land_area`, `building_area`, `floor_area`, `construction_year`, `latitude`, `longitude`

Geocoordinates are **persisted back into this CSV** after being resolved, so subsequent runs skip geocoding for known addresses. The file is both input and cache — do not truncate or replace it carelessly. Backups are in `data/` with timestamp suffixes.

`data/archive/` holds reference PDFs from printed REINS reports used when building initial data.

## Valuation Logic

**Detached houses (中古住宅（戸建て）)**: Land price × area × plot correction + building residual value. Building evaluation is 0 for buildings ≥ 35 years old (≥ 44 years = pre-Showa 56 = absolute zero). Land unit price is derived from nearby land transactions or old detached house transactions as a fallback.

**Condos (中古マンション)**: Exclusive area × IQR-robust average unit price × plot correction.

**Land (土地)**: Land area × robust average unit price × 1.20 markup (to convert from transaction data to asking-price basis) × plot correction.

Plot corrections (`kakuti_rate`): corner lot +5%, road width (< 4m: −10%, 4–6m: −5%, ≥ 8m: +3%), frontage width (< 4m: −15%, 4–8m: −5%, ≥ 15m: +5%).

## Secrets and Environment Variables

Configured via `.streamlit/secrets.toml` (not committed) or environment variables:

| Key | Purpose |
|-----|---------|
| `WEBHOOK_URL` | POST destination for appraisal results (Google Chat, Zapier, etc.) |
| `OPENAI_API_KEY` | Optional — enables AI-based correction advice instead of rule-based |
| `STREAMLIT_SHOW_MAP_AND_CASE_LIST` | `0`/`false` to hide map in `main.py` |

See `.streamlit/secrets.toml.example` for the format.

## Deployment

- **Streamlit Community Cloud**: push to GitHub, connect repo. Secrets go in the dashboard.
- **Docker / Render**: `Dockerfile` runs `app_simple.py` on port 8080. See `docs/Renderデプロイ手順_詳細.md`.
- **GitHub Actions keep-alive**: `.github/workflows/` contains scheduled workflows that were used to prevent Streamlit Community Cloud from sleeping. Currently disabled (`keepalive.yml` is the active file; the schedule is commented out after upgrading to a paid plan).

## Japanese Font

`ipaexg.ttf` is bundled in the project root and used by both ReportLab (PDF generation) and matplotlib (map plots). The font loader in `valuation_core.py:_get_reportlab_japanese_font()` checks this path first, then falls back to system fonts.
