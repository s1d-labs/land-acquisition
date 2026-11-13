#!/usr/bin/env python3
"""Join OCR award documents (la.award_doc) to the bhoomirashi NH register villages (la.bhoomi_village). G14, step 4.

The awards name villages in the state's script (OCR, with noise words around them); bhoomirashi names them in Latin script.
So: for each award, take the district from the site (data/s3waas_districts.json), transliterate each village candidate and
the title words to Latin, reduce both sides to a loose sound skeleton (no 'a', no aspiration 'h', no doubled letters), and
fuzzy-match against the villages of that district only. Words that appear as candidates on 3 or more sites are generic
words (येथील, द्वारा, ...) and are dropped.

Writes la.award_village (one row per award document and matched bhoomirashi village, with the score) and the view
la.award_rate (stated rate per hectare, total, area, matched villages). Re-runnable; run after tools/load_ocr.py.
Needs (box only, not the pool): .venv/bin/pip install rapidfuzz indic-transliteration
Usage, from the repo root: .venv/bin/python tools/join_villages.py [--min-score 93]
"""
import argparse
import collections
import csv
import io
import json
import pathlib
import re
import subprocess
import unicodedata

from indic_transliteration import sanscript
from rapidfuzz import fuzz, process

DB = "ws_ronik"
HERE = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = [(0x0900, sanscript.DEVANAGARI), (0x0980, sanscript.BENGALI), (0x0A00, sanscript.GURMUKHI), (0x0A80, sanscript.GUJARATI),
           (0x0B00, sanscript.ORIYA), (0x0B80, sanscript.TAMIL), (0x0C00, sanscript.TELUGU), (0x0C80, sanscript.KANNADA),
           (0x0D00, sanscript.MALAYALAM)]
# words around village names in the awards; dropped before matching (Latin skeletons are made from them too)
NOISE = {"village", "vill", "mouza", "mauza", "mauje", "mouje", "gram", "tahsil", "tehsil", "taluka", "tal", "district", "dist",
         "award", "land", "acquisition", "of", "no", "the", "and", "for", "in", "at"}
NOISE_NATIVE = re.compile(r"^(मौजे|मौजा|ग्राम|गांव|गाँव|गाव|येथील|येथे|हे|या|की|कि|के|का|में|मैं|स्थित|तहसील|तालुका|जिला|जिल्हा|"
                          r"निकटवर्ती|द्वारा|अधिनिर्णय|अभिनिर्णय|निवाडा|भूसंपादन|भूमि|अधिग्रहण)$")


def psql(sql, *args):
    return subprocess.run(["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", DB, *args, "-c", sql],
                          check=True, capture_output=True, text=True).stdout


def rows(sql):
    return list(csv.reader(io.StringIO(psql(f"copy ({sql}) to stdout with csv"))))


def to_latin(s):
    s = unicodedata.normalize("NFC", s)
    for i, ch in enumerate(s):
        o = ord(ch)
        if 0x0900 <= o < 0x0D80:
            scheme = max(b for b in SCRIPTS if b[0] <= o)[1]
            s = sanscript.transliterate(s, scheme, sanscript.HK)
            s = re.sub(r"M(?=[pbm])", "m", s)                     # anusvara: m before a labial, else n
            s = s.replace("M", "n").replace("r3", "r").replace("L", "l").replace("z", "sh").replace("S", "sh")
            break
    return s


