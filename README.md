# GridLock prototype

An interactive planning aid for finding nearby project pairs across Dominion Energy South Carolina (DESC) and Georgia Power (GPC). Click a project to see its qualifying matches, or click a match to inspect distance, in-service dates, and source caveats.

## Run locally

No package installation or API key is required. From this directory:

```bash
python -m http.server 8000
```

Open `http://localhost:8000` in a browser. The app is static and can be hosted on any static web host. Google Fonts is optional; the system font fallback works offline.

## Data and matching rule

`data/projects.json` contains the ten sponsor workbook records plus two DESC projects from the official 2026–2030 plan. Reviewed metadata and date revisions live in `data/research.json` and are merged by the importer. There are twelve projects and nine qualifying pairs; three projects have no candidates. The supplied PDFs are:

- `Project Listings/Dominion Energy/2024-2028-2million-and-above-project-descriptions.pdf`
- `Project Listings/Georgia Power/2025 IRP Volume 3 PUBLIC DISCLOSURE.pdf`

The prototype recalculates all cross-utility pairs from project center coordinates using the haversine straight-line distance. A pair qualifies only when its centers are **less than 25 miles** apart. Results rank by distance, then by the absolute gap between published in-service dates. The date gap is only a clue; it does not prove construction windows overlap. When a workbook endpoint lacks coordinates, the provided project center is used as supplied. The workbook's coordinates have not been independently verified, and map lines connecting matched projects are visual links, not proposed routes.

The map uses OpenStreetMap raster tiles with attribution and a latitude/longitude grid fallback if tiles are unavailable. Project markers and map tiles share the same Web Mercator projection. Internet access is needed for the basemap. The workspace fills the browser window on desktop, and project markers keep a constant on-screen size during zoom. The app has project selection, pair selection, search, an **in-service through year** slider, the original date-gap and distance filters, zoom/pan, and CSV export of the current ranked list. The year slider includes projects with published target years on or before the selected year; it does not indicate when construction occurred. Several published in-service dates are now in the past, and the app flags them without assuming the projects were completed. An editable impact scenario uses a cited DESC budget and explicitly assumed shareable/avoidance percentages. It is a sensitivity calculation, not an established savings claim; GPC spending is excluded.

To regenerate the JSON from your copy of the supplied workbook:

```bash
python scripts/import_workbook.py "/path/to/Projects_Overlaps.xlsx" data/projects.json
```

Only the small derived project table is committed. The supplied PDFs, Word files, and workbook remain outside this repository.

## Check

```bash
node --test tests/*.test.js
```

The tests verify the 25-mile boundary, pair cardinality, and date-gap arithmetic against the starter data. No backend or external service is needed to run the prototype.

## Review and demo workflow

- Selecting a pair fits the map and highlights both markers. Basemap tile resolution adjusts with zoom; marker size stays fixed.
- Review each project's source/page, coordinate method, confidence and filing status. The evidence register includes every record. Jasper–Okatie's revised target is preserved alongside the workbook date.
- Keep distance-first ranking or explore smallest date gaps. The existing gap filter remains. Reset filters and Clear selection are separate controls.
- Save pairs to a local-browser shortlist. Shortlist storage is browser-specific; it is not a shared team account.
- Edit impact assumptions, then open a printable brief carrying those exact inputs. Print/Save PDF uses the browser. Download/copy text exposes a portable text fallback if an embedded browser suppresses downloads.
- Pair links in brief return navigation reopen the selected pair. Scenario values are embedded in the brief URL; workspace scenarios otherwise last for the current session.

Read [DEMO.md](DEMO.md) for the 2–3 minute presentation and [DATA_REVIEW.md](DATA_REVIEW.md) for source findings and remaining verification gaps. Independent coordinate checks remain incomplete; all mapped records are explicitly lower-confidence. These records must not be presented as field-ready locations.
