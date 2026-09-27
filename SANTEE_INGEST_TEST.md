# Santee Cooper parser-generation demo

All Santee Cooper filings and parser registrations are removed for the demo. DESC has three filing editions and Georgia ITS has two: **five filings from two plan sources, 309 projects**. Importing Santee adds the third plan source, not a fourth company. Georgia ITS is a joint plan for Georgia Power, Georgia Transmission Corporation, MEAG Power, and Dalton Utilities.

From **Pairs → Add public filing**, enter:

- Public PDF URL: https://www.scrtp.com/assets/pdfs/meeting-archives/scrtp-meeting-2026-03-11-presentation.pdf
- Utility in this PDF: `Santee Cooper`
- Filing date: `2026-03-11`
- Edition: `2026-2030`

Alternatively, choose **Upload PDF**, select the downloaded presentation, and enter the same public URL to enable registration. Start a fresh preview. With no registered Santee parser, the agent identifies the filing and generates a parser. Expect approximately 12 projects, but review the actual extracted records and date conflicts against the PDF. Register and rebuild only after review. With one Santee edition, there is no Santee historical comparison in Changes yet.

The default agent model is `us.openai.gpt-6-sol` through Bedrock Converse, using provider-default reasoning. Explicit Claude cache points are omitted for OpenAI requests. Restart the local ingestion server to load this change; an explicit `GRIDLOCK_BEDROCK_MODEL` override takes precedence. Live GPT-6 access still needs verification with active AWS credentials.

To repeat the demo, run `python3 scripts/demo_reset.py reset` from the repository root. It backs up the current registry and generated data under `data/build/demo-resets/`, unregisters **all** Santee filings and parsers, and rebuilds offline. `python3 scripts/demo_reset.py restore` restores the latest reset backup. Raw PDFs and parser source files remain for recovery, but the importer only selects registered parsers; it cannot reuse the unregistered built-in parser. The build still imports that module for source-quality notes.
