"""Checks for backend/ (alert rules, API, worker) with an in-memory stand-in for AWS.
Run: python3 -m unittest discover tests"""
import json
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
import alerts  # noqa: E402
import api  # noqa: E402
import worker  # noqa: E402

LOG = json.loads((ROOT / "data" / "changes.json").read_text())
BOX = [[-81.3, 32.0], [-80.8, 32.0], [-80.8, 32.4], [-81.3, 32.4], [-81.3, 32.0]]   # same as tests/changes.test.js


class FakeStore:
    """The Store interface, in memory. confirmed_arns decides who has clicked the confirmation link."""
    app_url = "https://example.test/gridlock/"

    def __init__(self):
        self.objects, self.subs, self.rates, self.published, self.maint, self.confirmed_arns = {}, {}, {}, [], [], set()

    def get_json(self, key, default=None):
        return json.loads(self.objects[key]) if key in self.objects else default

    def put_json(self, key, obj):
        self.objects[key] = json.dumps(obj)

    def download(self, key, path):
        Path(path).write_bytes(self.objects[key] if isinstance(self.objects[key], bytes) else self.objects[key].encode())

    def upload(self, path, key, content_type=None):
        self.objects[key] = Path(path).read_bytes()

    def keys(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]

    def subscriber_id(self, email):
        return "id-" + email.lower()

    def get_subscriber(self, sid):
        return json.loads(json.dumps(self.subs[sid])) if sid in self.subs else None

    def put_subscriber(self, sub):
        self.subs[sub["id"]] = sub

    def subscribers(self):
        return [json.loads(json.dumps(s)) for s in self.subs.values()]

    def bump(self, key, limit, ttl_s):
        self.rates[key] = self.rates.get(key, 0) + 1
        return self.rates[key] <= limit

    def subscribe(self, email, sid):
        return f"arn:aws:sns:us-east-1:0:alerts:{sid}"

    def confirmed(self, arn):
        return arn in self.confirmed_arns

    def publish(self, sid, subject, body):
        self.published.append((sid, subject, body))

    def tell_maintainers(self, subject, body):
        self.maint.append((subject, body))


def post(path, body):
    return {"requestContext": {"http": {"method": "POST"}}, "rawPath": path, "body": json.dumps(body)}


class Rules(unittest.TestCase):
    def test_area_rule_matches_the_app(self):
        self.assertTrue(alerts.point_in_polygon([-81.1, 32.1], BOX))
        d = alerts.miles_to_polygon([-81.4, 32.2], BOX)
        self.assertTrue(5.7 < d < 6.0, d)
        self.assertTrue(alerts.in_area([[-81.4, 32.2]], BOX, 6))
        self.assertFalse(alerts.in_area([[-81.4, 32.2]], BOX))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_same_counts_as_the_changes_tab(self):
        js = subprocess.run(["node", "--input-type=module", "-e", f"""
            import {{ itemsIn }} from "{(ROOT / 'src' / 'changes.js').as_uri()}";
            import {{ readFileSync }} from "node:fs";
            const log = JSON.parse(readFileSync("{ROOT / 'data' / 'changes.json'}"));
            console.log(JSON.stringify(log.events.map(e => [itemsIn(e, {json.dumps(BOX)}, 0).length, itemsIn(e, {json.dumps(BOX)}, 10, ["added", "date"]).length])));
        """], capture_output=True, text=True, check=True).stdout
        py = [[len(alerts.items_for(e, {"area": BOX, "bufferMi": 0, "kinds": list(alerts.KINDS)})),
               len(alerts.items_for(e, {"area": BOX, "bufferMi": 10, "kinds": ["added", "date"]}))] for e in LOG["events"]]
        self.assertEqual(json.loads(js), py)

    def test_validation(self):
        ok = alerts.clean_subscription({"email": " a@b.co ", "area": BOX[:-1], "bufferMi": 5, "kinds": ["added"]})
        self.assertEqual(ok["area"][0], ok["area"][-1])
        self.assertEqual(ok["email"], "a@b.co")
        for bad in ({"email": "nope"}, {"email": "a@b.co", "area": [[0, 0], [1, 1], [2, 2], [0, 0]]},
                    {"email": "a@b.co", "bufferMi": 40}, {"email": "a@b.co", "kinds": ["everything"]},
                    {"email": "a@b.co", "area": [[-81, 32]] * 3}):
            with self.assertRaises(alerts.Invalid, msg=bad):
                alerts.clean_subscription(bad)

    def test_message_lists_only_the_area_and_says_sample(self):
        ev = LOG["events"][0]
        sub = {"area": BOX, "bufferMi": 0, "kinds": alerts.ALERT_KINDS}
        items = alerts.items_for(ev, sub)
        subject, body = alerts.message(ev, items, sub, sample=True, app_url="https://x.test/")
        self.assertTrue(subject.startswith("[Sample] Gridlock: DESC 2026-2030 filing"))
        self.assertLessEqual(len(subject), 100)
        self.assertIn("SAMPLE:", body)
        self.assertIn(ev["url"], body)
        self.assertNotIn("Columbia Canal", body)            # a Columbia project, far outside the box
        self.assertEqual(body.count("\n- "), len(items))


