"""SubSource (subsource.net) behind the provider interface.

API: ``https://api.subsource.net/api/v1`` — keyed with an ``X-API-Key``
header. Free API key from the SubSource profile page
(https://subsource.net/profile).

Flow: ``GET /movies/search`` (imdb id, or text) resolves a ``movieId``;
``GET /subtitles`` lists that title's subtitles (language filter, sort);
``GET /subtitles/{id}/download`` returns a zip archive.

Isolated: stdlib only; no music imports.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Callable

from ..archive import extract_srt, pick_member
from .base import (SubtitleCandidate, SubtitleProvider, SubtitleQuery, flag_filter,
                   http_error_message)

BASE = "https://api.subsource.net/api/v1"
_UA = "SoulSync v1.0"


def _headers(key: str) -> dict:
    return {"X-API-Key": key, "Accept": "application/json", "User-Agent": _UA}


def _get_json(url: str, headers: dict) -> Any:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_bytes(url: str, headers: dict) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _data_list(payload: Any) -> list[dict]:
    """The ``data`` array of an API response. Pure."""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    return [d for d in (data or []) if isinstance(d, dict)]


def _norm_imdb(value: Any) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    if not s.lower().startswith("tt"):
        digits = re.sub(r"\D", "", s)
        s = "tt" + digits if digits else s
    return s


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


#: subsource names languages in lowercase english with underscores (seen
#: live: 'english', 'brazilian_portuguese', 'farsi_persian'). the chinese
#: names are subscene's legacy pair: 'chinese_bg_code' is simplified (GB),
#: 'big_5_code' traditional.
_CODE_TO_NAME = {
    "pt-br": "brazilian_portuguese", "fa": "farsi_persian",
    "zh": "chinese_bg_code", "zh-cn": "chinese_bg_code", "zh-tw": "big_5_code",
}
_NAME_TO_CODE = {"brazilian_portuguese": "pt-br", "farsi_persian": "fa",
                 "chinese_bg_code": "zh", "big_5_code": "zh-tw"}


def _language_name(code: str) -> str | None:
    """A soulsync code -> subsource's language name, or None. Pure."""
    from ..languages import NAME_TO_CODE
    c = str(code or "").strip().lower()
    if c in _CODE_TO_NAME:
        return _CODE_TO_NAME[c]
    for name, mapped in NAME_TO_CODE.items():
        if mapped == c and name.isascii() and name.isalpha():
            return name
    return None


def _sub_lang(entry: dict) -> str:
    v = str(entry.get("language") or entry.get("lang") or "").strip().lower()
    if v in _NAME_TO_CODE:
        return _NAME_TO_CODE[v]
    if len(v) == 2 and v.isalpha():
        return v
    # Full language names ("portuguese", "dutch", "chinese") need the real
    # name→code map — naive [:2] truncation produces "po"/"du"/"ch", which
    # match nothing and silently drop those subtitles.
    from .subdl import _norm_lang
    return _norm_lang(v)


def _sub_hi(entry: dict) -> bool:
    for key in ("hearingImpaired", "hearing_impaired", "hi"):
        v = entry.get(key)
        if isinstance(v, bool):
            return v
        if str(v).strip().lower() in ("1", "true", "yes"):
            return True
    return False


def _sub_forced(entry: dict) -> bool:
    for key in ("foreignParts", "forced", "isForced", "is_forced"):
        v = entry.get(key)
        if isinstance(v, bool):
            return v
        if str(v).strip().lower() in ("1", "true", "yes"):
            return True
    return False


def _release_names(entry: dict) -> list[str]:
    """Every release name an entry lists. subsource puts them in the
    ``releaseInfo`` list. Pure."""
    rel = entry.get("releaseInfo")
    names = [str(r) for r in rel if r] if isinstance(rel, list) else ([str(rel)] if rel else [])
    for key in ("releaseName", "release_name", "release", "name", "title", "fileName"):
        v = entry.get(key)
        if v:
            names.append(str(v))
    return names


