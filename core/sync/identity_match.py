"""what a playlist sync knows a source track by, beyond its provider id (#2222).

a mirrored spotify playlist says "Despina Vandi". discovery matched the track
on itunes, which says "Δέσποινα Βανδή", and the sync only ever searched under
the itunes name and the itunes id:

  * the library, plex and the playlist all said "Despina Vandi", so the name
    search found nothing (the library now answers for the other script too,
    core/library2/artist_equivalence.py)
  * sync_match_cache held the pairing from an earlier sync under the deezer
    id, so the itunes id missed it

this module gives both sync matchers the rest of what they know: every name
the track's artist goes by (the matched source's, the playlist's own, and
the library's other names for them), and a cache lookup by title + artist
across every provider id.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("sync.identity_match")


def _name_of(artist: Any) -> str:
    if isinstance(artist, dict):
        artist = artist.get("name")
    elif artist is not None and not isinstance(artist, str):
        artist = getattr(artist, "name", None)
    return artist.strip() if isinstance(artist, str) else ""


def sync_artist_names(track: Any) -> List[str]:
    """the source's artists, then the playlist's own names for them, deduped
    (case-insensitive), in that order."""
    names: List[str] = []
    seen = set()
    extra = getattr(track, "source_artists", None)
    for artist in [*(getattr(track, "artists", None) or []),
                   *(extra if isinstance(extra, (list, tuple)) else [])]:
        name = _name_of(artist)
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            names.append(name)
    return names


def known_artist_names(db: Any, track: Any, alias_cache: Optional[dict] = None) -> List[str]:
    """``sync_artist_names`` plus the library's other names for each (another
    script, a musicbrainz alias, a linked alias row)."""
    names = sync_artist_names(track)
    lookup = getattr(db, "_cached_artist_alias_names", None)
    out = list(names)
    seen = {n.casefold() for n in names}
    if callable(lookup):
        cache = {} if alias_cache is None else alias_cache
        for name in names:
            try:
                aliases = lookup(name, cache)
            except Exception as e:  # noqa: BLE001 - no aliases is the old behaviour
                logger.debug("alias names for %r failed: %s", name, e)
                continue
            if not isinstance(aliases, (list, tuple)):
                continue
            for alias in aliases:
                if isinstance(alias, str) and alias.strip() and alias.strip().casefold() not in seen:
                    seen.add(alias.strip().casefold())
                    out.append(alias.strip())
    return out


def _cache_forms(value: str, clean) -> List[str]:
    """the forms the cache writers store a title/artist under: the matching
    engine's clean form (the sync's auto matches) and lower+strip (a manual
    match, core.sync.match_overrides.record_manual_match)."""
    forms = [value.lower().strip()]
    try:
        cleaned = clean(value)
        if isinstance(cleaned, str) and cleaned.strip():
            forms.insert(0, cleaned.strip())
    except Exception as e:  # noqa: BLE001 - the plain form still looks up
        logger.debug("cache form clean failed for %r: %s", value, e)
    return [f for f in dict.fromkeys(forms) if f]


def identity_cache_rows(db: Any, title: str, artist_names: List[str],
                        server_source: str) -> List[Dict[str, Any]]:
    """sync_match_cache rows for this title by any of these artists, under
    whatever source id they were saved (best first). empty when the db can't
    say."""
    finder = getattr(db, "find_sync_match_cache_by_identity", None)
    if not callable(finder) or not (title or "").strip() or not artist_names:
        return []
    try:
        from core.matching_engine import MusicMatchingEngine
        engine = MusicMatchingEngine()
        titles = _cache_forms(title, engine.clean_title)
        artists: List[str] = []
        for name in artist_names:
            artists += _cache_forms(name, engine.clean_artist)
        rows = finder(titles, list(dict.fromkeys(artists)), server_source)
    except Exception as e:  # noqa: BLE001 - a failed lookup is a miss
        logger.debug("identity cache lookup failed for %r: %s", title, e)
        return []
    return [r for r in rows or [] if isinstance(r, dict) and r.get("server_track_id") is not None]


__all__ = ["identity_cache_rows", "known_artist_names", "sync_artist_names"]
