"""What subtitles a video already has — bazarr's "have it" rules.

bazarr only fetches what's missing. a language counts as present when:

- an external sidecar carries it: any file next to the video that starts with
  the video's name and ends in a subtitle extension. language, hi and forced
  come from the dotted tokens in between (.en, .eng, .English, .pt-BR,
  .hi/.sdh/.cc, .forced/.foreign). a sidecar with no language in its name
  (movie.srt) gets its language detected from the text, like bazarr does.
- an embedded track carries it: every subtitle stream ffprobe reports with a
  language, bitmap ones included, unless embedded subs are switched off or
  that codec is ignored (pgs / vobsub / ass) — the same switches bazarr has.

matching: a plain request is covered by a plain or hearing-impaired track, a
hearing-impaired request needs hi, a forced request needs forced. a forced
track never covers a full one (it only has the foreign-language lines).

Isolated: stdlib + the providers' ffprobe helpers; no music imports.
"""

from __future__ import annotations

import os
import re
import threading
from collections import OrderedDict
from typing import Any, Callable, NamedTuple

from .languages import to_code, satisfies_language


class Track(NamedTuple):
    language: str
    hi: bool
    forced: bool
    source: str  # "external" | "embedded"


#: subtitle sidecar extensions bazarr indexes (.txt left out: too many
#: release notes share the video's name).
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".smi",
                       ".sup", ".mpl")
#: text formats whose content can be read for language/hi detection.
_TEXT_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".smi", ".mpl")
VIDEO_EXTENSIONS = (".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".ts",
                    ".m2ts", ".mpg", ".mpeg", ".webm", ".flv", ".ogm", ".divx")

_HI_TOKENS = {"sdh", "cc", "hearing", "hearingimpaired", "hearing-impaired", "hoh"}
_FORCED_TOKENS = {"forced", "foreign", "foreignpartsonly"}
_NOISE_TOKENS = {"default", "full", "sub", "subs", "subtitle", "subtitles",
                 "ext", "external", "track", "normal", "dialog", "dialogue"}

#: ffprobe codec names behind bazarr's ignore switches.
_PGS_CODECS = {"hdmv_pgs_subtitle", "pgssub"}
_VOBSUB_CODECS = {"dvd_subtitle", "dvdsub", "vobsub"}
_ASS_CODECS = {"ass", "ssa"}


# ── external sidecars ────────────────────────────────────────────────────────

def parse_sidecar_tokens(tokens: list[str]) -> tuple[str | None, bool, bool]:
    """(language | None, hi, forced) from the dotted tokens between the video
    name and the extension. Pure.

    '.hi' is hearing-impaired when another token names the language
    ('movie.en.hi.srt'), and hindi when it's the only language-ish token
    ('movie.hi.srt') — the plex/bazarr convention.
    """
    lang = None
    hi = forced = False
    saw_hi_token = False
    for raw in tokens:
        t = raw.strip().lower()
        if not t or t.isdigit() or t in _NOISE_TOKENS:
            continue
        if t in _HI_TOKENS:
            hi = True
            continue
        if t in _FORCED_TOKENS:
            forced = True
            continue
        if t == "hi":
            saw_hi_token = True
            continue
        code = to_code(t)
        if code:
            lang = code  # the last language token wins (plex reads it that way)
    if saw_hi_token:
        if lang is None:
            lang = "hi"
        else:
            hi = True
    return lang, hi, forced


def _stem_owner(name_lower: str, stems: list[str]) -> str | None:
    """The longest video stem this file name belongs to, or None. A sidecar
    for 'Movie.Part2.mkv' must not count for 'Movie.mkv' in the same folder."""
    best = None
    for st in stems:
        if name_lower.startswith(st + ".") and (best is None or len(st) > len(best)):
            best = st
    return best


