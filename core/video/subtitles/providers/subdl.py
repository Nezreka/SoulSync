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

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any, Callable

from ..archive import extract_srt, pick_member
from .base import (SubtitleCandidate, SubtitleProvider, SubtitleQuery, flag_filter,
                   http_error_message)

BASE = "https://api.subdl.com/api/v1"
DL_HOST = "https://dl.subdl.com"
_UA = "SoulSync v1.0"


def _norm_lang(value: Any) -> str:
    """Best-effort ISO 639-1 code for a language name or code. Pure."""
    from ..languages import to_code
    s = str(value or "").strip().lower()
    if not s:
        return ""
    # subdl writes regional codes as BR_PT / ZH_TW (region first)
    m = re.match(r"^([a-z]{2})_([a-z]{2})$", s)
    if m and m.group(2) in ("pt", "zh"):
        return to_code("%s-%s" % (m.group(2), m.group(1))) or ""
    return to_code(s) or ""


def _search_params(identity: dict, lang: str, api_key: str) -> dict | None:
    """SubDL /subtitles query for a title identity. Episodes search by series
    id + season/episode; movies by imdb/tmdb id. None if unidentified. Pure."""
    identity = identity if isinstance(identity, dict) else {}
    params: dict[str, Any] = {
        "api_key": api_key,
        "languages": str(lang or "en").upper(),
        "subs_per_page": 30,
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


def _entry_forced(entry: dict) -> bool:
    """subdl has no forced flag; like bazarr, read it off the name, release
    names and comment."""
    parts = [entry.get("release_name"), entry.get("name"), entry.get("comment")]
    rel = entry.get("releases")
    if isinstance(rel, list):
        parts.extend(rel)
    blob = " ".join(str(p) for p in parts if p).lower()
    return bool(re.search(r"\bforced\b|foreign[ ._-]?parts", blob))


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


def _entry_episode(entry: dict, want_season: int | None) -> tuple[int | None, int | None]:
    """(season, episode) of a plain single-episode entry, from its fields or
    the release-name tag. Pure. subdl sends 0 instead of leaving a field out,
    so 0 only counts as a season when specials are what we want."""
    season = _as_int(entry.get("season"))
    if season == 0 and want_season != 0:
        season = None
    episode = _as_int(entry.get("episode")) or None
    if episode is None:
        ep_from = _as_int(entry.get("episode_from")) or None
        if ep_from is not None and ep_from == (_as_int(entry.get("episode_end")) or None):
            episode = ep_from
    if season is None or episode is None:
        m = re.search(r"[Ss](\d{1,2})[Ee](\d{1,3})", _entry_title(entry))
        if m:
            season = int(m.group(1)) if season is None else season
            episode = int(m.group(2)) if episode is None else episode
    return season, episode


def _is_pack(entry: dict) -> bool:
    """True for a full-season or multi-episode range entry. Pure."""
    if entry.get("full_season"):
        return True
    ep_from = _as_int(entry.get("episode_from")) or None
    ep_end = _as_int(entry.get("episode_end")) or None
    return ep_from is not None and ep_end is not None and ep_from != ep_end


def _usable_file(f: dict) -> bool:
    """An unpack file worth a download: srt (other formats fail extraction
    after burning quota) and not a commentary track — movie zips often carry
    the director's commentary next to the film's own subtitles. Pure."""
    fmt = str(f.get("format") or "").strip().lower()
    if fmt and fmt != "srt":
        return False
    name = ("%s %s" % (f.get("name") or "", f.get("release_name") or "")).lower()
    return "commentary" not in name


def _entry_url_ok(url: str | None) -> bool:
    """Whole-entry downloads we can read: zips (or a bare .srt). subdl also
    serves .sub/.txt microdvd files that would never extract. Pure."""
    if not url:
        return False
    ext = os.path.splitext(urllib.parse.urlsplit(url).path)[1].lower()
    return ext in ("", ".zip", ".srt")


def _candidates_from_entries(entries: list[dict], lang: str, provider_id: str,
                             season: Any = None, episode: Any = None) -> list[SubtitleCandidate]:
    """Entries -> candidates, language-filtered. Pure.

    Entries with unpack_files contribute one candidate per usable file
    (direct .srt links): for an episode only the files placed on that
    season/episode, for a movie every usable file — never a commentary
    track. Entries without files fall back to the whole-entry zip, and for
    an episode only when the entry itself is placed on that episode (a pack
    zip can't be attributed — skipped, never guessed). ai-translated
    entries are skipped, same as bazarr.
    """
    want = str(lang or "en").lower()
    want_season = _as_int(season)
    want_ep = _as_int(episode)
    out: list[SubtitleCandidate] = []

    def add(title, url, hi, forced, entry):
        out.append(SubtitleCandidate(
            provider_id=provider_id, language=want, hi=hi, forced=forced,
            title=title, download_ref=url,
            download_count=_as_int(entry.get("download_count")) or 0))

    for entry in entries:
        if entry.get("ai_translated"):
            continue
        files = [f for f in (entry.get("unpack_files") or []) if isinstance(f, dict)]
        if files:
            item_season = _as_int(entry.get("season"))
            for f in files:
                if not _usable_file(f):
                    continue
                if (_norm_lang(f.get("language")) or _entry_lang(entry)) != want:
                    continue
                if season is not None:
                    try:
                        # subdl sends season 0 instead of omitting it — fall
                        # back to the pack's own season then
                        f_season = int(f.get("season")) if f.get("season") else item_season
                        f_ep = int(f.get("episode")) if f.get("episode") is not None else None
                    except (TypeError, ValueError):
                        continue
                    if f_season != want_season or (want_ep is not None and f_ep != want_ep):
                        continue
                url = _as_dl_url(f.get("url"))
                if not url:
                    continue
                add(str(f.get("release_name") or f.get("name") or _entry_title(entry)), url,
                    _entry_hi(f), _entry_forced(f) or _entry_forced(entry), entry)
            continue
        if season is not None:
            if _is_pack(entry):
                continue
            e_season, e_ep = _entry_episode(entry, want_season)
            # fail closed: an entry we can't place is never attributed
            if e_season != want_season or (want_ep is not None and e_ep != want_ep):
                continue
        if _entry_lang(entry) != want:
            continue
        url = _as_dl_url(_entry_url(entry))
        if not _entry_url_ok(url):
            continue
        add(_entry_title(entry), url, _entry_hi(entry), _entry_forced(entry), entry)
    return out


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# shared with the other providers: zip, RAR/7z (bsdtar) or a bare .srt,
# commentary tracks and macOS shadow files skipped.
_pick_zip_member = pick_member
_extract_srt = extract_srt


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
        return flag_filter(cands, query)

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
        return _extract_srt(payload)

    def test(self, get_setting: Callable[[str, Any], Any]) -> tuple[bool, str]:
        if not self.is_configured(get_setting):
            return False, "No SubDL API key"
        try:
            j = self._search_api({"api_key": self._api_key, "imdb_id": "tt0137523",
                                  "type": "movie", "languages": "EN",
                                  "subs_per_page": 1})
        except Exception as e:  # noqa: BLE001
            return False, http_error_message("SubDL", e)
        if isinstance(j, dict) and j.get("status") is True:
            return True, "SubDL key OK"
        msg = (j.get("error") or j.get("message")) if isinstance(j, dict) else None
        return False, "SubDL: %s" % (msg or "unexpected response")

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
