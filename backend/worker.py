"""Daily job: look for new filings, rebuild the data, email the subscribers whose area changed.

1. Watch: SCRTP's home page for a new DESC "$2M and above" list, and the GA PSC docket(s) in
   data/filings.json for a new annual transmission update. Anything already in the registry or already
   seen is skipped, so a filing is handled once.
2. Rebuild: the pipeline runs on a copy of the app in /tmp with the raw PDFs and caches from S3, then the
   results go back to S3 (state/) for the API and the site.
3. Notify: each confirmed subscriber gets one email per new filing, listing only the changes in their area.
If a new filing can't be downloaded or parsed, the maintainers get the error and subscribers get a short
"a new filing was posted" notice with the link, never a guessed comparison.
"""
import datetime as dt
import io
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import alerts
from store import Store

APP = Path(os.environ.get("APP_ROOT", Path(__file__).resolve().parent / "app"))
WORK = Path(os.environ.get("WORK_ROOT", "/tmp/work"))
UA = {"User-Agent": "Gridlock-ShellHacks-2026/0.1 (filing watcher)"}
PSC_DOCKET = "https://psc.ga.gov/search/service-facts-docket/?"
PSC_DOCUMENT = "https://psc.ga.gov/search/facts-document/?documentId={}"
PSC_FILE = "https://services.psc.ga.gov/api/v1/External/Public/Get/Document/DownloadFile/{}/{}"


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300).read()


# ---- 1. watch ----

def watch_desc(registry, http):
    cfg = registry["watch"]["desc"]
    html = http(cfg["page"]).decode("utf-8", "replace")
    known = {f["edition"] for f in registry["filings"] if f["parser"] == "desc"}
    out = []
    for m in re.finditer(r'href="([^"]*' + cfg["pattern"] + ')"', html):
        edition = m.group(2)
        if edition in known or any(f["edition"] == edition for f in out):
            continue
        out.append({"id": f"desc-{edition}", "utility": "DESC", "state": "SC", "edition": edition, "parser": "desc",
                    "title": f"DESC Planned Transmission Projects $2M and above, {edition}",
                    "url": urllib.parse.urljoin(cfg["page"], m.group(1)), "file": f"desc_{edition}.pdf"})
    return out


def watch_ga(registry, http):
    cfg = registry["watch"]["ga"]
    known = {f.get("document") for f in registry["filings"]}
    out = []
    for docket in cfg["dockets"]:
        q = urllib.parse.urlencode({"docketId": docket, "sortDirection": "DESC", "sortColumn": "Filed", "searchText": cfg["search"], "pageSize": 50, "pageNumber": 1})
        for doc in json.loads(http(PSC_DOCKET + q)).get("resultsItems", []):
            desc = doc.get("description") or ""
            if str(doc["documentId"]) in known or not re.search(r"transmission update|ten[- ]year plan", desc, re.I):
                continue
            out.append({"id": f"ga-doc-{doc['documentId']}", "utility": "Georgia ITS", "state": "GA", "parser": "ga",
                        "docket": docket, "document": str(doc["documentId"]), "description": desc.strip(),
                        "date": doc["filedDate"][:10], "dateBasis": "GA PSC filed date"})
    return out


# ---- 2. download what was found ----

def pdf_date(path):
    try:
        info = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, timeout=60).stdout
        m = re.search(r"CreationDate:\s+(.+)", info)
        return dt.datetime.strptime(" ".join(m.group(1).split()[:5]), "%a %b %d %H:%M:%S %Y").date().isoformat() if m else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def materialize(f, raw, http):
    """Download a found filing into raw/ and fill in its date, edition and title. Returns the registry entry."""
    raw.mkdir(parents=True, exist_ok=True)
    if f["parser"] == "desc":
        path = raw / f["file"]
        path.write_bytes(http(f["url"]))
        date = pdf_date(path)
        return {**f, "date": date or dt.date.today().isoformat(), "dateBasis": "PDF creation date" if date else "date the watcher found it"}
    page = http(PSC_DOCUMENT.format(f["document"])).decode("utf-8", "replace")
    files = re.findall(rf"DownloadFile/{f['document']}/(\d+)", page)
    if not files:
        raise ValueError(f"no attachment on GA PSC document {f['document']}")
    url = PSC_FILE.format(f["document"], files[0])
    data, member = http(url), None
    if data[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(data))
        member = next((n for n in z.namelist() if re.search(r"ten[- ]year plan.*public disclosure.*\.pdf$", n, re.I)), None)
        if not member:
            raise ValueError(f"no Ten-Year Plan PDF inside GA PSC document {f['document']}")
        data = z.read(member)
    tmp = raw / f"ga_doc{f['document']}.pdf"
    tmp.write_bytes(data)
    first = subprocess.run(["pdftotext", "-l", "3", str(tmp), "-"], capture_output=True, text=True, timeout=120).stdout
    m = re.search(r"(\d{4}) GA ITS Ten-Year Plan \((\d{4}-\d{4})\)", first)
    if not m:
        raise ValueError(f"GA PSC document {f['document']} doesn't name a GA ITS Ten-Year Plan edition on its first pages")
    edition = m.group(2)
    tmp.rename(raw / f"ga_its_{edition}.pdf")
    entry = {k: v for k, v in f.items() if k != "description"}
    return {**entry, "id": f"ga-{edition}", "edition": edition, "file": f"ga_its_{edition}.pdf", "url": url,
            **({"zipMember": member} if member else {}),
            "title": f"{m.group(1)} GA ITS Ten-Year Plan ({edition}), GA PSC docket {f['docket']} #{f['document']}"}