def _read_head(path: str, limit: int = 65536) -> str | None:
    try:
        with open(path, "rb") as f:
            data = f.read(limit)
    except OSError:
        return None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def external_tracks(video_path: str, names: list[str] | None = None,
                    read_text: Callable[[str], str | None] | None = None) -> list[Track]:
    """Sidecar subtitle tracks for ``video_path``.

    ``names`` is the folder listing (read from disk when None); ``read_text``
    returns a sidecar's leading text for language/hi detection (defaults to
    reading the real file — returning None just skips detection). Never
    raises.
    """
    try:
        folder = os.path.dirname(str(video_path))
        if names is None:
            try:
                names = os.listdir(folder)
            except OSError:
                return []
        read_text = read_text or _read_head
        video_name = os.path.basename(str(video_path)).lower()
        own_stem = os.path.splitext(video_name)[0]
        stems = {own_stem}
        for n in names:
            nl = str(n).lower()
            root, ext = os.path.splitext(nl)
            if ext in VIDEO_EXTENSIONS:
                stems.add(root)
        stems_list = sorted(stems)
        out: list[Track] = []
        for n in names:
            name = str(n)
            nl = name.lower()
            root, ext = os.path.splitext(nl)
            if ext not in SUBTITLE_EXTENSIONS:
                continue
            if _stem_owner(nl, stems_list) != own_stem:
                continue
            middle = root[len(own_stem):].lstrip(".")
            lang, hi, forced = parse_sidecar_tokens(middle.split(".") if middle else [])
            if ext in _TEXT_EXTENSIONS and (lang is None or not hi):
                text = None
                try:
                    text = read_text(os.path.join(folder, name))
                except Exception:  # noqa: BLE001 - detection is best-effort
                    text = None
                if text:
                    if lang is None:
                        lang = detect_language(text)
                    if not hi and not forced and looks_hearing_impaired(text):
                        hi = True
            if lang is None:
                continue  # unknown language can't satisfy anything
            out.append(Track(lang, hi, forced, "external"))
        return out
    except Exception:  # noqa: BLE001 - never breaks a fetch decision
        return []


# ── content detection (unlabeled sidecars) ───────────────────────────────────

_TIMING = re.compile(r"\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->.*")
_TAGS = re.compile(r"<[^>]*>|\{[^}]*\}")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

#: the most frequent short words per language. enough to tell a subtitle's
#: language with a clear margin; anything ambiguous returns None.
_STOPWORDS = {
    "en": "the you i to a and it is that what of in me this don't i'm we no be "
          "your have know not on for my just was he are with can it's do",
    "es": "que de no a la el es y en lo un por me qué una te los se con para "
          "mi está si bien pero yo eso las sí su tu",
    "fr": "je de est pas le vous la tu que et un à les il ce en ne une on "
          "c'est qui pour mais suis bien des du",
    "de": "ich sie das ist du nicht die und es der wir was zu ein er in mir "
          "mit ja wie den auf mich dass so hier",
    "it": "non di che è e la il un a per mi sono ti ho cosa lo si ma una ci "
          "le questo bene sei come",
    "pt": "que não de o a é e um você eu se para me uma do da com isso em "
          "está os mas por bem tem",
    "nl": "ik je het de dat is een niet en wat van we in ze hij op te zijn "
          "met maar voor hebben jij dit",
    "sv": "jag du det är att inte en och på har vi som för vad med mig han "
          "kan den så ska till hon",
    "da": "jeg du det er at ikke en og på har vi som for hvad med mig han "
          "kan den så skal til hun",
    "no": "jeg du det er å ikke en og på har vi som for hva med meg han kan "
          "den så skal til hun",
    "fi": "on ei se minä sinä mitä että ja hän me te tämä mutta en ole oli "
          "kun niin mutta nyt sen",
    "pl": "nie to się w na i jest że co z jak mnie tak do ja mi ty go już "
          "czy tylko jestem",
    "cs": "to je se na že ne a v jsem co jak mi tak by do ale už jsi ty mě "
          "tady",
    "ro": "nu să e de și în la ce o pe mă un am ai că este cu te sunt eu",
    "hu": "a az nem hogy és egy is van de meg ez mi te én csak már ki jó",
    "tr": "bir ve bu ne de da için mi sen ben çok o var ama değil evet",
    "id": "aku kau yang tidak itu ini kita dan di apa kamu ada dengan akan "
          "saya ke",
}
_STOPSETS = {lang: set(words.split()) for lang, words in _STOPWORDS.items()}

