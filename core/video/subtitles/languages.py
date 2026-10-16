"""Language tokens -> soulsync language codes (iso 639-1, optional region).

one place for the mapping the providers, the embedded reader and the
existing-subtitle scanner all need: '.eng.srt', ffprobe's 'ger', subdl's
'english', plex's 'pt-BR'.

Isolated: stdlib only; no music imports.
"""

from __future__ import annotations

import re
from typing import Any

#: every iso 639-1 code. a bare two-letter token only counts as a language
#: when it's in here, so '.hd.srt' or '.uk.srt'-style noise can't invent one
#: ('uk' is ukrainian, which is real, so that one does count).
ISO_639_1 = frozenset("""
aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch co
cr cs cu cv cy da de dv dz ee el en eo es et eu fa ff fi fj fo fr fy ga gd gl
gn gu gv ha he hi ho hr ht hu hy hz ia id ie ig ii ik io is it iu ja jv ka kg
ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu lv mg mh mi mk
ml mn mr ms mt my na nb nd ne ng nl nn no nr nv ny oc oj om or os pa pi pl ps
pt qu rm rn ro ru rw sa sc sd se sg si sk sl sm sn so sq sr ss st su sv sw ta
te tg th ti tk tl tn to tr ts tt tw ty ug uk ur uz ve vi vo wa wo xh yi yo za
zh zu
""".split())

#: iso 639-2 (both the B and T forms) -> 639-1, for the languages people
#: actually subtitle in. ffprobe and '.eng.srt' style names use these.
ISO_639_2_TO_1 = {
    "eng": "en", "spa": "es", "fra": "fr", "fre": "fr", "deu": "de", "ger": "de",
    "ita": "it", "por": "pt", "nld": "nl", "dut": "nl", "rus": "ru", "jpn": "ja",
    "zho": "zh", "chi": "zh", "kor": "ko", "ara": "ar", "hin": "hi", "tur": "tr",
    "pol": "pl", "swe": "sv", "nor": "no", "nob": "nb", "nno": "nn", "dan": "da",
    "fin": "fi", "hun": "hu", "ces": "cs", "cze": "cs", "slk": "sk", "slo": "sk",
    "ron": "ro", "rum": "ro", "bul": "bg", "ell": "el", "gre": "el", "heb": "he",
    "tha": "th", "vie": "vi", "ind": "id", "msa": "ms", "may": "ms", "ukr": "uk",
    "hrv": "hr", "srp": "sr", "slv": "sl", "est": "et", "lav": "lv", "lit": "lt",
    "isl": "is", "ice": "is", "cat": "ca", "eus": "eu", "baq": "eu", "glg": "gl",
    "fas": "fa", "per": "fa", "urd": "ur", "ben": "bn", "tam": "ta", "tel": "te",
    "mal": "ml", "kan": "kn", "mar": "mr", "guj": "gu", "pan": "pa", "tgl": "tl",
    "fil": "tl", "bos": "bs", "mkd": "mk", "mac": "mk", "sqi": "sq", "alb": "sq",
    "kat": "ka", "geo": "ka", "hye": "hy", "arm": "hy", "aze": "az", "kaz": "kk",
    "uzb": "uz", "mon": "mn", "khm": "km", "lao": "lo", "mya": "my", "bur": "my",
    "sin": "si", "nep": "ne", "afr": "af", "swa": "sw", "amh": "am", "som": "so",
    "cym": "cy", "wel": "cy", "gle": "ga", "bel": "be", "ltz": "lb", "mlt": "mt",
    "epo": "eo", "lat": "la",
    # regional 3-letter forms some tools write
    "pob": "pt-br", "zht": "zh-tw", "zhs": "zh-cn",
}

#: english language names (and the odd native one) -> code.
NAME_TO_CODE = {
    "english": "en", "spanish": "es", "espanol": "es", "español": "es",
    "castilian": "es", "french": "fr", "francais": "fr", "français": "fr",
    "german": "de", "deutsch": "de", "italian": "it", "italiano": "it",
    "portuguese": "pt", "brazilian": "pt-br", "brazillian": "pt-br",
    "dutch": "nl", "nederlands": "nl", "flemish": "nl", "russian": "ru",
    "arabic": "ar", "chinese": "zh", "japanese": "ja", "korean": "ko",
    "hindi": "hi", "turkish": "tr", "polish": "pl", "polski": "pl",
    "swedish": "sv", "svenska": "sv", "norwegian": "no", "norsk": "no",
    "danish": "da", "dansk": "da", "finnish": "fi", "suomi": "fi",
    "greek": "el", "hebrew": "he", "thai": "th", "vietnamese": "vi",
    "indonesian": "id", "malay": "ms", "czech": "cs", "slovak": "sk",
    "romanian": "ro", "hungarian": "hu", "magyar": "hu", "bulgarian": "bg",
    "ukrainian": "uk", "croatian": "hr", "serbian": "sr", "slovenian": "sl",
    "estonian": "et", "latvian": "lv", "lithuanian": "lt", "icelandic": "is",
    "catalan": "ca", "basque": "eu", "galician": "gl", "persian": "fa",
    "farsi": "fa", "urdu": "ur", "bengali": "bn", "tamil": "ta", "telugu": "te",
    "malayalam": "ml", "filipino": "tl", "tagalog": "tl", "bosnian": "bs",
    "macedonian": "mk", "albanian": "sq", "georgian": "ka", "armenian": "hy",
    "welsh": "cy", "irish": "ga", "belarusian": "be", "afrikaans": "af",
    "swahili": "sw",
}

_REGION_TOKEN = re.compile(r"^([a-z]{2,3})[-_]([a-z]{2,4})$")


def to_code(token: Any) -> str | None:
    """A soulsync language code for a token, or None when it isn't one.

    'en' / 'eng' / 'English' -> 'en'; 'pt-BR' / 'pt_br' -> 'pt-br';
    'Portuguese (Brazil)' -> 'pt-br'. Pure, never raises.
    """
    s = str(token or "").strip().lower()
    if not s:
        return None
    if s in ISO_639_1:
        return s
    if s in ISO_639_2_TO_1:
        return ISO_639_2_TO_1[s]
    if s in NAME_TO_CODE:
        return NAME_TO_CODE[s]
    m = _REGION_TOKEN.match(s)
    if m:
        base = to_code(m.group(1))
        if base and "-" not in base:
            return "%s-%s" % (base, m.group(2))
        return base
    # 'portuguese (brazil)', 'chinese - traditional', 'english sdh'
    words = re.split(r"[\s\-_()\[\],]+", s)
    words = [w for w in words if w]
    if words and words[0] in NAME_TO_CODE:
        base = NAME_TO_CODE[words[0]]
        rest = set(words[1:])
        if base == "pt" and rest & {"brazil", "brazilian", "br"}:
            return "pt-br"
        if base == "zh" and rest & {"traditional", "tw", "taiwan", "hk"}:
            return "zh-tw"
        if base == "zh" and rest & {"simplified", "cn", "mainland"}:
            return "zh-cn"
        return base
    return None


def satisfies_language(found: str | None, want: str) -> bool:
    """True when a track in ``found`` covers a wanted ``want`` code.

    exact match always counts; a regional track covers the bare language
    (an en-gb sub is english), never the other way round (a plain 'pt' sub
    is not brazilian portuguese).
    """
    f = str(found or "").lower()
    w = str(want or "").lower()
    if not f or not w:
        return False
    if f == w:
        return True
    return "-" not in w and f.split("-")[0] == w


__all__ = ["ISO_639_1", "ISO_639_2_TO_1", "NAME_TO_CODE", "to_code",
           "satisfies_language"]
