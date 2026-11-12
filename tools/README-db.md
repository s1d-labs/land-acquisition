# Land acquisition database (ws_ronik, schema la)

Source: scrape_hub projects `land-acquisition-ocr` and `land-acquisition-bhoomirashi` (read only).

## Tables

- `la.award_doc`: one row for each award PDF (key `path`): OCR summary and parsed fields.
- `la.award_amount`: one row for each amount in a PDF (key `path`, `line`).
- `la.award_area`: one row for each area in a PDF (key `path`).
- `la.award_page`: one row for each PDF page with its text (key `path`, `n`).
- `la.ocr_raw`: staging copy of the OCR records (`record_id`, `data` JSON).
- `la.bhoomi_raw`: staging copy of the parcel records, selected keys only.
- `la.bhoomi_parcel`: one row for each parcel (key `record_id`), with `district_key` and `village_key`.
- `la.bhoomi_village`: one row for each state, district, tehsil and village, with parcels, total area, highways, first and last notice date.
- `la.norm_place(text)`: normalized place name for joins.

## Reload

Run from the repo root. Each run replaces its tables in one transaction, so re-running is safe.

- `python3 tools/load_ocr.py`: copies OCR records, rebuilds `la.award_*`.
- `python3 tools/load_ocr.py --bhoomi`: also copies parcels (about 1.6 million rows), rebuilds `la.bhoomi_*`.
- `python3 tools/load_ocr.py --skip-copy`: rebuilds `la.award_*` from `la.ocr_raw` only.

## Join and notes

- Join on `state`, `district_key` and `village_key`. Build award keys with `la.norm_place(...)`.
- The source does not state the `area` unit. The median is about 0.04, so it is probably hectares. Not verified.
- `award_date` accepts ISO or `dd/mm/yyyy`. Other formats become NULL.
