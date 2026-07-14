"""Parse a scene/p2p release title into structured quality + scope fields.

This is the video equivalent of Sonarr/Radarr's release parser: given a raw name
like ``The Wire S02 1080p BluRay x265 DDP5.1-GROUP`` it pulls out resolution,
source, codec, HDR, audio, group, repack/proper, and the SEASON/EPISODE scope
(single episode vs season pack vs complete-series pack). The download engine and
the search UI both rely on this to validate that a hit actually matches what was
searched (a season search must return the *whole* season, etc.).

Pure + regex-only (no DB, no network) so it's unit-tested in isolation. Isolated —
imports only re/typing; the music side never imports it.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Resolution — first match wins (most specific first).
_RES = [
    (re.compile(r"\b(2160p|4k|uhd)\b", re.I), "2160p"),
    (re.compile(r"\b1080[pi]\b", re.I), "1080p"),
    (re.compile(r"\b720[pi]\b", re.I), "720p"),
    (re.compile(r"\b(480[pi]|576[pi])\b", re.I), "480p"),
]
# Source — order matters (remux before bluray; web-dl before webrip).
_SOURCE = [
    (re.compile(r"\bremux\b", re.I), "remux"),
    (re.compile(r"\b(blu-?ray|bdrip|brrip|bd25|bd50)\b", re.I), "bluray"),
    (re.compile(r"\b(web-?dl|web\.?dl|amzn|nf|dsnp|hmax|atvp)\b", re.I), "web-dl"),
    (re.compile(r"\bweb-?rip\b", re.I), "webrip"),
    (re.compile(r"\bweb\b", re.I), "web-dl"),   # plain "WEB" (very common) — treat as WEB-DL
    (re.compile(r"\bhdtv\b", re.I), "hdtv"),
    (re.compile(r"\b(dvdrip|dvd)\b", re.I), "dvd"),
    (re.compile(r"\b(cam|hdcam|ts|telesync|hdts)\b", re.I), "cam"),
    (re.compile(r"\b(scr|screener|dvdscr|bdscr)\b", re.I), "screener"),
    (re.compile(r"\bworkprint\b", re.I), "workprint"),
]
_CODEC = [
    (re.compile(r"\b(x265|h\.?265|hevc)\b", re.I), "hevc"),
    (re.compile(r"\b(x264|h\.?264|avc)\b", re.I), "x264"),
    (re.compile(r"\bav1\b", re.I), "av1"),
]
_HDR = [
    (re.compile(r"\b(dolby\s?vision|do?vi|\bdv\b)\b", re.I), "dv"),
    (re.compile(r"\bhdr10\+\b", re.I), "hdr10"),
    (re.compile(r"\bhdr10\b", re.I), "hdr10"),
    (re.compile(r"\bhdr\b", re.I), "hdr"),
]
_AUDIO = [
    (re.compile(r"\batmos\b", re.I), "atmos"),
    (re.compile(r"\b(truehd|true-hd)\b", re.I), "truehd"),
    (re.compile(r"\b(dts-?hd|dts-?x)\b", re.I), "dts-hd"),
    (re.compile(r"\bdts\b", re.I), "dts"),
    (re.compile(r"\b(ddp|eac3|dd\+)\b", re.I), "eac3"),
    (re.compile(r"\b(ac3|dd5\.?1|dd2\.?0)\b", re.I), "ac3"),
    (re.compile(r"\baac\b", re.I), "aac"),
]

_RANGE = re.compile(r"\bS(\d{1,2})\s*[-–]\s*S?(\d{1,2})\b", re.I)   # S01-S05
# Season allows 3 digits — long-running dailies really do reach S277 (House Hunters).
_SXXEXX = re.compile(r"\bS(\d{1,3})[\s.]?E(\d{1,3})\b", re.I)        # S02E03 / S277E05
_SXX = re.compile(r"\bS(\d{1,3})\b", re.I)                          # S02 (pack)
_SEASON_WORD = re.compile(r"\bseason[\s.]?(\d{1,2})\b", re.I)        # Season 2
_COMPLETE = re.compile(r"\b(complete|collection|all\s?seasons)\b", re.I)
_GROUP = re.compile(r"-([A-Za-z0-9]{2,})\s*$")
# Daily/date-named releases — 'The.Daily.Show.2026.07.08.Guest.1080p...' (Sonarr's
# daily-series naming). Dots/spaces/dashes between the parts; month/day validated.
_AIR_DATE = re.compile(r"\b((?:19|20)\d{2})[ ._-](0[1-9]|1[0-2])[ ._-](0[1-9]|[12]\d|3[01])\b")


def _first(table, text) -> Any:
    for rx, val in table:
        if rx.search(text):
            return val
    return None


# Release year — a 4-digit 19xx/20xx not embedded in a longer number. The LAST match is the
# release year (a year IN the title, e.g. "Blade Runner 2049", comes before it).
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def parse_release(title: Any) -> dict:
    """Parse a release name into quality + scope fields. Never raises."""
    t = str(title or "")
    _years = _YEAR.findall(t)
    out = {
        "title": t,
        "year": int(_years[-1]) if _years else None,
        "resolution": _first(_RES, t),
        "source": _first(_SOURCE, t),
        "codec": _first(_CODEC, t),
        "hdr": _first(_HDR, t),
        "audio": _first(_AUDIO, t),
        "group": None,
        "repack": bool(re.search(r"\brepack\b", t, re.I)),
        "proper": bool(re.search(r"\bproper\b", t, re.I)),
        "three_d": bool(re.search(r"\b3d\b", t, re.I)),
        "season": None,
        "season_end": None,
        "episode": None,
        "air_date": None,
        "is_season_pack": False,
        "is_series_pack": False,
    }
    g = _GROUP.search(t)
    if g:
        out["group"] = g.group(1)
    ad = _AIR_DATE.search(t)
    if ad:
        out["air_date"] = ad.group(1) + "-" + ad.group(2) + "-" + ad.group(3)

    rng = _RANGE.search(t)
    m = _SXXEXX.search(t)
    if rng:
        out["season"] = int(rng.group(1))
        out["season_end"] = int(rng.group(2))
        out["is_series_pack"] = True
    elif m:
        out["season"] = int(m.group(1))
        out["episode"] = int(m.group(2))
    else:
        sm = _SXX.search(t) or _SEASON_WORD.search(t)
        if sm:
            out["season"] = int(sm.group(1))
            out["is_season_pack"] = True
        if _COMPLETE.search(t):
            out["is_series_pack"] = True
            out["is_season_pack"] = False
    return out


# ── Title extraction + matching (Radarr/Sonarr-parity title gate) ──────────────
# The download engine must confirm a hit's TITLE matches the wanted film/show, not
# just the year — otherwise a text search for "Paradox (2017)" happily accepts
# "The Cloverfield Paradox 2018" (title is a substring, year is one off). We isolate
# the title portion of the release name, normalize it, and require a real match.

_YEAR_TOKEN = re.compile(r"\b(?:19|20)\d{2}\b")
# First "release metadata" token — where the title ends when there's no usable year.
_META_BOUNDARY = re.compile(
    r"\b(?:2160p|1080[pi]|720[pi]|480[pi]|576[pi]|4k|uhd"
    r"|blu-?ray|bdrip|brrip|web-?dl|web-?rip|web|hdtv|dvdrip|dvd|remux|hdcam|cam|telesync|hdts"
    r"|x264|x265|h\.?264|h\.?265|hevc|avc|av1"
    r"|s\d{1,3}(?:e\d{1,3})?|season)\b", re.I)
_ARTICLE = re.compile(r"^(?:the|a|an)\s+")
# Trailing words that are an edition of the SAME film, not a different title.
_EDITION_TOKENS = frozenset({
    "extended", "remastered", "remaster", "unrated", "uncut", "directors", "director",
    "cut", "edition", "theatrical", "special", "imax", "final", "ultimate", "definitive",
})


def _spaces(s: Any) -> str:
    """Separators (dots/underscores/dashes) → spaces, whitespace collapsed."""
    return re.sub(r"\s+", " ", re.sub(r"[._\-]+", " ", str(s or ""))).strip()


def extract_title(release_name: Any) -> str:
    """The title portion of a release name — everything before the release year (the
    LAST year token, so 'Blade Runner 2049 2017 1080p' keeps '2049') or before the
    first quality/scope token, whichever comes FIRST. The scope token matters for
    date-named daily releases ('Show.S277E05.2026.07.08...'): the date's year sits
    AFTER the SxxExx, and cutting only at the year would leave numbering junk in the
    title. Returns '' when the title can't be isolated (e.g. a numeric-only title
    like '2012' with no release year)."""
    t = str(release_name or "").strip()
    if not t:
        return ""
    cut = None
    years = list(_YEAR_TOKEN.finditer(t))
    if years and _spaces(t[:years[-1].start()]):
        cut = years[-1].start()          # cut at the release year (keeps years IN the title)
    m = _META_BOUNDARY.search(t)         # first quality/scope token wins if it comes earlier
    if m and _spaces(t[:m.start()]) and (cut is None or m.start() < cut):
        cut = m.start()
    if cut is None:
        cut = len(t)
    return _spaces(t[:cut])


def normalize_title(s: Any) -> str:
    """Fold a title to a comparable key: strip accents, lowercase, '&'→'and',
    punctuation → space, drop a single leading article. 'The Dark Knight' and
    'dark.knight' both fold to 'dark knight'."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii")
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return _ARTICLE.sub("", s, count=1)


