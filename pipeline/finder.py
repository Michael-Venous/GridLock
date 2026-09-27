"""Find a utility's public list of planned transmission projects from its name (ingest.py --find "Santee Cooper").

Claude on Amazon Bedrock browses from what it knows of the company's and its regulators' websites: fetch() gives a
page's links and text, check_pdf() downloads a PDF and shows its first pages. It proposes one PDF it has checked;
ingest.py then downloads it the same guarded way, identifies and checks it like any other, and stops if it turns out to
be another company's. There is no search engine and no API key, so it finds only what is linked from pages it can
reach: an address it types itself, and every redirect from one, must be a plain page address (no query, no address
inside another), which rules out search engines, proxies and archive lookups without listing them. Only public internet
addresses are fetched: each connection goes to an address checked to be public when it is made.
"""
import functools
import html.parser
import http.client
import io
import ipaddress
import re
import socket
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from bedrock import CACHE
from common import pdf_pages
from parser_agent import clip, dated_pages, input_problems, page_index, stop_problem

MAX_PAGE = 3 << 20
# a download (a PDF, or a zip of a filing's documents: GA PSC zips run over 100 MB) and a PDF unpacked from a zip
MAX_PDF = 256 << 20
MAX_RESULT = 20000
AGENT = "GridLock-ShellHacks-2026/0.1 (public transmission plans)"

TOOLS = [
    {"name": "fetch", "description": "Download a web page; returns its title, links (PDFs first) and the start of its text.",
     "inputSchema": {"json": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"name": "check_pdf", "description": "Download a PDF (or a zip of PDFs) and show its page count, dates, the pages that talk about in-service dates, each page's first lines and its first page's text.",
     "inputSchema": {"json": {"type": "object", "properties": {"url": {"type": "string"}, "zip_member": {"type": "string"}}, "required": ["url"]}}},
    {"name": "propose", "description": "The PDF to ingest: the newest public list of the company's planned transmission projects. Only a PDF check_pdf has read.",
     "inputSchema": {"json": {"type": "object", "properties": {"url": {"type": "string"}, "zip_member": {"type": "string"},
                                                              "why": {"type": "string"}}, "required": ["url", "why"]}}},
    {"name": "give_up", "description": "No such public list can be reached.",
     "inputSchema": {"json": {"type": "object", "properties": {"why": {"type": "string"}}, "required": ["why"]}}},
]

SYSTEM = """You find public documents for GridLock, which compares utilities' planned electric transmission projects.

Find the newest public PDF in which the named company lists its planned (future) transmission construction projects, one entry per project, with in-service dates (a project list, not a slide deck that summarizes a few, a map, or a rate case). The list may be a section or appendix of a larger document, such as an integrated resource plan or a ten-year or regional transmission plan; check_pdf shows which pages talk about in-service dates. Such documents are often on the utility's transmission planning, resource planning or OASIS pages, its regional transmission planning group's site, or its state regulator's public docket. Start from addresses you know and follow the links you find: an address you type yourself must be a plain page address (no query string, no address inside another), so search engines, proxies and web archives can't be used. Check a candidate with check_pdf before proposing it. Use only public material, never confidential or CEII filings. Be brief."""


def resolve(host, port):
    """The socket address to connect to for host, if every address it resolves to is a public internet address
    (no localhost, private, link-local or carrier-grade NAT); raises OSError otherwise."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as e:
        raise OSError(f"{host} does not resolve ({e})")
    if not infos or not all(ipaddress.ip_address(i[4][0].split("%")[0]).is_global for i in infos):
        raise OSError(f"{host} is not a public internet address")
    return infos[0][4][:2]


def public(url):
    """Only http(s) addresses whose host resolves to public internet addresses."""
    try:
        u = urllib.parse.urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        resolve(u.hostname, u.port or (443 if u.scheme == "https" else 80))
        return True
    except (OSError, ValueError):
        return False


def _pinned(base):
    class Pinned(base):
        """Connects to the address resolve() just vetted rather than resolving the name again, so a second DNS answer
        (DNS rebinding) can't send the request elsewhere. TLS still checks the certificate against the host name."""

        def connect(self):
            addr = resolve(self.host, self.port)
            self._create_connection = lambda _, *a, **kw: socket.create_connection(addr, *a, **kw)
            super().connect()
    return Pinned


class _HTTP(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_pinned(http.client.HTTPConnection), req)


class _HTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_pinned(http.client.HTTPSConnection), req, context=self._context)


class _Redirects(urllib.request.HTTPRedirectHandler):
    """Redirects only to public addresses, and, when allow is given (an address the model typed), only to ones it allows."""

    def __init__(self, allow=None):
        self.allow = allow

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not public(newurl):
            raise urllib.error.URLError(f"redirect to a non-public address {newurl}")
        if self.allow and not self.allow(newurl):
            raise urllib.error.URLError(f"redirect to {newurl}, which is not a plain page address (no query string, no address inside another)")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@functools.lru_cache(maxsize=256)
