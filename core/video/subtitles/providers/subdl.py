"""SubDL (subdl.com) behind the provider interface.

API: ``GET https://api.subdl.com/api/v1/subtitles`` — documented at
https://subdl.com/api-doc. Free API key from the SubDL account panel
(https://subdl.com/panel/api); search is key-gated, downloads are zips from
``https://dl.subdl.com`` (anonymous downloads are IP-limited, keyed downloads
use the account quota).

Search supports imdb_id / tmdb_id, type=movie|tv with season/episode numbers,
a languages filter, and an hi flag. ``unpack=1`` exposes the individual files
inside pack subtitles so episodes resolve to the right file.

Isolated: stdlib only; no music imports.
"""

from __future__ import annotations

import io
import json
import re
import urllib.parse
import urllib.request
import zipfile
from typing import Any, Callable

from .base import SubtitleCandidate, SubtitleProvider, SubtitleQuery

BASE = "https://api.subdl.com/api/v1"
DL_HOST = "https://dl.subdl.com"
_UA = "SoulSync v1.0"

# A few full language names -> ISO 639-1, for responses that spell it out.
_LANG_NAME_TO_CODE = {
    "english": "en", "spanish": "es", "french": "fr", "german": "de",
    "italian": "it", "portuguese": "pt", "dutch": "nl", "russian": "ru",
    "arabic": "ar", "chinese": "zh", "japanese": "ja", "korean": "ko",
    "hindi": "hi", "turkish": "tr", "polish": "pl", "swedish": "sv",
    "norwegian": "no", "danish": "da", "finnish": "fi", "greek": "el",
    "hebrew": "he", "thai": "th", "vietnamese": "vi", "indonesian": "id",
    "malay": "ms", "czech": "cs", "slovak": "sk", "romanian": "ro",
    "hungarian": "hu", "bulgarian": "bg", "ukrainian": "uk", "croatian": "hr",
    "serbian": "sr", "slovenian": "sl",
}


def _norm_lang(value: Any) -> str:
    """Best-effort ISO 639-1 code for a language name or code. Pure."""
    s = str(value or "").strip().lower()
    if not s:
        return ""
    if len(s) == 2 and s.isalpha():
        return s
    base = re.split(r"[\s\-_()]+", s)[0]
    if len(base) == 2 and base.isalpha():
        return base
    return _LANG_NAME_TO_CODE.get(s, _LANG_NAME_TO_CODE.get(base, ""))


def _search_params(identity: dict, lang: str, api_key: str) -> dict | None:
    """SubDL /subtitles query for a title identity. Episodes search by series
    id + season/episode; movies by imdb/tmdb id. None if unidentified. Pure."""
    identity = identity if isinstance(identity, dict) else {}
    params: dict[str, Any] = {
        "api_key": api_key,
        "languages": str(lang or "en").upper(),
        "subs_per_page": 30,
        "hi": 1,      # include the hearing-impaired flag; we filter locally
        "unpack": 1,  # expose per-episode files inside packs
        "client": "custom_integration",
    }
    season = identity.get("season")
    episode = identity.get("episode")
    if season is not None and (identity.get("imdb_id") or identity.get("tmdb_id")):
        params["type"] = "tv"
        params["season_number"] = season
        if episode is not None:
            params["episode_number"] = episode
        if identity.get("imdb_id"):
            params["imdb_id"] = str(identity["imdb_id"])
        else:
            params["tmdb_id"] = identity["tmdb_id"]
        return params
    if identity.get("imdb_id"):
        params["type"] = "movie"
        params["imdb_id"] = str(identity["imdb_id"])
        return params
    if identity.get("tmdb_id"):
        params["type"] = "movie"
        params["tmdb_id"] = identity["tmdb_id"]
        return params
    return None


def _subtitle_entries(search_json: Any) -> list[dict]:
    """The ``subtitles`` array of a /subtitles response. Pure."""
    if not isinstance(search_json, dict):
        return []
    if search_json.get("status") is not True:
        return []
    subs = search_json.get("subtitles")
    return [s for s in (subs or []) if isinstance(s, dict)]


def _entry_title(entry: dict) -> str:
    for key in ("release_name", "release", "name", "title"):
        v = entry.get(key)
        if v:
            return str(v)
    return ""


def _entry_lang(entry: dict) -> str:
    return _norm_lang(entry.get("language") or entry.get("lang"))


def _entry_hi(entry: dict) -> bool:
    v = entry.get("hi")
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes")


def _entry_url(entry: dict) -> str:
    for key in ("url", "download_url", "file_url"):
        v = entry.get(key)
        if v:
            return str(v)
    return ""


def _as_dl_url(url: str) -> str | None:
    """Absolute https URL on dl.subdl.com for a subtitle ``url`` value. Pure.

    Refuses anything off-host (a crafted API response must never make us fetch
    an arbitrary URL)."""
    url = str(url or "").strip()
    if not url:
        return None
    if url.startswith("https://dl.subdl.com/"):
        return url
    if url.startswith("http://dl.subdl.com/"):
        return "https://" + url[len("http://"):]
    if url.startswith("/"):
        return DL_HOST + url
    return None


