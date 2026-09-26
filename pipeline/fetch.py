"""Download the public source documents into data/raw/ (not committed). Standard library only."""
import io
import urllib.request
import zipfile

from common import RAW

DESC = "https://www.scrtp.com/assets/pdfs/home/{}-2million-and-above-project-descriptions.pdf"
GA_ZIP = "https://services.psc.ga.gov/api/v1/External/Public/Get/Document/DownloadFile/225600/106866"
GA_MEMBER = "Dkt 56002 2025 Annual Transmission Update PD/2025 GA ITS Ten Year Plan - PUBLIC DISCLOSURE.pdf"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "GridLock-ShellHacks-2026/0.1"})
    return urllib.request.urlopen(req, timeout=120).read()


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    for ed in ("2026-2030", "2025-2029", "2024-2028"):
        out = RAW / f"desc_{ed}.pdf"
        if not out.exists():
            out.write_bytes(get(DESC.format(ed)))
            print("fetched", out.name)
    out = RAW / "ga_its_2026-2035.pdf"
    if not out.exists():
        # GA PSC serves the filing as a zip even though the link looks like one document
        out.write_bytes(zipfile.ZipFile(io.BytesIO(get(GA_ZIP))).read(GA_MEMBER))
        print("fetched", out.name)


if __name__ == "__main__":
    main()