_SCRIPT_RANGES = (
    ("ja", re.compile(r"[぀-ヿ]")),          # hiragana / katakana
    ("ko", re.compile(r"[가-힯]")),          # hangul
    ("zh", re.compile(r"[一-鿿]")),          # han (after ja/ko)
    ("ar", re.compile(r"[؀-ۿ]")),
    ("he", re.compile(r"[֐-׿]")),
    ("el", re.compile(r"[Ͱ-Ͽ]")),
    ("th", re.compile(r"[฀-๿]")),
    ("ru", re.compile(r"[Ѐ-ӿ]")),
)


def _dialogue(text: str) -> str:
    """Just the spoken lines: ass/ssa keeps the Dialogue: text fields, srt and
    vtt drop cue numbers, timings and headers ([sound] cues stay — they're
    dialogue lines for the hi check)."""
    raw = str(text or "")
    ass = "[script info]" in raw[:2000].lower() or re.search(r"(?mi)^dialogue:", raw)
    lines = []
    for line in raw.splitlines():
        s = line.strip()
        if ass:
            if not s.lower().startswith("dialogue:"):
                continue
            s = s.split(",", 9)[-1].replace("\\N", " ").replace("\\n", " ")
        else:
            if not s or s.isdigit() or _TIMING.match(s):
                continue
            if s.upper().startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
                continue
        s = _TAGS.sub(" ", s).strip()
        if s:
            lines.append(s)
    return "\n".join(lines)


def detect_language(text: str) -> str | None:
    """Best-guess language of subtitle text, or None when it isn't clear.
    Script first (cyrillic, cjk, arabic, ...), then stopword counts for latin
    languages. Pure."""
    body = _dialogue(text)
    if not body.strip():
        return None
    letters = len(_WORD.findall(body))
    if letters == 0:
        return None
    for lang, rx in _SCRIPT_RANGES:
        hits = len(rx.findall(body))
        if hits >= 30:
            if lang == "ru" and re.search(r"[іїєґ]", body):
                return "uk"
            return lang
    words = [w.lower() for w in re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)?", body)]
    if len(words) < 30:
        return None
    scores = sorted(((sum(1 for w in words if w in stops), lang)
                     for lang, stops in _STOPSETS.items()), reverse=True)
    (best, lang), (second, _l2) = scores[0], scores[1]
    if best < 15 or best < second * 1.3:
        return None
    return lang


_HI_CUE = re.compile(r"^\s*(?:-\s*)?(?:[\[(][^\])]{2,40}[\])]|[A-Z][A-Z .']{1,20}:)")


def looks_hearing_impaired(text: str) -> bool:
    """True when the text reads like SDH: plenty of [sound] / (music) cues or
    SPEAKER: labels. (♪ lyrics alone don't count — plain subs carry them
    too.) Pure."""
    body = _dialogue(text)
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if len(lines) < 20:
        return False
    cues = sum(1 for ln in lines if _HI_CUE.match(ln))
    return cues >= 10 and cues / len(lines) >= 0.04


# ── embedded tracks ──────────────────────────────────────────────────────────

_cache_lock = threading.Lock()
_probe_cache: "OrderedDict[str, tuple]" = OrderedDict()
_CACHE_MAX = 4096


