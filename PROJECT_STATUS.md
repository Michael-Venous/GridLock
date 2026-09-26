# Project status

Last updated: 2026-09-26

Goal: Build and publish a usable public GridLock prototype for the Sperry Tech challenge.

Completed locally: Static map explorer, starter workbook import, six recalculated pair matches, ranked list, project and pair inspection, filters, pan/zoom, CSV export, source caveats, and tests. Browser interaction verified on the local server.

Decision: The downloaded `Finding_Real_Locations_Guide.docx` controls the matching rule: project center points, straight-line distance under 25 miles, and absolute difference between in-service dates. Do not infer construction window overlap from those dates.

Data limits: The ten starter coordinates were imported from `Projects_Overlaps.xlsx` and not independently verified. Several target dates have passed. There is no supported savings estimate yet. The app labels these limits.

Pending: Create a public GitHub repository and push `main`. The GitHub connector can read the signed-in profile but has no repository-creation operation; the browser is signed out and the local `gh` token is invalid. After the user signs in through the browser or refreshes `gh` authentication, create `gridlock-prototype` as a public repo, add `origin`, and push `main`.

Next product steps: Verify individual locations against public planning PDFs and map sources; obtain actual construction windows; add a sourced impact estimate if time permits.
