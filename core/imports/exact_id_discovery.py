"""Exact-ID album discovery for imports (Sokhi's request).

Files from Spotify-derived tools (spotiflac et al) carry two exact identifiers
the text-based identification can't use: an ISRC tag, and the track's Spotify
URL in the comment field. When artist/album TEXT search fails or mis-fires
(Japanese releases are the reported case), those IDs can answer "which album
is this?" directly:

* a Spotify track link resolves 1:1 to a track and the album Spotify files it
  under;
* an ISRC identifies the exact RECORDING — but the same recording appears on
  several releases (single / album / compilation), so one lookup gives an
  ambiguous album. The folder disambiguates: resolve several files' IDs and
  take the CONSENSUS album — the single only contains one of the folder's
  twelve codes, the real album contains them all.

The discovery tier runs FIRST in identification and degrades to the existing
strategies on any miss or error. Pure logic (extraction + consensus) is
injected-resolver based so it tests without clients; the real resolvers live
in ``default_resolvers`` with lazy imports.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("imports.exact_id_discovery")

_SPOTIFY_TRACK_RE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z\-]+/)?track/|spotify:track:)([A-Za-z0-9]{10,})")

# How many files we spend lookups on per candidate. Enough for a solid
# consensus, cheap enough to run on every import candidate that carries IDs.
MAX_ID_LOOKUPS = 5

# The winning album must cover this share of the RESOLVED files. A lone
# resolution wins outright (single-file imports).
MIN_CONSENSUS_SHARE = 0.6


def extract_spotify_track_id(text: Any) -> Optional[str]:
    """Spotify track id out of a comment blob (URL or URI form), or None."""
    m = _SPOTIFY_TRACK_RE.search(str(text or ""))
    return m.group(1) if m else None


def read_comment_text(file_path: str) -> str:
    """Best-effort comment tag across formats. Mutagen's easy mode exposes
    'comment' for Vorbis/FLAC but NOT ID3, so ID3 COMM frames (and MP4 ©cmt)
    are read from the raw tags."""
    try:
        from mutagen import File as MutagenFile
        easy = MutagenFile(file_path, easy=True)
        if easy and easy.tags:
            vals = easy.tags.get("comment") or easy.tags.get("description") or []
            if vals and str(vals[0]).strip():
                return str(vals[0]).strip()
        raw = MutagenFile(file_path)
        if raw and raw.tags:
            getall = getattr(raw.tags, "getall", None)
            if callable(getall):                     # ID3
                for frame in getall("COMM"):
                    text = " ".join(str(t) for t in getattr(frame, "text", []) if str(t).strip())
                    if text.strip():
                        return text.strip()
            cmt = raw.tags.get("\xa9cmt")            # MP4
            if cmt:
                return str(cmt[0]).strip()
    except Exception as exc:
        logger.debug("comment read failed for %s: %s", file_path, exc)
    return ""


def consensus_album(resolutions: List[Dict[str, Any]],
                    min_share: float = MIN_CONSENSUS_SHARE) -> Optional[Dict[str, Any]]:
    """The album most of the resolved files agree on, or None.

    ``resolutions`` entries: {'album_key', 'artist', 'album'}. A single
    resolution wins outright; with several, the top album_key must cover
    ``min_share`` of them (an ISRC that resolved to the compilation instead
    of the album must not be able to hijack the folder)."""
    rows = [r for r in (resolutions or []) if r and r.get("album_key") and r.get("album")]
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["album_key"]] = counts.get(r["album_key"], 0) + 1
    best_key, best_count = max(counts.items(), key=lambda kv: kv[1])
    if best_count < len(rows) * min_share:
        logger.info("exact-id discovery: no album consensus (%d resolutions, best %d)",
                    len(rows), best_count)
        return None
    return next(r for r in rows if r["album_key"] == best_key)


def discover_album_from_ids(
    tags_list: List[Dict[str, Any]],
    *,
    resolve_spotify_track: Callable[[str], Optional[Dict[str, Any]]],
    resolve_isrc: Callable[[str], Optional[Dict[str, Any]]],
    max_lookups: int = MAX_ID_LOOKUPS,
) -> Optional[Dict[str, Any]]:
    """Resolve a candidate's album from exact identifiers in its file tags.

    Spotify links are tried first (1:1), then ISRCs. Resolvers return
    {'album_key', 'artist', 'album'} or None and must never raise into here
    (individually guarded). Returns {'artist', 'album', 'via'} or None."""
    resolutions: List[Dict[str, Any]] = []
    via = None
    spent = 0

    for kind, key, resolver in (("spotify-link", "spotify_track_id", resolve_spotify_track),
                                ("isrc", "isrc", resolve_isrc)):
        ids = []
        for t in tags_list or []:
            value = str((t or {}).get(key) or "").strip()
            if value and value not in ids:
                ids.append(value)
        for value in ids:
            if spent >= max_lookups:
                break
            spent += 1
            try:
                res = resolver(value)
            except Exception as exc:
                logger.debug("exact-id resolver %s failed for %s: %s", kind, value, exc)
                res = None
            if res:
                resolutions.append(res)
                via = via or kind
        if resolutions:
            break                                   # links answered; skip isrc spend

    winner = consensus_album(resolutions)
    if not winner:
        return None
    logger.info("exact-id discovery: '%s' — '%s' via %s (%d/%d resolutions agree)",
                winner.get("artist"), winner.get("album"), via,
                sum(1 for r in resolutions if r["album_key"] == winner["album_key"]),
                len(resolutions))
    return {"artist": winner.get("artist") or "", "album": winner["album"],
            "title": winner.get("title") or "", "via": via}


# ── real resolvers ────────────────────────────────────────────────────────────

def _pick(d: Any, *keys: str) -> str:
    for k in keys:
        v = d.get(k) if isinstance(d, dict) else None
        if v:
            return str(v)
    return ""


def _resolution_from_track(track: Any, source: str) -> Optional[Dict[str, Any]]:
    """Normalize a source track payload into {'album_key','artist','album'}."""
    if not isinstance(track, dict):
        return None
    album = track.get("album") or {}
    album_name = _pick(album, "name", "title") or _pick(track, "album_name", "album")
    album_id = _pick(album, "id") or album_name
    artists = track.get("artists") or []
    if artists and isinstance(artists[0], dict):
        artist = _pick(artists[0], "name")
    elif artists:
        artist = str(artists[0])
    else:
        artist = _pick(track.get("artist") or {}, "name") or _pick(track, "artist_name")
    if not album_name:
        return None
    return {"album_key": f"{source}:{album_id}", "artist": artist, "album": album_name,
            "title": _pick(track, "name", "title")}


def default_resolvers():
    """(resolve_spotify_track, resolve_isrc) built on the live clients.

    Spotify link → the track's canonical album on Spotify. ISRC → Spotify's
    ``isrc:`` search when a real account is connected, else Deezer's public
    ``/track/isrc:`` endpoint. Every path degrades to None."""
    def resolve_spotify_track(track_id: str) -> Optional[Dict[str, Any]]:
        try:
            from core.metadata.registry import get_spotify_client
            client = get_spotify_client()
            if not client:
                return None
            track = client.get_track_details(track_id, allow_fallback=False)
            return _resolution_from_track(track, "spotify")
        except Exception as exc:
            logger.debug("spotify link resolve failed for %s: %s", track_id, exc)
            return None

    def resolve_isrc(isrc: str) -> Optional[Dict[str, Any]]:
        code = str(isrc or "").strip().upper()
        if not code:
            return None
        # Spotify's isrc: search needs a real authenticated account.
        try:
            from core.metadata.registry import get_spotify_client
            client = get_spotify_client()
            if client and client.is_spotify_authenticated() and not client._free_active():
                results = client.sp.search(q=f"isrc:{code}", type="track", limit=1)
                items = ((results or {}).get("tracks") or {}).get("items") or []
                if items:
                    res = _resolution_from_track(items[0], "spotify")
                    if res:
                        return res
        except Exception as exc:
            logger.debug("spotify isrc resolve failed for %s: %s", code, exc)
        try:
            from core.metadata.registry import get_client_for_source
            deezer = get_client_for_source("deezer")
            if deezer and hasattr(deezer, "_api_get"):
                track = deezer._api_get(f"/track/isrc:{code}")
                if isinstance(track, dict) and not track.get("error"):
                    return _resolution_from_track(track, "deezer")
        except Exception as exc:
            logger.debug("deezer isrc resolve failed for %s: %s", code, exc)
        return None

    return resolve_spotify_track, resolve_isrc
