# Gridlock

Finds planned transmission projects on either side of the Savannah River that should be coordinated: Dominion Energy South Carolina (DESC) and the Georgia Integrated Transmission System (Georgia Power, GTC, MEAG, Dalton). It covers every project in the latest public filings, not just a sample. Each pair comes with a side-by-side comparison, the build windows, shared stations, what still needs confirming, a ranking you can inspect, a staging-yard cost scenario with cited unit costs, and a printable one-page brief. Planners can shortlist pairs and export all their briefs at once. Every number links back to the page it came from.

## Run it

```bash
python3 -m http.server 8000      # then open http://localhost:8000
npm test                            # browser-independent matching, view, and brief checks
python3 -m unittest discover tests   # pipeline, ground geometry, and change-log checks
```

The app is static and has no build step or API keys. The map is MapLibre GL JS (loaded from jsDelivr) over free OpenFreeMap vector tiles of OpenStreetMap data, so it needs internet access and WebGL.

## Rebuild the data

```bash
python3 pipeline/build.py            # downloads the filings into data/raw/, parses, geocodes, traces lines
python3 pipeline/build.py --offline  # reuse data/raw/ and the cached OSM/Nominatim lookups
```

The pipeline uses only the Python standard library plus `pdftotext` (poppler). Every filing it reads is listed in `data/filings.json`; the newest per utility is current and the older ones feed schedule history and the change log. It writes `data/projects.json`, `data/changes.json` and the map layers in `data/env/`, which are the only data files the app reads.

To add the next public filing, add its URL, edition, date, parser and local file name to `data/filings.json`, run `python3 pipeline/build.py`, and review validation and uncertain locations before committing the rebuilt outputs. A changed PDF format may require a parser update. The Changes tab then compares its new or revised records with the preceding edition. This is a reviewed update path, not a browser upload of arbitrary map points.

