"""OpenSubtitles REST v1: the legacy single-fetcher code (moved verbatim — behavior
identical) plus ``OpenSubtitlesProvider`` implementing the provider interface on top
of it.

Search returns one candidate per matching-language subtitle, ordered most-downloaded
first — the old ``pick_best_file`` ranking, kept until real scoring lands in Phase 2.

Isolated: stdlib only; no music imports.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Callable

from .base import SubtitleCandidate, SubtitleProvider, SubtitleQuery

BASE = "https://api.opensubtitles.com/api/v1"
_UA = "SoulSync v1.0"


def pick_best_file(search_json: Any, lang: str) -> Any:
    """The ``file_id`` of the most-downloaded subtitle for ``lang`` in a /subtitles
    response, or None. Pure."""
    if not isinstance(search_json, dict):
        return None
    best, best_dl = None, -1
    want = str(lang or "").lower()
    for row in (search_json.get("data") or []):
        attrs = row.get("attributes") or {}
        if str(attrs.get("language") or "").lower() != want:
            continue
        files = attrs.get("files") or []
        if not files or files[0].get("file_id") is None:
            continue
        dl = attrs.get("download_count") or 0
        if dl > best_dl:
            best, best_dl = files[0]["file_id"], dl
    return best


def search_params(identity: dict, lang: str) -> dict | None:
    """OpenSubtitles /subtitles query for a title identity. For an episode the show's
    id is used with season/episode; for a movie, the imdb/tmdb id. None if unidentified."""
    identity = identity if isinstance(identity, dict) else {}
    params = {"languages": lang}
    if identity.get("season") is not None and identity.get("tmdb_id"):
        params["parent_tmdb_id"] = identity["tmdb_id"]
        params["season_number"] = identity["season"]
        params["episode_number"] = identity.get("episode")
        return params
    if identity.get("imdb_id"):
        params["imdb_id"] = re.sub(r"^tt", "", str(identity["imdb_id"]).lower())
        return params
    if identity.get("tmdb_id"):
        params["tmdb_id"] = identity["tmdb_id"]
        return params
    return None


# ── real HTTP fetcher (injected into write_subtitles) ─────────────────────────
def _headers(key: str) -> dict:
    return {"Api-Key": key, "User-Agent": _UA, "Accept": "application/json"}


def _get_json(url: str, params: dict, headers: dict) -> Any:
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _post_json(url: str, body: dict, headers: dict) -> Any:
    h = dict(headers)
    h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def opensubtitles_fetcher(api_key: Any) -> Callable | None:
    """Return ``fetch(identity, lang) -> srt_text | None`` backed by OpenSubtitles, or
    None when there's no key. ``identity`` = {imdb_id?, tmdb_id?, season?, episode?}."""
    key = str(api_key or "").strip()
    if not key:
        return None

    def fetch(identity, lang):
        params = search_params(identity, lang)
        if params is None:
            return None
        found = _get_json(BASE + "/subtitles", params, _headers(key))
        file_id = pick_best_file(found, lang)
        if file_id is None:
            return None
        dl = _post_json(BASE + "/download", {"file_id": file_id}, _headers(key))
        link = (dl or {}).get("link")
        return _get_text(link) if link else None

    return fetch


def _candidates_from_search(search_json: Any, lang: str, provider_id: str) -> list[SubtitleCandidate]:
    """Every matching-language subtitle in a /subtitles response as a candidate,
    most-downloaded first. Pure."""
    if not isinstance(search_json, dict):
        return []
    want = str(lang or "").lower()
    ranked = []
    for row in (search_json.get("data") or []):
        attrs = row.get("attributes") or {}
        if str(attrs.get("language") or "").lower() != want:
            continue
        files = attrs.get("files") or []
        file_id = files[0].get("file_id") if files else None
        if file_id is None:
            continue
        title = str(attrs.get("release") or (files[0].get("file_name") if files else "") or "")
        ranked.append((attrs.get("download_count") or 0, SubtitleCandidate(
            provider_id=provider_id,
            language=want,
            hi=bool(attrs.get("hearing_impaired")),
            forced=bool(attrs.get("forced")),
            title=title,
            download_ref=file_id,
        )))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [candidate for _dl, candidate in ranked]


class OpenSubtitlesProvider(SubtitleProvider):
    """OpenSubtitles behind the provider interface. The API key is resolved (and
    cached on the instance) by ``is_configured()``; ``search()``/``download()``
    before that behave as unconfigured. HTTP goes through small instance methods
    so tests can stub it without touching the network."""

    id = "opensubtitles"
    display_name = "OpenSubtitles"

    def __init__(self) -> None:
        self._api_key: str | None = None

    @property
    def needs_key(self) -> bool:
        return True

    def is_configured(self, get_setting: Callable[[str, Any], Any]) -> bool:
        key = str(get_setting("opensubtitles_api_key") or "").strip()
        self._api_key = key or None
        return self._api_key is not None

    def search(self, query: SubtitleQuery) -> list[SubtitleCandidate]:
        if not self._api_key:
            return []
        identity = query.identity if isinstance(query.identity, dict) else {}
        params = search_params(identity, query.language)
        if params is None:
            return []
        cands = _candidates_from_search(self._search_api(params), query.language, self.id)
        # Exact-flag matches are preferred; when nothing matches the query's
        # flags exactly, degrade (Bazarr-style) to the unfiltered list — still
        # most-downloaded-first — rather than returning a total miss. The old
        # pick_best_file had no flag filtering at all, so an only-HI 'en' sub
        # used to land as X.en.srt; dropping it now would turn a working fetch
        # into a 'failed' row Phase 2 retries forever.
        want_hi, want_forced = bool(query.hi), bool(query.forced)
        exact = [c for c in cands if c.hi == want_hi and c.forced == want_forced]
        return exact or cands

    def download(self, candidate: SubtitleCandidate) -> str | None:
        if not self._api_key or candidate is None:
            return None
        link = self._download_link(candidate.download_ref)
        return self._fetch_text(link) if link else None

    # ── HTTP injection points (stubbed in tests) ─────────────────────────────
    def _search_api(self, params: dict) -> Any:
        return _get_json(BASE + "/subtitles", params, _headers(self._api_key or ""))

    def _download_link(self, file_id: Any) -> str | None:
        dl = _post_json(BASE + "/download", {"file_id": file_id}, _headers(self._api_key or ""))
        return (dl or {}).get("link")

    def _fetch_text(self, url: str) -> str:
        return _get_text(url)


__all__ = ["pick_best_file", "search_params", "opensubtitles_fetcher",
           "OpenSubtitlesProvider", "BASE"]
