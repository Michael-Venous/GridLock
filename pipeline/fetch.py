"""Download the public source documents listed in data/filings.json into data/raw/ (not committed). Standard library only."""
import io
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
        return out
    RAW.mkdir(parents=True, exist_ok=True)
    data = get(f["url"])
    if f.get("zipMember"):
        data = zipfile.ZipFile(io.BytesIO(data)).read(f["zipMember"])
    out.write_bytes(data)
    print("fetched", out.name)
    return out


def main():
    for f in filings():
        fetch(f)


if __name__ == "__main__":
    main()
