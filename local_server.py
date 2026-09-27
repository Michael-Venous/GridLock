"""Loopback-only GridLock app server with a reviewed public-filing ingest workflow.

Run ``python3 local_server.py --port 8765`` from the repository. The static route
allowlist deliberately excludes data/raw, data/build, parsers, and Git metadata.
"""

import argparse
import datetime as dt
import hashlib
import importlib.util
import ipaddress
import json
import re
import shutil
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
RESULT_PREFIX = "GRIDLOCK_INGEST_RESULT "
MAX_BODY = 16_384
MAX_LOG_LINES = 200
ACTIVE = {"queued", "running", "registering", "rebuilding"}
STATIC = {
    "/": "index.html",
    "/index.html": "index.html",
    "/styles.css": "styles.css",
    "/data/projects.json": "data/projects.json",
    "/data/changes.json": "data/changes.json",
    "/data/filings.json": "data/filings.json",
    "/data/env/evidence.geojson": "data/env/evidence.geojson",
    "/data/env/habitat.geojson": "data/env/habitat.geojson",
    "/data/env/protected.geojson": "data/env/protected.geojson",
}
MIME = {".html": "text/html", ".css": "text/css", ".js": "text/javascript",
        ".json": "application/json", ".geojson": "application/geo+json"}


class RequestError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _short_text(value, field, limit=240):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > limit or any(ord(ch) < 32 for ch in value):
        raise RequestError(f"{field} must be plain text under {limit} characters")
    return value.strip() or None


def _public_url(value):
    value = _short_text(value, "Source URL", 4096)
    if not value:
        raise RequestError("Enter a public PDF or ZIP URL")
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise RequestError("Enter a valid public URL") from None
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password or parts.fragment:
        raise RequestError("Enter a public http(s) URL without credentials or a fragment")
    if port is not None and not 1 <= port <= 65535:
        raise RequestError("Invalid URL port")
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        raise RequestError("The source must be on the public internet")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise RequestError("The source must be on the public internet")
    return value


def validate_spec(body):
    if not isinstance(body, dict):
        raise RequestError("Expected a JSON object")
    allowed = {"mode", "source", "company", "date", "edition", "title", "zipMember"}
    if set(body) - allowed:
        raise RequestError("Unknown ingest field")
    mode = body.get("mode")
    if mode not in ("url", "find"):
        raise RequestError("Choose a URL or company search")
    source = _public_url(body.get("source")) if mode == "url" else _short_text(body.get("source"), "Company", 120)
    if not source:
        raise RequestError("Enter a company name")
    company = _short_text(body.get("company"), "Company", 120)
    date = _short_text(body.get("date"), "Filing date", 10)
    if date:
        try:
            if dt.date.fromisoformat(date).isoformat() != date:
                raise ValueError
        except ValueError:
            raise RequestError("Filing date must be YYYY-MM-DD") from None
    edition = _short_text(body.get("edition"), "Edition", 80)
    title = _short_text(body.get("title"), "Title", 240)
    member = _short_text(body.get("zipMember"), "ZIP member", 500)
    if mode == "find" and member:
        raise RequestError("ZIP member can only be set for a supplied URL")
    return {"mode": mode, "source": source, "company": company, "date": date,
            "edition": edition, "title": title, "zipMember": member}


def _preview(run, duplicate=False):
    if duplicate or not isinstance(run, dict):
        return {"alreadyRegistered": True, "issues": ["This PDF is already registered."]}
    filing = run.get("filing") or {}
    parsed = run.get("shape") or (run.get("agent") or {}).get("report") or {}
    generated = bool(run.get("agent"))
    if run.get("shape"):
        samples = [{"projectId": r.get("project_id"), "name": r.get("name"), "inService": r.get("isd")}
                   for r in parsed.get("reviewRecords", [])[:5]]
    else:
        samples = [{"projectId": r.get("project_id"), "name": r.get("name"),
                    "inService": r.get("in_service")}
                   for r in parsed.get("sample", [])[:5] if isinstance(r, dict)]
    issues = []
    if filing.get("dateBasis") == "date ingested (dry run)":
        issues.append("The PDF has no publication date. Add its filing date and preview again before registering.")
    if not filing.get("url"):
        issues.append("A public source URL is required to register this filing.")
    warnings = parsed.get("first_problems", []) if generated else []
    return {"title": filing.get("title"), "edition": filing.get("edition"),
            "date": filing.get("date"), "dateBasis": filing.get("dateBasis"),
            "plan": filing.get("plan"), "parser": filing.get("parser") or (run.get("agent") or {}).get("parser", {}).get("module"),
            "route": run.get("route"), "records": parsed.get("records"),
            "dated": parsed.get("dated", parsed.get("with_in_service_date")),
            "withId": parsed.get("withId", parsed.get("with_project_id")),
            "samples": samples,
            "sha256": run.get("sha256"), "sourceUrl": run.get("url"),
            "generatedParser": generated, "alreadyRegistered": False, "issues": issues,
            "recordsAvailable": bool(run.get("shape", {}).get("reviewRecords") or run.get("reviewRecordsSha256")),
            "warningCount": parsed.get("problems", 0) if generated else 0,
            "warnings": [str(item)[:500] for item in warnings[:8]]}


