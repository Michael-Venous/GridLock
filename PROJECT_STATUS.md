# Project status

Last updated: 2026-09-26

Goal: Build a usable GridLock prototype for the Sperry Tech challenge and keep its GitHub repository private until the user requests otherwise.

Completed locally (2026-09-26): Twelve projects and nine candidate pairs; per-record evidence, coordinate methods, confidence and date revisions; selected-pair map fitting/highlighting; zoom-appropriate tiles with fixed-size markers; timing sort and original date-gap filter; separate reset controls; local-browser shortlist; printable coordination brief and download/copyable text; editable DESC-side impact scenario with cited cost basis and assumptions. See DEMO.md for presentation and DATA_REVIEW.md for the audit.

Decision: The downloaded `Finding_Real_Locations_Guide.docx` controls the matching rule: project center points, straight-line distance under 25 miles, and absolute difference between in-service dates. Do not infer construction window overlap from those dates.

Date detail: The supplied PDFs list planned in-service dates as calendar dates (for example, DESC Okatie–Bluffton 06/01/2025 and Georgia Power transmission entries on June 1). Because many future entries share June 1, the year slider uses year granularity while preserving the exact published dates in project details and the existing day-gap filter.

Data limits: Independent coordinates remain unverified and explicitly lower-confidence. Public Overpass checks failed to connect/time out. New DESC projects reuse named workbook endpoint coordinates. Jasper–Okatie's target and budget were updated from the public 2026–2030 plan with its previous target retained. GPC remains attributed to the supplied workbook; conflicting public-disclosure/CEII headers in the supplied appendix are documented in DATA_REVIEW.md. The impact range is a hypothetical budget sensitivity, not confirmed savings.

Repository: `git@github.com:Michael-Venous/GridLock.git` is private. The user created it, and `main` was pushed on 2026-09-26. Keep it private per the user's latest instruction. The local `gh` token remains invalid, but SSH Git push succeeded.

Requirements review (2026-09-26): Re-read both downloaded challenge documents against the current implementation. The two explicit deliverables (interactive cross-utility map and ranked opportunities) are implemented. Per-project source evidence and explicit lower-confidence classification are now implemented; actual location corroboration remains open. Workbook ingestion is implemented; live PDF ingestion, a third utility, advanced scoring, driving distance, and actual construction windows are not baseline requirements. The guide accepts center distance under 25 miles plus in-service gap in days. Current distance-first ranking uses time only to break exact distance ties; the UI labels this and provides a separate optional timing sort.

Verification: Matching and impact tests passed; dataset regenerates identically from workbook plus research overlays. Browser verified timing sort, gap filter (five pairs within one year), filter reset, pair zoom, shortlist, edited assumptions transferred to brief, and copyable text with source URLs. Invalid percentages disable brief generation. Native download automation did not return a completion event in the embedded browser; the copyable-text fallback was verified. Native print dialog completion is unverified. Mobile results retain a 230 px scrollable list; Queensboro correctly shows zero matches.

Next research: Corroborate coordinates, resolve differing McIntosh locations, obtain unambiguous public GPC source confirmation, and replace impact assumptions with actual sharing feasibility and quotes. Current construction windows/status remain unknown. Expand further only with adequate provenance.
