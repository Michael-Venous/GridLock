# Project status

Last updated: 2026-09-26

Goal: a working GridLock entry for the Sperry Tech challenge (ShellHacks 2026), submitted before Sun 2026-09-27 11:00 EDT. `nick-main` is the primary branch; `origin/main` holds an older build of the 10-project prototype and is not being merged.

## What the app does now

- Parses every project in the current public filings: DESC 2026–2030 (54 projects) and the 2025 GA ITS Ten-Year Plan 2026–2035 (255 projects). 268 are located; 17 lines are traced along OSM power lines.
- Keeps the challenge's rule unchanged: project centers (midpoint of located endpoints) under 25 miles apart by haversine, time gap = absolute difference of in-service dates. The tests still reproduce the sponsor's six example rows exactly.
- Adds, for ranking only: build-window overlap (DESC first budget year with spend → in-service date; Georgia Start Date → Need Date; only overlap from today on counts), location certainty (robust / sensitive / possible), shared stations and closest approach of traced lines. Score = proximity 40 + shared station 20 + line proximity 10 + timing 30, × 0.85 when location-sensitive.
- MapLibre GL JS map, ranked pair list with filters and sorts, pair comparison, staging-yard cost scenario with cited unit costs (MISO, USDA), printable briefs, shortlist, CSV export, Data quality and Method tabs.

## Decisions

- The challenge's `Finding_Real_Locations_Guide.docx` controls the qualifying rule, and nothing added changes which pairs qualify.
- Build windows are inferred from the filings' own start dates and yearly budgets. (The early prototype deliberately avoided inferring them from in-service dates alone; that decision is superseded now that the full filings are parsed.)
- Geography outweighs timing in the score (70 vs 30 points), as the challenge makes distance the primary signal.
- No CEII, no non-public data, no reconstruction of Georgia's redacted costs.

## Data limits

- Locations are estimates with stated method, confidence and radius; 41 projects could not be placed (4 of them unplaced because their names matched sites far outside their planning zone) and 162 endpoints rest on town-level guesses (issue #3).
- Many published target dates have passed while the filings still list the projects as planned; the app marks their status as unconfirmed.
- The cost scenario is a reason to make a call, not a budget.

## Repository

- `github.com/Michael-Venous/GridLock`, private. Work lands on `nick-main`.
- Supplied and downloaded PDFs and the workbook stay out of git (`data/raw/` is ignored); `pipeline/fetch.py` downloads the public ones.

## Open work (GitHub issues)

- #3 station-name extraction; #4 routes traced to town guesses; #6 "none" confidence on placed projects; #7 pipeline vs app "today".
- #10 hand-site Hooks, Fenwick Street, Sand Bar Ferry, Rice Hope; #11 Santee Cooper's Purrysburg–McIntosh reconductor (optional).
- #12 submission checklist: deploy, demo video, Devpost.