def _ai_status():
    if importlib.util.find_spec("boto3") is None:
        return False, "AI discovery and new-layout parsing need boto3 and AWS Bedrock access. URL previews using known parsers still work."
    if not shutil.which("bwrap"):
        return True, "AI discovery can be attempted; registering a generated parser also needs working bubblewrap."
    return True, "AWS access and bubblewrap isolation are checked when the workflow runs."


class IngestManager:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.jobs = {}
        self.lock = threading.Lock()

    def status(self):
        ai, reason = _ai_status()
        return {"available": True, "aiAvailable": ai, "aiReason": reason}

    def create(self, body):
        spec = validate_spec(body)
        with self.lock:
            if any(j["status"] in ACTIVE for j in self.jobs.values()):
                raise RequestError("Another filing is being processed. Wait for it to finish.", 409)
            ident = uuid.uuid4().hex
            job = {"id": ident, "status": "queued", "stage": "Waiting to preview",
                   "logs": [], "preview": None, "error": None, "spec": spec, "run": None,
                   "registeredRun": None}
            self.jobs[ident] = job
        threading.Thread(target=self._preview_worker, args=(ident,), daemon=True).start()
        return self.get(ident)

    def get(self, ident):
        with self.lock:
            if ident not in self.jobs:
                raise RequestError("No such ingest job", 404)
            j = self.jobs[ident]
            return {k: j[k] for k in ("id", "status", "stage", "logs", "preview", "error")}

    def records(self, ident):
        with self.lock:
            job = self.jobs.get(ident)
            if not job:
                raise RequestError("No such ingest job", 404)
            if job["status"] not in ("succeeded", "registering", "registered", "needs_rebuild", "rebuilding") or not job["run"]:
                raise RequestError("Records are available after a successful preview", 409)
            run = job["run"]
        if run.get("agent") and run.get("reviewRecordsSha256"):
            digest = run["sha256"]
            path = self.root / "data" / "build" / "ingest" / digest[:12] / "records.json"
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != run["reviewRecordsSha256"]:
                raise RequestError("The reviewed records changed; preview this filing again", 409)
            return {"filing": run.get("filing"), "records": json.loads(path.read_text())}
        rows = (run.get("shape") or {}).get("reviewRecords")
        if rows is None:
            raise RequestError("No extracted records were saved for this preview", 404)
        return {"filing": run.get("filing"), "records": rows}

    def register(self, ident):
        with self.lock:
            if ident not in self.jobs:
                raise RequestError("No such ingest job", 404)
            job = self.jobs[ident]
            if any(j["status"] in ACTIVE for j in self.jobs.values()):
                raise RequestError("Another filing is being processed. Wait for it to finish.", 409)
            if job["status"] != "succeeded":
                raise RequestError("Preview this filing successfully before registering it", 409)
            preview = job["preview"] or {}
            if preview.get("alreadyRegistered") or preview.get("issues"):
                raise RequestError("Resolve the preview issues before registering", 409)
            digest = preview.get("sha256")
            if not digest or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise RequestError("Previewed PDF hash is missing", 409)
            pdf = self.root / "data" / "build" / "ingest" / digest[:12] / "source.pdf"
            if not pdf.is_file() or hashlib.sha256(pdf.read_bytes()).hexdigest() != digest:
                raise RequestError("The previewed PDF is missing or changed. Preview it again.", 409)
            job["status"] = "registering"
            job["stage"] = "Registering the reviewed PDF"
            job["error"] = None
        threading.Thread(target=self._register_worker, args=(ident, pdf), daemon=True).start()
        return self.get(ident)

    def retry_build(self, ident):
        with self.lock:
            job = self.jobs.get(ident)
            if not job:
                raise RequestError("No such ingest job", 404)
            if any(j["status"] in ACTIVE for j in self.jobs.values()):
                raise RequestError("Another filing is being processed. Wait for it to finish.", 409)
            if job["status"] != "needs_rebuild":
                raise RequestError("There is no incomplete rebuild to retry", 409)
            job.update(status="rebuilding", stage="Rebuilding project and change data", error=None)
        threading.Thread(target=self._rebuild_worker, args=(ident,), daemon=True).start()
        return self.get(ident)

    def _registered_pdf(self, ident):
        with self.lock:
            job = self.jobs[ident]
            run = job["run"] or {}
            filing = run.get("filing") or {}
            digest = (job["preview"] or {}).get("sha256")
        try:
            registry = json.loads((self.root / "data" / "filings.json").read_text())
        except (OSError, ValueError):
            return False
        return any(f.get("id") == filing.get("id") and f.get("sha256") == digest
                   for f in registry.get("filings", []))

    def _set(self, ident, **changes):
        with self.lock:
            self.jobs[ident].update(changes)

    def _append_log(self, ident, line):
        line = line.strip()
        if not line:
            return
        with self.lock:
            job = self.jobs[ident]
            job["logs"] = (job["logs"] + [line[:1000]])[-MAX_LOG_LINES:]
            if line.startswith("Looking for "):
                job["stage"] = "Finding a public filing"
            elif " pages" in line:
                job["stage"] = "Reading the PDF"
            elif line.startswith("Trying the ") or line.startswith("Writing a parser") or line.startswith("Repairing parser"):
                job["stage"] = "Extracting and checking projects"
            elif line.startswith("Rebuilding "):
                job["stage"] = "Rebuilding project and change data"

    def _execute(self, ident, argv):
        command = [sys.executable, str(self.root / "pipeline" / "ingest_bridge.py"), *argv]
        result = None
        process = subprocess.Popen(command, cwd=self.root, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in process.stdout:
            if line.startswith(RESULT_PREFIX):
                result = json.loads(line[len(RESULT_PREFIX):])
            else:
                self._append_log(ident, line)
        code = process.wait()
        if code:
            logs = self.get(ident)["logs"]
            raise RuntimeError(logs[-1] if logs else f"Ingest exited with code {code}")
        return result

    @staticmethod
    def _flags(spec):
        flags = []
        for key, flag in (("company", "--company"), ("date", "--date"), ("edition", "--edition"),
                          ("title", "--title"), ("zipMember", "--zip-member")):
            if spec.get(key):
                flags.extend((flag, spec[key]))
        return flags

    def _preview_worker(self, ident):
        try:
            self._set(ident, status="running", stage="Downloading and checking the public filing")
            with self.lock:
                spec = dict(self.jobs[ident]["spec"])
            argv = ([spec["source"]] if spec["mode"] == "url" else ["--find", spec["source"]]) + self._flags(spec) + ["--dry-run"]
            run = self._execute(ident, argv)
            preview = _preview(run, duplicate=run is None)
            self._set(ident, status="succeeded", stage="Preview ready for review", preview=preview, run=run)
        except Exception as exc:
            self._set(ident, status="failed", stage="Preview failed", error=str(exc)[:500])

    def _register_worker(self, ident, pdf):
        try:
            with self.lock:
                spec = dict(self.jobs[ident]["spec"])
                prior = self.jobs[ident]["run"]
                digest = self.jobs[ident]["preview"]["sha256"]
            if hashlib.sha256(pdf.read_bytes()).hexdigest() != digest:
                raise RuntimeError("The previewed PDF changed. Preview it again.")
            url = prior.get("url")
            if not url:
                raise RuntimeError("No public filing URL was found")
            argv = [str(pdf), "--url", url] + self._flags(spec)
            if spec["mode"] == "find" and prior.get("zipMember"):
                argv += ["--zip-member", prior["zipMember"]]
            if prior.get("reviewedParserSha256"):
                argv += ["--reviewed-parser-sha256", prior["reviewedParserSha256"]]
            elif prior.get("reviewRecordsSha256"):
                argv += ["--reviewed-records-sha256", prior["reviewRecordsSha256"]]
            run = self._execute(ident, argv)
            if not isinstance(run, dict) or (run.get("sha256") != digest):
                raise RuntimeError("Registration did not finish with the reviewed PDF hash")
            self._set(ident, status="registered", stage="Registered and rebuilt", registeredRun=run)
        except Exception as exc:
            if self._registered_pdf(ident):
                self._set(ident, status="needs_rebuild", stage="Filing registered; rebuild incomplete",
                          error=("The filing was registered, but rebuilding the dataset failed: " + str(exc))[:500])
            else:
                self._set(ident, status="failed", stage="Registration failed", error=str(exc)[:500])

    def _rebuild_worker(self, ident):
        try:
            command = [sys.executable, str(self.root / "pipeline" / "build.py")]
            process = subprocess.Popen(command, cwd=self.root, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in process.stdout:
                self._append_log(ident, line)
            if process.wait():
                logs = self.get(ident)["logs"]
                raise RuntimeError(logs[-1] if logs else "Build failed")
            self._set(ident, status="registered", stage="Registered and rebuilt", error=None)
        except Exception as exc:
            self._set(ident, status="needs_rebuild", stage="Filing registered; rebuild incomplete",
                      error=("Rebuild failed: " + str(exc))[:500])


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, root=ROOT):
        self.manager = IngestManager(root)
        super().__init__(address, AppHandler)


class AppHandler(BaseHTTPRequestHandler):
    server_version = "GridLockLocal/1"

    def _allowed_host(self):
        return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

    def _json(self, code, payload, attachment=None):
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(data)

    def _path(self):
        return urlsplit(self.path).path

    def do_GET(self):
        if not self._allowed_host():
            self._json(403, {"error": "Use the local GridLock address"})
            return
        path = self._path()
        try:
            if path == "/api/ingest/status":
                return self._json(200, self.server.manager.status())
            match = re.fullmatch(r"/api/ingest/jobs/([0-9a-f]{32})", path)
            if match:
                return self._json(200, self.server.manager.get(match[1]))
            match = re.fullmatch(r"/api/ingest/jobs/([0-9a-f]{32})/records", path)
            if match:
                return self._json(200, self.server.manager.records(match[1]), attachment="gridlock-ingest-preview.json")
            rel = STATIC.get(path)
            if rel is None and re.fullmatch(r"/src/[a-z0-9-]+\.js", path):
                rel = path.lstrip("/")
            if rel is None:
                raise RequestError("Not found", 404)
            file = self.server.manager.root / rel
            if not file.is_file():
                raise RequestError("Not found", 404)
            data = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", MIME.get(file.suffix, "application/octet-stream") + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)
        except RequestError as exc:
            self._json(exc.code, {"error": str(exc)})

    def do_POST(self):
        if not self._allowed_host():
            return self._json(403, {"error": "Use the local GridLock address"})
        origin = self.headers.get("Origin")
        if origin not in {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}:
            return self._json(403, {"error": "The request must come from this local app"})
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            return self._json(415, {"error": "Expected application/json"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY:
                raise RequestError("Invalid request size", 413)
            body = json.loads(self.rfile.read(size))
            path = self._path()
            if path == "/api/ingest/jobs":
                return self._json(202, self.server.manager.create(body))
            match = re.fullmatch(r"/api/ingest/jobs/([0-9a-f]{32})/register", path)
            if match:
                if body != {}:
                    raise RequestError("Registration takes no extra fields")
                return self._json(202, self.server.manager.register(match[1]))
            match = re.fullmatch(r"/api/ingest/jobs/([0-9a-f]{32})/rebuild", path)
            if match:
                if body != {}:
                    raise RequestError("Rebuild takes no extra fields")
                return self._json(202, self.server.manager.retry_build(match[1]))
            raise RequestError("Not found", 404)
        except RequestError as exc:
            self._json(exc.code, {"error": str(exc)})
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"error": "Invalid JSON request"})

    def log_message(self, format, *args):
        print("%s %s" % (self.address_string(), format % args), file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    if not 1 <= args.port <= 65535:
        ap.error("port must be between 1 and 65535")
    try:
        server = AppServer(("127.0.0.1", args.port))
    except OSError as exc:
        raise SystemExit(f"Cannot start GridLock on port {args.port}: {exc}") from None
    print(f"GridLock: http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
