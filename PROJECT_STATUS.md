# Project status

Last reviewed: 2026-09-26. The goal is a GridLock entry for the Sperry Tech challenge using only public planning data. The repository is private.

## Consolidated build

- Current `main` features and the `codex/gridlock-review-fixes` code have been reconciled. The app retains the ground-condition layers and Changes tab while gaining the review branch's search, navigation, evidence-scoring and brief fixes.
- Current filings provide 309 projects: 54 DESC and 255 Georgia ITS. All 309 internal IDs are unique. 277 projects are mapped and 32 are unplaced. The strict sponsor rule finds 72 qualifying cross-utility pairs; 71 appear by default because one pair has both published target dates in the past. Forty-five pairs have planning-window overlap still ahead as of the dataset date, 2026-09-26.
- Search includes projects with no partner or no usable location. A selected project shows its ranked partners. Pair details distinguish qualifying, possible and unverified leads; filters and selection can be shared by URL. Printable briefs include a self-contained schematic map, planning-forum links, source references and a conditional staging-yard scenario.
- Ground screening covers 29 projects with suitable station-level locations and traces. The map layers and pair briefs retain FEMA, NWI, critical-habitat and PAD-US context. The Changes tab compares five filed editions through three change events and can filter to a drawn area.

## Evidence and limits

- The qualifying distance remains center-to-center, under 25 miles. A score only ranks qualifying leads; the 100-point core is proximity (primary) and timing (secondary), matching the two signals the challenge itself defines. Nearby endpoints and estimated OSM routes are explicitly unverified and are never scored as a confirmed shared worksite. The prior confirmed-site/verified-route citation bonus was removed on 2026-09-27: neither the challenge brief nor the sponsor's own reference workbook has any such concept, and it registered zero for every one of the 309 records, so it was dead weight rather than a rare, real bonus.
- 163 endpoints use town-level estimates. 13 projects have estimated OSM route geometry, but none is a verified construction corridor. Locations, plans, target dates and cost savings require utility confirmation before action. Passed target dates are marked as status-unconfirmed.
- The cost scenario is an editable illustration, not an estimate of actual savings. It is suppressed when the projects do not qualify or when their planning windows do not overlap ahead. Ground layers are screening context, not field surveys or permit decisions. No CEII or non-public data is used.

## Verification

- `npm test` passes five JavaScript test files. `python3 -m unittest discover -s tests -v` passes 31 Python cases. Syntax and Git whitespace checks pass.
- The consolidated app loads the 309-project dataset and ranked list in a local browser. Rebuild reproducibility and full interaction/print regression should be repeated before deployment.

## UI review follow-through

- Consolidated pair sharing under Share pair, clarified the filtered-pairs CSV export, and hid pair exports on other views. Pair summaries now avoid repeating the detailed distance and timing explanations retained in briefs.
- Cost scenarios lead with their result and collapse editable assumptions. Changes leads with filings and places area filtering in a disclosure. Removed developer rebuild instructions from that planner view, the Data quality navigation badge, and the always-expanded sponsor sample audit.
- Verified disclosure controls, sharing-menu keyboard behavior, export visibility, and recalculation after changing and restoring a cost assumption in the browser. JavaScript tests (five files), Python tests (31 cases), syntax checks and whitespace checks passed.
- Scoring changes were explicitly deferred. The latest 60-point proximity / 40-point timing base and separate confirmed-site / verified-route bonuses are implemented, but all 72 qualifying pairs receive zero bonus: no project has a reviewed worksite record or verified route. The build currently has no persistent reviewed-worksite import path. Keep evidence gates; consider one sourced shared-site example with persistent overrides, and defer verified-route expansion for the demo.
- 2026-09-27: removed the confirmed-site/verified-route bonus entirely (`hasEvidence`, `sharedStations`, `verifiedRoute`, `closestApproachMiles` in `src/match.js`; the map's solid-route branch; the brief map's route drawing; the scoring UI's bonus block; the CSV's two evidence columns), after checking the original challenge zip: neither `ShellHacks_Challenge_Gridlock.docx` nor the sponsor's own `Projects_Overlaps.xlsx` answer key has any concept of a confirmed worksite or verified route — overlap is defined there purely by distance and timing. The score is now a clean 100 = proximity (60) + timing (40), matching the brief 1:1. `nearbyEndpoints` (an honest, unconfirmed "lead to investigate") is unchanged.

## Next actions

1. Verify stations, project scope and construction windows for the highest-ranked pairs with the public filings and utility teams. Improve the 32 unplaced projects and the town-level endpoints without treating guessed routes as verified corridors.
2. Run a full desktop/phone and printable-brief check, then deploy an HTTPS demo with internet access for the basemap. Verify judge access and complete the demo video and submission checklist. Confirm the event deadline directly.
3. Consider additional utilities, truck routing and richer cost estimates only after the evidence and demo are reliable.