def acceptable_titles(want_title: Any) -> set:
    """The set of normalized titles a release may legitimately carry. ``want_title`` is
    a single title OR an iterable of them (the film/show's primary title + its TMDB
    alternative / original-language titles) — this is how we beat Radarr/Sonarr on
    FALSE NEGATIVES: a release named by any known alias ('God Particle' for 'The
    Cloverfield Paradox', a foreign film under its original title) still matches."""
    if want_title is None:
        return set()
    items = [want_title] if isinstance(want_title, str) else list(want_title)
    return {n for n in (normalize_title(x) for x in items) if n}


def titles_match(release_name: Any, want_title: Any) -> bool:
    """True when a release's parsed title matches ANY acceptable title (primary +
    aliases). Exact after normalization, tolerating only trailing edition words
    ('Paradox Extended' for 'Paradox') and squeezed spacing ('90DayFiance').

    Soulseek results are share PATHS, not scene one-liners — the show name often
    lives in a parent folder ('TV/90 Day Fiancé/Season 12/ep.mkv'), so every path
    SEGMENT is tried as a title candidate, not just the whole string. An
    unknown/unisolable title passes (the year gate still applies) so a numeric
    title like '2012' is never falsely rejected — we only ever REJECT on a
    confident mismatch against every acceptable title, never guess a match."""
    wants = acceptable_titles(want_title)
    if not wants:
        return True
    raw = str(release_name or "")
    cands = [raw]
    segs = [s for s in re.split(r"[\\/]+", raw) if s.strip()]
    if len(segs) > 1:
        cands += segs
    saw_any = False
    for cand in cands:
        got = normalize_title(extract_title(cand))
        if not got:
            continue
        saw_any = True
        for want in wants:
            if got == want:
                return True
            if got.replace(" ", "") == want.replace(" ", ""):
                return True                              # '90DayFiance' == '90 day fiance'
            if got.startswith(want + " "):
                rest = got[len(want):].split()
                if rest and all(tok in _EDITION_TOKENS for tok in rest):
                    return True
    return not saw_any


__all__ = ["parse_release", "extract_title", "normalize_title",
           "acceptable_titles", "titles_match"]
