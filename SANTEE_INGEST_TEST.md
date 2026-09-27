# Temporary Santee Cooper import test

This branch temporarily unregisters Santee Cooper's shared parser and its three historical filings. DESC and Georgia remain active. The generated app data has 309 projects. The original 321-project dataset and Santee history are on `main`.

In the local importer (**Method → Preview a public filing**), enter:

- Public PDF URL: https://www.scrtp.com/assets/pdfs/meeting-archives/scrtp-meeting-2026-03-11-presentation.pdf
- Utility in this PDF: `Santee Cooper`
- Filing date: `2026-03-11`
- Edition: `2026-2030`

Preview should find 12 Santee projects through AI parser generation. Check the extracted records and the two conflicting-date notices against the PDF before registering. Registration is a separate action that rebuilds the local data. With only one Santee edition re-added, Changes has no Santee before/after comparison; restoring its older editions is required for that history.

This branch is for the import demo. Its baseline-specific matching and published-deck tests expect the full dataset on `main` and therefore fail here by design. Switch back to `main` for the complete dataset and the passing baseline checks. A local pre-test backup is at `/home/mike/Documents/code/gridlock-ingest-test-backup-2026-09-27/`.
