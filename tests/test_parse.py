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


# bhoomirashi.gov.in (G14): the state-wise Highway Register. The sample is the header, the first 12 rows, a district change and a
# highway change of the Kerala answer (cut from 41,906 rows).
import bhoomirashi


def register():
    return bhoomirashi.parse((PAGES / "bhoomirashi-kerala-sample.html").read_text(encoding="utf-8"), "KERALA")


def test_register_rows_fill_down():
    rows = register()
    assert len(rows) == 24                                              # 25 table rows minus the header
    rid, g = rows[0]
    assert (g["state"], g["highway"], g["chainage"], g["district"], g["sub_district"]) == \
           ("KERALA", "66", "379.100 - 460.700", "ALAPPUZHA", "Ambalappuzha")
    assert (g["village"], g["village_code"], g["survey_no"], g["area"], g["publish_date"]) == \
           ("Ambalappuzha Block 14", None, "1089737", 0.0015, "2020-12-03")
    assert rows[1][1]["village"] == "Ambalappuzha Block 14" and rows[1][1]["district"] == "ALAPPUZHA"   # empty cells = same as above


def test_register_village_code_and_changes():
    by_survey = {g["survey_no"]: g for _, g in register()}
    g = by_survey["1156909"]                                            # the row after a district change
    assert (g["district"], g["sub_district"], g["village"], g["village_code"]) == \
           ("ERNAKULAM", "Kanayannur", "Cheranallur (CT)", "628009")
    g = by_survey["2504231"]                                            # a new highway: district continues, the rest is printed
    assert (g["highway"], g["chainage"], g["district"], g["sub_district"], g["village_code"]) == \
           ("85", "98.527 - 151.613", "ERNAKULAM", "Kothamangalam", "628043")


def test_register_ids_are_stable_and_unique():
    a, b = register(), register()
    assert [r for r, _ in a] == [r for r, _ in b]
    assert len({r for r, _ in a}) == len(a) and all(r.startswith("kerala-") for r, _ in a)


def test_register_without_table_is_an_error():
    import pytest
    with pytest.raises(bhoomirashi.SiteError):
        bhoomirashi.parse("<html><body>Session expired</body></html>", "KERALA")


# District websites on S3WaaS (G14, phase 2): a search result page and an award page of imphalwest.nic.in (scripts cut).
import districts


def test_search_results_keep_land_titles():
    res, last = districts.results((PAGES / "s3waas-imphalwest-search-award.html").read_text(encoding="utf-8"), "imphalwest.nic.in")
    assert last == 2 and len(res) == 10
    land = [t for _, t in res if districts.is_land(t)]
    assert len(land) == 8                                                    # 2 of the 10 are prizes for electoral work
    assert not districts.is_land("Receiving of Best Electoral Practices Award for Innovative Measures, 2022")


def test_award_page_fields():
    url = "https://imphalwest.nic.in/document/award-of-compensation-u-s-3g-3h-of-the-national-highways-act-1956/"
    d = districts.item((PAGES / "s3waas-imphalwest-award-3g.html").read_text(encoding="utf-8"), url)
    assert d["title"].startswith("Award of Compensation u/s 3G & 3H of the National Highways Act, 1956")
    assert d["pdf_urls"] == ["https://cdn.s3waas.gov.in/s3faa9afea49ef2ff029a833cccc778fd0/uploads/2023/04/2023042822.pdf"]
    assert "2023-04-19" in d["dates"]
    assert districts.tags_of(d["title"]) == ["award", "nh"]
    assert districts.pdf_path("imphalwest.nic.in", d["pdf_urls"][0]) == "pdf/imphalwest.nic.in/2023-04-2023042822.pdf"


def test_land_titles_and_tags():
    assert districts.is_land("भू-अर्जन प्रकरणो मे पारित अवार्ड (ग्राम- जरेकेला और नवापारा)")
    assert districts.tags_of("भू-अर्जन प्रकरणो मे पारित अवार्ड") == ["award"]
    assert districts.is_land("Section 11 Notification for the villages of Kuranwadi/Chikhali and Konheri")
    assert not districts.is_land("Circular regarding Manipur Victim Compensation Scheme, 2019")
    assert not districts.is_land("Good Governance Awards")
    assert not districts.is_land("Bihar Education Project, Bhagalpur: Application Form for Teacher Award-2024")
    assert districts.tags_of("Notice regarding Camp for compensation of Land.") == ["payment", "notice"]
    assert districts.tags_of("Payable amount of compensation for the acquisition of land for New B.G. Railway line") == ["payment", "railway"]


