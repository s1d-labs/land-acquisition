#!/usr/bin/env python3
"""Build the OCR queue items (round 1) for the land-acquisition-districts project.

Reads public.records of scrape_hub (read-only, via psql) and writes
data/ocr_items.json: one entry per distinct PDF path, grouped into
queue items of at most PER_ITEM PDFs, sorted by host and path.
"""
import datetime
import json
import os
import re
import subprocess
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "ocr_items.json")
PROJECT = "land-acquisition-districts"
PER_ITEM = 10
KEY_MAX = 190
ROUND = 1

SQL = (
    "select coalesce(json_agg(x order by x.record_id), '[]'::json) from ("
    " select record_id, data->>'host' as host, data->>'state' as state,"
    " data->>'title' as title, data->'tags' as tags, data->'pdf_paths' as pdf_paths"
    " from public.records"
    f" where project = '{PROJECT}'"
    " and data->'tags' ?| array['award','payment']"
    " and jsonb_array_length(data->'pdf_paths') > 0"
    ") x"
)


def run_query():
    proc = subprocess.run(
        ["psql", "-d", "scrape_hub", "-AtXq", "-v", "ON_ERROR_STOP=1", "-c", SQL],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        sys.exit(f"psql failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def sanitize(key):
    return re.sub(r"[^A-Za-z0-9._~:-]", "-", key)


def main():
    rows = run_query()

    pdfs = {}
    for r in rows:
        tags = r.get("tags") or []
        for path in r.get("pdf_paths") or []:
            e = pdfs.get(path)
            if e is None:
                e = {
                    "path": path,
                    "record_ids": [],
                    "host": r.get("host") or "",
                    "state": r.get("state") or "",
                    "title": (r.get("title") or "")[:200],
                    "tags": set(),
                }
                pdfs[path] = e
            if r["record_id"] not in e["record_ids"]:
                e["record_ids"].append(r["record_id"])
            e["tags"].update(tags)

    entries = []
    for e in pdfs.values():
        entries.append({
            "path": e["path"],
            "record_ids": e["record_ids"],
            "host": e["host"],
            "state": e["state"],
            "title": e["title"],
            "tags": sorted(e["tags"]),
        })
    entries.sort(key=lambda e: (e["host"], e["path"]))

    items = []
    used = set()
    for i in range(0, len(entries), PER_ITEM):
        chunk = entries[i:i + PER_ITEM]
        key = sanitize(("ocr1:" + chunk[0]["path"].replace("/", "~"))[:KEY_MAX])
        if key in used:
            sys.exit(f"duplicate key: {key}")
        used.add(key)
        items.append({"key": key, "payload": {"pdfs": chunk, "expected": len(chunk)}})

    out = {
        "made": datetime.date.today().isoformat(),
        "round": ROUND,
        "pdfs": len(entries),
        "items": items,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=None, separators=(",", ":"))

    size = os.path.getsize(OUT)
    by_state = Counter(e["state"] for e in entries)
    print(f"records: {len(rows)}")
    print(f"distinct PDFs: {len(entries)}")
    print(f"items: {len(items)}")
    print("PDFs per state (top 15):")
    for state, n in by_state.most_common(15):
        print(f"  {state or '(none)'}: {n}")
    print(f"file size: {size} bytes ({size / 1024:.1f} KiB) -> {OUT}")


if __name__ == "__main__":
    main()
