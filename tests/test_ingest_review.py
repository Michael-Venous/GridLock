"""The model-written parser is frozen between preview and registration.

This uses the existing Acme PDF-text and parser fixtures. The PDF itself is a
minimal local byte fixture because pdf_pages is patched to return that text.
The Bedrock conversation is scripted, and no network or live model is used.
"""

import contextlib
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bedrock  # noqa: E402
import ingest  # noqa: E402
import registry  # noqa: E402
import sandbox  # noqa: E402
from test_ingest import GOOD, PAGES  # noqa: E402
from test_ingest_flow import IDENTITY, reply, session as fake_session  # noqa: E402


class ReviewedParserTests(unittest.TestCase):
    def test_registration_reuses_reviewed_parser_and_refuses_changed_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "raw").mkdir()
            (root / "filings.json").write_text(json.dumps({"about": "test", "plans": {}, "parsers": {},
                                                             "watch": {}, "filings": []}))
            source = root / "acme.pdf"
            source.write_bytes(b"%PDF-1.4\nAcme test fixture\n")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            work = root / "work" / digest[:12]
            url = "https://example.org/acme-plan.pdf"
            pages = list(PAGES)
            pages[0] = ("Acme Power Company\n" +
                        "Planning narrative without project markers. " * 20 + "\n" + pages[0])
            model = fake_session([
                reply(("identify", IDENTITY)),
                reply(("submit", {"code": GOOD, "signature": ["Acme Power Company"],
                                  "count_pattern": r"Project \S+", "notes": "one entry per project"})),
            ])
            flags = ["--url", url, "--date", "2026-05-01", "--edition", "2027", "--no-build"]

            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(ingest, "ROOT", root))
                stack.enter_context(mock.patch.object(ingest, "RAW", root / "raw"))
                stack.enter_context(mock.patch.object(ingest, "WORK", root / "work"))
                stack.enter_context(mock.patch.object(ingest, "pdf_pages", return_value=pages))
                stack.enter_context(mock.patch.object(ingest, "pdfinfo", return_value={}))
                stack.enter_context(mock.patch.object(ingest, "confirm_url", return_value=None))
                stack.enter_context(mock.patch.object(ingest, "log", return_value=None))
                stack.enter_context(mock.patch.object(registry, "PATH", root / "filings.json"))
                stack.enter_context(mock.patch.object(registry, "LOCK", root / "registry.lock"))
                stack.enter_context(mock.patch.object(registry, "PARSER_DIR", root / "parsers"))
                stack.enter_context(mock.patch.object(sandbox, "require_bubblewrap", return_value=None))
                stack.enter_context(mock.patch.dict(os.environ, {sandbox.UNISOLATED: "1"}))
                stack.enter_context(mock.patch.object(bedrock, "Session", return_value=model))

                preview = ingest.main([str(source), *flags, "--dry-run"])
                self.assertTrue((work / "review.json").is_file())
                self.assertTrue((work / "parser.py").is_file())
                self.assertTrue((work / "records.json").is_file())
                self.assertEqual(preview["reviewedParserSha256"],
                                 hashlib.sha256((work / "parser.py").read_bytes()).hexdigest())
                self.assertEqual(json.loads((work / "review.json").read_text())["reviewedParserSha256"],
                                 preview["reviewedParserSha256"])
                self.assertEqual(registry.load_registry()["filings"], [])
                self.assertEqual(len(model.client.calls), 2)

                register_args = [str(work / "source.pdf"), *flags,
                                 "--reviewed-parser-sha256", preview["reviewedParserSha256"]]
                original = {name: (work / name).read_bytes()
                            for name in ("source.pdf", "parser.py", "records.json")}
                with mock.patch.object(bedrock, "Session", side_effect=AssertionError("Bedrock called during registration")):
                    for name in original:
                        artifact = work / name
                        if name == "records.json":
                            changed = json.loads(original[name])
                            changed[0]["name"] = "Changed after review"
                            artifact.write_text(json.dumps(changed))
                        else:
                            artifact.write_bytes(original[name] + b"\nchanged after review")
                        try:
                            with self.subTest(artifact=name), self.assertRaises(SystemExit):
                                ingest.main(register_args)
                            self.assertEqual(registry.load_registry()["filings"], [])
                        finally:
                            artifact.write_bytes(original[name])

                    registered = ingest.main(register_args)

                self.assertEqual(registered["sha256"], digest)
                self.assertEqual((root / "parsers" / "acme.py").read_bytes(), original["parser.py"])
                self.assertEqual((root / "raw" / "acme_2027.pdf").read_bytes(), source.read_bytes())
                self.assertEqual(len(registry.load_registry()["filings"]), 1)
                self.assertEqual(len(model.client.calls), 2, "registration must make no model calls")


if __name__ == "__main__":
    unittest.main()