# Fixes after the pilot (2026-11-11): language versions, land acquisition pages, regional words, wider tags.
def s3(name):
    return (PAGES / f"s3waas-{name}.html").read_text(encoding="utf-8")


def test_home_page_gives_languages_and_land_pages():
    home = s3("solapur-home")
    assert districts.languages(home, "solapur.gov.in") == ["en/"]            # the root is Marathi; the awards are under /en/
    pages, items = districts.land_links(home, "solapur.gov.in")
    assert any("%e0%a4%ad%e0%a5%82-%e0%a4%b8%e0%a4%82%e0%a4%aa%e0%a4%be%e0%a4%a6%e0%a4%a8" in u for u in pages)   # भू संपादन
    assert all("/notice/" in u or "/document/" in u for u, _ in items)


def test_english_search_finds_the_solapur_awards():
    res, last = districts.results(s3("solapur-en-search-award"), "solapur.gov.in")
    assert last == 1 and len(res) == 2
    assert all(districts.is_land(t) for _, t in res)
    assert all("award" in districts.tags_of(t) for _, t in res)


def test_land_page_table_karimnagar():
    es, links, last = districts.listing(s3("karimnagar-land-page"), "https://karimnagar.telangana.gov.in/land-acquisition-notifications/")
    assert len(es) == 94 and last == 1
    e = es[0]
    assert e["fields"]["Project Name"] == "Irrigation Projects" and e["fields"]["Mandal"] == "Gangadhara"
    assert e["dates"] == ["2026-11-03"] and len(e["pdf_urls"]) == 1
    assert len({x["pdf_urls"][0] for x in es}) == 94                        # one record id per row


def test_land_page_lists_chaibasa():
    es, _, _ = districts.listing(s3("chaibasa-land-page"), "https://chaibasa.nic.in/land-acquisitionnotification/")
    assert len(es) == 71
    assert es[0]["section"] == "Prarambhik Adhisoochna" and es[0]["dates"] == ["2026-04-13"]
    assert "sec_19" in districts.tags_of(es[0]["title"])                     # "Adhighosna" = declaration (section 19)


def test_land_category_solapur_is_paged():
    es, _, last = districts.listing(s3("solapur-land-category"), "https://solapur.gov.in/document-category/x/")
    assert len(es) == 10 and last == 2 and es[0]["section"] == "भू संपादन"
    assert districts.tags_of(es[1]["title"]) == ["sec_19"]                  # "... कलम 19 प्रकाशन"


def test_pilot_titles_get_tags():
    t = districts.tags_of
    assert t("भू-अर्जन प्रकरण/अधिसूचना/जनसुनवाई [ग्राम/नगर – टुरटुरा, तहसील-लैलूँगा, जिला-रायगढ़]") == ["hearing", "notice"]
    assert t("भू-अर्जन प्रकरण [ग्राम – जामगाँव, तहसील-रायगढ़, जिला-रायगढ़] – जनसुनवाई दिनांक – 13.02.2026") == ["hearing"]
    assert t("3G(3) പബ്ലിക് ഹിയറിംഗ് നോട്ടീസ്") == ["hearing", "notice"]     # malappuram: the claims hearing, not the award
    assert "award" in t("Award of Compensation u/s 3G & 3H of the National Highways act,1956")
    assert t("Land Acquisition-Preliminary Notification (Under Section-11(1) of the Act-30/2013)") == ["sec_11", "notice"]
    assert t("Order (District Land Acquisition office)") == ["order"]
    assert "award" in t("ग्राम तिलणी की अनुसूची-02 भूमि अर्जन अधिनिमय, 2013 की धारा-31 में प्रतिकर अवार्ड का सूचना पट्ट")


def test_regional_words():
    assert districts.is_land("भूसंपादन महाराष्ट्र शक्तीपीठ द्रुतगती महामार्ग प्रकल्प कलम 15 (२) ची अधिसुचना")
    assert districts.is_land("Click here to see the Awards of Hingani Nipani, Bhoyre, Kalaman, Khuneshwar, Padsali")
    assert districts.is_land("భూసేకరణ నోటిఫికేషన్")
    assert not districts.is_land("Awards")
    assert districts.STATE_TERMS["Maharashtra"][0] == "भूसंपादन"
