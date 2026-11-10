"""The `districts` task (see scrape.json), project land-acquisition-districts (G14, phase 2): land acquisition awards and notices
on district collectorate websites.

Most district sites run on S3WaaS (one WordPress template for ~all districts). The site's own public search (`/?s=<term>`,
`/page/<n>/?s=<term>`) finds notices and documents by title. The WordPress REST API answers 401 and is not used.

One queue item = one district site (data/s3waas_districts.json, from india.s1d.me/data-rich, 2026-11-07). For each search term
(TERMS) the task reads the result pages, keeps the results whose title is about land acquisition (is_land()), opens each kept
page (title, description, dates, PDF links), and stores one record per notice or document. It downloads the PDFs of awards and
payment lists (tags `award`, `payment`) only; for the rest it keeps the PDF links.

The amounts are inside the PDFs (often scans, in Hindi or the state language): this task does not read them.

The sites answer only from India: the pool runs this through the hub's Indian proxies (set_pool(..., proxy="IN")).

Try it without the hub (from a team box, through a proxy):
    myproxy run .venv/bin/python districts.py --local imphalwest.nic.in [--save DIR]
"""
import hashlib
import html
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from urllib.parse import quote, unquote, urlparse

import requests

PROJECT = os.environ.get("SCRAPE_POOL_PROJECT", "")
BUDGET = float(os.environ.get("SCRAPE_MAX_HOURS", "1")) * 3600
DEADLINE = time.time() + BUDGET - min(300, 0.1 * BUDGET)
RESERVE = min(600, 0.2 * BUDGET)
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
MAX_PAGES = 10                       # result pages per search term (10 results each)
MAX_PDF_MB = 40
PDF_TAGS = {t.strip() for t in os.environ.get("PDF_TAGS", "award,payment").split(",") if t.strip()}

TERMS = ["award", "land acquisition", "compensation", "3G", "अवार्ड", "अधिनिर्णय", "भू-अर्जन", "मुआवजा"]

# Titles about land acquisition. STRONG alone is enough; a bare "award" needs a land word too (sites also post prize awards).
STRONG = re.compile(
    r"acqui|aquisi|compensat|rehabilit|resettle|\bR\s*&\s*R\b|RFCTLARR|\bLARR\b|\bCALA\b|competent\s+authority|"
    r"\b3\s*-?\s*\(?\s*[ADGH]\s*\)?(?![a-z])|section\s*(?:11|19|21|23|30)\b|u/s\s*(?:11|19|23)\b|"
    r"भू\s*-?\s*अर्जन|भू\s*-?\s*अधिग्रहण|अधिग्रहण|अर्जन|अधिनिर्णय|मुआवज|प्रतिकर|पुनर्वास|पुनर्व्यवस्थापन",
    re.I)
AWARD = re.compile(r"\baward|अवार्ड|एवार्ड", re.I)
LAND = re.compile(r"\bland|\bNH\b|NH\s*-?\s*\d|highway|expressway|road|railway|rail\s+line|canal|project|village|survey|khasra|"
                  r"patta|mouza|ग्राम|भूमि|राजमार्ग|सड़क|मार्ग|रेल|परियोजना|खसरा|मौजा|नहर", re.I)
PRIZE = re.compile(r"electoral|booth|voter|ceremony|teacher|student|school|education|scholar|शिक्षक|छात्र|विद्यालय|excellence|prize|puraskar|पुरस्कार|sports|felicitat|governance\s+award|"
                   r"best\s+(?:practice|district|performing)|PM'?s?\s+award|national\s+award|award\s+ceremony", re.I)

# Compensation that is not for land (victims, accidents, crops, disasters): dropped unless the title also names acquisition.
OTHER_COMP = re.compile(r"victim|accident|motor|crop|flood|disaster|drought|ex-?\s*gratia|wild\s*life|animal|elephant|covid|"
                        r"cyclone|lightning|मृत|दुर्घटना|फसल|आपदा|सूखा|बाढ़|वन्य", re.I)
ACQ = re.compile(r"acqui|aquisi|भू\s*-?\s*अर्जन|अधिग्रहण|अर्जन|अधिनिर्णय|\b3\s*-?\s*\(?\s*[ADGH]\s*\)?(?![a-z])", re.I)

