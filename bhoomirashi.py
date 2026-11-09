"""The `bhoomirashi` task (see scrape.json), project land-acquisition-bhoomirashi (G14, phase 1).

bhoomirashi.gov.in is the land acquisition portal of the Ministry of Road Transport and Highways (MoRTH, NHAI, NHIDCL). Its public
"Highway Register (State-wise)" (RepState.cshtml -> state_register1.cshtml) lists, for one state, every parcel of land on record
for national highways: highway, chainage, district, sub-district, village, survey number, area and publish date. No login, no
captcha. The compensation amounts (3G awards) are behind the portal's login and are not here.

One queue item = one state (the form's own value, e.g. "MAHARASHTRA"). One POST returns the whole state as one HTML table (Kerala:
42k rows, 19 MB; Maharashtra: 90k rows, 42 MB, ~40 s). Within a highway's group the table leaves a cell empty when it equals the
cell above (state, chainage, district, sub-district, village), so parse() fills those down.

The site answers only from India: the pool runs this through the hub's Indian proxies (set_pool(..., proxy="IN")).

Try it without the hub (from a team box, through a proxy):
    myproxy run .venv/bin/python bhoomirashi.py --local KERALA [--save DIR]
"""
import gzip
import hashlib
import html
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime

import requests

PROJECT = os.environ.get("SCRAPE_POOL_PROJECT", "")
SITE = os.environ.get("SITE", "https://bhoomirashi.gov.in").rstrip("/")
BASE = f"{SITE}/auth/revamp"
BUDGET = float(os.environ.get("SCRAPE_MAX_HOURS", "1")) * 3600
DEADLINE = time.time() + BUDGET - min(300, 0.1 * BUDGET)
RESERVE = min(900, 0.25 * BUDGET)          # one big state: download ~1-3 min, then sending its records takes a while
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

COLS = ["state", "highway", "chainage", "district", "sub_district", "village", "survey_no", "area", "publish_date"]
FILL_DOWN = {"state", "chainage", "district", "sub_district", "village"}
HEADER = "Highway Name"
ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
TAG = re.compile(r"<[^>]+>")
ENC = re.compile(r'<input[^>]*NAME="EncHid"[^>]*>', re.I)
ENC_VALUE = re.compile(r'VALUE="(\d*)"', re.I)
VILLAGE = re.compile(r"^(.*?)\s*\(\s*([^()]*)\s*\)\s*$")


class SiteError(Exception):
    pass


def slug(state):
    """Queue key and file name of a state: "DADRA & NAGAR HAVELI" -> "dadra-nagar-haveli"."""
    return re.sub(r"[^a-z0-9]+", "-", state.lower()).strip("-")


def text(cell):
    return " ".join(html.unescape(TAG.sub(" ", cell)).split())