class Api(unittest.TestCase):
    def test_subscribe_then_sample(self):
        s = FakeStore()
        s.put_json("state/changes.json", LOG)
        api._changes.update(at=0, log=None)
        r = api.handler(post("/subscribe", {"email": "p@utility.com", "area": BOX, "kinds": ["added", "pairNew"]}), None, store=s)
        self.assertEqual(r["statusCode"], 200)
        sid = json.loads(r["body"])["id"]
        self.assertEqual(json.loads(r["body"])["status"], "pending")
        r = api.handler(post("/sample", {"id": sid}), None, store=s)
        self.assertEqual(r["statusCode"], 409)
        s.confirmed_arns.add(s.subs[sid]["snsArn"])
        r = api.handler(post("/sample", {"id": sid}), None, store=s)
        self.assertEqual(r["statusCode"], 200, r["body"])
        self.assertTrue(s.published[-1][1].startswith("[Sample]"))
        self.assertEqual(s.published[-1][0], sid)
        self.assertEqual(api.handler(post("/sample", {"id": sid}), None, store=s)["statusCode"], 429)

    def test_resubscribing_a_confirmed_address_updates_it_without_a_new_confirmation(self):
        s = FakeStore()
        api.handler(post("/subscribe", {"email": "p@utility.com"}), None, store=s)
        sid = "id-p@utility.com"
        s.confirmed_arns.add(s.subs[sid]["snsArn"])
        s.subscribe = lambda *a: self.fail("should not resubscribe")
        r = api.handler(post("/subscribe", {"email": "p@utility.com", "area": BOX}), None, store=s)
        self.assertEqual(json.loads(r["body"])["status"], "confirmed")
        self.assertEqual(s.subs[sid]["area"], BOX)

    def test_limits_and_errors(self):
        s = FakeStore()
        codes = [api.handler(post("/subscribe", {"email": "p@utility.com"}), None, store=s)["statusCode"] for _ in range(6)]
        self.assertEqual(codes, [200] * 5 + [429])
        self.assertEqual(api.handler(post("/subscribe", {"email": "bad"}), None, store=s)["statusCode"], 400)
        self.assertEqual(api.handler({"requestContext": {"http": {"method": "POST"}}, "rawPath": "/subscribe", "body": "{"}, None, store=s)["statusCode"], 400)
        self.assertEqual(api.handler(post("/nope", {}), None, store=s)["statusCode"], 404)