TAGS = [
    ("award", re.compile(r"\baward|अवार्ड|एवार्ड|अधिनिर्णय|\b3\s*-?\s*\(?\s*G\s*\)?(?![a-z])|section\s*(?:23|30)\b|u/s\s*23\b", re.I)),
    ("payment", re.compile(r"payment|payable|compensation\s+camp|camp\s+for\s+compensation|शिविर|amount\s+of\s+compensation|disburs|भुगतान|वितरण|मुआवजा\s*(?:राशि|सूची)|प्रतिकर\s*(?:राशि|सूची)|cheque", re.I)),
    ("arbitration", re.compile(r"arbitrat|मध्यस्थ|आर्बिट्रेटर", re.I)),
    ("nh", re.compile(r"\bNH\b|NH\s*-?\s*\d|national\s+highway|राष्ट्रीय\s+राजमार्ग|bharat\s*mala|भारतमाला|expressway|greenfield|"
                      r"economic\s+corridor|\bNHAI\b|NHIDCL", re.I)),
    ("railway", re.compile(r"railway|rail\s+line|रेल", re.I)),
    ("notice_3a", re.compile(r"\b3\s*-?\s*\(?\s*A\s*\)?(?![a-z])", re.I)),
    ("notice_3d", re.compile(r"\b3\s*-?\s*\(?\s*D\s*\)?(?![a-z])", re.I)),
    ("sec_11", re.compile(r"(?:section|u/s|धारा)\s*11\b|preliminary\s+notification|प्रारंभिक\s+अधिसूचना", re.I)),
    ("sec_19", re.compile(r"(?:section|u/s|धारा)\s*19\b|declaration|घोषणा|अंतिम\s+अधिसूचना", re.I)),
    ("sia", re.compile(r"social\s+impact|\bSIA\b|सामाजिक\s+समाघात|सामाजिक\s+प्रभाव|public\s+hearing|जन\s+सुनवाई", re.I)),
    ("rnr", re.compile(r"rehabilit|resettle|\bR\s*&\s*R\b|पुनर्वास|पुनर्व्यवस्थापन", re.I)),
]

RESULT = re.compile(r'<a[^>]+href="(https?://[^"]+/(?:notice|document)/[^"#?]+)"[^>]*>(.*?)</a>', re.S | re.I)
NEXT = re.compile(r'href="[^"]*/page/(\d+)/\?s=', re.I)
H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
TABLE = re.compile(r"<table.*?</table>", re.S | re.I)
ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
TH = re.compile(r"<th[^>]*>(.*?)</th>", re.S | re.I)
TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
PDF = re.compile(r'href="(https?://[^"]+\.pdf)"', re.I)
DATE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")


class SiteError(Exception):
    pass


def clean(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def iso(s):
    m = DATE.search(s or "")
    if not m:
        return None
    try:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date().isoformat()
    except ValueError:
        return None


def is_land(title):
    """Pure: is a result title about land acquisition?"""
    if OTHER_COMP.search(title) and not ACQ.search(title):
        return False
    if STRONG.search(title):
        return True
    return bool(AWARD.search(title) and LAND.search(title) and not PRIZE.search(title))


def tags_of(text):
    return [name for name, rx in TAGS if rx.search(text)]


def results(page, host):
    """Pure: one search result page -> ([(url, title)], highest page number linked)."""
    out = []
    for url, a in RESULT.findall(page):
        if urlparse(url).hostname not in (host, f"www.{host}") or "sharer" in url or "share?" in url:
            continue
        t = clean(a)
        if t and t.lower() not in ("read more", "more", "view", "देखें"):
            out.append((url, t))
    pages = [int(n) for n in NEXT.findall(page)]
    return list(dict.fromkeys(out)), max(pages, default=1)


def item(page, url):
    """Pure: a notice or document page -> its fields."""
    h1 = [clean(x) for x in H1.findall(page)]
    title = next((x for x in h1 if x), "")
    fields, desc, dates = {}, None, []
    for tb in TABLE.findall(page):
        heads = [clean(x).lower() for x in TH.findall(tb)]
        for r in ROW.findall(tb):
            cells = [clean(x) for x in TD.findall(r)]
            if not cells:
                continue
            if heads and len(heads) == len(cells):
                fields.update({h: c for h, c in zip(heads, cells) if c})
            dates += [iso(c) for c in cells if DATE.search(c)]
    for k, v in fields.items():
        if any(w in k for w in ("description", "विवरण")):
            desc = v
    pdfs = list(dict.fromkeys(PDF.findall(page)))
    pdfs = [p for p in pdfs if "s3waas" in p or urlparse(p).hostname == urlparse(url).hostname]
    return {"title": title, "description": desc, "dates": [d for d in dict.fromkeys(dates) if d], "pdf_urls": pdfs,
            "fields": fields or None}


class Site:
    def __init__(self, hub, host):
        self.hub, self.host = hub, host
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA})

    def get(self, url, binary=False):
        hub = self.hub
        time.sleep(max(0.3, hub.settings.get("delay_ms", 0) / 1000))
        t = time.time()
        try:
            r = self.s.get(url, timeout=(20, 120 if binary else 60))
        except requests.RequestException as e:
            hub.metric("http_error"); hub.error()
            raise SiteError(f"{type(e).__name__}") from e
        hub.metric("n_http"); hub.metric("t_http_s", time.time() - t)
        if r.status_code != 200:
            hub.metric(f"http_{r.status_code}")
            if r.status_code in (403, 429, 503):
                hub.block()
            raise SiteError(f"HTTP {r.status_code}")
        if binary:
            return r.content
        r.encoding = "utf-8"
        return r.text