# ---- rebuild ----

def prepare(store):
    if WORK.exists():
        shutil.rmtree(WORK)
    shutil.copytree(APP, WORK)
    for prefix, dest in (("raw/", "data/raw"), ("state/cache/", "data/cache"), ("state/env/", "data/env")):
        (WORK / dest).mkdir(parents=True, exist_ok=True)
        for key in store.keys(prefix):
            store.download(key, WORK / dest / key.rsplit("/", 1)[1])
    return WORK


def run_build(work):
    env = {**os.environ, "GRIDLOCK_TODAY": dt.date.today().isoformat()}
    return subprocess.run([sys.executable, "pipeline/build.py"], cwd=work, capture_output=True, text=True, timeout=780, env=env)


def publish(store, work, new_files):
    data = work / "data"
    for name in ("projects.json", "changes.json", "filings.json"):
        store.upload(data / name, f"state/{name}", "application/json")
    for folder in ("env", "cache"):
        for p in sorted((data / folder).glob("*")):
            store.upload(p, f"state/{folder}/{p.name}", "application/json")
    for name in new_files:
        store.upload(data / "raw" / name, f"raw/{name}", "application/pdf")


# ---- 3. notify ----

def notify(store, event):
    sent = 0
    for sub in store.subscribers():
        if event["id"] in sub.get("sent", []) or not store.confirmed(sub.get("snsArn")):
            continue
        items = alerts.items_for(event, sub)
        if not items:
            continue
        subject, body = alerts.message(event, items, sub, app_url=store.app_url)
        store.publish(sub["id"], subject, body)
        store.put_subscriber({**sub, "sent": sub.get("sent", []) + [event["id"]]})
        sent += 1
    return sent


def notice_everyone(store, filing):
    subject, body = alerts.new_filing_notice(filing)
    for sub in store.subscribers():
        if store.confirmed(sub.get("snsArn")):
            store.publish(sub["id"], subject, body)


def handler(event, context, store=None, http=get, build=run_build):
    store = store or Store.from_env()
    registry = store.get_json("state/filings.json")
    seen = store.get_json("state/seen.json", {"ids": []})
    found = []
    for watcher in (watch_desc, watch_ga):
        try:
            found += [f for f in watcher(registry, http) if f["id"] not in seen["ids"]]
        except Exception as e:   # a source site being down is not a reason to stop the other one
            store.tell_maintainers("Gridlock watcher: a source couldn't be checked", f"{watcher.__name__}: {e!r}")
    if not found:
        print("no new filings")
        return {"new": []}
    work = prepare(store)
    ready = []
    for f in found:
        try:
            entry = materialize(f, work / "data" / "raw", http)
        except ValueError as e:                       # not what we expected (e.g. no plan inside): don't retry
            seen["ids"].append(f["id"])
            store.tell_maintainers(f"Gridlock: new filing {f['id']} isn't one the pipeline can read", repr(e))
            continue
        except Exception as e:                        # network trouble: try again tomorrow
            store.tell_maintainers(f"Gridlock: new filing {f['id']} couldn't be downloaded; retrying tomorrow", repr(e))
            continue
        seen["ids"].append(f["id"])
        if any(x["id"] == entry["id"] for x in registry["filings"]):
            continue                                  # e.g. an errata copy of a plan we already have
        seen["ids"].append(entry["id"])
        ready.append(entry)
    if ready:
        registry["filings"] += ready
        (work / "data" / "filings.json").write_text(json.dumps(registry, indent=2))
        result = build(work)
        if result.returncode != 0:
            store.tell_maintainers(f"Gridlock: the pipeline failed on {', '.join(f['id'] for f in ready)}",
                                   (result.stdout or "")[-3000:] + "\n" + (result.stderr or "")[-3000:])
            for f in ready:
                notice_everyone(store, f)
        else:
            publish(store, work, [f["file"] for f in ready])
            store.put_json("state/filings.json", registry)
            log = json.loads((work / "data" / "changes.json").read_text())
            for f in ready:
                ev = next((e for e in log["events"] if e["id"] == f["id"]), None)
                n = notify(store, ev) if ev else 0
                print(f"{f['id']}: {'no change event' if not ev else f'{n} subscribers emailed'}")
    store.put_json("state/seen.json", seen)
    return {"new": [f["id"] for f in ready]}
