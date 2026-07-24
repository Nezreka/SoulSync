"""The one place that maps a Watchlist provider name to its id column.

Audit finding P1-05: the native Watchlist contract only carried a bare
``artist_id``, so the database had to *guess* the provider from the id's shape —
"all digits means iTunes, anything else means Spotify". That is wrong for every
numeric-id provider (Deezer, Discogs) and for MusicBrainz UUIDs, and Amazon was
missing from the update/remove mappings entirely. A client could not reliably
put a non-Spotify artist on the watchlist, and a later scan could not resolve it.

Callers that know the provider should pass it explicitly; ``infer_source`` stays
as the documented legacy fallback for requests written before ``source`` existed.
"""

from __future__ import annotations

from typing import Dict, Optional

#: provider -> ``watchlist_artists`` column. Order is the lookup order used when
#: matching a bare id against every provider column.
SOURCE_COLUMNS: Dict[str, str] = {
    "spotify": "spotify_artist_id",
    "itunes": "itunes_artist_id",
    "deezer": "deezer_artist_id",
    "discogs": "discogs_artist_id",
    "musicbrainz": "musicbrainz_artist_id",
    "amazon": "amazon_artist_id",
}

#: Spellings the UI, the metadata registry and older payloads actually send.
SOURCE_ALIASES: Dict[str, str] = {
    "apple": "itunes",
    "apple_music": "itunes",
    "applemusic": "itunes",
    "itunes_link": "itunes",
    "spotify_public": "spotify",
    "musicbrainz_ng": "musicbrainz",
    "mb": "musicbrainz",
    "amazon_music": "amazon",
}

ARTIST_ID_COLUMNS = tuple(SOURCE_COLUMNS.values())


def normalize_source(value) -> Optional[str]:
    """Canonical provider name, or ``None`` if this is not a provider we store.

    ``None`` deliberately means "unknown", not "spotify" — the caller decides
    whether that is a 400 (explicit bad input) or a fall back to ``infer_source``
    (a legacy request that never sent one).
    """
    if not isinstance(value, str):
        return None
    key = value.strip().casefold()
    if not key:
        return None
    key = SOURCE_ALIASES.get(key, key)
    return key if key in SOURCE_COLUMNS else None


def source_column(source) -> Optional[str]:
    """The ``watchlist_artists`` column holding this provider's artist id."""
    canonical = normalize_source(source)
    return SOURCE_COLUMNS.get(canonical) if canonical else None


def infer_source(artist_id) -> str:
    """Legacy fallback for requests that never carried a provider.

    Keeps the historic "digits mean iTunes" rule so existing callers behave
    exactly as before. It is a guess and is documented as one; anything that can
    pass a real ``source`` must.
    """
    return "itunes" if str(artist_id or "").isdigit() else "spotify"


def artist_id_match_sql(alias: str = "") -> str:
    """``(col = ? OR col = ? ...)`` over every provider id column.

    Used by lookup/remove/update so a provider we added later can never be
    forgotten in one of the three places (Amazon was, before P1-05).
    """
    prefix = f"{alias}." if alias else ""
    return "(" + " OR ".join(f"{prefix}{column} = ?" for column in ARTIST_ID_COLUMNS) + ")"


__all__ = [
    "SOURCE_COLUMNS",
    "SOURCE_ALIASES",
    "ARTIST_ID_COLUMNS",
    "normalize_source",
    "source_column",
    "infer_source",
    "artist_id_match_sql",
]
