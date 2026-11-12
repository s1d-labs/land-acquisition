"""land-acquisition-ocr: text of the award and payment PDFs that land-acquisition-districts stored in the bucket (G14, step 3).

Each queue item holds up to 10 PDFs (data/ocr_items.json, made by tools/ocr_items.py from the districts records). For each PDF:
- download it with a short-lived link from the hub (hub.read_urls; no storage keys, no proxy: the files are in our own bucket);
- for each page (up to MAX_PAGES): use the PDF's own text layer when it has one (pdftotext), else OCR the page with Tesseract;
- the OCR language follows the page's script (Tesseract OSD): Latin pages use `eng` only, because the Indic models drop the
  digit 1 on English pages ("2013" -> "203"); other pages use the state's language + eng;
- store one record per PDF (id = the PDF's bucket path) with the text of every page, its confidence, and the fields that
  ocr_extract.extract() finds (amounts, total, villages, survey numbers, area). The full text also goes to the bucket as
  ocr/<path>.txt, because very long PDFs are cut in the record.

Tesseract is installed by this task on its first run (GitHub runners have sudo), so the other tasks of the family do not pay for it.
Local run, no hub:  .venv/bin/python ocr.py --local some.pdf "Madhya Pradesh"
"""
import concurrent.futures as cf
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

PROJECT = os.environ.get("SCRAPE_POOL_PROJECT", "land-acquisition-ocr")
SOURCE = os.environ.get("SOURCE_PROJECT", "land-acquisition-districts")
MAX_PAGES = int(os.environ.get("MAX_PAGES", "60"))
DPI = int(os.environ.get("OCR_DPI", "300"))
RECORD_TEXT_MAX = 150_000          # characters of text kept in the record; the full text is always in the bucket
BUDGET = float(os.environ.get("SCRAPE_MAX_HOURS", "1")) * 3600
DEADLINE = time.time() + BUDGET - min(300, 0.1 * BUDGET)
WORKERS = max(1, os.cpu_count() or 2)

LANG = {"Maharashtra": "mar", "Andhra Pradesh": "tel", "Telangana": "tel", "Kerala": "mal", "Lakshadweep": "mal",
        "Tamil Nadu": "tam", "Puducherry": "tam", "Tripura": "ben", "West Bengal": "ben", "Punjab": "pan",
        "Karnataka": "kan", "Gujarat": "guj", "Odisha": "ori", "Assam": "asm"}
ALL_LANGS = sorted(set(LANG.values()) | {"hin", "eng"})
SCRIPT_LANG = {"Devanagari": "hin", "Telugu": "tel", "Malayalam": "mal", "Tamil": "tam", "Bengali": "ben", "Gurmukhi": "pan",
               "Kannada": "kan", "Gujarati": "guj", "Oriya": "ori"}


def ensure_tesseract():
    if shutil.which("tesseract") and shutil.which("pdftoppm"):
        return
    pkgs = ["tesseract-ocr", "poppler-utils"] + [f"tesseract-ocr-{l}" for l in ALL_LANGS if l != "eng"]
    subprocess.run(["sudo", "apt-get", "update", "-qq"], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["sudo", "apt-get", "install", "-y", "-qq", *pkgs], check=True, stdout=subprocess.DEVNULL)


def page_count(pdf):
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(m.group(1)) if m else 0


