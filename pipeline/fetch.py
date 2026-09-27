"""Download the public source documents listed in data/filings.json into data/raw/ (not committed). Standard library only."""
import io
import hashlib
import urllib.request
import zipfile

from common import RAW, filings


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "GridLock-ShellHacks-2026/0.1"})
    return urllib.request.urlopen(req, timeout=300).read()


def fetch(f):
    """One filing into data/raw/. GA PSC serves filings as zips even when the link looks like one document."""
    out = RAW / f["file"]
    if out.exists():
        if f.get("sha256") and hashlib.sha256(out.read_bytes()).hexdigest() != f["sha256"]:
            raise ValueError(f"{out} differs from the sha256 registered for {f['id']}")
        return out
    RAW.mkdir(parents=True, exist_ok=True)
    if f.get("sha256"):
        import finder
        # Agent-registered URLs must be checked again on every fresh rebuild, including every redirect and DNS answer.
        data, _, _ = finder.get(f["url"], finder.MAX_PDF)
        data, _ = finder.pdf_from(data, f.get("zipMember"))
    else:
        data = get(f["url"])
        if f.get("zipMember"):
            data = zipfile.ZipFile(io.BytesIO(data)).read(f["zipMember"])
    if f.get("sha256") and hashlib.sha256(data).hexdigest() != f["sha256"]:
        raise ValueError(f"{f['url']} no longer serves the PDF registered for {f['id']} (sha256 mismatch)")
    out.write_bytes(data)
    print("fetched", out.name)
    return out


def main():
    for f in filings():
        fetch(f)


if __name__ == "__main__":
    main()
