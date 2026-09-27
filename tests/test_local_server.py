"""HTTP and review-boundary checks for the loopback filing ingest interface.

The ingest subprocess is replaced here: these tests exercise the real HTTP routes,
job transitions, and the rule that registration uses the bytes shown in preview.
"""

import hashlib
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import local_server  # noqa: E402
from local_server import AppServer  # noqa: E402


class LocalServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "index.html").write_text("GridLock app")
        (self.root / "styles.css").write_text("body {}")
        (self.root / "data" / "raw").mkdir(parents=True)
        (self.root / "data" / "raw" / "private.pdf").write_bytes(b"private filing")
        self.server = AppServer(("127.0.0.1", 0), self.root)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            hdrs = {"Host": f"127.0.0.1:{self.port}"}
            if method == "POST":
                hdrs.update({"Origin": f"http://127.0.0.1:{self.port}",
                             "Content-Type": "application/json"})
            if headers:
                hdrs.update(headers)
            payload = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
            conn.request(method, path, payload, hdrs)
            response = conn.getresponse()
            raw = response.read()
            return response.status, (json.loads(raw) if response.getheader("Content-Type", "").startswith("application/json") else raw)
        finally:
            conn.close()

    def wait_job(self, ident, terminal=("succeeded", "failed", "registered")):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            code, job = self.request("GET", f"/api/ingest/jobs/{ident}")
            self.assertEqual(code, 200)
            if job["status"] in terminal:
                return job
            time.sleep(0.01)
        self.fail(f"ingest job {ident} did not reach {terminal}")

    def test_serves_only_app_assets_and_keeps_ingest_inputs_private(self):
        code, body = self.request("GET", "/")
        self.assertEqual((code, body), (200, b"GridLock app"))
        code, status = self.request("GET", "/api/ingest/status")
        self.assertEqual(code, 200)
        self.assertTrue(status["available"])
        for path in ("/data/raw/private.pdf", "/pipeline/ingest.py", "/data/build/ingest/source.pdf", "/.git/config"):
            with self.subTest(path=path):
                code, _ = self.request("GET", path)
                self.assertEqual(code, 404)
        code, _ = self.request("GET", "/", headers={"Host": "attacker.example"})
        self.assertEqual(code, 403)

    def upload(self, pdf, spec, filename="plan.pdf", origin=None):
        boundary = "gridlock-upload-test"
        data = (f'--{boundary}\r\nContent-Disposition: form-data; name="spec"\r\n\r\n'.encode()
                + json.dumps(spec).encode()
                + f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: application/pdf\r\n\r\n'.encode()
                + pdf + f'\r\n--{boundary}--\r\n'.encode())
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        if origin is not None:
            headers["Origin"] = origin
        return self.request("POST", "/api/ingest/uploads", data, headers)

    def test_upload_preview_and_registration_keep_exact_bytes(self):
        content = b"%PDF-1.4\npublic uploaded fixture\n"
        digest = hashlib.sha256(content).hexdigest()
        source_url = "https://example.org/plan.pdf"
        seen = []

        def execute(ident, argv):
            seen.append(argv)
            self.assertEqual(Path(argv[0]).read_bytes(), content)
            saved = self.root / "data/build/ingest" / digest[:12] / "source.pdf"
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(content)
            return {"sha256": digest, "url": source_url,
                    "filing": {"url": source_url, "title": "Plan", "dateBasis": "published"},
                    "shape": {"records": 1, "reviewRecords": [{"name": "Station"}]}}

        with mock.patch.object(self.server.manager, "_execute", side_effect=execute):
            code, job = self.upload(content, {"mode": "upload", "source": source_url}, "../../escape.pdf")
            self.assertEqual(code, 202)
            job = self.wait_job(job["id"])
            self.assertEqual(job["status"], "succeeded", job)
            self.assertEqual(job["preview"]["issues"], [])
            self.assertEqual(Path(seen[0][0]).parent, self.root / "data/build/uploads")
            self.assertFalse((self.root / "escape.pdf").exists())
            code, _ = self.request("GET", "/data/build/uploads/" + Path(seen[0][0]).name)
            self.assertEqual(code, 404)
            code, _ = self.request("POST", f'/api/ingest/jobs/{job["id"]}/register', {})
            self.assertEqual(code, 202)
            self.assertEqual(self.wait_job(job["id"])["status"], "registered")
            self.assertIn("--dry-run", seen[0])
            self.assertNotIn("--dry-run", seen[1])
            self.assertEqual(seen[1][seen[1].index("--url") + 1], source_url)

    def test_upload_without_url_can_preview_but_cannot_register(self):
        run = {"sha256": "a" * 64, "filing": {"title": "Plan", "dateBasis": "published"}, "shape": {"records": 1}}
        with mock.patch.object(self.server.manager, "_execute", return_value=run):
            code, job = self.upload(b"%PDF-1.4\nfixture", {"mode": "upload"})
            self.assertEqual(code, 202)
            job = self.wait_job(job["id"])
            self.assertEqual(job["status"], "succeeded")
            self.assertTrue(any("public source URL" in issue for issue in job["preview"]["issues"]))
            code, _ = self.request("POST", f'/api/ingest/jobs/{job["id"]}/register', {})
            self.assertEqual(code, 409)

    def test_upload_rejects_non_pdf_private_urls_and_cross_origin(self):
        self.assertEqual(self.upload(b"not a pdf", {"mode": "upload"})[0], 400)
        self.assertEqual(self.upload(b"%PDF-1.4", {"mode": "upload", "source": "file:///etc/passwd"})[0], 400)
        self.assertEqual(self.upload(b"%PDF-1.4", {"mode": "upload"}, origin="https://evil.example")[0], 403)
        self.assertEqual(self.request("POST", "/api/ingest/jobs", {"mode": "upload"})[0], 400)
        with mock.patch.object(local_server, "MAX_PDF_UPLOAD", 4):
            self.assertEqual(self.upload(b"%PDF-1.4", {"mode": "upload"})[0], 413)
        self.assertEqual(self.server.manager.jobs, {})

    def test_requires_local_origin_and_rejects_private_source_urls(self):
        spec = {"mode": "url", "source": "https://example.org/public-plan.pdf"}
        for origin in ("http://evil.example", ""):
            with self.subTest(origin=origin):
                code, _ = self.request("POST", "/api/ingest/jobs", spec, {"Origin": origin})
                self.assertEqual(code, 403)
        code, _ = self.request("POST", "/api/ingest/jobs", spec, {"Content-Type": "text/plain"})
        self.assertEqual(code, 415)
        for source in ("http://127.0.0.1/private.pdf", "http://10.1.2.3/plan.pdf",
                       "http://192.168.0.5/plan.pdf", "http://[::1]/plan.pdf",
                       "http://localhost/plan.pdf", "file:///tmp/plan.pdf"):
            with self.subTest(source=source):
                code, _ = self.request("POST", "/api/ingest/jobs", {"mode": "url", "source": source})
                self.assertEqual(code, 400)
        self.assertEqual(self.server.manager.jobs, {})

    def test_reviewed_pdf_is_required_for_registration_and_only_one_job_runs(self):
        content = b"%PDF-1.4\npublic fixture\n"
        digest = hashlib.sha256(content).hexdigest()
        pdf = self.root / "data" / "build" / "ingest" / digest[:12] / "source.pdf"
        url = "https://example.org/public-plan.pdf"
        first_started = threading.Event()
        release_first = threading.Event()
        seen = []

        def execute(ident, argv):
            seen.append(list(argv))
            if "--dry-run" in argv:
                first_started.set()
                self.assertTrue(release_first.wait(3))
                pdf.parent.mkdir(parents=True, exist_ok=True)
                pdf.write_bytes(content)
            else:
                self.assertEqual(Path(argv[0]), pdf)
                self.assertEqual(pdf.read_bytes(), content)
            return {"sha256": digest, "url": url,
                    "filing": {"title": "Public Plan", "edition": "2027", "date": "2026-04-01",
                               "dateBasis": "published", "url": url, "parser": "known"},
                    "shape": {"records": 3, "dated": 2, "withId": 3,
                              "reviewRecords": [{"project_id": f"P-{n}", "name": f"Project {n}", "isd": "2028-01-01"}
                                                for n in range(3)]}}

        self.server.manager._execute = execute
        code, created = self.request("POST", "/api/ingest/jobs", {"mode": "url", "source": url})
        self.assertEqual(code, 202)
        self.assertTrue(first_started.wait(2))
        code, _ = self.request("POST", "/api/ingest/jobs", {"mode": "url", "source": url})
        self.assertEqual(code, 409, "a second preview must wait for the active job")
        release_first.set()
        preview = self.wait_job(created["id"])
        self.assertEqual(preview["status"], "succeeded")
        self.assertEqual(preview["preview"]["sha256"], digest)
        self.assertEqual(preview["preview"]["records"], 3)
        self.assertTrue(preview["preview"]["recordsAvailable"])
        code, report = self.request("GET", f"/api/ingest/jobs/{created['id']}/records")
        self.assertEqual(code, 200)
        self.assertEqual(len(report["records"]), 3)

        pdf.write_bytes(b"tampered after review")
        code, _ = self.request("POST", f"/api/ingest/jobs/{created['id']}/register", {})
        self.assertEqual(code, 409, "changed bytes must never be registered")
        pdf.write_bytes(content)
        code, _ = self.request("POST", f"/api/ingest/jobs/{created['id']}/register", {})
        self.assertEqual(code, 202)
        registered = self.wait_job(created["id"])
        self.assertEqual(registered["status"], "registered")
        self.assertEqual(registered["preview"]["sha256"], digest)
        code, report = self.request("GET", f"/api/ingest/jobs/{created['id']}/records")
        self.assertEqual((code, len(report["records"])), (200, 3))
        self.assertEqual(len(seen), 2)
        self.assertIn("--dry-run", seen[0])
        self.assertEqual(seen[1][:3], [str(pdf), "--url", url])

    def test_registered_filing_can_retry_only_the_failed_rebuild(self):
        content = b"%PDF-1.4\npublic fixture with a failed build\n"
        digest = hashlib.sha256(content).hexdigest()
        pdf = self.root / "data" / "build" / "ingest" / digest[:12] / "source.pdf"
        url = "https://example.org/public-plan.pdf"
        filing_id = "acme-2027"
        calls = []

        def execute(ident, argv):
            calls.append(list(argv))
            if "--dry-run" in argv:
                pdf.parent.mkdir(parents=True, exist_ok=True)
                pdf.write_bytes(content)
                return {"sha256": digest, "url": url,
                        "filing": {"id": filing_id, "title": "Acme plan", "edition": "2027",
                                   "date": "2026-04-01", "dateBasis": "published", "url": url,
                                   "parser": "known"},
                        "shape": {"records": 3, "dated": 2, "withId": 3}}
            self.assertEqual(Path(argv[0]), pdf)
            registry = self.root / "data" / "filings.json"
            registry.write_text(json.dumps({"filings": [{"id": filing_id, "sha256": digest}]}))
            raise RuntimeError("project rebuild failed after filing registration")

        class SuccessfulBuild:
            stdout = ["Rebuilding project and change data\n"]

            def wait(self):
                return 0

        self.server.manager._execute = execute
        code, created = self.request("POST", "/api/ingest/jobs", {"mode": "url", "source": url})
        self.assertEqual(code, 202)
        preview = self.wait_job(created["id"])
        self.assertEqual(preview["status"], "succeeded")
        code, _ = self.request("POST", f"/api/ingest/jobs/{created['id']}/register", {})
        self.assertEqual(code, 202)
        partial = self.wait_job(created["id"], terminal=("needs_rebuild", "failed"))
        self.assertEqual(partial["status"], "needs_rebuild")
        self.assertIn("registered", partial["error"])
        code, _ = self.request("POST", f"/api/ingest/jobs/{created['id']}/register", {})
        self.assertEqual(code, 409, "a partial success must not ingest the filing again")

        with mock.patch.object(local_server.subprocess, "Popen", return_value=SuccessfulBuild()) as popen:
            code, _ = self.request("POST", f"/api/ingest/jobs/{created['id']}/rebuild", {})
            self.assertEqual(code, 202)
            done = self.wait_job(created["id"], terminal=("registered", "needs_rebuild"))
        self.assertEqual(done["status"], "registered")
        self.assertIsNone(done["error"])
        self.assertEqual(len(calls), 2, "retry should run build.py, not re-ingest the filing")
        self.assertEqual(popen.call_args.args[0],
                         [sys.executable, str(self.root / "pipeline" / "build.py")])
        self.assertEqual(popen.call_args.kwargs["cwd"], self.root)
        code, _ = self.request("POST", f"/api/ingest/jobs/{created['id']}/rebuild", {})
        self.assertEqual(code, 409, "a completed rebuild must not be repeated")


