# Project status

Last updated: 2026-09-26

Goal: Build a usable GridLock prototype for the Sperry Tech challenge and keep its GitHub repository private until the user requests otherwise.

Completed locally: Map explorer with an OpenStreetMap basemap and grid fallback, starter workbook import, six recalculated pair matches, ranked list, project and pair inspection, in-service year slider plus date-gap and distance filters, pan/zoom, CSV export, source caveats, and tests. Browser interaction verified on the local server. The planning workspace now fills the desktop viewport and map markers retain a 12 px diameter through zoom; both were verified in-browser on 2026-09-26.

Decision: The downloaded `Finding_Real_Locations_Guide.docx` controls the matching rule: project center points, straight-line distance under 25 miles, and absolute difference between in-service dates. Do not infer construction window overlap from those dates.

Date detail: The supplied PDFs list planned in-service dates as calendar dates (for example, DESC Okatie–Bluffton 06/01/2025 and Georgia Power transmission entries on June 1). Because many future entries share June 1, the year slider uses year granularity while preserving the exact published dates in project details and the existing day-gap filter.

Data limits: The ten starter coordinates were imported from `Projects_Overlaps.xlsx` and not independently verified. Several target dates have passed. There is no supported savings estimate yet. The app labels these limits.

Repository: `git@github.com:Michael-Venous/GridLock.git` is private. The user created it, and `main` was pushed on 2026-09-26. Keep it private per the user's latest instruction. The local `gh` token remains invalid, but SSH Git push succeeded.

Next product steps: Verify individual locations against public planning PDFs and map sources; obtain actual construction windows; add a sourced impact estimate if time permits.

## nick-main branch (2026-09-26)

- Replaced the 10-project sample with a pipeline over the current public filings: DESC 2026-2030 (54 projects) and the 2025 GA ITS Ten-Year Plan 2026-2035 (255 projects). 272 are located; 18 lines are traced along OSM power lines.
- Matching keeps the 25-mile center rule and adds build-window overlap (only overlap from today onward counts), location certainty, shared stations and line closest approach. The tests still reproduce the sponsor's six rows exactly.
- Added Data quality and Method tabs, a cost card that uses a DESC $/mile benchmark, a coordination brief, and a CSV export with source links.
- Supplied and downloaded PDFs stay out of git (`data/raw/` is ignored); `pipeline/fetch.py` downloads the public ones.
- Next: hand-site the remaining border endpoints (Hooks, Fenwick Street, Sand Bar Ferry, Rice Hope); optionally add Santee Cooper's Purrysburg-McIntosh tie reconductor (SCRTP 2026-03-11 deck).
