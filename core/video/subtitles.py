"""Download external subtitle .srt files from OpenSubtitles and drop them next to the
imported video as ``<video stem>.<lang>.srt``.

The enrichment worker only records subtitle-language AVAILABILITY; this fetches the
actual file: search → pick the most-downloaded subtitle for the language → request the
(time-limited) download link → save it. Parsing (``parse_langs`` / ``pick_best_file``)
is pure; the HTTP and filesystem are injected, so it's unit-tested without network or
disk. Best-effort BY CONTRACT — OpenSubtitles' free tier is daily-quota-limited, so a
miss is normal and never breaks an import.

Isolated: stdlib only; no music imports.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any, Callable

BASE = "https://api.opensubtitles.com/api/v1"
_UA = "SoulSync v1.0"


def parse_langs(raw: Any) -> list:
    """'en, es ; fr' → ['en','es','fr'] (lower-cased, de-duped). Defaults to ['en']."""
    out = []
    for tok in re.split(r"[,\s;]+", str(raw or "")):
        t = tok.strip().lower()
        if t and t not in out:
            out.append(t)
    return out or ["en"]


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


def srt_name(video_path: Any, lang: str) -> str:
    """'<video stem>.<lang>.srt' (no directory)."""
    stem = os.path.splitext(os.path.basename(str(video_path or "")))[0]
    return "%s.%s.srt" % (stem, lang)


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


def write_subtitles(video_path: str, langs: list, identity: dict, fetch: Callable, fs: Any) -> None:
    """For each language not already present as ``<stem>.<lang>.srt`` next to the video,
    fetch and write it via the injected ``fetch`` + ``fs`` (``list_dir``, ``write_text``).
    Idempotent + best-effort."""
    if not fetch:
        return
    folder = os.path.dirname(str(video_path or ""))
    try:
        existing = {str(n).lower() for n in (fs.list_dir(folder) or [])}
    except Exception:   # noqa: BLE001
        existing = set()
    for lang in (langs or []):
        name = srt_name(video_path, lang)
        if name.lower() in existing:
            continue
        try:
            text = fetch(identity, lang)
            if text:
                fs.write_text(os.path.join(folder, name), text)
        except Exception:   # noqa: BLE001 - a quota miss / network blip is expected, never fatal
            pass


__all__ = ["parse_langs", "pick_best_file", "srt_name", "search_params",
           "opensubtitles_fetcher", "write_subtitles"]
