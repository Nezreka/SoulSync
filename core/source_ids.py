"""Canonical registry of external/source ID column names.

SoulSync stores each metadata provider's ID for an artist/album/track under a
column whose NAME is inconsistent across tables — e.g. Deezer's artist id is
``deezer_id`` on the ``artists`` table but ``deezer_artist_id`` on
``watchlist_artists`` and ``album_deezer_id`` / ``similar_artist_deezer_id`` on
the discovery tables. Spotify/iTunes keep an entity qualifier on the core tables
while Deezer/Amazon/Tidal/... don't, and MusicBrainz uses three different nouns.
The result is code that checks 2–5 property-name variants everywhere.

This module is the single source of truth for "(provider, entity) → column".
It does NOT rename any database column — these ARE the real names today; the
registry just centralizes the knowledge and offers accessors that read an ID
from a dict / sqlite3.Row robustly (canonical column first, then known aliases),
so callers stop hand-rolling variant checks.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

# Entity types this registry knows about.
ENTITIES = ("artist", "album", "track")

# Canonical column name on the CORE table (artists / albums / tracks) for each
# (entity, provider). This is the name to prefer when reading/writing.
_CORE_ID_COLUMNS: Dict[str, Dict[str, str]] = {
    "artist": {
        "spotify": "spotify_artist_id",
        "itunes": "itunes_artist_id",
        "deezer": "deezer_id",
        "musicbrainz": "musicbrainz_id",
        "discogs": "discogs_id",
        "amazon": "amazon_id",
        "tidal": "tidal_id",
        "qobuz": "qobuz_id",
        "audiodb": "audiodb_id",
        "genius": "genius_id",
        "hydrabase": "soul_id",
    },
    "album": {
        "spotify": "spotify_album_id",
        "itunes": "itunes_album_id",
        "deezer": "deezer_id",
        "musicbrainz": "musicbrainz_release_id",
        "discogs": "discogs_id",
        "amazon": "amazon_id",
        "tidal": "tidal_id",
        "qobuz": "qobuz_id",
        "audiodb": "audiodb_id",
        "hydrabase": "soul_id",
    },
    "track": {
        "spotify": "spotify_track_id",
        "itunes": "itunes_track_id",
        "deezer": "deezer_id",
        "musicbrainz": "musicbrainz_recording_id",
        "amazon": "amazon_id",
        "tidal": "tidal_id",
        "qobuz": "qobuz_id",
        "audiodb": "audiodb_id",
        "genius": "genius_id",
        "hydrabase": "soul_id",
    },
}

# Other column / dict-key names the SAME (entity, provider) ID appears under
# elsewhere (satellite tables, API payloads). Accessors check the canonical
# column first, then these, so a read works regardless of where the row/dict
# came from. Keyed by (entity, provider).
_ALIASES: Dict[tuple, tuple] = {
    ("artist", "spotify"): ("similar_artist_spotify_id",),
    ("artist", "itunes"): ("artist_itunes_id", "similar_artist_itunes_id"),
    ("artist", "deezer"): ("deezer_artist_id", "artist_deezer_id", "similar_artist_deezer_id"),
    ("artist", "musicbrainz"): ("musicbrainz_artist_id", "similar_artist_musicbrainz_id"),
    ("artist", "discogs"): ("discogs_artist_id",),
    ("artist", "amazon"): ("amazon_artist_id",),
    ("album", "spotify"): ("album_spotify_id",),
    ("album", "itunes"): ("album_itunes_id",),
    ("album", "deezer"): ("deezer_album_id", "album_deezer_id"),
    ("album", "discogs"): ("discogs_release_id",),
    ("track", "deezer"): ("deezer_track_id",),
}


def id_column(provider: str, entity: str = "artist") -> Optional[str]:
    """Canonical core-table column for this provider + entity, or None if the
    provider isn't tracked for that entity."""
    return _CORE_ID_COLUMNS.get(entity, {}).get(provider)


def id_keys(provider: str, entity: str = "artist") -> tuple:
    """All known key names (canonical first, then aliases) the ID may live
    under. Useful for code that needs the full variant list explicitly."""
    keys = []
    canon = id_column(provider, entity)
    if canon:
        keys.append(canon)
    for alias in _ALIASES.get((entity, provider), ()):  # preserve order, no dups
        if alias not in keys:
            keys.append(alias)
    return tuple(keys)


def _read(data: Any, key: str) -> Any:
    """Read ``key`` from a dict or sqlite3.Row, returning None if absent."""
    try:
        keys = data.keys()  # dict and sqlite3.Row both support .keys()
    except AttributeError:
        return None
    if key in keys:
        try:
            return data[key]
        except (KeyError, IndexError):
            return None
    return None


def get_id(data: Any, provider: str, entity: str = "artist") -> Optional[str]:
    """Read this provider's ID for ``entity`` from a dict / sqlite3.Row.

    Tries the canonical column first, then every known alias, and returns the
    first non-empty value (or None). Replaces hand-rolled
    ``row.get('deezer_id') or row.get('deezer_artist_id')`` chains.
    """
    for key in id_keys(provider, entity):
        value = _read(data, key)
        if value:
            return value
    return None


def source_id_map(
    data: Any,
    entity: str = "artist",
    providers: Optional[Iterable[str]] = None,
) -> Dict[str, Optional[str]]:
    """Build a ``{provider: id}`` dict for ``entity`` from a row/dict — the
    common "artist_source_ids" pattern. Defaults to every provider known for the
    entity; pass ``providers`` to restrict/order the result.
    """
    if providers is None:
        providers = list(_CORE_ID_COLUMNS.get(entity, {}).keys())
    return {p: get_id(data, p, entity) for p in providers}
