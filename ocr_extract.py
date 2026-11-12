"""Extract compensation fields from OCR text of Indian land acquisition award PDFs.

Pure functions only: no I/O, standard library only. Call ``extract(text)``.
Be conservative: a field is None or empty when the text does not clearly support it.
"""
import re
import unicodedata
from datetime import date

# Digits of the scripts used in the OCR output, mapped to ASCII first.
_DIGIT_BASES = (0x0966, 0x09E6, 0x0A66, 0x0BE6, 0x0C66, 0x0D66)  # Devanagari, Bengali, Gurmukhi, Tamil, Telugu, Malayalam
DIGITS = str.maketrans({chr(base + i): str(i) for base in _DIGIT_BASES for i in range(10)})

# Amount: Indian grouping (1,10,02,05,726 or 2,26,63,274/-), or plain 4+ digits with paise or "/-".
# Same regex as /home/ronik/la-ocr/bin/score.py, except that "Rs.2,26" is allowed (the dot is not a digit).
NUM = re.compile(
    r"(?<![\d/-])(?<!\d\.)"
    r"(?P<num>\d{1,3}(?:,\d{2})*,\d{3}(?:\.\d{1,2})?|\d{4,}\.\d{1,2}|\d{4,}(?=\s*/-))"
    r"(?:\s*/-)?(?![\d/])"
)
MONEY = re.compile(
    r"(?<![A-Za-z])rs\.?(?![A-Za-z])|₹|rupees|amount|compensation|solatium|total|रु|रू|रुपये|राशि|मुआवजा|प्रतिकर|"
    r"मोबदला|रक्कम|एकूण|योग|कुल|రూ|మొత్తం|പരിഹാര|രൂപ|ரூ|টাকা|ਰੁਪਏ",
    re.I,
)

# Label words. Order of the checks in _label(): rate, total, solatium, interest, additional, land, other.
RATE = re.compile(
    r"per\s+(?:acre|hect\w*|ha\b|sq\.?\s*m\w*|sqm|bigha|kanal|marla|cent|decimal)"
    r"|/\s*(?:acre|hect\w*|ha\b|sqm|sq\.?\s*m)"
    r"|प्रति\s*(?:हेक्टेयर|हेक्टर|हे\.?0?(?![\u0900-\u097F])|एकड़|एकड|एकर|वर्ग|गुंठा|बीघा|sqm|sq)"
    r"|ప్రతి\s*ఎకరా|ఎకరాకు",
    re.I,
)
TOTAL = re.compile(r"(?<![a-z])total|कुल|महायोग|एकूण|मोबदला|মোট|మొత్తం|மொத்த|മൊത്തം|ਕੁੱਲ", re.I)
# Strong total wording: a total of the whole award, not a part of it.
STRONG_TOTAL = re.compile(
    r"total\s+(?:payable|award|amount|compensation|of\s+amounts|value\s+of\s+award)|महायोग|कुल\s+मुआवजा|grand\s+total",
    re.I,
)
SOLATIUM = re.compile(r"solat|तोषण|सोलेशियम|సోలేషియం", re.I)
INTEREST = re.compile(r"interest|ब्याज|वड्डी|வட்டி|പലിശ|ਵਿਆਜ|সুদ|వడ్డీ", re.I)
ADDITIONAL = re.compile(r"additional|addl|अतिरिक्त", re.I)
LAND = re.compile(r"\bland\b|land\s*value|market\s*value|बाजार\s*मूल्य|भूमि|जमीन|जमीं|मूल्य|कीमत", re.I)

VILLAGE = re.compile(
    r"(?<![A-Za-z])(?:village(?![a-z])|vill\.(?=\s)|mouza(?![a-z])|mauza(?![a-z])|mauja(?![a-z])"
    r"|ग्राम(?![\u0900-\u097F])|गांव(?![\u0900-\u097F])|गाँव(?![\u0900-\u097F])|गाव(?![\u0900-\u097F])"
    r"|मौजा(?![\u0900-\u097F])|मौजे(?![\u0900-\u097F])|గ్రామం|గ్రామ|கிராமம்|ਪਿੰਡ|গ্রাম)",
    re.I,
)
VILLAGE_STOP = {
    "the", "of", "in", "and", "is", "at", "by", "for", "to", "from", "no", "name", "village", "number", "sub",
    "tehsil", "tahsil", "taluka", "taluk", "mandal", "district", "dist", "distt", "block", "division", "panchayat",
    "situated", "situate", "name", "date", "page", "or", "as", "etc", "this", "that", "which", "who", "are", "was", "be",
    "been", "has", "have", "all", "said", "such", "being", "during", "under", "with", "on", "into", "per",
    "तहसील", "तालुका", "तालुक", "जिला", "जिले", "मंडल", "उपखंड", "में", "के", "की", "का", "है", "और", "वह",
    "पटल", "कार्यालय", "पंचायत", "प्रकरण", "खसरा", "सर्वे", "गट", "दाग", "नं", "क्र", "ह", "प", "तह", "उप",
}

