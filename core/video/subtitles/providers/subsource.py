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

import io
import json
import re
import urllib.parse
import urllib.request
import zipfile
from typing import Any, Callable

from .base import SubtitleCandidate, SubtitleProvider, SubtitleQuery, flag_filter

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


def _sub_lang(entry: dict) -> str:
    v = str(entry.get("language") or entry.get("lang") or "").strip().lower()
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
    for key in ("forced", "isForced", "is_forced"):
        v = entry.get(key)
        if isinstance(v, bool):
            return v
        if str(v).strip().lower() in ("1", "true", "yes"):
            return True
    return False


def _sub_title(entry: dict) -> str:
    for key in ("releaseName", "release_name", "release", "name", "title", "fileName"):
        v = entry.get(key)
        if v:
            return str(v)
    return ""


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
        m = re.search(r"[Ss](\d{1,2})[Ee](\d{1,3})", _sub_title(entry))
        if m:
            try:
                if season is None:
                    season = int(m.group(1))
                if episode is None:
                    episode = int(m.group(2))
            except (TypeError, ValueError):
                pass
    return season, episode


def _pick_zip_member(names: list[str]) -> str | None:
    """First real .srt in a zip; subtitle zips are single-file. Pure.

    macOS-made zips carry AppleDouble shadow files (``__MACOSX/._movie.srt``)
    that also end in .srt — picking one reads binary metadata and fails the
    text check even though a valid subtitle sits next to it. Skip resource
    forks and dotfiles.
    """
    for n in names:
        if n.startswith("__MACOSX/"):
            continue
        base = n.rsplit("/", 1)[-1]
        if base.startswith("."):
            continue
        if n.lower().endswith(".srt"):
            return n
    return None


def _extract_srt(payload: bytes) -> str | None:
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
        params: dict[str, Any] = {
            "movieId": movie_id,
            "language": query.language,
            "limit": 100,
            "sort": "popular",
        }
        if query.hi:
            params["hearingImpaired"] = "true"
        return _data_list(self._api_get("/subtitles", params))

    def _entries_to_candidates(self, entries: list[dict],
                               query: SubtitleQuery) -> list[SubtitleCandidate]:
        want = str(query.language or "en").lower()
        identity = query.identity if isinstance(query.identity, dict) else {}
        want_season = _as_int(identity.get("season"))
        want_ep = _as_int(identity.get("episode"))
        out: list[SubtitleCandidate] = []
        for entry in entries:
            sid = _sub_id(entry)
            if sid is None:
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

    # ── HTTP injection points (stubbed in tests) ─────────────────────────────
    def _api_get(self, path: str, params: dict) -> Any:
        url = BASE + path + "?" + urllib.parse.urlencode(params)
        return _get_json(url, _headers(self._api_key or ""))

    def _download_bytes(self, subtitle_id: Any) -> bytes:
        url = BASE + "/subtitles/%s/download" % urllib.parse.quote(str(subtitle_id), safe="")
        return _get_bytes(url, _headers(self._api_key or ""))


__all__ = ["SubSourceProvider", "BASE"]