def skeleton(s):
    s = to_latin(s).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"ga+(?:o|u)n?\b|ga+nva?\b|ga+va?\b", "gav", s)   # -gaon, -gaanv, -gav: the same word
    s = re.sub(r"[^a-z ]", " ", s)
    s = re.sub(r"ph", "f", s)
    s = re.sub(r"([bcdgjklmnpqrstvwxyz])h", r"\1", s)        # aspiration
    s = s.replace("w", "v").replace("z", "j").replace("sh", "s").replace("ee", "i").replace("oo", "u").replace("y", "i")
    s = s.replace("a", "")                                       # inherent vowel, written or not
    s = re.sub(r"(.)\1+", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def candidates(villages, title):
    out = set()
    for v in villages:
        words = [w for w in re.split(r"[\s,;:()\-–/|.]+", v) if w and not NOISE_NATIVE.match(w)]
        if not words:
            continue
        out.add(" ".join(words))
        out.update(words)
    m = re.findall(r"(?:village|vill\.?|mouza|mauza|mauje|mau\.|मौजे|ग्राम|गांव)\s*[-:]?\s*([^\s,;:()|]+(?:\s+[^\s,;:()|]+)?)", title or "", re.I)
    for g in m:
        out.add(g)
        out.update(g.split())
    return {c for c in out if c.lower() not in NOISE and not NOISE_NATIVE.match(c)}


RATE_VIEW = """
drop view if exists la.award_rate;
create view la.award_rate as
with stated as (           -- median of the rates the award states; 'per cent' is the land unit only in the south
  select s.path, percentile_cont(0.5) within group (order by s.per_ha) per_ha, count(*) n_rates
  from la.award_stated_rate s join la.award_doc d using (path)
  where s.unit <> 'cent' or d.state in ('Kerala','Tamil Nadu','Karnataka','Andhra Pradesh','Telangana','Puducherry')
  group by 1),
rv as (select v.path, string_agg(distinct v.village, ', ') villages, sum(b.parcels) reg_parcels, sum(b.total_area) reg_area
       from la.award_village v join la.bhoomi_village b on b.village_key = v.village_key and b.district = v.district and b.state = v.state
       group by 1)
select d.path, d.state, d.host, d.award_no, d.award_date, d.total_amount, d.total_area_ha,
       round(d.total_amount / nullif(d.total_area_ha, 0)) implied_per_ha,   -- weak: the total line is often the village area
       round(st.per_ha) stated_per_ha, st.n_rates, rv.villages, rv.reg_parcels, rv.reg_area, d.conf, d.title
from la.award_doc d left join stated st using (path) left join rv using (path)
where d.is_award;
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-score", type=float, default=93)
    a = ap.parse_args()

    hosts = {h["host"]: h for h in json.loads((HERE / "data" / "s3waas_districts.json").read_text())["hosts"]}
    awards = rows("select path, host, state, title, coalesce(array_to_json(villages)::text,'[]') from la.award_doc where is_award")
    bv = rows("select state, district, village, village_key from la.bhoomi_village")

    by_district = collections.defaultdict(list)                  # (state skeleton, district skeleton) -> villages
    for st, dist, vil, vkey in bv:
        by_district[(skeleton(st), skeleton(dist))].append((vil, vkey, dist, st))
    districts_of = collections.defaultdict(list)
    for (sst, sd) in by_district:
        districts_of[sst].append(sd)

    # generic words: candidates that appear on 3 or more sites
    cand_of = {}
    sites = collections.defaultdict(set)
    for path, host, state, title, vj in awards:
        c = candidates(json.loads(vj), title)
        cand_of[path] = c
        for x in c:
            sites[skeleton(x)].add(host)
    generic = {k for k, v in sites.items() if len(v) >= 3}

    out, no_district = [], set()
    for path, host, state, title, vj in awards:
        h = hosts.get(host, {})
        sst = skeleton(h.get("state") or state or "")
        dname = skeleton(h.get("name") or "")
        best = process.extractOne(dname, districts_of.get(sst, []), scorer=fuzz.ratio, score_cutoff=80) if dname else None
        if not best:
            no_district.add(host)
            continue
        pool = by_district[(sst, best[0])]
        names = [skeleton(re.sub(r"\(.*?\)", " ", v[0])) for v in pool]   # 'Pardi (Gotmare)' -> 'Pardi'
        seen = set()
        for c in cand_of[path]:
            k = skeleton(c)
            if len(k.replace(" ", "")) < 4 or k in generic:
                continue
            hits = process.extract(k, names, scorer=fuzz.ratio, score_cutoff=a.min_score, limit=3)
            if any(h[1] == 100 for h in hits):                   # an exact match: drop the near ones
                hits = [h for h in hits if h[1] == 100]
            for name, score, i in hits:
                vil, vkey, dist, st = pool[i]
                if (vkey, dist) in seen:
                    continue
                seen.add((vkey, dist))
                out.append((path, c, to_latin(c), st, dist, vil, vkey, round(score, 1)))

    psql("""create table if not exists la.award_village (path text, cand text, cand_latin text, state text, district text,
            village text, village_key text, score numeric);
            truncate la.award_village;""")
    buf = io.StringIO()
    csv.writer(buf).writerows(out)
    subprocess.run(["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", DB, "-c",
                    "copy la.award_village from stdin with csv"], input=buf.getvalue(), text=True, check=True)
    psql("create index if not exists award_village_path_idx on la.award_village (path)")
    psql(RATE_VIEW)
    matched = len({r[0] for r in out})
    print(f"awards {len(awards)}, matched to >= 1 register village: {matched}, rows {len(out)}")
    print(f"sites with no district in the register ({len(no_district)}): {sorted(no_district)}")


if __name__ == "__main__":
    main()
