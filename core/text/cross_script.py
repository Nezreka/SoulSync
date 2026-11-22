"""latin spellings of a greek or cyrillic name (#2222).

itunes calls Despina Vandi "Δέσποινα Βανδή", spotify, deezer and the user's
plex call her "Despina Vandi". unidecode can't bridge that: it spells the
greek letter by letter ("Despoina Bande"), not the way the name is said and
written in latin. so the sync searched the library for the greek name, found
nothing, and the track stayed off the playlist while the file was right there.

``romanized_forms`` gives the latin spellings a name is commonly written in:

  * greek: modern pronunciation (οι, ει, η, υ all "i", β "v", αι "e",
    initial μπ/ντ/γκ "b"/"d"/"g"), which is how artists' names are spelled
    in latin ("Despina Vandi", "Sakis Rouvas", "Eleni Foureira"), and the
    ELOT 743 letter-for-letter form ("Despoina Vandi") greek sites use.
  * cyrillic: spotify's own romanization (я "ya", й "y"), plus the ё "e" and
    х "h" spellings, the same tables the #1885 provider-id match uses.

latin (or any other script) gives nothing back: there is nothing to bridge.
these are candidate spellings, not proof. callers look them up in the
library and still need the title to agree.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import List, Tuple

# cyrillic to latin the way spotify's own metadata spells it. covers russian,
# ukrainian, belarusian and serbian letters. latin text passes through.
CYRILLIC_BASE = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g", "ў": "u",
    "ђ": "dj", "ј": "j", "љ": "lj", "њ": "nj", "ћ": "c", "џ": "dzh",
}
# every table gets keyed, agreeing under one is enough. ё is spelled both
# ways on spotify ("Yolka" the artist, "Elka" the tree), and х is often one
# letter ("Horosho", "Mihail"). no table folds г or ґ to "h" or щ to "sh":
# those merge different letters, and Голод (hunger) would key the same as
# Холод (cold).
CYRILLIC_FOLDS = (
    str.maketrans(CYRILLIC_BASE),
    str.maketrans({**CYRILLIC_BASE, "ё": "e"}),
    str.maketrans({**CYRILLIC_BASE, "х": "h"}),
)
CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")  # U+0400 to U+04FF
GREEK_RE = re.compile(r"[Ͱ-Ͽἀ-῿]")  # greek and coptic, greek extended

# the voiceless consonants after which αυ/ευ are said "af"/"ef"
_GREEK_VOICELESS = set("θκξπστφχψ")

_GREEK_SINGLE = {
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i",
    "θ": "th", "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x",
    "ο": "o", "π": "p", "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y",
    "φ": "f", "χ": "ch", "ψ": "ps", "ω": "o",
}


def _greek_base(text: str) -> str:
    """lowercase greek without accents. a diaeresis (Παϊδάκια) or an accent
    on the first of two vowels (Φουρέιρα) says the two apart, so it becomes
    a marker the digraph rules skip."""
    out = []
    accented = set()
    for ch in unicodedata.normalize("NFD", text.lower()):
        if ch == "\u0308":  # diaeresis
            out.append("\x00")
        elif ch in ("\u0301", "\u0341"):  # tonos
            if out:
                accented.add(len(out) - 1)
        elif unicodedata.category(ch) == "Mn":
            continue
        else:
            out.append(ch)
    split = []
    for idx, ch in enumerate(out):
        split.append(ch)
        if idx in accented and ch in "αεοη" and idx + 1 < len(out) and out[idx + 1] in "ιυ":
            split.append("\x00")
    return "".join(split)


def _romanize_greek(text: str, phonetic: bool) -> str:
    """one latin spelling. ``phonetic`` spells the name the way it is said
    (the usual latin spelling of a greek artist), else ELOT 743."""
    s = _greek_base(text)
    out = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        nxt = s[i + 1] if i + 1 < n else ""
        after = s[i + 2] if i + 2 < n else ""
        at_word_start = i == 0 or not s[i - 1].isalpha()
        pair = ch + nxt
        if ch == "\x00":
            i += 1
            continue
        if after == "\x00" and pair in ("αι", "ει", "οι", "ου", "υι", "αυ", "ευ", "ηυ"):
            # the diaeresis on the second vowel keeps the two apart
            out.append(_GREEK_SINGLE.get(ch, ch))
            i += 1
            continue
        if pair == "ου":
            out.append("ou")
            i += 2
            continue
        if pair in ("αυ", "ευ", "ηυ"):
            voiceless = not after.isalpha() or after in _GREEK_VOICELESS
            out.append({"α": "a", "ε": "e", "η": "i"}[ch] + ("f" if voiceless else "v"))
            i += 2
            continue
        if phonetic and pair in ("οι", "ει", "υι"):
            out.append("i")
            i += 2
            continue
        if phonetic and pair == "αι":
            out.append("e")
            i += 2
            continue
        if pair == "μπ":
            out.append("b" if at_word_start or phonetic else "mp")
            i += 2
            continue
        if pair == "ντ":
            out.append("d" if at_word_start else "nt")
            i += 2
            continue
        if pair == "γκ":
            out.append("g" if at_word_start else ("ng" if phonetic else "gk"))
            i += 2
            continue
        if pair == "γγ":
            out.append("ng")
            i += 2
            continue
        if pair in ("γξ", "γχ"):
            out.append("n" + _GREEK_SINGLE[nxt])
            i += 2
            continue
        if ch == "υ" and phonetic:
            out.append("i")
        elif ch == "χ" and phonetic:
            out.append("h")
        else:
            out.append(_GREEK_SINGLE.get(ch, ch))
        i += 1
    return "".join(out)


def _title_case(latin: str) -> str:
    """the latin spelling cased like a name: each word capitalised."""
    return " ".join(w[:1].upper() + w[1:] for w in latin.split(" "))


@lru_cache(maxsize=8192)
def _romanized(name: str) -> Tuple[str, ...]:
    if not name or not name.strip():
        return ()
    forms: List[str] = []
    if GREEK_RE.search(name):
        for phonetic in (True, False):
            forms.append(_title_case(_romanize_greek(name, phonetic)))
    if CYRILLIC_RE.search(name):
        lowered = name.lower()
        for table in CYRILLIC_FOLDS:
            forms.append(_title_case(lowered.translate(table)))
    clean = []
    for form in forms:
        form = re.sub(r"\s+", " ", form).strip()
        if form and form != name and form not in clean:
            clean.append(form)
    return tuple(clean)


def romanized_forms(name: str) -> List[str]:
    """latin spellings of a greek or cyrillic ``name``, most common first.
    empty for a name with nothing to romanize."""
    if not isinstance(name, str):
        return []
    return list(_romanized(name))


def has_romanizable_letters(name: str) -> bool:
    """whether ``name`` has greek or cyrillic letters to romanize."""
    return bool(isinstance(name, str) and (GREEK_RE.search(name) or CYRILLIC_RE.search(name)))


__all__ = ["CYRILLIC_BASE", "CYRILLIC_FOLDS", "CYRILLIC_RE", "GREEK_RE",
           "has_romanizable_letters", "romanized_forms"]
