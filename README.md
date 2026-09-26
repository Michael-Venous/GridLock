# GridLock

Finds planned transmission projects on either side of the Savannah River that should be coordinated: Dominion Energy South Carolina (DESC) and the Georgia Integrated Transmission System (Georgia Power, GTC, MEAG, Dalton). It covers every project in the latest public filings, not just a sample. Each pair comes with the build windows, the shared stations, a ranking you can inspect, an illustrative savings range, and a first-call brief. Every number links back to the page it came from.

## Run it

```bash
python3 -m http.server 8000      # then open http://localhost:8000
node --test tests/*.test.js      # 10 tests, including the sponsor's six example rows
```

The app is static and has no build step, API keys or backend. The OpenStreetMap basemap needs internet access.

## Rebuild the data

```bash
python3 pipeline/build.py            # downloads the filings into data/raw/, parses, geocodes, traces lines
python3 pipeline/build.py --offline  # reuse data/raw/ and the cached OSM/Nominatim lookups
```

The pipeline uses only the Python standard library plus `pdftotext` (poppler). It writes `data/projects.json`, the only file the app reads.

| Source | What we take | Used for |
|---|---|---|
| [DESC $2M+ list 2026–2030](https://www.scrtp.com/assets/pdfs/home/2026-2030-2million-and-above-project-descriptions.pdf) (SCRTP) | 54 projects: ID, status, in-service date, yearly budget, description | Projects, costs, build windows |
| DESC lists 2024–2028 (the challenge zip) and 2025–2029 | The same fields | Schedule history (slip) only |
| [2025 GA ITS Ten-Year Plan (2026–2035)](https://services.psc.ga.gov/api/v1/External/Public/Get/Document/DownloadFile/225600/106866), GA PSC docket 56002 #225600 | Table 2 (zone, sponsor), 255 detail pages (start date, need date, description, change from last plan); Tables 3–4 (cancelled, completed) | Projects, build windows |
| OpenStreetMap via Overpass (cached in `data/cache/`) | Named substations and plants in GA and SC; power lines near the border | Endpoint locations, traced routes |
| `data/starter_projects.json` (the sponsor's `Projects_Overlaps.xlsx`) | 10 sample projects with coordinates | Test fixture and trusted coordinates |

The zip's documents are out of date. Since they were published, Georgia cancelled Evans Primary–Thurmond Dam #5/#6, the McIntosh–Purrysburg reactors were completed, and DESC's Hooks–Thurmond rebuild dropped off its list. The Data quality tab shows what became of every sample project.

## How it decides

- **Qualifying rule (the challenge's rule, unchanged):** project centers less than 25 miles apart by haversine distance. A center is the midpoint of the located endpoints, or the single located endpoint. The time gap is the absolute difference between in-service dates.
- **Build windows:** DESC runs from its first budget year with spend to the in-service date. Georgia runs from the detail page's Start Date to its Need Date. Only overlap from today onward counts toward ranking.
- **Certainty:** every location has an uncertainty radius. *Robust* pairs stay under 25 mi even at the edges of both radii. *Sensitive* pairs qualify only at the best estimate. *Possible* pairs don't qualify but could, and are shown only on request.
- **Score (ranking only):** proximity (35) + timing (35) + shared station within 0.5 mi (20) + closest approach of traced lines (10), multiplied by 0.85 when the pair is location-sensitive.
- **Locations, in order of trust:** hand-sited points with a written reason (`data/overrides.json`), then the sponsor's coordinates, then an OSM exact or partial name match on the correct side of the state line, then a town-level fallback (±6 mi). Same-name places, such as the two Goshens 87 mi apart, are resolved against the other endpoint and the planning zone. Matches implausibly far from the rest of the project are rejected.
- **Cost (bonus):** Georgia costs are redacted. For Georgia lines that state a length, we apply a benchmark from DESC's own list: the median cost per mile of line projects whose cost rows add up. Savings are a range on the smaller project's cost (a shareable share, 4% by default and adjustable, covering mobilization, access mats, a laydown yard and traffic control) and apply only when the build windows overlap. It is an aid for deciding whether to make a call, not a budget.

## Validation

The parser flags problems instead of fixing them silently. In the 2026–2030 DESC list it found impossible dates (04/31/26, 06/31/2026), ten cost rows whose years don't sum to the printed total, two projects below the list's own $2M floor, one Project ID used by two different projects (`6809 M`), and one ID that is a prefix of another (`6367 D` / `06367 D - G`). It also flags passed in-service dates, endpoints it could not locate, and traced routes whose length disagrees with the stated mileage.

## Limits

This is a planning aid. Locations are estimates with stated confidence; 37 of 309 projects could not be placed and are listed in the Data quality tab. We used no CEII or non-public data and did not try to reconstruct redacted costs.
