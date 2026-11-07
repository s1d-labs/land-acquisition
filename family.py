"""What this family scrapes. Read by setup_family.py (the setup workflow).

land-acquisition: official notices of land acquisition (and the appointments and struck-off notices around them) from the
Gazette of India and, later, the state gazettes. One project per gazette site; `target` is the host it hits.

egazette.gov.in answers only from India and has an incomplete TLS chain: the pool runs it through the hub's Indian proxies
(proxy="IN"), and crawl.py does not verify certificates. Queue items are publish-date ranges sized from the per-month counts in
data/egazette_months.json (counted on 2026-11-05). Running setup again adds the days that have passed since (keys are kept).
"""
import calendar
import json
from datetime import date, timedelta
from pathlib import Path

FAMILY = json.loads((Path(__file__).parent / "scrape.json").read_text())["family"]
HERE = Path(__file__).parent
MONTHS = json.loads((HERE / "data" / "egazette_months.json").read_text())["months"]

MAX_ITEM = 1200          # gazettes per queue item: about 80 result pages and up to ~600 PDFs, well inside one run
BULK_DAYS = {"2013-03-26": 61442}   # the 2013 load of the old archive: one publish date, split into ranges of result pages
BULK_PAGES = 100
START = date(2008, 1, 1)


def d(x):
    return x.strftime("%d-%b-%Y")


def item(a, b, expected, pages=None):
    key = f"pub-{a.isoformat()}" + (f"_{b.isoformat()}" if b != a else "") + (f"-p{pages[0]}-{pages[1]}" if pages else "")
    payload = {"from": d(a), "to": d(b), "expected": int(expected)}
    if pages:
        payload["pages"] = list(pages)
    return {"key": key, "payload": payload}


def egazette_items(today=None):
    today = today or date.today()
    last_full_day = today - timedelta(days=1)
    out, y, m = [], START.year, START.month
    while (y, m) <= (last_full_day.year, last_full_day.month):
        first, mend = date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
        n = MONTHS.get(f"{y}-{m:02d}")
        complete = mend <= last_full_day and n is not None     # counted, and over
        if complete and n == 0:
            pass                                               # nothing was published that month
        elif complete and n <= MAX_ITEM:
            out.append(item(first, mend, n))
        else:                                                  # big month, or the current one: ranges of a week (1-7, 8-14, 15-21, 22-end)
            bulk = {date.fromisoformat(x): c for x, c in BULK_DAYS.items() if first <= date.fromisoformat(x) <= mend}
            per_day = ((n or 0) - sum(bulk.values())) / max(1, mend.day - len(bulk))
            for s in (1, 8, 15, 22):
                a, e = date(y, m, s), (date(y, m, s + 6) if s < 22 else mend)
                if e > last_full_day:
                    break                                      # an incomplete week: the next setup run adds it
                cur = a
                for day in sorted(x for x in bulk if a <= x <= e) + [e + timedelta(days=1)]:
                    if cur < day:
                        out.append(item(cur, day - timedelta(days=1), per_day * (day - cur).days))
                    if day in bulk:
                        pages = -(-bulk[day] // 15)
                        for p in range(1, pages + 1, BULK_PAGES):
                            q = min(pages, p + BULK_PAGES - 1)
                            out.append(item(day, day, min(bulk[day], q * 15) - (p - 1) * 15, (p, q)))
                    cur = day + timedelta(days=1)
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


SEED_LIMIT = None        # first pool run used 5 (2026-11-07: 5 of 5 done, 4,548 records, 0 errors). None = seed everything.
EGAZETTE = egazette_items()
if SEED_LIMIT:
    EGAZETTE = EGAZETTE[-SEED_LIMIT:]

PROJECTS = [
    {
        "name": f"{FAMILY}-egazette",
        "title": "Gazette of India (egazette.gov.in): all central gazettes, PDFs of land acquisition, appointment and struck-off notices",
        "task": "egazette",
        "target": "egazette.gov.in",
        "env": {"SITE": "https://egazette.gov.in",
                "PDF_TOPICS": "land_acquisition,right_of_way,appointment,struck_off,weekly_multi"},
        "probe": None,               # see PROBES: attach f"{FAMILY}:egazette.gov.in" once the hub shows it green
        "proxy": "IN",
        "max_workers": 4,
        "threads": 1, "delay_ms": 300,
        "unit_label": "date range", "record_label": "gazette",
        "items": EGAZETTE,
    },
]

# Health checks the hub runs every minute (through an Indian proxy: the host is .gov.in). Not attached to the project yet: the site's
# TLS chain is incomplete, and a check that fails for that reason alone would hold all the work.
PROBES = [{"name": f"{FAMILY}:egazette.gov.in", "url": "https://egazette.gov.in/", "expect": "Gazette of India", "proxy": "IN"}]