SURVEY = re.compile(
    r"(?<![A-Za-z])(?:survey\s*(?:no|nos|number|numbers)\.?|surv\.?\s*no\.?|s\.?\s*y\.?\s*no\.?s?\.?"
    r"|r\.?\s*s\.?\s*nos?\.?|khasra|खसरा|गट|gut\s*no\.?|gat\s*no\.?|dag\b|सर्वे|सर्व्हे)"
    r"[\s.:\-–—#]*"
    r"(?:(?:no|nos|क्र|नं|न|क्रमांक)\.?\s*[.:\-]?\s*)?"
    r"(\d+(?:\s*/\s*\d+)?[A-Za-z]?)(?![\d])",
    re.I,
)

_UNITS = [
    ("hectare", r"hect(?:are|ares|ar)?|हेक्टेयर|हेक्टर|हे(?:\.|0)?|ha\.?"),
    ("acre", r"acres?|ac\.?|एकड़|एकड|एकर|ఎకరా|ఎకరాలు|ஏக்கர்|ഏക്കർ|ਏਕੜ|একর"),
    ("sqm", r"sq\.?\s*m(?:tr|t|eters?|etres?)?|sqm|sq\.?\s*mt|square\s+met(?:er|re)s?|वर्ग\s*मीटर|चौरस\s*मीटर|वर्ग\s*मी\.?|m2|m²"),
    ("bigha", r"bigha|बीघा|बीघे|बिघा"),
    ("kanal", r"kanals?|कनाल|ਕਨਾਲ"),
    ("marla", r"marlas?|मरला|मरले|ਮਰਲੇ"),
    ("cent", r"cents?|cts|सेंट|सेन्ट|సెంట్లు|సెంట్|சென்ட்"),
    ("decimal", r"decimals?|डिसमिल"),
    ("other", r"gunta|guntha|gts|गुंठा|गुंठे|गुंट"),
]
AREA = re.compile(
    r"(?<![\d.,/\-])(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*-?\s*"
    r"(?P<unit>" + "|".join(f"(?:{pat})" for _, pat in _UNITS) + r")(?![A-Za-z\u0900-\u097F])",
    re.I,
)
_UNIT_NAME = {}
for _name, _pat in _UNITS:
    _UNIT_NAME[_name] = re.compile(r"^(?:" + _pat + r")$", re.I)

AWARD_WORD = re.compile(
    r"award|अधिनिर्णय|अभिनिर्णय|अवॉर्ड|अवार्ड|निवाडा|निवाड़ा|section\s*23|धारा\s*23|sec\.?\s*23|u/s\.?\s*23|3\s*\(?g\)?(?![A-Za-z])",
    re.I,
)
AWARD_NO = re.compile(
    r"(?:award|अवार्ड|अधिनिर्णय|निवाडा)\s*(?:no\.?|number|nos?\.?|क्रमांक|संख्या|नं\.?)?\s*[:.\-]?\s*"
    r"(\d+[A-Za-z]?(?:\s*[/_\-]\s*[A-Za-z0-9()]+)+)",
    re.I,
)
AWARD_DATE = re.compile(
    r"(?:award|अधिनिर्णय|अवार्ड)[^\n]{0,120}?(?:dated|दिनांक|तारीख)\s*[:\-–.]?\s*"
    r"(\d{1,2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{4})",
    re.I,
)

WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
WORD_SCALE = {
    "thousand": 1_000, "thousands": 1_000, "lakh": 100_000, "lakhs": 100_000,
    "crore": 10_000_000, "crores": 10_000_000,
}
RUPEES_WORDS = re.compile(r"rupees\s+([A-Za-z\s,.\-]+?)\s+only\b", re.I)


def _label(before, after):
    """Return the label of one amount from the text just before and just after it."""
    if RATE.search(after) or RATE.search(before):
        return "rate"
    if TOTAL.search(before):
        return "total"
    if SOLATIUM.search(before):
        return "solatium"
    if INTEREST.search(before):
        return "interest"
    if ADDITIONAL.search(before):
        return "additional"
    if LAND.search(before):
        return "land"
    return "other"


def _amounts(lines):
    out = []
    for idx, line in enumerate(lines):
        # A money word on this line or in the three lines above (table rows often carry the label higher up).
        if not any(MONEY.search(lines[j]) for j in range(max(0, idx - 3), idx + 1)):
            continue
        matches = list(NUM.finditer(line))
        for k, m in enumerate(matches):
            prev_end = matches[k - 1].end() if k else 0
            next_start = matches[k + 1].start() if k + 1 < len(matches) else len(line)
            before = line[max(prev_end, m.start() - 60): m.start()]
            if k == 0 and idx and _label(before, "") == "other" and not NUM.search(lines[idx - 1]):
                # The label may end the line above, e.g. "...total amount of" then "Rs. 53,38,602".
                before = lines[idx - 1][-80:] + " " + before
            after = line[m.end(): min(next_start, m.end() + 30)]
            value = float(m.group("num").replace(",", ""))
            out.append({
                "value": value,
                "raw": m.group(0).strip(),
                "label": _label(before, after),
                "line": idx + 1,
                "_strong": bool(STRONG_TOTAL.search(before)),
            })
    return out


