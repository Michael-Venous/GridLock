# Project status

Reviewed 2026-09-27 against the merged code, `data/projects.json` (analysis date 2026-09-26), and the current matching code. GridLock is a private-repository entry for the Sperry Tech Gridlock Challenge using public planning data.

Documentation audit on 2026-09-27 aligned the README, this tracker, the leading-pair research note, and the in-app Method/intro copy with the current dataset and code. The current rank-2 Rice Hope placement is recorded as a demo limitation rather than a verified site.

## Current build

- The latest public editions contain **321 projects**: 54 DESC, 12 Santee Cooper, and 255 Georgia ITS. **294** have comparison points and **27** are unplaced. Under the challenge's center-distance rule, **95** South Carolina–Georgia pairs qualify (<25 mi); **93** show by default because two have both target dates in the past. **45** qualifying pairs have inferred planning-window overlap after the analysis date. Counts were recalculated from `data/projects.json` using `src/match.js` on 2026-09-27.
- The interactive map, ranked list, pair comparison, Changes, Data quality, ground layers, coordinate editor, CSV, shortlist, and printable brief are implemented. Users can filter by South Carolina utility. Changes compares three editions per utility; adding a filing to `data/filings.json` and rerunning the pipeline is the reviewed refresh path.
- The 100-point review score is proximity (60) and timing (40), with a 0.85 multiplier for location-sensitive pairs. The former confirmed-site/verified-route bonus was **removed**; estimated routes and nearby network endpoints do not prove a shared worksite.
- The Cost scenario is an optional, conditional example for pairs with future planning overlap. Inputs come first; reference rates are cited, while yard needs and shared-site feasibility are assumptions. No-overlap pairs show their dates without a savings figure.
- Browser coordinate edits require a source note and uncertainty radius, recalculate pairs, and stay local to that browser. They do not alter published filings or Changes. Edited projects lose precomputed route and ground screening until rebuilt and reviewed.
- The ingest-agent branch is integrated with a plan/parser registry. All eight current filings use deterministic built-in parsers; the committed snapshot does not depend on an LLM. The optional CLI can search public links, identify a new document with Bedrock, draft a sandboxed parser, and propose a registry update. Novel-format registration remains experimental and should follow a reviewed dry run. Generated parsers require working bubblewrap for registration or production rebuilds.

## Evidence and limits

- Map dots are comparison points: a mapped endpoint, a midpoint/average of endpoints, a town estimate, or a location inferred from stations named in a description. They are not verified construction sites. Twenty-seven projects remain unplaced; 126 endpoints use town-level estimates. Ground screening is available for 33 projects and is context, not a field survey or permit decision. Twelve traced routes are inferred from OSM lines and are not verified construction corridors.
- DESC planning windows begin in the first budget year with spending; Georgia uses published Start and Need dates. Santee Cooper supplies target in-service dates but no comparable build windows. Actual crew, outage, access, and sharing arrangements still require utility confirmation.
- The manual review in [TOP_THREE_PAIR_RESEARCH.md](TOP_THREE_PAIR_RESEARCH.md) identifies a key demo risk: DESC Deerfield remains unlocated. Current rank 2, Georgia TEAMS 20989, is placed near a McIntosh station named in its description because Rice Hope itself is unlocated. TEAMS 20065 is placed using a longer named line than the filed Goshen–Georgia-Pacific work section. Do not present these calculated distances as verified construction-site separation.
- Only public-disclosure filings and public map services are used. No restricted CEII is intentionally included. Redacted Georgia costs are not reconstructed; a DESC-based benchmark is shown for scale when a line length is available.

## Verification

- 2026-09-27: The ingest merge rebuilt all eight downloaded public filings offline: 321 projects, 294 located, 95 qualifying cross-state pairs, 93 visible by default, 45 with future planning-window overlap, and 33 ground-screened projects. All eight PDFs matched exactly one parser signature. Six JavaScript test files and 160 Python tests passed (one skipped); the ground-screening cross-state regression test also passed after the full run. A live Bedrock run and a full desktop/phone/print browser pass remain unverified.

## Next actions

1. Correct or explicitly qualify the highest-ranked uncertain locations, especially Rice Hope, Deerfield, and the TEAMS 20065 work section. Demonstrate a defensible lead rather than treating several pairs involving the same DESC project as independent opportunities.
2. Run a full browser and printable-brief regression, then deploy an HTTPS demo with working map tiles and confirm judge access.
3. On a host with Bedrock access and bubblewrap, dry-run one genuinely new public filing, inspect the extracted records against its PDF, then decide whether to register it. Keep README, Method, and demo claims aligned with each reviewed snapshot.