def _resolves(name):
    try:
        socket.getaddrinfo(name, None)
        return True
    except OSError:
        return False


HOSTLIKE = re.compile(r"(?:[a-z0-9-]+\.)+[a-z]{2,}", re.I)


def typed_ok(url):
    """An address the model typed rather than found as a link: a plain page address, with no query string and no other
    address inside it, with or without a scheme (a path segment that is a host name that exists, as in
    archive.example/web/2025/utility.com/...). Search engines, proxies and archive lookups all need one or the other."""
    u = urllib.parse.urlparse(url)
    path = u.path + (";" + u.params if u.params else "")
    for _ in range(3):   # %3F, and %253F
        path = urllib.parse.unquote(path)
    if u.query or "?" in path or re.search(r"https?:", path, re.I):
        return False
    return not any(HOSTLIKE.fullmatch(seg) and _resolves(seg.lower()) for seg in path.split("/"))


def get(url, limit, allow=None):
    """(bytes, content type, final url): only public addresses, checked at each connection, redirects included;
    allow(url), when given, must accept every redirect target; at most limit bytes. Proxies are not used (a proxy
    resolves names itself)."""
    if not public(url):
        raise ValueError(f"{url} is not a public internet address")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _HTTP, _HTTPS, _Redirects(allow))
    with opener.open(urllib.request.Request(url, headers={"User-Agent": AGENT}), timeout=60) as r:
        data = r.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f"larger than {limit >> 20} MB")
        return data, r.headers.get("Content-Type", ""), r.geturl()