class ImportProtocolTests(unittest.TestCase):
    def test_missing_result_is_not_a_duplicate(self):
        manager = local_server.IngestManager()
        process = mock.Mock(stdout=[])
        process.wait.return_value = 0
        with mock.patch.object(local_server.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "without a result"):
                manager._execute("job", [])
            process.stdout = [local_server.RESULT_PREFIX + "null\n"]
            self.assertIsNone(manager._execute("job", []))
            process.stdout = [local_server.RESULT_PREFIX + "[]\n"]
            with self.assertRaisesRegex(RuntimeError, "invalid result"):
                manager._execute("job", [])

    def test_discovered_utility_survives_registration(self):
        pdf = mock.Mock()
        pdf.read_bytes.return_value = b"%PDF-fixture"
        digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
        manager = local_server.IngestManager()
        manager.jobs["job"] = {"spec": {"mode": "find", "source": "Santee Cooper"},
            "run": {"url": "https://example.org/deck.pdf", "reviewedParserSha256": "a" * 64},
            "preview": {"sha256": digest}}
        with mock.patch.object(manager, "_execute", return_value={"sha256": digest}) as execute:
            manager._register_worker("job", pdf)
        argv = execute.call_args.args[1]
        self.assertEqual(argv[argv.index("--company") + 1], "Santee Cooper")
        self.assertNotIn("--find", argv)
        self.assertEqual(manager.jobs["job"]["status"], "registered")


if __name__ == "__main__":
    unittest.main()