def _candidates_from_entries(entries: list[dict], lang: str, provider_id: str,
                             season: Any = None, episode: Any = None) -> list[SubtitleCandidate]:
    """Entries -> candidates, language-filtered. For episodes, unpack_files
    entries matching the requested season/episode become their own candidates
    (the pack itself is skipped — its zip rarely names the episode). Pure."""
    want = str(lang or "en").lower()
    out: list[SubtitleCandidate] = []
    for entry in entries:
        if season is not None:
            # Episode query: only per-episode unpack files count. A pack zip
            # can never be attributed to an episode — skip, never guess.
            files = entry.get("unpack_files") or []
            if isinstance(files, list):
                for f in files:
                    if not isinstance(f, dict):
                        continue
                    try:
                        f_season = int(f.get("season")) if f.get("season") is not None else None
                        f_ep = int(f.get("episode")) if f.get("episode") is not None else None
                    except (TypeError, ValueError):
                        continue
                    if f_season != _as_int(season):
                        continue
                    if episode is not None and f_ep != _as_int(episode):
                        continue
                    url = _as_dl_url(f.get("url"))
                    if not url:
                        continue
                    f_lang = _norm_lang(f.get("language")) or want
                    if f_lang != want:
                        continue
                    out.append(SubtitleCandidate(
                        provider_id=provider_id,
                        language=f_lang,
                        hi=_entry_hi(f),
                        forced=False,
                        title=str(f.get("release_name") or f.get("name") or _entry_title(entry)),
                        download_ref=url,
                        download_count=_as_int(entry.get("download_count")) or 0,
                    ))
            continue
        if _entry_lang(entry) != want:
            continue
        url = _as_dl_url(_entry_url(entry))
        if not url:
            continue
        out.append(SubtitleCandidate(
            provider_id=provider_id,
            language=want,
            hi=_entry_hi(entry),
            forced=False,
            title=_entry_title(entry),
            download_ref=url,
            download_count=_as_int(entry.get("download_count")) or 0,
        ))
    return out


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pick_zip_member(names: list[str]) -> str | None:
    """First .srt in a zip. Pure."""
    for n in names:
        if n.lower().endswith(".srt"):
            return n
    return None


def _extract_srt(payload: bytes, lang: str = "en") -> str | None:
    """SRT text from a download payload: zip -> first .srt, else raw text.
    Pure (no network)."""
    if not payload:
        return None
    data = payload
    if payload[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                member = _pick_zip_member(zf.namelist())
                if not member:
                    return None
                data = zf.read(member)
        except Exception:  # noqa: BLE001 - a bad zip is a miss
            return None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
        if "-->" in text:
            return text
    return None


class SubDLProvider(SubtitleProvider):
    """SubDL behind the provider interface. Free API key from the SubDL account
    panel (https://subdl.com/panel/api), resolved (and cached on the instance)
    by ``is_configured()``. HTTP goes through small instance methods so tests
    can stub it without touching the network."""

    id = "subdl"
    display_name = "SubDL"

    def __init__(self) -> None:
        self._api_key: str | None = None

    @property
    def needs_key(self) -> bool:
        return True

    def is_configured(self, get_setting: Callable[[str, Any], Any]) -> bool:
        key = str(get_setting("subdl_api_key") or "").strip()
        self._api_key = key or None
        return self._api_key is not None

    def search(self, query: SubtitleQuery) -> list[SubtitleCandidate]:
        if not self._api_key:
            return []
        try:
            params = _search_params(query.identity, query.language, self._api_key)
            if params is None:
                return []
            entries = _subtitle_entries(self._search_api(params))
        except Exception:  # noqa: BLE001 - provider failure is a miss
            return []
        season = query.identity.get("season") if isinstance(query.identity, dict) else None
        episode = query.identity.get("episode") if isinstance(query.identity, dict) else None
        cands = _candidates_from_entries(entries, query.language, self.id,
                                         season=season, episode=episode)
        # Exact-flag matches preferred; degrade to the unfiltered list rather
        # than a total miss (same Bazarr-style degradation as OpenSubtitles).
        want_hi, want_forced = bool(query.hi), bool(query.forced)
        exact = [c for c in cands if c.hi == want_hi and c.forced == want_forced]
        return exact or cands

    def download(self, candidate: SubtitleCandidate) -> str | None:
        if not self._api_key or candidate is None:
            return None
        url = _as_dl_url(candidate.download_ref)
        if not url:
            return None
        try:
            payload = self._fetch_bytes(url)
        except Exception:  # noqa: BLE001 - provider failure is a miss
            return None
        return _extract_srt(payload, lang=candidate.language)

    # ── HTTP injection points (stubbed in tests) ─────────────────────────────
    def _search_api(self, params: dict) -> Any:
        url = BASE + "/subtitles?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                                   "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))

    def _fetch_bytes(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()


__all__ = ["SubDLProvider", "BASE"]
