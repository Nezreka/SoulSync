"""second try for a spotify playlist track the metadata source missed (#1721).

a track on a spotify playlist that the primary source (deezer, itunes...)
couldn't match used to become a wing it stub: raw title + artist, no release
behind it, never wishlisted, never on the synced playlist. but the playlist
already told us exactly which track it is. its spotify id fetches the real
track, no login needed (SpotipyFree, same thing that read the playlist).

with that track in hand:
1. logged in to spotify, the track carries an isrc. deezer looks that up
   directly, so the primary source gets its own exact track.
2. otherwise search the primary source again and take a candidate only when
   spotify backs it up: same album, same length (within 3s). a weaker title
   score is fine when the release itself agrees.
3. still nothing, keep the spotify track. it's the real release, with album,
   cover, track number and date, which beats a stub every time. the fix modal
   already stores spotify picks on a deezer setup, so this is a shape the rest
   of the app knows.

anything that doesn't pan out returns None and the track stays wing it, so
this can never do worse than before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Optional

from utils.logging_config import get_logger

logger = get_logger("discovery.spotify_second_pass")

# where a spotify id comes from: a spotify mirror's own source id, or the
# origin a user playlist's track was added with
SPOTIFY_ID_SOURCES = frozenset({"spotify", "spotify_public"})
_SPOTIFY_ID_RE = re.compile(r"[A-Za-z0-9]{22}")

# a primary-source candidate the release agrees on still has to look like the
# song. 0.7 is the bar the pipeline worker already uses for a plain match.
CORROBORATED_MIN_CONFIDENCE = 0.7
DURATION_TOLERANCE_MS = 3000

# what a matched album keeps. the free client's album carries the whole
# tracklist, copyright text and more, none of which belongs in extra_data.
_ALBUM_KEYS = ("id", "name", "release_date", "album_type", "total_tracks", "images")


@dataclass
class SecondPassMatch:
    """what the second pass found. ``track`` is the provider's own Track object,
    the same thing a search returns, so the workers store it unchanged."""
    track: Any
    provider: str
    confidence: float
    how: str  # 'isrc' | 'corroborated' | 'spotify'
    # set when provider is spotify: the full album the Track object flattens
    # to a name, plus the numbers tagging wants
    album: Optional[Dict[str, Any]] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None


def is_spotify_id(value: Any) -> bool:
    return bool(_SPOTIFY_ID_RE.fullmatch(str(value or "").strip()))


def _norm(value: Any) -> str:
    return re.sub(r"[^0-9a-z]+", "", str(value or "").casefold())


def _artist_names(raw_artists: Iterable[Any]) -> list:
    names = []
    for a in raw_artists or []:
        name = a.get("name") if isinstance(a, dict) else a
        name = str(name or "").strip()
        if name:
            names.append(name)
    return names


def albums_agree(a: Any, b: Any) -> bool:
    """same album name, give or take punctuation and an edition tail
    ("Album" vs "Album (Deluxe)"). two empty names prove nothing."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _free_track(track_id: str) -> Optional[Dict[str, Any]]:
    from core.spotify_free_metadata import SpotifyFreeMetadataClient
    return SpotifyFreeMetadataClient().get_track_details(track_id)


def fetch_spotify_track(
    track_id: str,
    spotify_client: Any = None,
    fetch_free: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
) -> Optional[Dict[str, Any]]:
    """the raw spotify track for this id, or None.

    the official api first when logged in, it's the one that hands over the
    isrc. then the no-login client, which needs nothing from the user."""
    if not is_spotify_id(track_id):
        return None
    if spotify_client is not None:
        try:
            if spotify_client.is_spotify_authenticated():
                # allow_fallback=False: a spotify id must never be looked up in
                # another catalogue, where it could hit a different track
                details = spotify_client.get_track_details(track_id, allow_fallback=False)
                raw = (details or {}).get("raw_data")
                if isinstance(raw, dict) and raw.get("id"):
                    return raw
        except Exception as e:  # noqa: BLE001 - the free client is still there
            logger.debug("official spotify lookup failed for %s: %s", track_id, e)
    try:
        raw = (fetch_free or _free_track)(track_id)
    except Exception as e:  # noqa: BLE001 - no track just means wing it
        logger.debug("free spotify lookup failed for %s: %s", track_id, e)
        return None
    return raw if isinstance(raw, dict) else None


def _usable(raw: Dict[str, Any], track_id: str, artist: str) -> bool:
    """the fetched track is the one we asked for and has what a match needs."""
    if str(raw.get("id") or "") != track_id or not str(raw.get("name") or "").strip():
        return False
    names = _artist_names(raw.get("artists"))
    album = raw.get("album")
    if not names or not isinstance(album, dict) or not str(album.get("name") or "").strip():
        return False
    from core.discovery.isrc_match import artists_agree
    # the id is exact, but a row whose id doesn't belong to its own artist means
    # something upstream is off. don't build on it.
    return artists_agree([artist], names)


