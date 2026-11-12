#!/usr/bin/env python3
"""Copy land-acquisition OCR records (and optionally bhoomirashi parcels) from scrape_hub into ws_ronik, schema la.

Usage, from the repo root:
    python tools/load_ocr.py               # OCR records -> la.ocr_raw -> la.award_*
    python tools/load_ocr.py --bhoomi      # also bhoomirashi parcels -> la.bhoomi_raw -> la.bhoomi_*
    python tools/load_ocr.py --skip-copy   # rebuild la.award_* from the existing la.ocr_raw only

Needs the psql client. scrape_hub is read only; ws_ronik is written.
Re-runnable: staging is truncated, derived tables are rebuilt in one transaction.
"""
import argparse
import pathlib
import subprocess
import sys

SRC_DB = "scrape_hub"
DST_DB = "ws_ronik"
OCR_PROJECT = "land-acquisition-ocr"
BHOOMI_PROJECT = "land-acquisition-bhoomirashi"
BHOOMI_SQL = pathlib.Path(__file__).resolve().parent / "bhoomirashi_villages.sql"
BHOOMI_KEYS = [
    "state", "district", "sub_district", "village", "village_code", "survey_no",
    "area", "area_raw", "highway", "chainage", "publish_date", "page_path",
]
PSQL = ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1"]

SETUP_SQL = r"""
set client_min_messages = warning;
create schema if not exists la;

create or replace function la.try_numeric(t text) returns numeric
language sql immutable as $$
  select case when btrim(replace($1, ',', '')) ~ '^-?[0-9]+(\.[0-9]+)?$'
              then btrim(replace($1, ',', ''))::numeric end $$;

create or replace function la.try_int(t text) returns integer
language sql immutable as $$
  select case when btrim($1) ~ '^-?[0-9]+$' then btrim($1)::integer end $$;

create or replace function la.try_date(t text) returns date
language sql immutable as $$
  select case when btrim($1) ~ '^\d{4}-\d{2}-\d{2}$' then btrim($1)::date
              when btrim($1) ~ '^\d{2}/\d{2}/\d{4}$' then to_date(btrim($1), 'DD/MM/YYYY') end $$;

create or replace function la.jsonb_text_array(j jsonb) returns text[]
language sql immutable as $$
  select case when jsonb_typeof($1) = 'array' then array(select jsonb_array_elements_text($1))
              when jsonb_typeof($1) = 'string' then array[$1 #>> '{}'] end $$;

create or replace function la.jsonb_elems(j jsonb) returns setof jsonb
language sql immutable as $$
  select e from jsonb_array_elements(case when jsonb_typeof($1) = 'array' then $1 else '[]'::jsonb end) e $$;

create table if not exists la.ocr_raw (record_id text primary key, data jsonb not null);

create table if not exists la.award_doc (
  path text primary key, host text, state text, title text, tags text[], record_ids text[],
  pages_total integer, pages_read integer, pages_ocr integer, conf numeric,
  is_award boolean, total_amount numeric, total_words_match boolean, award_no text,
  award_date date, villages text[], survey_numbers text[],
  loaded_at timestamptz not null default now()
);
create table if not exists la.award_amount (path text, line integer, value numeric, label text, raw text);
create table if not exists la.award_area (path text, value numeric, unit text, raw text);
create table if not exists la.award_page (
  path text, n integer, method text, script text, lang text, conf numeric, words integer, text text,
  primary key (path, n)
);
create index if not exists award_doc_state_idx on la.award_doc (state);
create index if not exists award_doc_is_award_idx on la.award_doc (is_award);
create index if not exists award_amount_path_idx on la.award_amount (path);
create index if not exists award_area_path_idx on la.award_area (path);
"""