def _probe_streams(file_path: str) -> list[dict] | None:
    """ffprobe subtitle streams, cached by path + size + mtime. None when
    ffprobe isn't available (unknown, not 'none'). Never raises."""
    try:
        st = os.stat(file_path)
    except OSError:
        return None
    sig = (st.st_size, getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
    with _cache_lock:
        hit = _probe_cache.get(file_path)
        if hit and hit[0] == sig:
            _probe_cache.move_to_end(file_path)
            return hit[1]
    from .providers.embedded import (_probe_subtitle_streams, resolve_ffmpeg,
                                     resolve_ffprobe)
    ffprobe = resolve_ffprobe(resolve_ffmpeg())
    if not ffprobe:
        return None
    streams = _probe_subtitle_streams(ffprobe, file_path)
    with _cache_lock:
        _probe_cache[file_path] = (sig, streams)
        _probe_cache.move_to_end(file_path)
        while len(_probe_cache) > _CACHE_MAX:
            _probe_cache.popitem(last=False)
    return streams


def clear_probe_cache() -> None:
    with _cache_lock:
        _probe_cache.clear()


def _setting(settings: Any, key: str, default: Any) -> Any:
    if isinstance(settings, dict) and key in settings:
        return settings[key]
    if callable(settings):
        try:
            return settings(key, default)
        except Exception:  # noqa: BLE001
            return default
    return default


def embedded_tracks(video_path: str, settings: Any = None,
                    probe: Callable[[str], list[dict] | None] | None = None) -> list[Track]:
    """Embedded subtitle tracks that count as present, per the settings
    (``subtitle_use_embedded``, ``subtitle_ignore_pgs/vobsub/ass``).
    ``settings`` is a dict or a get_setting callable. Never raises."""
    try:
        if not bool(_setting(settings, "subtitle_use_embedded", True)):
            return []
        if not video_path or not os.path.isfile(str(video_path)):
            return []
        streams = (probe or _probe_streams)(str(video_path)) or []
        ignore = set()
        if _setting(settings, "subtitle_ignore_pgs", False):
            ignore |= _PGS_CODECS
        if _setting(settings, "subtitle_ignore_vobsub", False):
            ignore |= _VOBSUB_CODECS
        if _setting(settings, "subtitle_ignore_ass", False):
            ignore |= _ASS_CODECS
        out: list[Track] = []
        for s in streams:
            codec = str(s.get("codec_name") or "").strip().lower()
            if codec in ignore:
                continue
            tags = s.get("tags") if isinstance(s.get("tags"), dict) else {}
            lang = to_code(tags.get("language"))
            if not lang:
                continue  # 'und' / untagged: unknown language
            disp = s.get("disposition") if isinstance(s.get("disposition"), dict) else {}
            title = str(tags.get("title") or "").lower()
            forced = bool(disp.get("forced")) or "forced" in title or "foreign" in title
            hi = bool(disp.get("hearing_impaired")) or bool(
                re.search(r"\b(sdh|cc|hearing|hoh)\b", title))
            out.append(Track(lang, hi, forced, "embedded"))
        return out
    except Exception:  # noqa: BLE001
        return []


# ── the decision ─────────────────────────────────────────────────────────────

def existing_tracks(video_path: str, settings: Any = None,
                    names: list[str] | None = None,
                    read_text: Callable[[str], str | None] | None = None,
                    probe: Callable[[str], list[dict] | None] | None = None) -> list[Track]:
    """External + embedded tracks for one video. Never raises."""
    return (external_tracks(video_path, names=names, read_text=read_text)
            + embedded_tracks(video_path, settings, probe=probe))


def covered(tracks: list[Track], language: str, hi: bool = False,
            forced: bool = False) -> bool:
    """True when ``tracks`` already cover a wanted language×flags, by
    bazarr's rules (see the module docstring). Pure."""
    for t in tracks or []:
        if not satisfies_language(t.language, language):
            continue
        if forced:
            if t.forced:
                return True
            continue
        if t.forced:
            continue  # foreign-parts-only never covers a full track
        if hi and not t.hi:
            continue
        return True
    return False


__all__ = ["Track", "SUBTITLE_EXTENSIONS", "parse_sidecar_tokens",
           "external_tracks", "embedded_tracks", "existing_tracks", "covered",
           "detect_language", "looks_hearing_impaired", "clear_probe_cache"]