def _isrc_of(raw: Dict[str, Any]) -> str:
    from core.discovery.isrc_match import clean_isrc
    return clean_isrc(((raw.get("external_ids") or {}).get("isrc")))


def _corroborated(
    raw: Dict[str, Any],
    title: str,
    artist: str,
    primary_client: Any,
    score_candidates: Callable,
) -> Optional[tuple]:
    """(match, confidence) from the primary source when spotify's own album and
    length back a candidate up, else None."""
    from core.metadata.song_search import search_song

    sp_album = (raw.get("album") or {}).get("name") or ""
    sp_duration = int(raw.get("duration_ms") or 0)
    if not sp_duration:
        return None
    sp_title = str(raw.get("name") or "").strip()

    results = []
    seen = set()
    for t in {title, sp_title}:
        if not t:
            continue
        try:
            found = search_song(primary_client, t, artist, limit=25) or []
        except Exception as e:  # noqa: BLE001
            logger.debug("second pass search failed for %r: %s", t, e)
            continue
        for r in found:
            key = getattr(r, "id", None) or id(r)
            if key not in seen:
                seen.add(key)
                results.append(r)

    backed = [
        r for r in results
        if albums_agree(getattr(r, "album", ""), sp_album)
        and int(getattr(r, "duration_ms", 0) or 0)
        and abs(int(getattr(r, "duration_ms", 0) or 0) - sp_duration) <= DURATION_TOLERANCE_MS
    ]
    if not backed:
        return None
    match, confidence, _ = score_candidates(title, artist, sp_duration, backed)
    if match is None or confidence < CORROBORATED_MIN_CONFIDENCE:
        return None
    return match, confidence


def _spotify_album(raw: Dict[str, Any]) -> Dict[str, Any]:
    album = raw.get("album") or {}
    out = {k: album[k] for k in _ALBUM_KEYS if album.get(k) not in (None, "", [])}
    artists = [{"name": n} for n in _artist_names(album.get("artists"))]
    if artists:
        out["artists"] = artists
    return out


def spotify_second_pass(
    *,
    id_source: str,
    source_track_id: Any,
    discovery_source: str,
    use_spotify: bool,
    title: str,
    artist: str,
    primary_client: Any,
    score_candidates: Callable,
    spotify_client: Any = None,
    fetch_free: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
) -> Optional[SecondPassMatch]:
    """the track a spotify id names, matched on the primary source when it can
    be and kept as spotify when it can't. None leaves the track wing it.

    ``id_source`` is where the id came from (the mirror's source, or an origin
    service). ``score_candidates(title, artist, duration_ms, results)`` is the
    discovery scorer and returns (match, confidence, index)."""
    if (id_source or "").lower() not in SPOTIFY_ID_SOURCES:
        return None
    # spotify as the primary already searched spotify, and direct_match already
    # fetched the id. nothing new to learn here.
    if use_spotify and (discovery_source or "").lower() == "spotify":
        return None
    track_id = str(source_track_id or "").strip()
    if not is_spotify_id(track_id) or not (title or "").strip():
        return None

    raw = fetch_spotify_track(track_id, spotify_client=spotify_client, fetch_free=fetch_free)
    if not raw or not _usable(raw, track_id, artist):
        return None

    source = (discovery_source or "").lower()
    if primary_client is not None and source and source != "spotify":
        isrc = _isrc_of(raw)
        if isrc:
            from core.discovery.isrc_match import isrc_match
            hit = isrc_match(isrc, source, False, fallback_client=primary_client,
                             artists=_artist_names(raw.get("artists")))
            if hit is not None:
                logger.info("second pass: '%s' by %s matched on %s by isrc %s",
                            title, artist, source, isrc)
                return SecondPassMatch(track=hit, provider=source, confidence=1.0, how="isrc")
        backed = _corroborated(raw, title, artist, primary_client, score_candidates)
        if backed:
            match, confidence = backed
            logger.info("second pass: '%s' by %s matched on %s (%.2f), spotify's album and length agree",
                        title, artist, source, confidence)
            return SecondPassMatch(track=match, provider=source, confidence=confidence,
                                   how="corroborated")

    from core.spotify_client import Track as SpotifyTrack
    try:
        sp_track = SpotifyTrack.from_spotify_track(raw)
    except Exception as e:  # noqa: BLE001 - a shape we can't read is a stub
        logger.debug("second pass: spotify track %s unreadable: %s", track_id, e)
        return None
    logger.info("second pass: '%s' by %s kept as spotify track %s, %s had no match",
                title, artist, track_id, source or "the primary source")
    return SecondPassMatch(
        track=sp_track,
        provider="spotify",
        confidence=1.0,
        how="spotify",
        album=_spotify_album(raw),
        track_number=raw.get("track_number") or None,
        disc_number=raw.get("disc_number") or None,
    )


__all__ = [
    "SPOTIFY_ID_SOURCES",
    "SecondPassMatch",
    "albums_agree",
    "fetch_spotify_track",
    "is_spotify_id",
    "spotify_second_pass",
]
