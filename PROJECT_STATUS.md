# Project status

Last updated: 2026-09-26

Goal: a usable, evidence-led GridLock entry for the Sperry Tech challenge (ShellHacks 2026). GitHub issue #12 records Sun 2026-09-27 11:00 EDT as the submission deadline; check the event dashboard before submission.

## Review and implementation baseline

- The latest fetched `main` and `nick-main` both pointed to `8b0762a`. The user explicitly approved proceeding with that latest available code on 2026-09-26.
- Review fixes are on `codex/gridlock-review-fixes`; `main` has not been merged or deployed by this task. GitHub issues #21-26 were opened for the specific previously untracked review defects.
- The original challenge rule is unchanged: cross-state centers strictly under 25 miles by haversine; absolute in-service date gap. The six sponsor fixture pairs still reproduce exactly.

## Current implemented state

- 309 unique records, 277 mapped projects, 32 unplaced. Full dataset: 72 qualifying pairs (60 robust / 12 sensitive) and 11 possible leads; default hide-past filter displays 71 qualifying pairs. No mapped project has location confidence `none`.
- Source parsing preserves station names, handles work-description prefixes and directional distinctions, and flags zero-endpoint extraction. Geocoder evidence names the actual selected candidate. Ambiguous printed IDs now have separate internal IDs.
- The 13 remaining OSM traces are explicitly unverified; four town/road-based traces were removed. Only routes with source-backed circuit evidence may appear as verified routes or earn corridor points.
- A common network endpoint is an investigation lead, not proof of a shared worksite. Shared-site ranking points require sourced construction scope for both projects. Inferred planning windows and illustrative savings remain clearly conditional.
- Universal project search includes unmatched and unlocated records. Pair/project selection opens details on narrow screens, Back to results preserves position, keyboard focus is restored and detail tabs support arrow/Home/End navigation.
- Combined filters use a consistent distance policy: qualifying centers must meet the selected cutoff; explicitly enabled possible leads use their minimum distance across location uncertainty. The UI labels that distinction.
- The app uses the dataset's `generated` date consistently. Counts explain dataset versus visible scope. Shareable URL state restores selection and filters without exposing local shortlist state.
- Screen, copied text and printable briefs share scenario eligibility. Nonqualifying and zero-duration cases show no modeled saving. Briefs include a self-contained SVG location overview and source-checked SCRTP/SERTP contact links with conditional transition wording.

## Validation

- 29 JavaScript regressions pass, including sponsor fixtures, source-backed ranking, geometry intersections, scenario eligibility, URL state, project search and SVG geometry/escaping.
- 17 Python pipeline regressions pass. The downloaded public source editions reproduced the original committed JSON byte for byte; the fixed pipeline then produced identical output on two successive offline builds.
- 18 Chromium desktop/mobile and print checks pass. Keyboard and touch-size navigation, exact list-position restoration, standalone search, duplicate IDs, combined filters and URL/shortlist persistence were verified. Qualifying and possible brief examples each print to one unclipped Letter page. Tests used a 412 × 915 mobile viewport, not a physical phone.

## Remaining work

- #3 / #10: review unresolved or town-level station placements and actual work scopes. Manual overrides now support explicit confidence/radius, but Hooks, Fenwick Street, Sand Bar Ferry and Rice Hope were not assigned unverified coordinates.
- #12: verify the real judge URL/deployment, video and Devpost submission. No deployment or submission is implied by these local tests.
- #19: optional LLM-suggested names remain a separate, source-verifiable enrichment step. Deterministic parser fixes already improve coverage.
- #1 / #11 / #13-16: optional routing, extra utility coverage, shared inventory, Maps API/environmental layers and cloud alerts remain future scope. Existing satellite/Maps links and source history are available; broad versions are not implemented.

## Evidence and handoff

- Previous review: `../gridlock-review-2026-09-26/` (challenge, code, usability and all original issue details).
- Current update bundle: `../gridlock-update-2026-09-26/` (before/after metrics, created-issue URLs, regression results, browser evidence, screenshots and concise PDF).
- Public raw inputs remain ignored under `data/raw/`. No CEII, non-public utility data or redacted-cost reconstruction is used.
