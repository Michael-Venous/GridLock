# Data review — 2026-09-26

The source of truth for reviewed additions and metadata is `data/research.json`. The importer merges this with the sponsor workbook into `data/projects.json`. Source/page references and coordinate methods appear per project in the app and `evidence.html`.

## Completed

- Checked all five starter DESC project names, IDs and target dates against the supplied 2024–2028 PDF: pages 14, 31, 23, 1 and 10 respectively. Recorded published total estimates. The similarly named page 15 project is a different phase (6809 G), not a correction to starter DESC_1 (6809 E).
- Found the official SCRTP 2026–2030 plan via its publication page. Updated DESC_3 from 2025-12-31 to 2026-12-01 and retained the previous target. Updated its cost basis to $19,280,474 on PDF page 12. The source itself has malformed comma placement in the previous-spend figure, so only its explicitly printed total is used.
- Added Okatie substation expansion (0139 M,N), PDF page 11, and Stevens Creek–Graniteville (6809 T), page 49. Both use one already-named workbook endpoint, following the guide's single-known-point fallback. Project identities/dates are documented; coordinates are still low-confidence. Dataset is 12 projects, nine pairs; three existing projects remain unmatched.
- Added a direct public Dominion siting-map reference for Jasper/Okatie. It corroborates project geography in broad terms but is not treated as exact-coordinate verification.
- Found differing McIntosh coordinates in GPC_2 and GPC_3 and disclosed the discrepancy without silently choosing one.

## Unresolved

- Independent coordinate corroboration remains incomplete for all records. Two public Overpass endpoints were attempted: the main endpoint could not connect and the alternate timed out. No confidence was upgraded because of those failures.
- The supplied Georgia Power file is titled PUBLIC DISCLOSURE and contains redactions but also retains CEII/confidential headers. To respect the challenge's public-only constraint, this update adds no project details from those pages; the five original GPC records remain explicitly attributed to the sponsor workbook. Public source clarification is useful before expanding GPC.
- Published status is filing-era status, not a verified live update. Absence from a newer plan does not prove a project completed.
- Several dates in the newer DESC document are invalid calendar dates (e.g. April 31). They were not imported. A future automated extractor must quarantine these for human review.

## Impact calculation

The model uses a documented DESC total budget, an assumed shareable fraction, assumed low/high avoidance rates, and additional coordination expense. It is a sensitivity scenario only. It excludes GPC spend and does not identify an actual shared staging yard. No savings rate, site feasibility, quote, or construction overlap has been established. Negative net amounts are retained. Print and text briefs include the assumptions and caveats.

Public sources:
- https://www.scrtp.com/
- https://www.scrtp.com/assets/pdfs/home/2026-2030-2million-and-above-project-descriptions.pdf
- https://www.dominionenergy.com/-/media/content/about/power-line-projects/jasper-okatie-riverport/pdfs/jor-route-options-street-map.pdf