def pdf_path(host, url):
    p = urlparse(url).path
    m = re.search(r"/uploads/(\d{4})/(\d{2})/([^/]+\.pdf)$", p, re.I)
    name = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else hashlib.sha1(url.encode()).hexdigest()[:16] + ".pdf"
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", unquote(name))
    return f"pdf/{host}/{name}"


def do_site(hub, host, meta, save=None):
    site = Site(hub, host)
    found, hits = {}, Counter()
    try:
        site.get(f"https://{host}/")
    except SiteError as e:
        raise SiteError(f"home page: {e}")
    for term in TERMS:
        n, last = 1, 1
        while n <= min(last, MAX_PAGES):
            u = f"https://{host}/" + (f"page/{n}/" if n > 1 else "") + f"?s={quote(term)}"
            try:
                page = site.get(u)
            except SiteError:
                hub.metric("search_failed")
                break
            res, last = results(page, host)
            for url, title in res:
                if is_land(title):
                    found.setdefault(url, title)
                    hits[url] += 1
            hub.metric("search_pages")
            if not res:
                break
            n += 1
    n_rec = 0
    for url, title in found.items():
        try:
            data = item(site.get(url), url)
        except SiteError as e:
            hub.failure("item", url[:200], str(e))
            continue
        data["title"] = data["title"] or title
        text = " ".join(x for x in (data["title"], data["description"]) if x)
        data.update({"host": host, "site": meta.get("name"), "state": meta.get("state"), "url": unquote(url),
                     "type": "document" if "/document/" in url else "notice", "result_title": title,
                     "tags": tags_of(text), "date": (data["dates"] or [None])[0], "search_hits": hits[url]})
        if not is_land(text):
            hub.metric("items_not_land")
            continue
        data["pdf_paths"] = []
        if PDF_TAGS & set(data["tags"]):
            files = []
            for p in data["pdf_urls"][:10]:
                try:
                    body = site.get(p, binary=True)
                except SiteError as e:
                    hub.failure("pdf", p[:200], str(e))
                    continue
                if not body.startswith(b"%PDF") or len(body) > MAX_PDF_MB * 2**20:
                    hub.metric("pdf_skipped")
                    continue
                path = pdf_path(host, p)
                files.append((path, body, "application/pdf"))
                data["pdf_paths"].append(path)
                hub.metric("pdfs"); hub.metric("pdf_bytes", len(body))
            if files and save:
                for path, body, _ in files:
                    os.makedirs(os.path.join(save, os.path.dirname(path)), exist_ok=True)
                    with open(os.path.join(save, path), "wb") as f:
                        f.write(body)
            elif files:
                hub.upload_many(files)
        rid = f"{host}:{hashlib.sha1(unquote(url).encode()).hexdigest()[:16]}"
        hub.record(rid, data)
        n_rec += 1
        for t in data["tags"]:
            hub.metric(f"tag_{t}")
    hub.metric("documents", n_rec)
    return n_rec


class LocalHub:
    settings = {"threads": 1, "delay_ms": 500, "paused": False}

    def __init__(self):
        self.counts = Counter()

    def metric(self, name, value=1):
        self.counts[name] += value

    def error(self, n=1):
        self.metric("errors", n)

    def block(self, n=1):
        self.metric("blocked", n)

    def failure(self, kind, key, reason, **detail):
        print(f"failure {kind} {key}: {reason}", file=sys.stderr)

    def record(self, rid, data):
        print(json.dumps({"record_id": rid, "data": data}, ensure_ascii=False))

    def upload_many(self, files):
        return []


def local(args):
    host, save = args[0], (args[args.index("--save") + 1] if "--save" in args else None)
    hub = LocalHub()
    n = do_site(hub, host, {"name": host, "state": None}, save=save)
    print(f"{host}: {n} documents; {dict(hub.counts)}", file=sys.stderr)


def main():
    from scrape_hub_client import HubClient
    hub = HubClient(PROJECT, job_index=int(os.environ.get("SCRAPE_JOB_INDEX", 0)),
                    total_jobs=int(os.environ.get("SCRAPE_TOTAL_JOBS", 1)))
    try:
        while time.time() < DEADLINE - RESERVE:
            if hub.settings.get("paused"):
                time.sleep(30)
                continue
            items = hub.claim(1, steal=True, retry_failed=True)
            if not items:
                break
            it = items[0]
            try:
                n = do_site(hub, it["payload"]["host"], it["payload"])
            except SiteError as e:
                hub.failure("site", it["key"], str(e)[:200])
                hub.complete([it["key"]], "failed", error=str(e)[:200])
                continue
            hub.complete([it["key"]], "done", fetched=n)
        hub.close()
    except BaseException as e:
        hub.close("failed", f"{type(e).__name__}: {e}"[:200])
        raise


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--local":
        local(sys.argv[2:])
    else:
        main()