def _words_to_number(phrase):
    total, cur = 0, 0
    for tok in re.findall(r"[a-z]+", phrase.lower()):
        if tok == "and" or tok == "rupees":
            continue
        if tok in WORD_NUM:
            cur += WORD_NUM[tok]
        elif tok == "hundred":
            cur = (cur or 1) * 100
        elif tok in WORD_SCALE:
            total += (cur or 1) * WORD_SCALE[tok]
            cur = 0
        else:
            return None
    return total + cur


def _total_words_match(text, total):
    """True or False when the English words are complete and parse; None otherwise."""
    m = RUPEES_WORDS.search(text)
    if not m or total is None:
        return None
    parsed = _words_to_number(m.group(1))
    if parsed is None or parsed == 0:
        return None
    return parsed == round(total)


def _pick_total(amounts):
    """Strong total wording first, then any total word, then any labeled non-rate amount.

    Within a group the largest amount wins: partial sums of an award are smaller than its total.
    """
    labeled = [a for a in amounts if a["label"] not in (None, "rate")]
    totals = [a for a in labeled if a["label"] == "total"]
    strong = [a for a in totals if a["_strong"]]
    for group in (strong, totals, labeled):
        if group:
            return max(group, key=lambda a: a["value"])["value"]
    return None


def _villages(lines):
    found = []
    for line in lines:
        for m in VILLAGE.finditer(line):
            rest = line[m.end(): m.end() + 60]
            rest = re.sub(r"^[\s:\-–—.]+", "", rest)
            seg = re.split(r"[,;:()\[\]|/\n\d]", rest)[0]
            toks = seg.split()
            if not toks or not _is_village_word(toks[0]):
                continue
            while toks and toks[0].lower() in ("of", "the", "in", "at", "situated"):
                toks.pop(0)  # "village of X" and "village in X" name X
            if not toks or not _is_village_word(toks[0]):
                continue
            name = toks[0]
            if len(toks) > 1 and _is_village_word(toks[1], second=True):
                name = f"{name} {toks[1]}"
            if name not in found:
                found.append(name)
    # "Ladha" next to "Ladha Sarai" is one village cut by a line break: keep the longer name.
    return [v for v in found if not any(o != v and o.split()[: len(v.split())] == v.split() for o in found)]


def _is_village_word(tok, second=False):
    if len(tok) < 2 or tok.lower() in VILLAGE_STOP or tok in VILLAGE_STOP:
        return False
    if not all(unicodedata.category(c)[0] in "LM" or c == "-" for c in tok):
        return False
    latin = [c for c in tok if c.isascii() and c.isalpha()]
    if latin:
        if not tok[0].isupper():
            return False  # a name starts with a capital letter in Latin script
        if tok.isupper() and len(tok) <= 3:
            return False  # abbreviation such as WEA
        if second and not (tok[0].isupper() and tok[1:] == tok[1:].lower()):
            return False  # the second word of a name is title case
    return True


def _survey_numbers(text):
    out = []
    for m in SURVEY.finditer(text):
        num = re.sub(r"\s+", "", m.group(1))
        if num not in out:
            out.append(num)
    return out


def _areas(text):
    out = []
    for m in AREA.finditer(text):
        unit = next(name for name, _ in _UNITS if _UNIT_NAME[name].match(m.group("unit").strip()))
        out.append({"value": float(m.group("num").replace(",", "")), "unit": unit, "raw": m.group(0).strip()})
    return out


def _award_no_and_date(lines):
    award_no, award_date = None, None
    for idx, line in enumerate(lines):
        m = AWARD_NO.search(line)
        if m and award_no is None:
            award_no = re.sub(r"\s+", "", m.group(1))
        if award_date is None and AWARD_WORD.search(line):
            # The award's own date follows the award word, on this line or the next one
            # ("Award No. 02/2026 ... Dated: 13-01-2026"). A date before the word belongs to another letter.
            window = line + " " + (lines[idx + 1] if idx + 1 < len(lines) else "")
            d = AWARD_DATE.search(window)
            if d:
                try:
                    award_date = date(int(d.group(3)), int(d.group(2)), int(d.group(1))).isoformat()
                except ValueError:
                    award_date = None
    return award_no, award_date


def extract(text: str) -> dict:
    text = (text or "").translate(DIGITS).replace("\f", "\n")
    lines = text.split("\n")

    amounts = _amounts(lines)
    total = _pick_total(amounts)
    for a in amounts:
        del a["_strong"]

    award_no, award_date = _award_no_and_date(lines)
    has_amount = bool(amounts)

    return {
        "amounts": amounts,
        "total": total,
        "total_words_match": _total_words_match(text, total),
        "villages": _villages(lines),
        "survey_numbers": _survey_numbers(text),
        "area": _areas(text),
        "award_no": award_no,
        "award_date": award_date,
        "is_award": bool(AWARD_WORD.search(text)) and has_amount,
    }
