# land-acquisition

This family collects official notices of land acquisition from the gazettes of India. It also collects appointment notices and struck-off notices. It runs on the s1d-labs scrape pool.

## Sources

| Project | Site | Status |
|---|---|---|
| `land-acquisition-egazette` | egazette.gov.in (Gazette of India: central ministries and Delhi) | Complete (216,930 gazettes, 9 November 2026) |
| `land-acquisition-bhoomirashi` | bhoomirashi.gov.in (land acquisition portal of MoRTH: national highways) | New (G14, phase 1) |
| State gazettes | One site for each state | Survey in progress |

## What the bhoomirashi project collects

- bhoomirashi.gov.in is the land acquisition portal of the Ministry of Road Transport and Highways (MoRTH, NHAI and NHIDCL).
- The portal has a public "Highway Register (State-wise)". It does not need a login or a captcha.
- The register gives one row for each parcel of land: state, highway, chainage, district, sub-district, village, survey number, area and publish date.
- The project stores one record for each row. The queue has one item for each state (38 items).
- The register leaves a cell empty when the value is the same as in the row above. The scraper copies the value down.
- The village cell often has a code in brackets, for example `Bastora (626678)`. The scraper puts the code in `village_code`.
- The project keeps each answer at `land-acquisition-bhoomirashi/register/<state>.html.gz`.
- The register does not give compensation amounts. The awards (section 3G) are behind the login of the portal.
- The site answers only from India. The pool sends the work through the Indian proxies of the hub.

Run one state from a team box, without the hub:

```
myproxy run python bhoomirashi.py --local GOA
```

## What the egazette project collects

- The project stores one record for each gazette that the site lists. The site lists approximately 217,000 gazettes from 2008 to today.
- The 26 March 2013 publish date holds 61,442 gazettes. These are the digitized archive. Their issue dates go back to 1969 or earlier.
- Each record has these fields: ministry, department, office, subject, category, part and section, issue date, publish date, gazette ID, serial and file size.
- Each record has topic tags. The tags come from the subject, the ministry and the office.
- The project downloads the PDF of a gazette when one of its tags is in `PDF_TOPICS`. The PDFs go to the family bucket at `land-acquisition-egazette/pdf/<year>/<record id>.pdf`.
- The project keeps each result page at `land-acquisition-egazette/pages/<queue key>/p<n>.html.gz`.

### Topic tags

| Tag | Meaning |
|---|---|
| `land_acquisition` | Railways Act sections 20A to 20H, 7A and 37A. National Highways Act sections 3A, 3D and 3G. Pipelines (PMP) Act sections 3(1) and 6(1). Coal Bearing Areas Act. RFCTLARR Act. Other acquisition of land. |
| `right_of_way` | Ministry of Power notices for transmission lines ("in respect of M/s ...", section 164 of the Electricity Act). |
| `appointment` | Appointment, nomination, deputation, constitution of a board or committee. |
| `struck_off` | Companies struck off or names removed (section 248 or section 560). |
| `weekly_multi` | A weekly gazette with many notices and no single subject. |

The tags are broad. Each record keeps the full subject, so you can change the tags in SQL later.

### Record IDs

- A gazette with a Unique Gazette ID (UGID) uses the UGID, for example `CG-DL-E-02112026-277588`.
- An older gazette uses `NA-<serial>`. The serial is the number in brackets on the site.

## How the scraper works

1. The site answers only from India. The pool sends the work through the Indian proxies of the hub (`proxy="IN"`).
2. The TLS certificate chain of the site is not complete. The scraper does not verify certificates.
3. The scraper opens the "Search by Publish Date" form. It sends a `Referer` header, because the site refuses requests without one.
4. Each queue item is one range of publish dates. Big ranges are split into ranges of pages.
5. The scraper reads each result page of 15 rows in sequence. The site refuses a jump to a page that its pager does not show. The scraper moves through the pager in steps of ten pages.
6. New gazettes have their PDF at `WriteReadData/<year>/<serial>.pdf`. For older gazettes, the scraper uses the download button and the `ViewPDF.aspx` page of the site.

## Use

Run the parsing tests:

```
python -m pytest tests -q
```

Run one date range from a team box, without the hub:

```
myproxy run python crawl.py --local 01-Oct-2026 31-Oct-2026            # records only
myproxy run python crawl.py --local 02-Nov-2026 02-Nov-2026 --pdf      # records and PDFs (the PDFs are not kept)
```

Run the check against a local stand-in hub:

```
myproxy run scrape-check --items 2 --timeout 1800
```

The `check` workflow on GitHub cannot reach the site, because its runner is not in India. In that workflow, the scrape-check step does not stop the check.

## Keep the queue up to date

- Run the **setup** workflow (Actions, setup, Run workflow) after each change to `family.py`.
- For the first pool run, `SEED_LIMIT` in `family.py` is 5. The setup then adds only the 5 newest items. Look at scrape.s1d.me, Runs. If the runs are good, set `SEED_LIMIT = None` and run the setup again.
- The setup adds each complete week of the current month (days 1 to 7, 8 to 14, 15 to 21, 22 to the end). It keeps the items that exist.
- Run the setup again each week to add the new week.

## Health check

The health check `land-acquisition:egazette.gov.in` is registered, but the project does not use it yet. The TLS chain of the site is not complete, and a failed check holds all the work. Look at the check on scrape.s1d.me. If it shows "up", set `"probe": f"{FAMILY}:egazette.gov.in"` in `family.py` and run the setup again.

The full technical reference for the pool is in `CLAUDE.md`.
