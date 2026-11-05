"""Parsing and planning tests on saved egazette.gov.in pages: fast, no network. View state blobs are cut from the saved pages."""
from datetime import date
from pathlib import Path

import crawl
import family

PAGES = Path(__file__).parent / "pages"


def page(name):
    return crawl.parse_page((PAGES / f"{name}.html").read_text(encoding="utf-8"))


def test_recent_page_gives_fifteen_gazettes_with_ugid():
    p = page("2026-10-p1")
    assert p["total"] == 906 and p["first_sno"] == 1 and len(p["rows"]) == 15
    rid, g, button = p["rows"][0]
    assert rid == "CG-AP-E-31102026-277572" == g["ugid"] and g["serial"] == 277572      # the site's own id: stable across runs
    assert g["ministry"] == "Ministry of Railways" and g["category"] == "Extra Ordinary"
    assert g["part_section"] == "Part II-Section 3-Sub-Section (ii)"
    assert (g["issue_date"], g["publish_date"], g["file_mb"]) == ("2026-10-27", "2026-10-31", 1.59)
    assert g["topics"] == ["land_acquisition"] and g["has_pdf"] and button == "gvGazetteList$ctl02$imgbtndownload"
    assert len({r for r, _, _ in p["rows"]}) == 15


def test_last_page_is_short_and_numbered():
    p = page("2026-10-p61")
    assert p["first_sno"] == 901 and len(p["rows"]) == 906 - 900


def test_old_rows_use_the_serial_in_brackets():
    p = page("2013-06-10-p1")
    assert p["total"] == 18
    rid, g, _ = p["rows"][0]
    assert rid == "NA-154413" and g["ugid"] is None and g["serial"] == 154413
    assert g["department"] is None                                  # "NOT available" -> None


def test_weekly_gazette_has_no_single_subject():
    p = page("2013-03-26-p3991")
    assert p["total"] == 61442 and p["first_sno"] == 59851
    rid, g, _ = p["rows"][0]
    assert rid == "NA-43251" and g["multiple"] and g["subject"] is None and g["ministry"] is None
    assert g["category"] == "Weekly" and g["part_section"] == "Part III-Section 2" and g["issue_date"] == "1998-04-25"
    assert g["topics"] == ["weekly_other"]                           # Patent Office part: record only, no PDF
    assert crawl.topics_of({"multiple": True, "part_section": "Part II-Section 3-Sub-Section (ii)"}) == ["weekly_multi"]


def test_viewer_names_the_pdf():
    assert crawl.pdf_src((PAGES / "viewpdf.html").read_text(encoding="utf-8")) == "../WriteReadData/2008/E_1827_2010_001.pdf"


def test_an_empty_page_gives_nothing():
    assert crawl.parse("<html><body>No gazettes</body></html>") == []


def tags(subject, ministry="Ministry of Railways", **kw):
    return crawl.topics_of({"subject": subject, "ministry": ministry, **kw})


def test_topics():
    assert tags("20ACURSADULPUR") == ["land_acquisition"]                       # section glued to a station code
    assert tags("Publicationofnotificationundersection20EforacquisitionoflandintheextraordinaryGazetteofIndia") == ["land_acquisition"]
    assert tags("Publication of notification under the Railways Act, 1989") == ["land_acquisition"]
    assert tags("Publication of Norms for filling up of the post of Director General, Railway Health Services") == []
    assert tags("Publication of notification under Section 3D", "Ministry of Road Transport and Highways") == ["land_acquisition"]
    assert tags("Publication of user fee notification", "Ministry of Road Transport and Highways") == []
    assert tags("Publication of notification under Section 6(1) of the PMP Act 1962", "Ministry of Petroleum and Natural Gas") == ["land_acquisition"]
    assert tags("Notification in respect of M/s NTPC Renewable Energy Limited", "Ministry of Power") == ["right_of_way"]
    assert tags("Notification for appointment of Shri X as Member in the NCBC", "Ministry of Social Justice") == ["appointment"]
    assert tags("Notifing in Cookware, Utensils and Cans quality control order", "Ministry of Commerce and Industry",
                department="Department for Promotion of Industry and Internal Trade") == []
    assert tags("Removal of names of companies under section 248", "Ministry of Corporate Affairs") == ["struck_off"]


def test_pager_window():
    assert crawl.pager_window(1, 4097) == (1, 10)
    assert crawl.pager_window(11, 4097) == (11, 20)
    assert crawl.pager_window(4097, 4097) == (4088, 4097)             # near the end the window shows the last ten pages
    assert crawl.pager_window(3, 5) == (1, 5)


def test_queue_plan():
    items = family.egazette_items(today=date(2026, 11, 5))
    keys = [i["key"] for i in items]
    assert len(keys) == len(set(keys))
    assert all(i["payload"]["expected"] <= 1500 for i in items)
    assert keys[-1] == "pub-2026-10-01_2026-10-31"                     # 1-4 Nov is an incomplete week: not seeded yet
    bulk = [i for i in items if i["key"].startswith("pub-2013-03-26-p")]
    assert sum(i["payload"]["expected"] for i in bulk) == 61442 and bulk[-1]["payload"]["pages"] == [4001, 4097]
    later = [i["key"] for i in family.egazette_items(today=date(2026, 11, 9))]
    assert later[-1] == "pub-2026-11-01_2026-11-07" and set(keys) <= set(later)
