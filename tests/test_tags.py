"""Tags of district land acquisition titles (G14): real awards keep `award`; enquiries, hearings, time extensions and payments
do not; unrelated titles (tenders, prizes, RTI, cultural, recruitment) get no tags. Titles are copied from the scrape_hub data
(project land-acquisition-districts, 2026-11-13)."""
import districts


AWARDS = [   # real award or compensation order, or award statement: tag `award`
    "Land Acquisition Award No. 03/2024 of Village-Bhadot, Tehsil Shahpur, District Kangra, H.P. Dated 06-11-2024",
    "Award Declaration Of Village Utarna (Ganga Expressway)",
    "Award of acquired land of different villages for the construction of Ganga Expressway project 24.08.2022",
    "Award Order for the payment of compensation to the affected pattadars along the stretch of the NH-2 Under package-5B within the jurisdiction of the Imphal West District.",
    "Compensation Award Order of NH-37 (Imphal-Jiribam Road) for 24 meter ROW",
    "Award No. 03_2019_DC(SW) of Village Kazipur",
    "Public Notice regarding announcement of Award No. 01/2024/DC/South-West dated 22/03/2024 of Village Tajpur Khurd.",
    "Final Award Mouza – Bhagebori , Taluka – Bhiwapur , Dist- Nagpur regarding publication case no-11/A-65/2018-19",
    "भूसंपादन प्रस्तावाचे निवाडे",
    "Declaration of Award Under MRTP Act 1966 along with section 23 of The Right to Fair Compensation and Transparency in land Acquisition Rehabilitation and Resettlement Act 2013",
]
HEARINGS = [  # enquiry or hearing about an award or a plan: tag `hearing`, never `award`
    "Award Enquiry Notice for acquisition of land for Kuppam Airport at Ammavaripeta",
    "Ettumanoor Drinking Water Project-Award Enquiry Notice-reg",
    "Land Acquisition – Development of Madavoorpara Tourism Zone – Award Enquiry Notice – Reg",
    "3(G)3 Hearing – Documents may be submitted by post",
    "Notice under section 3(G)3 – Hearing Notice",
    "Land Acquisition for Marmala Hydro Electric Project – R & R Package Hearing- reg",
    "Notice in Forum 9, draft package for public hearing for preparation of R&R package related to land acquisition for Kannur Airport runway development",
    "3G(3) പബ്ലിക് ഹിയറിംഗ് നോട്ടീസ്",
]
NOTICES = [  # time extensions of an award, and other notices: never `award`
    "Extension for time of award and deletion of house property in land acquisition case number 15/2008-09 village Dadulgaon Tq. Jalgaon Ja. Dist. Buldhana",
    "Extension of time for award in land acquisition case no. 05/2008-09 of village Hingna Bhota Tq. Nandura Dist. Buldhana",
    "Land Acquisition International Conventional Centre Award Time Extension",
    "Land Acquisition for ROB Kuruppanthura- Extension for Award- reg",
]
PAYMENTS = [  # payment lists and disbursement notices: tag `payment`, not `award`
    "Notice regarding release of compensation of Award no. 05/2011-12 Village Mehrauli",
    "Public notice to receive the award/compensation for land acquisition for NH-208.",
]
OTHER_LA = [  # other land acquisition steps: no `award`
    "Declaration Under Section 19(1) – Land measuring 82KI-06Ml situated at village Balli(36Kl-03Ml), Nathal(11Kl-03Ml), Dori(19Kl-15Ml), Kartyal Simbal(05Kl-00Ml) of Tehsil Kharah Balli and Kalah(10Kl-05Ml) of Tehsil Khour District Jammu | 05/02/2026 | View (2 MB)",
    "Notice Inviting Applications for Social Impact Assessment and Social Impact Management for Land Acquisition for Ganga Expressway Project, District Amroha",
    "Land Acquisition for Marmala Hydro Electric Project- Extension for 19(1) Declaration- reg",
]
UNRELATED = [  # not land acquisition: no tags, not stored
    "Tender issued by the District Collector’s Office, Akola, for awarding a contract to implement a fish production project in the lake located at Mauje Kapshi, in Akola taluka.",
    "Press release regarding award of Padma Vibhushan, Padma Bhushan and Padma Shri in the year 2024",
    "DMHO- Erstwhile KRISHNA-Village/Ward Health Secretaries-For Awarding promotions to the ANMs Gr-III/Village/Ward Health Secretariats to the post of MPHA(F)- Certain Instructions received from CH&FW, A.P, Mangalagiri to prepare the Seniority Lists of ANM Gr-III(Merit cum Roster)-Communicated- Revised Seniority",
    "Opening of Portal inviting application under the “Scheme of Financial Assistance for administering Road Safety Advocacy and awards for the outstanding work done in the field of Road Safety”.",
]


def test_real_awards_keep_the_award_tag():
    for t in AWARDS:
        assert "award" in districts.tags_of(t), t
        assert "hearing" not in districts.tags_of(t), t


def test_enquiries_and_hearings_are_not_awards():
    for t in HEARINGS:
        tags = districts.tags_of(t)
        assert "hearing" in tags and "award" not in tags, t
    assert districts.tags_of("Notice under section 3(G)3 – Hearing Notice") == ["hearing", "notice"]     # 3(G)3: the claims hearing


def test_time_extensions_are_notices_not_awards():
    for t in NOTICES:
        tags = districts.tags_of(t)
        assert "award" not in tags and "notice" in tags, t


def test_payments_are_not_awards():
    for t in PAYMENTS:
        tags = districts.tags_of(t)
        assert "payment" in tags and "award" not in tags, t


def test_other_land_acquisition_has_no_award():
    for t in OTHER_LA:
        assert districts.is_land(t) and "award" not in districts.tags_of(t), t
    assert districts.tags_of(OTHER_LA[0]) == ["sec_19"]


def test_unrelated_titles_get_no_tags_and_are_not_land():
    for t in UNRELATED:
        assert districts.tags_of(t) == [], t
        assert not districts.is_land(t), t


def test_known_false_positives_are_gone():
    assert districts.tags_of("LIST OF CPIOS AND APPELLATE AUTHORITIES") == []
    assert districts.tags_of("Ganga Cultural Documentation") == []
    assert districts.tags_of("Award Enquiry Notice") == ["hearing", "notice"]


def test_description_does_not_change_award_rules():
    # the hearing and time rules read the title; a description that mentions objections must not turn a real award into a hearing
    title = "Land Acquisition Award No. 03/2024 of Village-Bhadot, Tehsil Shahpur, District Kangra, H.P. Dated 06-11-2024"
    assert districts.tags_of(title + ". Objections may be filed within 15 days", title) == ["award", "hearing"]


def test_general_award_with_payment_notice_keeps_award():
    from districts import tags_of
    t = "General Award, Notice U/s 37(2) for disburse the land compensation amount to the concerned Awardees"
    assert "award" in tags_of(t)