def _sub_title(entry: dict) -> str:
    names = _release_names(entry)
    return names[0] if names else ""


def _is_srt(entry: dict) -> bool:
    """subsource shows a ``preview`` of the file. srt has '-->' timings;
    subviewer/microdvd uploads don't and would fail extraction after
    burning a download. No preview: give it the benefit of the doubt."""
    preview = entry.get("preview")
    return not preview or "-->" in str(preview)


_EP_RANGE = re.compile(r"[Ss]\d{1,2}[Ee]\d{1,3}\s*(?:-\s*[Ee]?\d{1,3}|[Ee]\d{1,3})")


def _sub_id(entry: dict) -> Any:
    for key in ("id", "subtitleId", "subtitle_id"):
        if entry.get(key) is not None:
            return entry[key]
    return None


def _sub_episode(entry: dict) -> tuple[int | None, int | None]:
    """(season, episode) for an entry, from fields or the release name. Pure."""
    # Explicit None checks: season 0 (Specials) is falsy, so `or` would
    # fall through to the alternate field and lose it.
    s_val = entry.get("season") if entry.get("season") is not None \
        else entry.get("seasonNumber")
    e_val = entry.get("episode") if entry.get("episode") is not None \
        else entry.get("episodeNumber")
    season = _as_int(s_val)
    episode = _as_int(e_val)
    if season is None or episode is None:
        names = _release_names(entry)
        if any(_EP_RANGE.search(n) for n in names):
            return season, None  # a multi-episode pack: never one episode
        m = next((m for m in (re.search(r"[Ss](\d{1,2})[Ee](\d{1,3})", n) for n in names) if m),
                 None)
        if m:
            try:
                if season is None:
                    season = int(m.group(1))
                if episode is None:
                    episode = int(m.group(2))
            except (TypeError, ValueError):
                pass
    return season, episode


# shared with the other providers: zip, RAR/7z (bsdtar) or a bare .srt,
# commentary tracks and macOS shadow files skipped.
_pick_zip_member = pick_member
_extract_srt = extract_srt


