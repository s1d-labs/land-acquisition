"""The `egazette` task (see scrape.json): the central Gazette of India at egazette.gov.in.

One queue item = one month of gazettes by publish date. The task opens the site's "Search by Publish Date" form (ASP.NET
WebForms: a cookieless session in the URL, view state, and a Referer check), walks every result page, stores one record per
gazette (all of them: ministry, subject, part, dates, gazette id, topic tags) and downloads the PDF of the gazettes whose topic
is in PDF_TOPICS into the family's bucket (hub.upload_many). The site answers only from India: the pool runs it through the
hub's Indian proxies (HTTPS_PROXY). Its TLS chain is incomplete, so certificates are not verified (VERIFY_TLS=1 turns it on).

Try it without the hub (from the box: `myproxy run python crawl.py --local 01-Oct-2026 31-Oct-2026`):
    python crawl.py --local FROM TO [--pdf] [--save DIR] [--pages A B]      dates as dd-Mon-yyyy
"""
import gzip
import html
import json
import math
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import urljoin

import requests
import urllib3

PROJECT = os.environ.get("SCRAPE_POOL_PROJECT", "")
SITE = os.environ.get("SITE", "https://egazette.gov.in").rstrip("/")
VERIFY = os.environ.get("VERIFY_TLS", "0") == "1"
PDF_TOPICS = {t.strip() for t in os.environ.get("PDF_TOPICS", "land_acquisition,right_of_way,appointment,struck_off,weekly_multi").split(",") if t.strip()}
BUDGET = float(os.environ.get("SCRAPE_MAX_HOURS", "1")) * 3600
DEADLINE = time.time() + BUDGET - min(300, 0.1 * BUDGET)    # stop here: pages and uploads after it are not started
RESERVE = min(1800, 0.3 * BUDGET)                            # a big date range takes up to ~30 minutes: start one only with this left
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
PER_PAGE = 15
if not VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------- parsing (pure)

