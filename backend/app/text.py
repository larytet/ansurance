"""Name normalization, transliteration and consonant skeletons for multi-script search.

Search must match a name written in Hebrew, Arabic, Cyrillic or Latin letters, in any
word order, ignoring accents and apostrophes, plus any digits of the ID number. The
skeleton (consonants only, folded) makes "דוד כהן", "Давид Коэн" and "David Cohen" meet.
It is a fuzzy key, not a spelling authority.
"""

import re
import unicodedata

HE = {
    "א": "",
    "ב": "b",
    "ג": "g",
    "ד": "d",
    "ה": "h",
    "ו": "v",
    "ז": "z",
    "ח": "kh",
    "ט": "t",
    "י": "y",
    "כ": "k",
    "ך": "k",
    "ל": "l",
    "מ": "m",
    "ם": "m",
    "נ": "n",
    "ן": "n",
    "ס": "s",
    "ע": "",
    "פ": "p",
    "ף": "f",
    "צ": "ts",
    "ץ": "ts",
    "ק": "k",
    "ר": "r",
    "ש": "sh",
    "ת": "t",
}
CYR = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sh",
    "ъ": "",
    "ы": "i",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}
AR = {
    "ا": "a",
    "أ": "a",
    "إ": "i",
    "آ": "a",
    "ب": "b",
    "ت": "t",
    "ث": "s",
    "ج": "j",
    "ح": "h",
    "خ": "kh",
    "د": "d",
    "ذ": "z",
    "ر": "r",
    "ز": "z",
    "س": "s",
    "ش": "sh",
    "ص": "s",
    "ض": "d",
    "ط": "t",
    "ظ": "z",
    "ع": "",
    "غ": "g",
    "ف": "f",
    "ق": "k",
    "ك": "k",
    "ل": "l",
    "م": "m",
    "ن": "n",
    "ه": "h",
    "و": "v",
    "ي": "y",
    "ى": "a",
    "ة": "h",
    "ء": "",
}
# Latin -> Hebrew (consonants only; vowels are not written)
LAT_TO_HE = [
    ("sh", "ש"),
    ("kh", "ח"),
    ("ch", "כ"),
    ("ts", "צ"),
    ("zh", "ז"),
    ("th", "ת"),
    ("ph", "פ"),
    ("b", "ב"),
    ("c", "כ"),
    ("d", "ד"),
    ("f", "פ"),
    ("g", "ג"),
    ("j", "ג"),
    ("k", "כ"),
    ("l", "ל"),
    ("m", "מ"),
    ("n", "נ"),
    ("p", "פ"),
    ("q", "ק"),
    ("r", "ר"),
    ("s", "ס"),
    ("t", "ט"),
    ("v", "ו"),
    ("w", "ו"),
    ("x", "כס"),
    ("z", "ז"),
    ("h", "ה"),
]
FINALS = {"כ": "ך", "מ": "ם", "נ": "ן", "פ": "ף", "צ": "ץ"}


def _strip_marks(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize(text: str) -> str:
    """Casefold, strip accents, niqqud and punctuation, collapse spaces."""
    text = _strip_marks(text).casefold()
    text = re.sub(r"[ـ־]", "", text)  # tatweel, maqaf
    text = re.sub(r"['’`\"״׳.,\-_/()]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def to_latin(text: str) -> str:
    """Letter-by-letter Latin rendering of Hebrew, Cyrillic and Arabic; Latin unchanged."""
    out: list[str] = []
    for ch in normalize(text):
        out.append(HE[ch] if ch in HE else CYR[ch] if ch in CYR else AR[ch] if ch in AR else ch)
    return "".join(out)


def to_hebrew(text: str) -> str:
    """Rough Hebrew rendering of a Latin name (consonants only). Hebrew stays as is."""
    out: list[str] = []
    for word in normalize(to_latin_for_hebrew(text)).split():
        s, res, i = word, [], 0
        while i < len(s):
            for lat, he in LAT_TO_HE:
                if s.startswith(lat, i):
                    res.append(he)
                    i += len(lat)
                    break
            else:
                if re.match(r"[֐-׿]", s[i]):
                    res.append(s[i])
                i += 1
        if res and res[-1] in FINALS:
            res[-1] = FINALS[res[-1]]
        out.append("".join(res))
    return " ".join(w for w in out if w)


def to_latin_for_hebrew(text: str) -> str:
    """Cyrillic and Arabic go through Latin first; Hebrew is kept."""
    return "".join(
        CYR.get(ch, AR.get(ch, ch)) if ch in CYR or ch in AR else ch for ch in normalize(text)
    )


def skeleton(text: str) -> str:
    """Consonant skeleton: fold similar consonants, drop vowels and h, collapse repeats."""
    s = to_latin(text)
    for a, b in (("sh", "S"), ("ch", "k"), ("kh", "k"), ("ts", "z"), ("zh", "z"), ("ph", "f")):
        s = s.replace(a, b)
    s = s.translate(str.maketrans("cqwxjS", "kkvkgs"))
    s = re.sub(r"[aeiouyh\s]", "", s)
    s = re.sub(r"[^a-z]", "", s)
    return re.sub(r"(.)\1+", r"\1", s)


def word_skeletons(text: str) -> list[str]:
    return [sk for w in normalize(text).split() if (sk := skeleton(w))]


def build_search(name: str, id_number: str) -> tuple[str, str]:
    """(search_text, search_skel) for a client."""
    plain = " ".join(
        dict.fromkeys(
            normalize(name).split() + to_latin(name).split() + to_hebrew(name).split() + [id_number]
        )
    )
    skel = " ".join(word_skeletons(name))
    return plain, skel