REGISTRY = json.loads((ROOT / "data" / "filings.json").read_text())
SCRTP = b'<a href="assets/pdfs/home/2026-2030-2million-and-above-project-descriptions.pdf">x</a> <a href="assets/pdfs/home/2027-2031-2million-and-above-project-descriptions.pdf">y</a>'
DOCKET = json.dumps({"resultsItems": [{"documentId": "225600", "description": "2025 Annual Transmission Update PD", "filedDate": "2026-02-27T00:00:00"},
                                      {"documentId": "230001", "description": "2026 Annual Transmission Update PD", "filedDate": "2027-02-26T00:00:00"},
                                      {"documentId": "230002", "description": "Staff data request", "filedDate": "2027-03-01T00:00:00"}]}).encode()


class Worker(unittest.TestCase):
    def test_watchers_find_only_new_filings(self):
        http = lambda url: SCRTP if "scrtp" in url else DOCKET
        (d,) = worker.watch_desc(REGISTRY, http)
        self.assertEqual((d["id"], d["url"]), ("desc-2027-2031", "https://www.scrtp.com/assets/pdfs/home/2027-2031-2million-and-above-project-descriptions.pdf"))
        (g,) = worker.watch_ga(REGISTRY, http)
        self.assertEqual((g["document"], g["date"]), ("230001", "2027-02-26"))

    def test_new_desc_list_rebuilds_and_emails_only_matching_confirmed_subscribers(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        app = tmp / "app"; (app / "data").mkdir(parents=True)
        worker.APP, worker.WORK = app, tmp / "work"
        s = FakeStore()
        s.put_json("state/filings.json", REGISTRY)
        new_event = {**LOG["events"][0], "id": "desc-2027-2031"}
        s.put_subscriber({"id": "near", "snsArn": "arn:near", "area": BOX, "bufferMi": 0, "kinds": alerts.ALERT_KINDS})
        s.put_subscriber({"id": "far", "snsArn": "arn:far", "area": [[-84.5, 33.6], [-84.2, 33.6], [-84.2, 33.9], [-84.5, 33.9], [-84.5, 33.6]], "bufferMi": 0})
        s.put_subscriber({"id": "unconfirmed", "snsArn": "arn:unconfirmed", "area": None})
        s.confirmed_arns |= {"arn:near", "arn:far"}

        def build(work):
            (work / "data" / "changes.json").write_text(json.dumps({"events": [new_event]}))
            for name in ("projects.json",):
                (work / "data" / name).write_text("{}")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        http = lambda url: SCRTP if url.endswith("scrtp.com/") else (b"%PDF-1.4 fake" if url.endswith(".pdf") else b'{"resultsItems": []}')
        out = worker.handler({}, None, store=s, http=http, build=build)
        self.assertEqual(out, {"new": ["desc-2027-2031"]})
        self.assertEqual([p[0] for p in s.published], ["near"])
        self.assertIn("desc-2027-2031", s.subs["near"]["sent"])
        self.assertIn("desc-2027-2031", [f["id"] for f in s.get_json("state/filings.json")["filings"]])
        self.assertIn("raw/desc_2027-2031.pdf", s.objects)
        # the next day, nothing new: no second email
        self.assertEqual(worker.handler({}, None, store=s, http=http, build=build), {"new": []})
        self.assertEqual(len(s.published), 1)

    def test_a_failed_build_sends_only_a_notice(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        (tmp / "app" / "data").mkdir(parents=True)
        worker.APP, worker.WORK = tmp / "app", tmp / "work"
        s = FakeStore()
        s.put_json("state/filings.json", REGISTRY)
        s.put_subscriber({"id": "near", "snsArn": "arn:near", "area": BOX})
        s.confirmed_arns.add("arn:near")
        http = lambda url: SCRTP if url.endswith("scrtp.com/") else (b"%PDF-1.4 fake" if url.endswith(".pdf") else b'{"resultsItems": []}')
        worker.handler({}, None, store=s, http=http, build=lambda w: types.SimpleNamespace(returncode=1, stdout="", stderr="boom"))
        self.assertEqual(len(s.maint), 1)
        self.assertEqual(s.published[0][1], "Gridlock: new DESC filing posted")
        self.assertNotIn("desc-2027-2031", [f["id"] for f in s.get_json("state/filings.json")["filings"]])


if __name__ == "__main__":
    unittest.main()