HIDDEN = re.compile(r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"')
CELL = re.compile(r'<span id="gvGazetteList_lbl_(\w+?)_(\d+)"[^>]*>(.*?)</span>', re.S)
BUTTON = re.compile(r'name="(gvGazetteList\$ctl\d+\$imgbtndownload)" id="gvGazetteList_imgbtndownload_(\d+)"')
SNO = re.compile(r'<td align="right">\s*(\d+)\.&nbsp;')
TOTAL = re.compile(r'lbl_Result"[^>]*>[^<]*?Total No\. of Gazettes\s*:\s*(\d+)')
IFRAME = re.compile(r'<iframe[^>]+src="([^"]+\.pdf)"', re.I)
UGID = re.compile(r"^([A-Z]{2}-[A-Z]{2}-[A-Z]-\d{8})-(\d+)$")
OLD_ID = re.compile(r"\(\s*(\d+)\s*\)")
MULTI = "This Gazette may contains Multiple"
# Parts of a weekly gazette worth the whole PDF: Part I-2 holds appointments and promotions of officers, Part II-3(ii) the
# statutory orders (S.O.), where acquisition notices under the Railways, National Highways and Pipelines Acts appear. Other parts
# (Patent Office, private notices such as change of name, ...) keep their record but get the tag "weekly_other" and no PDF.
WEEKLY_PDF_PARTS = {"Part I-Section 2", "Part II-Section 3-Sub-Section (ii)"}

# Topic tags from the subject line, the office and the ministry. Broad on purpose: every record keeps the full subject, ministry
# and office, so the tags can be redone in SQL later; the tags only decide which PDFs are downloaded now.
LAND_ANY = re.compile(
    r"land[\s_-]*acqui|acqui\w*?\s*of\s*(?:the\s*)?land|acqui\w*\s+(?:the\s+)?lands?\b|acquire\s+(?:of\s+)?(?:the\s+)?lands?\b|intention\s+to\s+acquire"
    r"|right\s+to\s+fair\s+compensation|RFCTLARR|LARR\s+Act|rehabilitation\s+and\s+resettlement"
    r"|special\s+railway\s+project|railway\s+amendment\s+act|national\s+highways?\s+act|competent\s+authority|\bCALA\b"
    r"|petroleum\s+and\s+minerals?\s+pipelines?|\bP\s*&\s*MP\b|\bPMP\s+Act|right\s+of\s+user|\bRoU\b|coal\s+bearing\s+areas|\bCBA\s+Act"
    r"|requisition(?:ing)?\s+and\s+acquisition|land\s+schedule|metro\s+railways?\s*\(\s*construction", re.I)
RAIL_SECTION = re.compile(  # Railways Act 1989 (amended 2008): 20A intention, 20E declaration, 20F award, 7A competent authority,
    # 37A special project. Case-sensitive: subjects like "20ACURSADULPUR" or "7AGN" glue the section to a station code.
    r"(?:[Ss]ection|[Cc]lause|u/s|[Uu]nder|^|[^0-9A-Za-z])\s*(?:20\s*-?\s*\(?\s*[A-H]|7\s*-?\s*\(?\s*A|37\s*-?\s*\(?\s*A)(?=[^a-z]|$)")
RAIL_WORDS = re.compile(
    r"railways?\s+act|\bROB\b|road\s+over\s+bridge|level\s+crossing|\bLC\s*No|doubling|new\s+(?:BG\s+)?line|line\s+project"
    r"|multi\s*-?\s*tracking|quadrupling|rail(?:way)?\s+project|corrigendum", re.I)
HIGHWAY = re.compile(r"(?:section|u/s|under)\s*-?\s*3\s*-?\s*\(?\s*[ADGHJ]\b|\b3\s*-?\s*[ADGHJ]\s+notification|arbitrat", re.I)
PIPELINE = re.compile(r"section\s*[36]\s*\(\s*1\s*\)|pipeline", re.I)
COAL = re.compile(r"^publication\s+of\s+(?:the\s+)?notification\.?$|section\s*(?:4|7|9|11)\s*\(", re.I)
POWER_164 = re.compile(r"notification\s+in\s+respect\s+of\s+M/?s|section\s*164|transmission\s+line", re.I)
APPOINT = re.compile(
    r"\b(?:re-?)?appoint|\bnominat|\bpromoted\s+to|\bpromotion\s+(?:of\s+(?:shri|smt|sh\.|dr)|to\s+the)|\bdeputation\b|additional\s+charge"
    r"|assum\w*\s+(?:the\s+)?charge|\bposting\b|\bconstitution\s+of\b|\b(?:re\s*-?\s*)?constitut\w+\s+(?:the\s+)?(?:board|committee|authority"
    r"|commission|council|tribunal|SEIAA|SEAC)|\bcomposition\s+of\b|\bmembers?\s+(?:of|in|to)\s+the\b|\bchair(?:man|person)\b|tenure\s+of",
    re.I)
STRUCK = re.compile(
    r"struck\s*[-\s]?off|strik(?:e|ing)\s*[-\s]?off|section\s*248|section\s*560|removal\s+of\s+(?:the\s+)?names?"
    r"|names?\s+of\s+(?:the\s+)?compan\w+\s+(?:has|have|is|are)?\s*(?:been\s+)?removed|\bSTK\s*-?\s*[57]\b|de-?register", re.I)


def clean(s):
    s = re.sub(r"<br\s*/?>", " ", s or "", flags=re.I)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    s = re.sub(r"\s+", " ", s).strip()
    return None if s.lower() in ("", "not available", "not applicable") else s


def iso(d):
    try:
        return datetime.strptime(d, "%d-%b-%Y").date().isoformat()
    except (TypeError, ValueError):
        return None


def topics_of(row):
    if row.get("multiple"):
        return ["weekly_multi"] if row.get("part_section") in WEEKLY_PDF_PARTS else ["weekly_other"]
    subject, ministry = row.get("subject") or "", row.get("ministry") or ""
    office = " ".join(filter(None, [row.get("department"), row.get("office")]))
    land = bool(LAND_ANY.search(subject) or re.search(r"land\s+acqui", office, re.I)
                or ("Railways" in ministry and (RAIL_SECTION.search(subject) or RAIL_WORDS.search(subject)))
                or ("Road Transport" in ministry and HIGHWAY.search(subject))
                or ("Petroleum" in ministry and PIPELINE.search(subject))
                or ("Coal" in ministry and COAL.search(subject.strip())))
    tags = ["land_acquisition"] if land else []
    if "Ministry of Power" in ministry and POWER_164.search(subject):
        tags.append("right_of_way")
    if APPOINT.search(subject):
        tags.append("appointment")
    if STRUCK.search(subject):
        tags.append("struck_off")
    return tags


def parse_page(page):
    """One result page -> {"total", "first_sno", "rows": [(record_id, data, button_name)]}. Pure: tested on saved pages."""
    cells = {}
    for name, idx, value in CELL.findall(page):
        cells.setdefault(int(idx), {})[name] = value
    buttons = {int(i): n for n, i in BUTTON.findall(page)}
    total = TOTAL.search(page)
    snos = [int(x) for x in SNO.findall(page)]
    rows = []
    for i in sorted(cells):
        c = cells[i]
        raw_id = clean(c.get("UGID", "")) or ""
        m = UGID.match(raw_id)
        if m:
            rid, ugid, serial = raw_id, raw_id, int(m.group(2))
        else:
            o = OLD_ID.search(c.get("UGID", ""))
            if not o:
                continue                       # a row without any id cannot be stored stably
            ugid, serial = None, int(o.group(1))
            rid = f"NA-{serial}"
        ministry = clean(c.get("Ministry"))
        multiple = bool(ministry and ministry.startswith(MULTI))
        size = re.search(r"([\d.]+)\s*MB", c.get("FileSize", ""))
        data = {
            "ugid": ugid, "serial": serial,
            "ministry": None if multiple else ministry,
            "department": None if multiple else clean(c.get("Department")),
            "office": None if multiple else clean(c.get("Office")),
            "subject": None if multiple else clean(c.get("Subject")),
            "multiple": multiple,
            "category": clean(c.get("Category")),
            "part_section": clean(c.get("PartSection")),
            "issue_date": iso(clean(c.get("IssueDate"))),
            "publish_date": iso(clean(c.get("PublishDate"))),
            "file_mb": float(size.group(1)) if size else None,
            "has_pdf": i in buttons,
        }
        data["topics"] = topics_of(data)
        rows.append((rid, data, buttons.get(i)))
    return {"total": int(total.group(1)) if total else None, "first_sno": snos[0] if snos else None, "rows": rows}


def parse(page):
    """[(record_id, data)] for the gazettes on one result page."""
    return [(rid, data) for rid, data, _ in parse_page(page)["rows"]]


def pager_window(current, pages, count=10):
    """The page numbers the GridView pager shows as links on page `current` (1-based): the group of `count` that holds it, shifted
    to the last `count` pages near the end."""
    first = (current - 1) // count * count
    if first + count > pages:
        first = max(0, pages - count)
    return first + 1, min(first + count, pages)


def hidden(page):
    return {n: html.unescape(v) for n, v in HIDDEN.findall(page)}


def pdf_src(page):
    m = IFRAME.search(page)
    return html.unescape(m.group(1)) if m else None


# ---------------------------------------------------------------- the site

class SiteError(Exception):
    pass


class Site:
    """One browsing session through egazette's search forms. Every request reports the hub's standard counters."""

    def __init__(self, hub):
        self.hub = hub
        self.s = requests.Session()
        self.s.verify = VERIFY
        self.s.headers["User-Agent"] = UA
        self.form = None                       # the last search page (its URL and view state drive the next postback)

    def _req(self, method, url, referer=None, expect_pdf=False, **kw):
        delay = max(0.2, self.hub.settings.get("delay_ms", 0) / 1000)
        time.sleep(delay)
        t = time.time()
        try:
            r = self.s.request(method, url, timeout=(20, 180), headers={"Referer": referer} if referer else None, **kw)
        except requests.RequestException as e:
            self.hub.metric("http_error")
            self.hub.error()
            raise SiteError(f"{type(e).__name__}") from e
        self.hub.metric("n_http")
        self.hub.metric("t_http_s", time.time() - t)
        if r.status_code != 200:
            self.hub.metric(f"http_{r.status_code}")
            if r.status_code in (403, 429, 503):
                self.hub.block()
            raise SiteError(f"HTTP {r.status_code}")
        if expect_pdf:
            if not r.content.startswith(b"%PDF"):
                raise SiteError("not a PDF")
            return r
        if "/error.aspx" in r.url.lower():
            self.hub.metric("site_error_page")
            raise SiteError("site error page")
        r.encoding = "utf-8"
        return r

    def open_search(self):
        home = self._req("GET", SITE + "/")
        f = hidden(home.text)
        f.update({"__EVENTTARGET": "sgzt", "__EVENTARGUMENT": ""})
        menu = self._req("POST", home.url, data=f, referer=home.url)
        f = hidden(menu.text)
        f["btnPublish"] = "Search by Publish Date"
        self.form = self._req("POST", menu.url, data=f, referer=menu.url)
        if "txtDateFrom" not in self.form.text:
            raise SiteError("search form not found")

    def _post(self, extra, dates):
        f = hidden(self.form.text)
        f.update({"txtDateFrom": dates[0], "txtDateTo": dates[1]})
        f.update(extra)
        r = self._req("POST", self.form.url, data=f, referer=self.form.url)
        return r

    def search(self, dates):
        self.open_search()
        self.form = self._post({"ImgSubmit.x": "20", "ImgSubmit.y": "10"}, dates)
        return self.form.text

    def page(self, n, dates):
        self.form = self._post({"__EVENTTARGET": "gvGazetteList", "__EVENTARGUMENT": f"Page${n}"}, dates)
        return self.form.text

    def goto(self, target, pages, dates):
        """Walk the pager from page 1 to page `target`. The site refuses a jump to a page its pager does not show (event
        validation); the pager shows the current group of ten and the "..." links to the next and the previous group. Pages in
        the second half are nearer from the "Last" link, walking back."""
        page, current = None, 1
        if target > pages / 2 + 10:
            page, current = self.page("Last", dates), pages
            self.hub.metric("pager_hops")
        while current != target:
            lo, hi = pager_window(current, pages)
            if lo <= target <= hi:
                current = target
            elif target > hi:
                current = hi + 1                       # the "..." after the window
            else:
                current = lo - 1                       # the "..." before the window
            page = self.page(current, dates)
            self.hub.metric("pager_hops")
        return page

    def pdf_via_viewer(self, button, dates):
        """The site's own download path: postback on the row's button, then ViewPDF.aspx names the file in an iframe."""
        self.form = self._post({f"{button}.x": "5", f"{button}.y": "5"}, dates)
        if "ViewPDF.aspx" not in self.form.text:
            raise SiteError("download postback gave no viewer")
        viewer = self._req("GET", urljoin(self.form.url, "ViewPDF.aspx"), referer=self.form.url)
        src = pdf_src(viewer.text)
        if not src:
            raise SiteError("viewer has no PDF")
        url = urljoin(viewer.url, src)
        return url, self._req("GET", url, referer=viewer.url, expect_pdf=True).content

    def pdf(self, data, button, dates):
        """(source url, bytes). New gazettes (with a UGID) sit at WriteReadData/<year>/<serial>.pdf; older ones need the viewer."""
        if data.get("ugid") and data.get("publish_date"):
            for year in dict.fromkeys([data["publish_date"][:4], (data.get("issue_date") or "")[:4]]):
                if not year:
                    continue
                url = f"{SITE}/WriteReadData/{year}/{data['serial']}.pdf"
                try:
                    return url, self._req("GET", url, referer=self.form.url, expect_pdf=True).content
                except SiteError:
                    self.hub.metric("pdf_direct_miss")
        if not button:
            raise SiteError("no download button")
        return self.pdf_via_viewer(button, dates)


# ---------------------------------------------------------------- one queue item

def walk(hub, key, dates, want_pdfs=True, save=None, page_range=None):
    """Store every gazette published in `dates` (only result pages `page_range` = [first, last] when given; the site's order is
    stable, newest serial first); download the PDFs in PDF_TOPICS. Returns (records, pdfs, pdf_failures)."""
    site = Site(hub)
    first = site.search(dates)
    p = parse_page(first)
    total = p["total"] or 0
    if p["total"] is None and "gvGazetteList" in first:
        raise SiteError("result page without a total")
    pages = pages_all = math.ceil(total / PER_PAGE)
    n, page = 1, first
    if page_range:
        n = max(1, int(page_range[0]))
        pages = min(pages, int(page_range[1]))
        if n > pages:
            return 0, 0, 0
        if n > 1:
            page = site.goto(n, pages_all, dates)
    seen, n_pdf, n_fail = set(), 0, 0
    while True:
        if save:
            os.makedirs(save, exist_ok=True)
            open(os.path.join(save, f"{key}-p{n}.html"), "w", encoding="utf-8").write(page)
        elif hasattr(hub, "upload_many"):
            hub.upload_many([(f"pages/{key}/p{n}.html.gz", gzip.compress(page.encode()), "application/gzip")])
        p = parse_page(page)
        expect_first = (n - 1) * PER_PAGE + 1
        if total and p["first_sno"] != expect_first:
            raise SiteError(f"page {n} starts at row {p['first_sno']}, expected {expect_first}")
        files, stored = [], []
        for rid, data, button in p["rows"]:
            if rid in seen:
                hub.metric("dup_id")
            seen.add(rid)
            data = {**data, "source": "egazette.gov.in", "query": key}
            if want_pdfs and data["has_pdf"] and ("all" in PDF_TOPICS or PDF_TOPICS & set(data["topics"])):
                try:
                    url, body = site.pdf(data, button, dates)
                    path = f"pdf/{(data['publish_date'] or '0000')[:4]}/{rid}.pdf"
                    files.append((path, body, "application/pdf"))
                    data["pdf_url"] = url
                    data["pdf_path"] = path
                    n_pdf += 1
                    hub.metric("pdfs")
                    hub.metric("pdf_bytes", len(body))
                except SiteError as e:
                    n_fail += 1
                    hub.metric("pdf_failed")
                    if hasattr(hub, "failure"):
                        hub.failure("pdf", rid, str(e), query=key, subject=(data.get("subject") or "")[:200])
            stored.append((rid, data))
        if files and hasattr(hub, "upload_many"):
            for up in hub.upload_many(files):
                for rid, data in stored:
                    if data.get("pdf_path") == up["path"]:
                        data["pdf_key"], data["pdf_sha256"], data["pdf_size"] = up.get("key"), up.get("sha256"), up.get("bytes")
        for rid, data in stored:
            hub.record(rid, data)
        hub.metric("result_pages")
        if n >= pages:
            break
        if time.time() > DEADLINE:
            raise TimeoutError(f"time budget ended at page {n} of {pages}")
        while hub.settings.get("paused"):
            time.sleep(30)
        n += 1
        page, site = next_page(hub, site, n, pages_all, dates)
    want = min(total, pages * PER_PAGE) - ((page_range[0] if page_range else 1) - 1) * PER_PAGE
    if len(seen) < want:
        hub.metric("rows_missing", want - len(seen))
    return len(seen), n_pdf, n_fail


def next_page(hub, site, n, pages_all, dates):
    """Page n of the results. A page that fails is tried twice more in a new session (search again, walk the pager to it)."""
    try:
        return site.page(n, dates), site
    except SiteError:
        hub.metric("page_retry")
    for wait in (15, 30):
        time.sleep(wait)
        try:
            site = Site(hub)
            site.search(dates)
            return site.goto(n, pages_all, dates), site
        except SiteError:
            hub.metric("page_retry")
    raise SiteError(f"page {n} did not load")


# ---------------------------------------------------------------- runners

class LocalHub:
    """Stand-in for HubClient so `--local` runs without the hub: counters go to stderr, records to stdout."""
    settings = {"threads": 1, "delay_ms": 300, "paused": False}

    def __init__(self):
        self.counts, self.topics = {}, {}

    def metric(self, name, value=1):
        self.counts[name] = self.counts.get(name, 0) + value

    def error(self, n=1):
        self.metric("errors", n)

    def block(self, n=1):
        self.metric("blocked", n)

    def record(self, rid, data):
        for t in data["topics"] or ["-"]:
            self.topics[t] = self.topics.get(t, 0) + 1
        print(json.dumps({"record_id": rid, "data": data}, ensure_ascii=False))


def local(args):
    hub = LocalHub()
    a, b = args[0], args[1]
    save = args[args.index("--save") + 1] if "--save" in args else None
    pages = [int(x) for x in args[args.index("--pages") + 1:args.index("--pages") + 3]] if "--pages" in args else None
    n, n_pdf, n_fail = walk(hub, f"{a}_{b}", (a, b), want_pdfs="--pdf" in args, save=save, page_range=pages)
    print(f"{n} gazettes, {n_pdf} PDFs ({n_fail} failed), topics {hub.topics}, counters {hub.counts}", file=sys.stderr)


def main():
    from scrape_hub_client import HubClient      # imported here so --local works without it
    hub = HubClient(PROJECT, job_index=int(os.environ.get("SCRAPE_JOB_INDEX", "0")), total_jobs=int(os.environ.get("SCRAPE_TOTAL_JOBS", "1")))
    try:
        while time.time() < DEADLINE - RESERVE:
            if hub.settings.get("paused"):
                time.sleep(30)
                continue
            items = hub.claim(1, steal=True, retry_failed=True)
            if not items:
                break
            item = items[0]
            pl = item["payload"]
            dates = (pl["from"], pl["to"])
            try:
                n, n_pdf, n_fail = walk(hub, item["key"], dates, page_range=pl.get("pages"))
            except (SiteError, TimeoutError) as e:
                hub.complete([item["key"]], "failed", error=str(e)[:200])
                if isinstance(e, TimeoutError):
                    break
                time.sleep(10)
                continue
            if n_fail and n_fail > max(3, 0.2 * (n_pdf + n_fail)):
                hub.complete([item["key"]], "failed", error=f"{n_fail} of {n_pdf + n_fail} PDFs failed")
            else:
                hub.complete([item["key"]], "done", fetched=n)
        hub.close()
    except BaseException as e:
        hub.close("failed", f"{type(e).__name__}: {e}"[:200])
        raise


if __name__ == "__main__":
    if sys.argv[1:2] == ["--local"]:
        local(sys.argv[2:])
    else:
        main()
