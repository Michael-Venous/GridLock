# Santee filing import demo

Run `python3 scripts/demo_reset.py reset` from the repository root. It backs up the current dataset and prepares a baseline containing Santee's **2024–2028 and 2025–2029 filings**, using the built-in `parse_santee` parser shared by these editions. The **2026–2030 filing is not registered**. Existing geographic caches are retained.

The first setup, or a change to build inputs, rebuilds offline. Subsequent resets restore a hash-verified baseline snapshot. `python3 scripts/demo_reset.py restore` restores the previous dataset. The older PDFs must be present in `data/raw/`; their public source URLs are in `scripts/demo_santee_baseline.json`.

Start `python3 local_server.py --port 8765`. In **Pairs → Add public filing**, choose **Public PDF link**:

- URL: https://www.scrtp.com/assets/pdfs/meeting-archives/scrtp-meeting-2026-03-11-presentation.pdf
- Utility to import: **Santee Cooper**
- Filing date: **2026-03-11**
- Edition: **2026-2030**

Preview downloads the public PDF and uses the preset parser. **No AWS credentials or AI parser generation are needed for this recognized layout.** Expect 12 projects; review source-date warnings and records before selecting **Register and rebuild**. Wait for **Local dataset rebuilt**, then **Reload Seamline**. Changes can now compare 2026–2030 against 2025–2029.

This demo demonstrates importing a new public filing with an existing parser, not generating a parser from scratch. Unknown layouts can still use the configured AI workflow (Opus 5 by default). No total completion-time guarantee is claimed.

## Second demo: fresh AI parser generation

Run `python3 scripts/demo_reset.py reset-ai`. This removes **all Santee filings and parser registrations**, retaining geographic caches and using a separate clean snapshot from the preset demo. The same public URL and Santee Cooper selection now trigger fresh Opus 5 parser generation. Relevant pages are supplied upfront. AWS credentials and boto3 are required. Opus 5 is the code default; clear an old model override before starting the server. This mode has no prior Santee edition for historical comparisons and no fixed completion-time guarantee. Run `reset` again to return to the preset-parser baseline, or `restore` to undo the last reset.