def iso(d):
    try:
        return datetime.strptime(d, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def number(s):
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def rows_of(page):
    """The table's rows as lists of cell texts, header excluded, with the empty "same as above" cells filled down."""
    out, last, seen_header = [], {}, False
    for r in ROW.findall(page):
        cells = [text(c) for c in CELL.findall(r)]
        if len(cells) != len(COLS):
            continue
        if cells[1] == HEADER:
            seen_header = True
            continue
        row = dict(zip(COLS, cells))
        for c in FILL_DOWN:
            if not row[c]:
                row[c] = last.get(c, "")
        last = row
        out.append(row)
    if not seen_header:
        raise SiteError("no register table in the answer")
    return out


def parse(page, state):
    """Pure: the answer for one state -> [(record_id, data)]. The id is the row's content (stable across runs) plus a counter for
    rows that repeat exactly."""
    out, n = [], Counter()
    for row in rows_of(page):
        m = VILLAGE.match(row["village"])
        data = {
            "state": row["state"] or state, "state_key": state, "highway": row["highway"], "chainage": row["chainage"] or None,
            "district": row["district"] or None, "sub_district": row["sub_district"] or None,
            "village": (m.group(1) if m else row["village"]) or None, "village_code": (m.group(2) or None) if m else None,
            "village_raw": row["village"] or None, "survey_no": row["survey_no"] or None,
            "area": number(row["area"]), "area_raw": row["area"] or None,
            "publish_date": iso(row["publish_date"]), "publish_date_raw": row["publish_date"] or None,
        }
        key = "|".join(row[c] for c in COLS)
        n[key] += 1
        digest = hashlib.sha1(key.encode()).hexdigest()[:16]
        rid = f"{slug(state)}-{digest}" + (f"-{n[key]}" if n[key] > 1 else "")
        out.append((rid, data))
    return out


def fetch_state(hub, state, session=None):
    """GET the home page (session cookie; the form's EncHid token is empty without it), the form, then POST the state.
    Returns the answer's HTML."""
    s = session or requests.Session()
    s.headers.update({"User-Agent": UA})

    def req(method, url, **kw):
        t = time.time()
        try:
            r = s.request(method, url, timeout=(30, 900), **kw)
        except requests.RequestException as e:
            hub.metric("http_error"); hub.error()
            raise SiteError(f"{type(e).__name__} on {url.rsplit('/', 1)[-1]}") from e
        hub.metric("n_http"); hub.metric("t_http_s", time.time() - t)
        if r.status_code != 200:
            hub.metric(f"http_{r.status_code}")
            if r.status_code in (403, 429, 503):
                hub.block()
            raise SiteError(f"HTTP {r.status_code} on {url.rsplit('/', 1)[-1]}")
        r.encoding = "utf-8"
        return r.text

    req("GET", f"{BASE}/login1.cshtml")               # the home page: it gives the session its EncHid token
    form = req("GET", f"{BASE}/RepState.cshtml", headers={"Referer": f"{BASE}/login1.cshtml"})
    m = ENC.search(form)
    if not m:
        raise SiteError("no EncHid on the form (layout changed?)")
    tok = ENC_VALUE.search(m.group(0))
    if not (tok and tok.group(1)):
        hub.metric("enchid_empty")
    time.sleep(max(0.5, hub.settings.get("delay_ms", 0) / 1000))
    files = {k: (None, v) for k, v in {"EncHid": tok.group(1) if tok else "", "act": "1", "type": "2", "highway_id": state, "but": "Submit"}.items()}
    return req("POST", f"{BASE}/state_register1.cshtml", files=files, headers={"Referer": f"{BASE}/RepState.cshtml"})


def do_state(hub, key, state, save=None):
    page = fetch_state(hub, state)
    rows = parse(page, state)
    hub.metric("page_bytes", len(page))
    path = f"register/{key}.html.gz"
    body = gzip.compress(page.encode("utf-8"), 6)
    if save:
        os.makedirs(save, exist_ok=True)
        with open(os.path.join(save, f"{key}.html.gz"), "wb") as f:
            f.write(body)
    else:
        hub.upload_many([(path, body, "text/html")])
    for rid, data in rows:
        data["page_path"] = path
        hub.record(rid, data)
    hub.metric("rows", len(rows))
    hub.metric("rows_no_survey", sum(1 for _, d in rows if not d["survey_no"]))
    hub.metric("rows_no_date", sum(1 for _, d in rows if not d["publish_date"]))
    return len(rows)


class LocalHub:
    """Stand-in for HubClient so `--local` runs without the hub: counters go to stderr, records to stdout."""
    settings = {"threads": 1, "delay_ms": 300, "paused": False}

    def __init__(self):
        self.counts = Counter()

    def metric(self, name, value=1):
        self.counts[name] += value

    def error(self, n=1):
        self.metric("errors", n)

    def block(self, n=1):
        self.metric("blocked", n)

    def record(self, rid, data):
        print(json.dumps({"record_id": rid, "data": data}, ensure_ascii=False))

    def upload_many(self, files):
        return []


def local(args):
    state, save = args[0], (args[args.index("--save") + 1] if "--save" in args else None)
    hub = LocalHub()
    n = do_state(hub, slug(state), state, save=save)
    print(f"{state}: {n} rows; {dict(hub.counts)}", file=sys.stderr)


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
            item = items[0]
            try:
                n = do_state(hub, item["key"], item["payload"]["state"])
            except SiteError as e:
                hub.failure("state", item["key"], str(e)[:200])
                hub.complete([item["key"]], "failed", error=str(e)[:200])
                continue
            hub.complete([item["key"]], "done", fetched=n)
            time.sleep(max(1.0, hub.settings.get("delay_ms", 0) / 1000))
        hub.close()
    except BaseException as e:
        hub.close("failed", f"{type(e).__name__}: {e}"[:200])
        raise


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--local":
        local(sys.argv[2:])
    else:
        main()