def pdf_from(data, zip_member=None, limit=MAX_PDF):
    """(PDF bytes, zip member or None) from a download: the bytes themselves, or one PDF in a zip, unpacked to at most
    limit bytes. Raises ValueError saying what is wrong: no PDF, no such member, several to choose from, too large."""
    member = None
    if data[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise ValueError(f"not a readable zip ({e})")
        pdfs = [i for i in z.infolist() if i.filename.lower().endswith(".pdf")]
        names = "\n  " + "\n  ".join(i.filename for i in pdfs)
        if not pdfs:
            raise ValueError("a zip holding no PDF")
        if zip_member is None and len(pdfs) > 1:
            raise ValueError(f"a zip holding several PDFs; name one as its zip member:{names}")
        info = next((i for i in pdfs if i.filename == (zip_member or pdfs[0].filename)), None)
        if info is None:
            raise ValueError(f"the zip holds no PDF named {zip_member!r}; it holds:{names}")
        if info.file_size > limit:
            raise ValueError(f"{info.filename} unpacks to {info.file_size >> 20} MB, over the {limit >> 20} MB limit")
        try:
            with z.open(info) as fh:
                data = fh.read(limit + 1)   # the size in the zip's directory can be false
        except Exception as e:
            raise ValueError(f"could not unpack {info.filename}: {e}")
        if len(data) > limit:
            raise ValueError(f"{info.filename} unpacks to more than {limit >> 20} MB")
        member = info.filename
    if data[:5] != b"%PDF-":
        raise ValueError(f"{member or 'it'} is not a PDF")
    return data, member


class Links(html.parser.HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base, self.links, self.text, self.title, self._a, self._t, self._skip = base, [], [], "", None, False, 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self._a = [urllib.parse.urljoin(self.base, a["href"]), ""]
        self._t = tag == "title"
        self._skip += tag in ("script", "style")

    def handle_endtag(self, tag):
        if tag == "a" and self._a:
            self.links.append(self._a)
            self._a = None
        self._skip -= tag in ("script", "style") and self._skip > 0
        self._t = False

    def handle_data(self, d):
        if self._skip:
            return
        if self._t:
            self.title += d
        if self._a:
            self._a[1] += d
        if d.strip():
            self.text.append(d.strip())


def fetch(url, linked=None, allow=None):
    """A page's title, links (PDFs first) and the start of its text; its links are added to linked."""
    try:
        data, ctype, final = get(url, MAX_PAGE, allow)
    except Exception as e:
        return f"could not fetch {url}: {e}"
    if "pdf" in ctype or data[:5] == b"%PDF-":
        return f"{final} is a PDF; use check_pdf."
    p = Links(final)
    p.feed(data.decode("utf-8", "replace"))
    if linked is not None:
        linked.update(href for href, _ in p.links)
    seen, links = set(), []
    for href, text in sorted(p.links, key=lambda l: ".pdf" not in l[0].lower()):
        if href not in seen and href.startswith("http"):
            seen.add(href)
            links.append(f"- {' '.join(text.split())[:90]} -> {href[:300]}")
    return clip(f"{final}\ntitle: {' '.join(p.title.split())[:200]}\nlinks ({len(links)}):\n" + "\n".join(links[:200]) +
                "\n\ntext:\n" + " ".join(p.text)[:3000], MAX_RESULT)


def check_pdf(url, zip_member=None, allow=None, checked=None):
    """What a candidate PDF is; one that reads is added to checked ({url: {zip members read, None for a plain PDF}})."""
    try:
        data, _, final = get(url, MAX_PDF, allow)
    except Exception as e:
        return f"could not download {url}: {e}"
    try:
        data, member = pdf_from(data, zip_member)
    except ValueError as e:
        return f"{final}: {e}" + ("; call check_pdf again with zip_member" if "zip member" in str(e) else "")
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(data)
        f.flush()
        info = subprocess.run(["pdfinfo", "-isodates", f.name], capture_output=True, text=True, timeout=60).stdout
        try:
            pages = pdf_pages(f.name)
        except subprocess.CalledProcessError:
            return f"{final}: pdftotext can't read it (damaged, or it needs a password)\n{info[:2000]}"
    if len("".join(pages[:3]).strip()) < 200:
        return f"{final}: no text layer on its first pages (a scan?)\n{info[:2000]}"
    if checked is not None:
        checked.setdefault(url, set()).add(member)
    return (f"{final}{' (zip member ' + member + ')' if member else ''}\n{info[:2000]}\npages that mention in-service or completion dates "
            f"(count): {dated_pages(pages)}\n\npage index:\n{clip(page_index(pages, 90), 12000)}\n\nfirst page:\n{pages[0][:3000]}")


def find(company, session, log=print, max_turns=25):
    """{"url", "zip_member", "why", "steps"} for the company's list, or raises SystemExit if none can be reached.
    The url is one check_pdf read in this session."""
    linked, checked, steps = set(), {}, []

    def gated(tool):
        def run(url, **kw):
            if url in linked:
                return tool(url, **kw)   # a link's redirects are the site's own
            if not typed_ok(url):
                return (f"{url} was not a link on any page you fetched, and a typed address must be a plain page address (no query "
                        "string, no address inside another), so search engines, proxies and web archives aren't used. Follow links instead.")
            return tool(url, allow=lambda u: u in linked or typed_ok(u), **kw)
        return run

    def chosen(url, zip_member=None):
        read = checked.get(url, set())
        if zip_member in read or (zip_member is None and len(read) == 1):
            return zip_member if zip_member in read else next(iter(read))
        raise LookupError(f"propose only a PDF check_pdf has read in this session; {url}{' (' + zip_member + ')' if zip_member else ''} "
                          "has not been, so check it first")
    handlers = {"fetch": gated(lambda url, allow=None: fetch(url, linked, allow)),
                "check_pdf": gated(lambda url, zip_member=None, allow=None: check_pdf(url, zip_member, allow, checked))}
    tools = {t["name"]: t for t in TOOLS}
    messages = [{"role": "user", "content": [{"text": f"Company: {company}"}]}]
    for turn in range(1, max_turns + 1):
        resp = session.converse(SYSTEM, messages, tools=TOOLS, max_tokens=16000)
        msg = resp["output"]["message"]
        messages.append(msg)
        cut = stop_problem(resp)
        results = []
        for b in msg["content"]:
            if "toolUse" not in b:
                continue
            u = b["toolUse"]
            bad = [cut] if cut else input_problems(tools.get(u["name"]), u["input"])
            out = cut or (f"bad tool call: {'; '.join(bad)}" if bad else None)
            if not bad and u["name"] == "propose":
                try:
                    member = chosen(u["input"]["url"], u["input"].get("zip_member"))
                    log(f"  proposed {u['input']['url']}: {u['input']['why']}")
                    return {"url": u["input"]["url"], "zip_member": member, "why": u["input"]["why"], "steps": steps}
                except LookupError as e:
                    out = str(e)
            elif not bad and u["name"] == "give_up":
                raise SystemExit(f"No public project list found for {company}: {u['input']['why']}")
            elif not bad:
                log(f"  {u['name']} {u['input'].get('url', '')}")
                steps.append(f"{u['name']} {u['input'].get('url', '')}")
                try:
                    out = handlers[u["name"]](**u["input"])
                except Exception as e:
                    out = f"{u['name']} failed: {type(e).__name__}: {e}"
            results.append({"toolResult": {"toolUseId": u["toolUseId"], "content": [{"text": clip(out, MAX_RESULT)}]}})
        # one moving cache point, on the newest turn, so later turns don't re-send the history uncached
        for m in messages[1:]:
            m["content"] = [b for b in m["content"] if "cachePoint" not in b]
        messages.append({"role": "user", "content": (results or [{"text": cut or "Use the tools, then propose or give_up."}]) + [CACHE]})
    raise SystemExit(f"No public project list found for {company} within {max_turns} steps.")
