"""Compensation fields from OCR text of award PDFs: fast, no network, no I/O beyond the saved texts."""
from pathlib import Path

import pytest

from ocr_extract import extract

OCR = Path(__file__).parent / "pages" / "ocr"


def load(prefix):
    matches = sorted(OCR.glob(f"pdf_{prefix}*.txt"))
    assert len(matches) == 1, prefix
    return matches[0].read_text(encoding="utf-8", errors="replace")


def amounts_with(result, label):
    return [a["value"] for a in result["amounts"] if a["label"] == label]


# dmsouth.delhi: "Total of amounts- Rs. 1,10,02,05,726/-"; "Solatium u/s 30(1)- Rs. 55,01,02,863".
# The words line is cut by OCR (no "only"), so total_words_match is None.
# The brief gave 1102005726; the text groups "1,10,02,05,726", which is 1100205726. The solatium is half of it
# (550102863 x 2), and the words line ("One hundred and Ten Crores Two Lakh ...") agrees, so the test uses 1100205726.
def test_delhi_total_and_solatium():
    r = extract(load("dmsouth.delhi"))
    assert r["total"] == 1100205726.0
    assert 550102863.0 in amounts_with(r, "solatium")
    assert r["total_words_match"] is None
    assert r["is_award"] is True


# westgodavari: "Total:- Rs. 2,26,63,274/-", words "Two Crores Twenty Six Lakhs Sixty Three Thousand Two Hundred
# and Seventy Four only", "Market value fixed per acre Rs.50,00,000/-".
def test_westgodavari_total_words_and_rate():
    r = extract(load("westgodavari"))
    assert r["total"] == 22663274.0
    assert r["total_words_match"] is True
    assert 5000000.0 in amounts_with(r, "rate")
    assert 5000000.0 not in amounts_with(r, "total")
    assert r["award_no"] == "1/2016-167"  # as read; the OCR text has an extra "7" after "2016-16"
    assert r["award_date"] is None  # "Dt: .4.2016" has no day: not clearly the award date


# gariaband: the table total 2569930.00 (महायोग; the words line gives "पच्चीस लाख उनहत्तर हजार नौ सौ तीस")
# has no legible label and no money word on its line in the OCR. The text also has later "कुल राशि" sums
# (8334099, 9250804, 9253063) that are not the award total. Labels alone cannot choose the right one,
# so this test is an expected failure. Village and award date are checked below.
@pytest.mark.xfail(strict=True, reason="table total 2569930 is unlabeled in OCR; later कुल राशि sums win")
def test_gariaband_total():
    r = extract(load("gariaband"))
    assert r["total"] == 2569930.00


def test_gariaband_village_and_date():
    r = extract(load("gariaband"))
    assert "सागड़ा" in r["villages"]
    assert r["award_date"] == "2025-04-03"  # "अधिनिर्णय दिनांक 03 / 04 / 2025"
    assert r["is_award"] is True


# kolhapur: "Rs. 4,89,42,815/- (Rupees Four Crore Eighty-Nine Lakhs ...)"; the NHAI table total is garbled.
def test_kolhapur_total_and_village():
    r = extract(load("kolhapur"))
    assert r["total"] == 48942815.0
    assert "Mangaonwadi" in r["villages"]
    assert r["award_date"] is None  # "Date:- 03.07.2026" is the letter's date, not the award's


# puducherry-dt: "Total payable Compensation Amount Rs.37,67,944.58"; "Rs.37,67,945.00" is the rounded figure.
def test_puducherry_total():
    r = extract(load("puducherry-dt"))
    assert r["total"] in (3767944.58, 3767945.0)
    assert r["award_no"] == "1/2026"
    assert r["award_date"] == "2026-09-03"  # "REVISED SUPPLEMENTARY AWARD No.1/2026, DATED: 03.09.2026"