| Source | What we take | Used for |
|---|---|---|
| [DESC $2M+ list 2026–2030](https://www.scrtp.com/assets/pdfs/home/2026-2030-2million-and-above-project-descriptions.pdf) (SCRTP) | 54 projects: ID, status, in-service date, yearly budget, description | Projects, costs, build windows |
| DESC lists [2024–2028](https://www.scrtp.com/assets/pdfs/home/2024-2028-2million-and-above-project-descriptions.pdf) (also in the challenge zip) and [2025–2029](https://www.scrtp.com/assets/pdfs/home/2025-2029-2million-and-above-project-descriptions.pdf) | The same fields | Schedule history and the change log |
| [2024 GA ITS Ten-Year Plan (2025–2034)](https://services.psc.ga.gov/api/v1/External/Public/Get/Document/DownloadFile/221233/102406), 2025 IRP Technical Appendix Volume 3, GA PSC docket 56002 #221233 (also in the challenge zip) | The same fields as the current plan | Schedule history and the change log |
| [2025 GA ITS Ten-Year Plan (2026–2035)](https://services.psc.ga.gov/api/v1/External/Public/Get/Document/DownloadFile/225600/106866), GA PSC docket 56002 #225600 | Table 2 (zone, sponsor), 255 detail pages (start date, need date, description, change from last plan); Tables 3–4 (cancelled, completed) | Projects, build windows |
| OpenStreetMap via Overpass (cached in `data/cache/`) | Named substations and plants in GA and SC; power lines near the border | Endpoint locations, traced routes |
| `data/starter_projects.json` (the sponsor's `Projects_Overlaps.xlsx`) | 10 sample projects with coordinates | Test fixture and trusted coordinates |
| Federal map services (cached in `data/cache/environment.json`): [USFWS National Wetlands Inventory](https://www.fws.gov/program/national-wetlands-inventory), [FEMA National Flood Hazard Layer](https://www.fema.gov/flood-maps/national-flood-hazard-layer), [USFWS](https://ecos.fws.gov/ecp/report/table/critical-habitat.html) and [NOAA Fisheries](https://www.fisheries.noaa.gov/national/endangered-species-conservation/critical-habitat) critical habitat, [USGS PAD-US 4.1](https://www.usgs.gov/programs/gap-analysis-project/science/pad-us-data-overview) | Wetlands and open water, flood zones, critical habitat, protected lands | Ground at the work sites, map layers |

The zip's documents are out of date. Since they were published, Georgia cancelled Evans Primary–Thurmond Dam #5/#6, the McIntosh–Purrysburg reactors were completed, and DESC's Hooks–Thurmond rebuild dropped off its list. The Data quality tab shows what became of every sample project.

## How it decides

- **Qualifying rule (the challenge's rule, unchanged):** project centers less than 25 miles apart by haversine distance. A center is the midpoint of the located endpoints, or the single located endpoint. The time gap is the absolute difference between in-service dates.
- **Build windows:** DESC runs from its first budget year with spend to the in-service date. Georgia runs from the detail page's Start Date to its Need Date. Only overlap from today onward counts toward ranking.
- **Certainty:** every location has an uncertainty radius. *Robust* pairs stay under 25 mi even at the edges of both radii. *Sensitive* pairs qualify only at the best estimate. *Possible* pairs don't qualify but could, and are shown only on request.
- **Score (ranking only):** the challenge makes geography the primary signal and timing a strong secondary one, so the score is proximity (40) + confirmed common worksite (20) + verified construction-corridor proximity (10) + timing (30), multiplied by 0.85 when the pair is location-sensitive. Nearby network endpoints and estimated routes are shown as leads, but earn no worksite or corridor bonus without source-backed evidence. The current dataset has no verified common worksites or construction corridors, so these two bonuses are zero for every pair. The list can also be sorted by distance, by closest in-service dates, or by build overlap still ahead.
- **Records:** each project shows its published target date separately from its status in the filing. When the target date has passed, the status is marked unconfirmed. Each project also shows its type, how its map point was set (midpoint of two located endpoints, or one known endpoint with a wider radius), its location confidence and its source page.
- **Locations, in order of trust:** hand-sited points with a written reason (`data/overrides.json`), then the sponsor's coordinates, then an OSM exact or partial name match on the correct side of the state line, then a town-level fallback (±6 mi). Same-name places, such as the two Goshens 87 mi apart, are resolved against the other endpoint and the planning zone. Matches implausibly far from the rest of the project are rejected. Printed DESC IDs are retained for display; repeated IDs get unique internal IDs.
- **Impact scenario (bonus):** for a pair building at the same time, one shared staging yard instead of two. One yard = surface (acres × $/acre) + lease (acres × land value × rate × months) + access road (miles × $/mile). Cited unit costs: timber mats $69,975/acre and access road $593,636/mile ([MISO MTEP24 cost guide](https://cdn.misoenergy.org/20240501%20PSC%20Item%2004%20MISO%20Transmission%20Cost%20Estimation%20Guide%20for%20MTEP24632680.pdf), pp. 19 and 23), and pasture land at $5,100/acre in GA and $4,500/acre in SC ([USDA Land Values 2026](https://www.nass.usda.gov/Publications/Todays_Reports/reports/land0726.pdf), p. 15). Yard size, months, lease rate and road length are labeled assumptions that can be edited. A combined yard is assumed to be 1.0–1.5× one yard, so sharing avoids 0.5–1.0 of a yard. Proximity alone can't prove sharing is possible.
- **Ground at the work sites:** for projects that could appear in a pair, every endpoint placed at a station (not a town guess) is checked within 0.25 mi: the FEMA flood zone at the station, the share of land in the 1% annual-chance flood area, the share mapped as wetland or open water, and any critical habitat or protected land. Lines traced between two such stations are measured along their length. The pair panel, brief and CSV state what is mapped; the map shows the outlines inside the checked areas and hands over to the agencies' full maps when zoomed in. These are mapped conditions, not a survey or a permit decision, and they don't change which pairs qualify or how they score. When a station sits outside the 1% flood area, the cost scenario notes that the floodplain mat rate may overstate the surface cost.
- **Changes between filings:** each new edition is compared with the one before it. Projects are matched on their ID (for DESC, which reuses IDs, also on a similar name), and the Changes tab lists what was added, dropped (with Georgia's own reason from its Tables 3 and 4), rescheduled, renamed or re-costed, plus qualifying pairs that appeared, went away or moved by 30 days or more. Planners can draw their area and see only what touches it.
- **Cost for scale:** Georgia costs are redacted. For Georgia lines that state a length, we apply a benchmark from DESC's own list: the median cost per mile of line projects whose cost rows add up. The scenario is compared against the smaller project's cost for scale. It is an aid for deciding whether to make a call, not a budget.

## Validation

The parser flags problems instead of fixing them silently. In the 2026–2030 DESC list it found impossible dates (04/31/26, 06/31/2026), ten cost rows whose years don't sum to the printed total, two projects below the list's own $2M floor, one Project ID used by two different projects (`6809 M`), and one ID that is a prefix of another (`6367 D` / `06367 D - G`). It also flags passed in-service dates, endpoints it could not locate, and traced routes whose length disagrees with the stated mileage.

## Limits

This is a planning aid. Locations are estimates with stated confidence; 32 of 309 projects could not be placed and are listed in the Data quality tab. Another 163 endpoints use town-level estimates. Ground screening is available for 29 projects with station-level locations; the other locations are not screened. We used no CEII or non-public data and did not try to reconstruct redacted costs.
