# Hand-off: ingest agent branch (`ingest-agent`)

Written 2026-09-27. This branch is based on `30b7d2e` and is **not merged into `main`**. `origin/main` has since gained 8 commits, including the built-in Santee Cooper parser (`d7b8388`), local endpoint editing (`53b1894`), cost-scenario layout (`47dd48e`) and doc alignment (`8221fb7`). A trial merge conflicts in 12 files; see "Merging into main" below.

## What this branch adds

**1. The ingest agent** (`pipeline/ingest.py`). It takes a PDF and turns it into registered data.
- **Inputs.** A URL, a local file, or `--find "Company"`, which searches the company's websites for its list.
- **Routing.** If a registered parser's signature matches, that parser reads the PDF with no model involved. Otherwise Claude on Amazon Bedrock:
  1. says whose list it is;
  2. tries that company's parsers;
  3. writes a new parser when none reads it.
- **Sandbox.** Agent-written parsers run in `pipeline/sandbox.py`: AST allowlist, bubblewrap, rlimits, no network or credentials.
- **Checks.** Host-side checks in `pipeline/generated_parser.py` decide whether a parser is registered:
  - every value grounded in its project's own entry;
  - record count vs a per-project marker;
  - cost units stated on the page;
  - at least half the records dated;
  - shape compared with the previous edition.
- **Registration.** A parser that passes is saved in `pipeline/parsers/`, the filing is registered, and the data rebuilt.
- **Files.**
  - `bedrock.py`: Converse client with model fallback.
  - `parser_agent.py`: the parser-writing loop.
  - `finder.py`: finds a company's PDF from typed addresses or links on fetched pages, with no search engines; downloads are DNS-pinned to public IPs.
  - `registry.py`: the `data/filings.json` registry of plans, parsers and filings.

**2. Any number of plans.** `build.py`, `changes.py` and the app no longer assume DESC and Georgia. Colors, legend, bounds, labels and edition text come from `plans[]` in `data/projects.json`.

**3. Data fixes.**
- `parse_ga` reads a Table 2 sponsor from the row's last cell, which gives 3 Dalton rows their zones.
- Planning-zone outliers use a data-driven spread rule.
- Repeated lineages stay unique.
- 13 cached Nominatim lookups were added.

## Running it

```bash
python3 -m unittest discover -s tests     # 141 pass
npm test                                   # 48 pass
python3 pipeline/build.py --offline        # 309 projects, 284 located (pre-Santee data)

pip install -r requirements-agent.txt      # boto3, agent only
AWS_PROFILE=<your profile> python3 pipeline/ingest.py URL --dry-run
AWS_PROFILE=<your profile> python3 pipeline/ingest.py --find "Santee Cooper" --dry-run
python3 pipeline/ingest.py DECK.pdf --company "Santee Cooper" --dry-run   # several utilities in one PDF
```

- **AWS credentials.** Use your own credentials with Bedrock access. The `gridlock` profile on Nick's machine is a temporary Workshop Studio role, so it can't be shared.
- **Models.** The agent tries Opus 5.5 first; that account can't use it ("private marketplace eligibility"), so it falls back to Opus 5. Set `GRIDLOCK_BEDROCK_MODEL` to override.
- **Other flags.**
  - `--date` is required when neither the PDF nor its metadata gives a date.
  - `--skip-plan` works only with `--dry-run`.
  - `GRIDLOCK_ALLOW_UNISOLATED=1` is needed on hosts without bubblewrap.
- **Usage log.** Each run appends its Bedrock usage to `data/build/ingest/usage.jsonl`.

## Results so far (dry runs, nothing registered)

| Test | Result | Bedrock calls / tokens (in / out / cache read / cache write) |
|---|---|---|
| DESC 2026–2030, own parser hidden | 54/54 identical in every field | 5 turns |
| Georgia 2026–2035, own parser hidden | 255/255 identical | 7 turns |
| Santee Cooper 2025 IRP update | 23 records, 17 dated | 7 calls; 10.6k / 13.1k / 107.7k / 37.7k |
| SCRTP 2026-03-11 deck, `--company "Santee Cooper"` | 12 records; now fails the Cross–Jefferies date check (a known parser defect) | 12 calls; 3.1k / 37.1k / 222.5k / 50.1k |
| `--find "Santee Cooper"` | proposed the SCRTP deck | — |

## Not finished

1. **Agent prompt out of date.** The checks in `generated_parser.py` were tightened (entry-level grounding, count pairing, `count_scope`), but three things still need updating:
   - `parser_agent.py` SYSTEM still describes the old rules;
   - the `run_parser` and `submit` tools don't pass `count_scope` yet;
   - `ingest.py` doesn't store `countScope`.

   Until this is fixed, drafts fail on rules the agent wasn't told. The exact wording is in the review notes; ask Nick.
2. **Small review leftovers:**
   - `common.dump` should use `allow_nan=False`;
   - signature regexes in `registry.signature_matches` still run outside the sandbox (use `sandbox.find`);
   - `generated_parser.checked_against` orders by date, not edition;
   - `fetch.py` doesn't check sha256;
   - the `changes.py` docstring still says "date order".
3. **Second round of build/app fixes.** These were in progress on Nick's machine and are *not* in this branch:
   - build side: zones for dropped records per edition, and tie-breaking repeats by in-service date;
   - Georgia Table 2: `parse_ga` row robustness, and Southern Company's sponsor code;
   - app side: per-state USDA pasture values, a color-distance check, and the fit-all zoom limit.
4. **Docs.**
   - The README ingest section doesn't cover `--find`/`--company`, the finder's link rule, `--date`/`--skip-plan`, `usage.jsonl` or `sha256`.
   - README line 61 still describes the removed +20/+10 bonus.
   - The README Limits numbers are stale: the data shows 25 unplaced and 30 screened.
   - `PROJECT_STATUS.md` doesn't mention the agent.
5. **Known limits.** The finder can't use search engines, so it needs a findable company site. A second printing that a document omits can't be detected.

## Merging into main

`origin/main` added Santee Cooper by hand, using the old registry shape: filings carry `utility`, with no `plans`/`parsers` entries. It also has Santee-specific code: `cur_santee` in `build.py`, `SCPSA` naming in `changes.py`/`geocode.py`, and the `sc-utility` filter and `SC_UTILITIES` in `app.js`/`index.html`.

To merge:
1. **Registry.** Give Santee a `plans.santee` entry (owner Santee Cooper, state SC, own-name aliases) and a builtin `parsers.santee` entry with a signature specific to the SCRTP "Transmission Projects YYYY-YYYY" slide. The DESC signature must not match SCRTP decks.
2. **Hand-coded plan lists.** Replace the three-way `desc + santee + ga` code in `build.py` with this branch's per-plan loop.
3. **Pairing rule.** Main pairs Santee only with Georgia, not with DESC. This branch pairs across plans. Pick one: pairing only across states keeps main's behavior without hardcoding.
4. **Keep main's app work:** the SC utility filter, endpoint editing, cost-scenario inputs and the Santee "no build window" reason.
5. **Tests.** Some ingest tests use Santee Cooper as the example of an unregistered company, so check that they use a scratch registry.
6. **Data.** Take the union of `data/cache/nominatim.json`. Regenerate `projects.json`/`changes.json` with `build.py --offline`; don't hand-merge them. The Santee PDFs are in `../GridLock-santee/data/raw`.
