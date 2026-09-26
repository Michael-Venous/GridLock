# GridLock prototype

An interactive planning aid for finding nearby project pairs across Dominion Energy South Carolina (DESC) and Georgia Power (GPC). Click a project to see its qualifying matches, or click a match to inspect distance, in-service dates, and source caveats.

## Run locally

No package installation or API key is required. From this directory:

```bash
python -m http.server 8000
```

Open `http://localhost:8000` in a browser. The app is static and can be hosted on any static web host. Google Fonts is optional; the system font fallback works offline.

## Data and matching rule

`data/projects.json` contains the ten projects in the challenge's `Projects_Overlaps.xlsx` starter workbook (five per utility). The supplied PDFs are:

- `Project Listings/Dominion Energy/2024-2028-2million-and-above-project-descriptions.pdf`
- `Project Listings/Georgia Power/2025 IRP Volume 3 PUBLIC DISCLOSURE.pdf`

The prototype recalculates all cross-utility pairs from project center coordinates using the haversine straight-line distance. A pair qualifies only when its centers are **less than 25 miles** apart. Results rank by distance, then by the absolute gap between published in-service dates. The date gap is only a clue; it does not prove construction windows overlap. When a workbook endpoint lacks coordinates, the provided project center is used as supplied. The workbook's coordinates have not been independently verified, and map lines connecting matched projects are visual links, not proposed routes.

The map shows the supplied geographic coordinates on an interactive latitude/longitude grid. Named places are orientation labels, not a surveyed basemap. The app has project selection, pair selection, search, distance and timing filters, zoom/pan, and CSV export of the current ranked list. It does not make a savings claim because there is no supported cost basis in the starter package.

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