def text_layer(pdf, n):
    r = subprocess.run(["pdftotext", "-layout", "-f", str(n), "-l", str(n), str(pdf), "-"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def ocr_page(pdf, n, state_lang, tmp):
    """One page: (text, method, script, lang, mean confidence, words)."""
    t = text_layer(pdf, n)
    if len(re.sub(r"\s", "", t)) >= 100:
        return t, "text", None, None, None, len(t.split())
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    base = Path(tmp) / f"p{n}"
    subprocess.run(["pdftoppm", "-r", str(DPI), "-f", str(n), "-l", str(n), "-gray", "-png", "-singlefile", str(pdf), str(base)],
                   check=True, capture_output=True)
    img = f"{base}.png"
    osd = subprocess.run(["tesseract", img, "-", "--psm", "0"], capture_output=True, text=True, env=env).stdout
    m = re.search(r"^Script:\s*(\S+)", osd, re.M)
    script = m.group(1) if m else None
    if script == "Latin":
        lang = "eng"
    else:
        lang = f"{SCRIPT_LANG.get(script) or state_lang}+eng"
    subprocess.run(["tesseract", img, str(base), "-l", lang, "--psm", "6", "txt", "tsv"], capture_output=True, env=env)
    text = Path(f"{base}.txt").read_text(encoding="utf-8", errors="replace") if Path(f"{base}.txt").exists() else ""
    confs = []
    if Path(f"{base}.tsv").exists():
        for line in Path(f"{base}.tsv").read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
            c = line.split("\t")
            if len(c) >= 12 and c[0] == "5" and c[11].strip():
                try:
                    confs.append(float(c[10]))
                except ValueError:
                    pass
    for f in Path(tmp).glob(f"p{n}.*"):
        f.unlink()
    conf = round(sum(confs) / len(confs), 1) if confs else 0.0
    return text, "ocr", script, lang, conf, len(confs)


def ocr_pdf(pdf, state, pool):
    """All pages of one PDF, OCR spread over the pool's threads. Returns the record fields (without the source metadata)."""
    n_pages = page_count(pdf)
    todo = list(range(1, min(n_pages, MAX_PAGES) + 1))
    state_lang = LANG.get(state, "hin")
    with tempfile.TemporaryDirectory() as tmp:
        futs = [pool.submit(ocr_page, pdf, n, state_lang, tmp) for n in todo]
        pages = []
        for n, f in zip(todo, futs):
            text, method, script, lang, conf, words = f.result()
            pages.append({"n": n, "method": method, "script": script, "lang": lang, "conf": conf, "words": words, "text": text})
    ocr = [p for p in pages if p["method"] == "ocr" and p["words"]]
    conf = round(sum(p["conf"] * p["words"] for p in ocr) / sum(p["words"] for p in ocr), 1) if ocr else None
    full = "\f\n".join(p["text"] for p in pages)
    return {"pages_total": n_pages, "pages_read": len(pages), "pages_ocr": len(ocr),
            "pages_text_layer": sum(p["method"] == "text" for p in pages), "conf": conf, "pages": pages}, full


def extract_fields(full):
    try:
        from ocr_extract import extract
    except ImportError:
        return None
    try:
        return extract(full)
    except Exception as e:                      # a parser bug must not lose the OCR text
        return {"error": f"{type(e).__name__}: {e}"[:200]}


def build_record(meta, res, full):
    data = {"path": meta["path"], "source_project": SOURCE, "record_ids": meta.get("record_ids", []), "host": meta.get("host"),
            "state": meta.get("state"), "title": meta.get("title"), "tags": meta.get("tags", []),
            "text_key": f"ocr/{meta['path']}.txt", **res, "fields": extract_fields(full)}
    kept, cut = 0, False
    for p in data["pages"]:
        if kept + len(p["text"]) > RECORD_TEXT_MAX:
            p["text"], cut = "", True
        kept += len(p["text"])
    data["text_cut"] = cut
    return data


def download(hub, session, key, url):
    t = time.time()
    try:
        r = session.get(url, timeout=120)
    except requests.RequestException:
        hub.metric("http_error"); hub.error()
        return None
    hub.metric("n_http"); hub.metric("t_http_s", time.time() - t)
    if r.status_code != 200:
        hub.metric(f"http_{r.status_code}")
        return None
    return r.content


def read_links(hub, paths):
    """Links for SOURCE's stored files. The hub names stored objects '<project>/<path>'; accept either form back."""
    keys = [f"{SOURCE}/{p}" for p in paths]
    urls = hub.read_urls(SOURCE, keys)
    out = {}
    for p, k in zip(paths, keys):
        out[p] = urls.get(k) or urls.get(p)
    missing = [p for p in paths if not out[p]]
    if missing:                                 # the other key form
        alt = hub.read_urls(SOURCE, missing)
        for p in missing:
            out[p] = alt.get(p) or alt.get(f"{SOURCE}/{p}")
    return out


def do_item(hub, session, item, pool):
    pdfs = item["payload"]["pdfs"]
    links = read_links(hub, [m["path"] for m in pdfs])
    done, texts = 0, []
    with tempfile.TemporaryDirectory() as tmp:
        for m in pdfs:
            if time.time() > DEADLINE:
                raise TimeoutError("out of time inside an item")
            url = links.get(m["path"])
            body = download(hub, session, m["path"], url) if url else None
            if not body or not body.startswith(b"%PDF"):
                hub.failure("pdf", m["path"], "no link" if not url else "download failed" if not body else "not a PDF")
                continue
            f = Path(tmp) / "doc.pdf"
            f.write_bytes(body)
            try:
                res, full = ocr_pdf(f, m.get("state"), pool)
            except subprocess.CalledProcessError as e:
                hub.failure("pdf", m["path"], f"pdftoppm/tesseract failed: {e}"[:200])
                continue
            hub.record(m["path"], build_record(m, res, full))
            texts.append((f"ocr/{m['path']}.txt", full.encode("utf-8"), "text/plain; charset=utf-8"))
            hub.metric("pdfs_read"); hub.metric("pages_read", res["pages_read"]); hub.metric("pages_ocr", res["pages_ocr"])
            done += 1
    if texts:
        hub.upload_many(texts)
    return done, len(pdfs)


def main():
    from scrape_hub_client import HubClient
    ensure_tesseract()
    hub = HubClient(PROJECT, job_index=int(os.environ.get("SCRAPE_JOB_INDEX", 0)),
                    total_jobs=int(os.environ.get("SCRAPE_TOTAL_JOBS", 1)))
    s = requests.Session()
    try:
        with cf.ThreadPoolExecutor(WORKERS) as pool:
            while time.time() < DEADLINE - 600:          # an item takes a few minutes: start none in the last 10
                if hub.settings.get("paused"):
                    time.sleep(30)
                    continue
                items = hub.claim(1, steal=True, retry_failed=True)
                if not items:
                    break
                it = items[0]
                done, n = do_item(hub, s, it, pool)
                if done == 0:
                    hub.complete([it["key"]], "failed", error=f"0 of {n} PDFs read")
                else:
                    hub.complete([it["key"]], "done", fetched=done)
        hub.close()
    except BaseException as e:
        hub.close("failed", f"{type(e).__name__}: {e}"[:200])
        raise


def local(args):
    pdf, state = Path(args[0]), (args[1] if len(args) > 1 else "")
    t = time.time()
    with cf.ThreadPoolExecutor(WORKERS) as pool:
        res, full = ocr_pdf(pdf, state, pool)
    data = build_record({"path": pdf.name}, res, full)
    for p in data["pages"]:
        print(f"page {p['n']:3d} {p['method']:4} {p['script'] or '-':11} {p['lang'] or '-':8} conf={p['conf']} words={p['words']}")
    print(f"{res['pages_read']} of {res['pages_total']} pages, conf {res['conf']}, {time.time() - t:.0f} s")
    f = data["fields"]
    if f and "error" not in f:
        print("total:", f.get("total"), "villages:", f.get("villages", [])[:3], "is_award:", f.get("is_award"))
    elif f:
        print("extract:", f)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--local":
        local(sys.argv[2:])
    else:
        main()
