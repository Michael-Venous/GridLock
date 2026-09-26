# Project status

Last updated: 2026-09-26

Goal: Build a usable GridLock prototype for the Sperry Tech challenge and keep its GitHub repository private until the user requests otherwise.

Completed locally: Static map explorer, starter workbook import, six recalculated pair matches, ranked list, project and pair inspection, filters, pan/zoom, CSV export, source caveats, and tests. Browser interaction verified on the local server.

Decision: The downloaded `Finding_Real_Locations_Guide.docx` controls the matching rule: project center points, straight-line distance under 25 miles, and absolute difference between in-service dates. Do not infer construction window overlap from those dates.

Data limits: The ten starter coordinates were imported from `Projects_Overlaps.xlsx` and not independently verified. Several target dates have passed. There is no supported savings estimate yet. The app labels these limits.

Repository: `git@github.com:Michael-Venous/GridLock.git` is private. The user created it, and `main` was pushed on 2026-09-26. Keep it private per the user's latest instruction. The local `gh` token remains invalid, but SSH Git push succeeded.

Next product steps: Verify individual locations against public planning PDFs and map sources; obtain actual construction windows; add a sourced impact estimate if time permits.