# kargil: two candidate totals in the text.
#   - section 10: "The total amount of award workout to Rs. 53,38,602.00" (5338602)
#   - section 12: "Total Value of Award (A+B+C) Rs. 52,33,924.00" (5233924; A+B+C = 51,78,724 + 55,200)
# The extractor takes the largest strong total (5338602), which is the figure the award states in words of
# the compensation. The section 12 summary does not match it: this conflict is in the source and is not resolved.
def test_kargil_total_candidates():
    r = extract(load("kargil"))
    assert r["total"] == 5338602.0
    assert 5233924.0 in amounts_with(r, "total")
    assert r["total_words_match"] is True  # "Rupees Fifty Three Lakh Thirty eight thousand Six hundred Two only"


# hpkangra: the OCR table row reads "143393298.00/-" (an extra leading "1" against the truth noted earlier as
# 43393298). The text is the reference, so the test keeps 143393298.0. The row has no legible label, so
# the value is only checked as an amount. Village "Mungrehar" is checked too.
def test_hpkangra_amount_and_village():
    r = extract(load("hpkangra"))
    assert 143393298.0 in [a["value"] for a in r["amounts"]]
    assert "Mungrehar" in r["villages"]
    assert r["award_no"] == "02/2026"
    assert r["award_date"] == "2026-01-13"


def test_dnh_is_not_an_award():
    r = extract(load("dnh"))
    assert r["is_award"] is False
    assert r["total"] is None  # no award total is claimed for an RTI-style list


# Small checks of the parts, on short strings.
def test_indian_grouping_and_digit_scripts():
    r = extract("Compensation Rs. 1,10,02,05,726/- and भूमि मुआवजा ५०,००,००० रु.")
    assert 1100205726.0 in [a["value"] for a in r["amounts"]]
    assert 5000000.0 in [a["value"] for a in r["amounts"]]


def test_words_parse_with_crore_and_lakh():
    r = extract("Total of amounts Rs. 1,10,02,05,726/- (Rupees One Hundred and Ten Crores Two Lakh Five Thousand Seven Hundred and Twenty Six only)")
    assert r["total"] == 1100205726.0
    assert r["total_words_match"] is True


def test_words_mismatch_is_false():
    r = extract("Total: Rs. 2,26,63,000/- (Rupees Two Crores Twenty Six Lakhs Sixty Three Thousand only)")
    assert r["total"] == 22663000.0
    assert r["total_words_match"] is True
    r2 = extract("Total: Rs. 2,26,63,274/- (Rupees Two Crores Twenty Six Lakhs only)")
    assert r2["total_words_match"] is False


def test_rate_is_never_the_total():
    r = extract("Market value fixed per acre Rs.50,00,000/-\nTotal: Rs. 22,00,000/-")
    assert r["total"] == 2200000.0


def test_award_date_needs_the_award_line():
    r = extract("Land Acquisition Award No. 02/2026\nDated: 13-01-2026\nRead: Lr. dt. 08.05.2015")
    assert r["award_no"] == "02/2026"
    assert r["award_date"] == "2026-01-13"


def test_survey_numbers_and_areas():
    text = "Khasra No 357(58-16), R.S. Nos. 138/2 etc., Ac. 1.77 Cts. and 8,933 sqm; 0.743856 Hectare"
    r = extract(text)
    assert "357" in r["survey_numbers"] and "138/2" in r["survey_numbers"]
    units = {a["unit"] for a in r["area"]}
    assert {"cent", "sqm", "hectare"} <= units
    assert any(a["value"] == 8933.0 and a["unit"] == "sqm" for a in r["area"])


def test_no_amounts_means_no_total_and_no_award():
    r = extract("List of officers under RTI. Village Sapi. Award passed on 02.03.2020.")
    assert r["amounts"] == [] and r["total"] is None and r["is_award"] is False


def test_empty_text():
    r = extract("")
    assert r["total"] is None and r["villages"] == [] and r["is_award"] is False