class SubSourceProvider(SubtitleProvider):
    """SubSource behind the provider interface. Free API key from the SubSource
    profile page (https://subsource.net/profile), resolved (and cached on the
    instance) by ``is_configured()``. HTTP goes through small instance methods
    so tests can stub it without touching the network."""

    id = "subsource"
    display_name = "SubSource"

    def __init__(self) -> None:
        self._api_key: str | None = None

    @property
    def needs_key(self) -> bool:
        return True

    def is_configured(self, get_setting: Callable[[str, Any], Any]) -> bool:
        key = str(get_setting("subsource_api_key") or "").strip()
        self._api_key = key or None
        return self._api_key is not None

    def search(self, query: SubtitleQuery) -> list[SubtitleCandidate]:
        if not self._api_key:
            return []
        try:
            movie_id = self._resolve_title(query)
            if not movie_id:
                return []
            entries = self._list_subtitles(movie_id, query)
        except Exception:  # noqa: BLE001 - provider failure is a miss
            return []
        cands = self._entries_to_candidates(entries, query)
        # Exact-flag matches preferred; degrade to the unfiltered list rather
        # than a total miss (same Bazarr-style degradation as OpenSubtitles).
        return flag_filter(cands, query)

    def download(self, candidate: SubtitleCandidate) -> str | None:
        if not self._api_key or candidate is None:
            return None
        try:
            payload = self._download_bytes(candidate.download_ref)
        except Exception:  # noqa: BLE001 - provider failure is a miss
            return None
        return _extract_srt(payload)

    # ── title resolution + listing ───────────────────────────────────────────
    def _resolve_title(self, query: SubtitleQuery) -> Any:
        """The SubSource movieId for the query's identity, or None."""
        identity = query.identity if isinstance(query.identity, dict) else {}
        imdb = _norm_imdb(identity.get("imdb_id"))
        params: dict[str, Any] = {}
        if imdb:
            params = {"searchType": "imdb", "imdb": imdb}
        else:
            return None  # no text title in the identity — can't search
        if identity.get("season") is not None:
            params["type"] = "series"
            params["season"] = identity["season"]
        titles = _data_list(self._api_get("/movies/search", params))
        if not titles:
            return None
        # Prefer an exact imdb match when the API returns several rows.
        for t in titles:
            if _norm_imdb(t.get("imdbId") or t.get("imdb_id")) == imdb:
                return t.get("movieId") or t.get("movie_id") or t.get("id")
        first = titles[0]
        return first.get("movieId") or first.get("movie_id") or first.get("id")

    def _list_subtitles(self, movie_id: Any, query: SubtitleQuery) -> list[dict]:
        name = _language_name(query.language)
        if not name:
            return []  # a language subsource doesn't carry
        out: list[dict] = []
        # 100 per page; a long-running show's season can run past one page.
        for page in (1, 2, 3):
            payload = self._api_get("/subtitles", {
                "movieId": movie_id, "language": name, "limit": 100,
                "page": page, "sort": "popular"})
            out.extend(_data_list(payload))
            pages = ((payload or {}).get("pagination") or {}).get("pages") \
                if isinstance(payload, dict) else None
            if not isinstance(pages, int) or page >= pages:
                break
        return out

    def _entries_to_candidates(self, entries: list[dict],
                               query: SubtitleQuery) -> list[SubtitleCandidate]:
        want = str(query.language or "en").lower()
        identity = query.identity if isinstance(query.identity, dict) else {}
        want_season = _as_int(identity.get("season"))
        want_ep = _as_int(identity.get("episode"))
        out: list[SubtitleCandidate] = []
        for entry in entries:
            sid = _sub_id(entry)
            if sid is None or not _is_srt(entry):
                continue
            if _sub_lang(entry) not in ("", want):
                # Empty language on the entry: keep it (can't do better);
                # a definite mismatch is dropped.
                if _sub_lang(entry):
                    continue
            if want_season is not None:
                season, episode = _sub_episode(entry)
                # Fail closed: the API is queried by show, not by episode, so
                # this local filter is the only thing stopping a wrong-episode
                # subtitle. An entry whose season/episode can't be determined
                # is never attributed — skipped, not guessed.
                if season is None or season != want_season:
                    continue
                if want_ep is not None and (episode is None or episode != want_ep):
                    continue
            try:
                dl_count = int(entry.get("downloadCount") or entry.get("downloads") or 0)
            except (TypeError, ValueError):
                dl_count = 0
            out.append(SubtitleCandidate(
                provider_id=self.id,
                language=want,
                hi=_sub_hi(entry),
                forced=_sub_forced(entry),
                title=_sub_title(entry),
                download_ref=sid,
                download_count=dl_count,
            ))
        return out

    def test(self, get_setting: Callable[[str, Any], Any]) -> tuple[bool, str]:
        if not self.is_configured(get_setting):
            return False, "No SubSource API key"
        try:
            j = self._api_get("/movies/search", {"searchType": "imdb", "imdb": "tt0137523"})
        except Exception as e:  # noqa: BLE001
            return False, http_error_message("SubSource", e)
        if isinstance(j, dict) and isinstance(j.get("data"), list):
            return True, "SubSource key OK"
        msg = (j.get("error") or j.get("message")) if isinstance(j, dict) else None
        return False, "SubSource: %s" % (msg or "unexpected response")

    # ── HTTP injection points (stubbed in tests) ─────────────────────────────
    def _api_get(self, path: str, params: dict) -> Any:
        url = BASE + path + "?" + urllib.parse.urlencode(params)
        return _get_json(url, _headers(self._api_key or ""))

    def _download_bytes(self, subtitle_id: Any) -> bytes:
        url = BASE + "/subtitles/%s/download" % urllib.parse.quote(str(subtitle_id), safe="")
        return _get_bytes(url, _headers(self._api_key or ""))


__all__ = ["SubSourceProvider", "BASE"]
