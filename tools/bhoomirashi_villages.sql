-- Bhoomirashi parcels and village summary in ws_ronik, schema la.
-- Input: la.bhoomi_raw (record_id, data), filled from scrape_hub by tools/load_ocr.py --bhoomi.
-- Run:   psql -d ws_ronik -v ON_ERROR_STOP=1 -1 -f tools/bhoomirashi_villages.sql
-- Rebuilds la.bhoomi_parcel and la.bhoomi_village. Safe to re-run.

create schema if not exists la;
create table if not exists la.bhoomi_raw (record_id text primary key, data jsonb not null);

-- Normalized place key for joins: lower case, no codes or ASCII punctuation (Devanagari marks are kept), no 'village'/'gram'/'mauza' words,
-- common transliteration noise unified (aa->a, ee->i, ii->i, oo->u, uu->u, ph->f, w->v).
create or replace function la.norm_place(t text) returns text
language plpgsql immutable as $$
declare
  s text := lower(coalesce(t, ''));
begin
  s := replace(s, 'ग्राम', ' ');
  s := regexp_replace(s, '\([^)]*\)', ' ', 'g');
  s := replace(s, '।', ' ');
  s := regexp_replace(s, '[\x21-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e]', ' ', 'g');
  s := regexp_replace(s, '\m(village|vill|gram|grama|mauza|mouza)\M', ' ', 'g');
  s := replace(s, 'aa', 'a');
  s := replace(s, 'ee', 'i');
  s := replace(s, 'ii', 'i');
  s := replace(s, 'oo', 'u');
  s := replace(s, 'uu', 'u');
  s := replace(s, 'ph', 'f');
  s := replace(s, 'w', 'v');
  s := regexp_replace(s, '\s+', ' ', 'g');
  return nullif(btrim(s), '');
end $$;

drop table if exists la.bhoomi_village;
drop table if exists la.bhoomi_parcel;

create table la.bhoomi_parcel as
select
  record_id,
  btrim(data->>'state')                                   as state,
  nullif(btrim(data->>'district'), '')                    as district,
  nullif(btrim(data->>'sub_district'), '')                as sub_district,
  nullif(btrim(data->>'village'), '')                     as village,
  la.norm_place(data->>'district')                        as district_key,
  la.norm_place(data->>'village')                         as village_key,
  nullif(data->>'village_code', '')                       as village_code,
  nullif(data->>'survey_no', '')                          as survey_no,
  case when data->>'area' ~ '^[0-9]+(\.[0-9]+)?$' then (data->>'area')::numeric end as area,
  nullif(data->>'area_raw', '')                           as area_raw,
  nullif(data->>'highway', '')                            as highway,
  nullif(data->>'chainage', '')                           as chainage,
  case when data->>'publish_date' ~ '^\d{4}-\d{2}-\d{2}$' then (data->>'publish_date')::date end as publish_date,
  nullif(data->>'page_path', '')                          as page_path
from la.bhoomi_raw;

alter table la.bhoomi_parcel add primary key (record_id);
create index bhoomi_parcel_place_idx on la.bhoomi_parcel (state, district_key, village_key);
create index bhoomi_parcel_village_key_idx on la.bhoomi_parcel (village_key);
create index bhoomi_parcel_village_code_idx on la.bhoomi_parcel (village_code) where village_code is not null;

create table la.bhoomi_village as
select
  state,
  district,
  sub_district,
  village,
  district_key,
  village_key,
  max(village_code)                                       as village_code,
  count(*)                                                as parcels,
  sum(area)                                               as total_area,
  array_agg(distinct highway order by highway) filter (where highway is not null) as highways,
  min(publish_date)                                       as first_notified,
  max(publish_date)                                       as last_notified
from la.bhoomi_parcel
group by state, district, sub_district, village, district_key, village_key;

create index bhoomi_village_place_idx on la.bhoomi_village (state, district_key, village_key);
create index bhoomi_village_key_idx on la.bhoomi_village (village_key);

analyze la.bhoomi_parcel;
analyze la.bhoomi_village;