BUILD_SQL = r"""
truncate la.award_doc, la.award_amount, la.award_area, la.award_page;

insert into la.award_doc (path, host, state, title, tags, record_ids, pages_total, pages_read, pages_ocr,
                          conf, is_award, total_amount, total_words_match, award_no, award_date,
                          villages, survey_numbers)
select
  r.data->>'path',
  r.data->>'host',
  r.data->>'state',
  r.data->>'title',
  la.jsonb_text_array(r.data->'tags'),
  la.jsonb_text_array(r.data->'record_ids'),
  la.try_int(r.data->>'pages_total'),
  la.try_int(r.data->>'pages_read'),
  la.try_int(r.data->>'pages_ocr'),
  la.try_numeric(r.data->>'conf'),
  case when r.data->'fields'->>'is_award' in ('true', 'false') then (r.data->'fields'->>'is_award')::boolean end,
  la.try_numeric(coalesce(r.data->'fields'->'total'->>'value', r.data->'fields'->>'total')),
  case when r.data->'fields'->>'total_words_match' in ('true', 'false')
       then (r.data->'fields'->>'total_words_match')::boolean end,
  r.data->'fields'->>'award_no',
  la.try_date(r.data->'fields'->>'award_date'),
  la.jsonb_text_array(r.data->'fields'->'villages'),
  la.jsonb_text_array(r.data->'fields'->'survey_numbers')
from la.ocr_raw r;

insert into la.award_amount (path, line, value, label, raw)
select r.data->>'path', coalesce(la.try_int(a->>'line'), x.o::integer),
       la.try_numeric(a->>'value'), a->>'label', a->>'raw'
from la.ocr_raw r
cross join lateral la.jsonb_elems(r.data->'fields'->'amounts') with ordinality as x(a, o);

insert into la.award_area (path, value, unit, raw)
select r.data->>'path', la.try_numeric(a->>'value'), a->>'unit', a->>'raw'
from la.ocr_raw r
cross join lateral la.jsonb_elems(r.data->'fields'->'area') as x(a);

insert into la.award_page (path, n, method, script, lang, conf, words, text)
select r.data->>'path', coalesce(la.try_int(p->>'n'), x.o::integer), p->>'method', p->>'script',
       p->>'lang', la.try_numeric(p->>'conf'), la.try_int(p->>'words'), p->>'text'
from la.ocr_raw r
cross join lateral la.jsonb_elems(r.data->'pages') with ordinality as x(p, o);
"""

BHOOMI_EXPORT = (
    "\\copy (select record_id, jsonb_build_object("
    + ", ".join(f"'{k}', data->'{k}'" for k in BHOOMI_KEYS)
    + f") from records where project = '{BHOOMI_PROJECT}') to stdout"
)
OCR_EXPORT = (
    f"\\copy (select record_id, data from records where project = '{OCR_PROJECT}') to stdout"
)


def psql(*args, **kw):
    return subprocess.run([*PSQL, *args], check=True, text=True, **kw)


def run_sql(sql, db=DST_DB, single=False):
    cmd = [*PSQL, "-d", db]
    if single:
        cmd.append("-1")
    subprocess.run(cmd + ["-f", "-"], input=sql, check=True, text=True)


def stream_copy(export_cmd, target, columns):
    """Pipe records from scrape_hub straight into a ws_ronik staging table."""
    src = subprocess.Popen([*PSQL, "-d", SRC_DB, "-c", export_cmd], stdout=subprocess.PIPE)
    try:
        subprocess.run(
            [*PSQL, "-d", DST_DB, "-c", f"truncate {target}",
             "-c", f"\\copy {target} ({columns}) from stdin"],
            stdin=src.stdout, check=True,
        )
    finally:
        src.stdout.close()
        rc = src.wait()
    if rc != 0:
        sys.exit(f"export from {SRC_DB} failed (exit {rc})")


def print_counts(tables):
    q = " union all ".join(f"select '{t}', count(*) from {t}" for t in tables)
    out = subprocess.run([*PSQL, "-d", DST_DB, "-At", "-F", "\t", "-c", q],
                         check=True, text=True, capture_output=True).stdout
    print("row counts (ws_ronik):")
    for line in out.splitlines():
        name, n = line.split("\t")
        print(f"  {name:<18} {int(n):>12,}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bhoomi", action="store_true", help="also load the bhoomirashi parcels")
    ap.add_argument("--skip-copy", action="store_true", help="rebuild OCR tables from la.ocr_raw only")
    args = ap.parse_args()

    run_sql(SETUP_SQL)
    if args.skip_copy:
        print("skip copy: using existing la.ocr_raw")
    else:
        print(f"copying {OCR_PROJECT} from {SRC_DB} ...")
        stream_copy(OCR_EXPORT, "la.ocr_raw", "record_id, data")
    run_sql(BUILD_SQL, single=True)
    print_counts(["la.ocr_raw", "la.award_doc", "la.award_amount", "la.award_area", "la.award_page"])

    if args.bhoomi:
        run_sql("create table if not exists la.bhoomi_raw (record_id text primary key, data jsonb not null);")
        print(f"copying {BHOOMI_PROJECT} from {SRC_DB} (about 1.6 million rows) ...")
        stream_copy(BHOOMI_EXPORT, "la.bhoomi_raw", "record_id, data")
        subprocess.run([*PSQL, "-d", DST_DB, "-1", "-f", str(BHOOMI_SQL)], check=True)
        print_counts(["la.bhoomi_raw", "la.bhoomi_parcel", "la.bhoomi_village"])


if __name__ == "__main__":
    main()
