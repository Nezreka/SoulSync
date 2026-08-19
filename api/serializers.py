"""
Centralized serializers for the SoulSync API v1.

All serializers accept a sqlite3.Row, a dict, or a dataclass instance
and normalize the output to a plain dict. This allows the same serializer
to be used whether the data comes from raw queries or existing methods.
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Set


def _to_dict(obj) -> dict:
    """Convert a sqlite3.Row, dataclass, or dict to a plain dict."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "keys"):  # sqlite3.Row
        return {k: obj[k] for k in obj.keys()}
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict
        return asdict(obj)
    raise TypeError(f"Cannot serialize {type(obj)}")


def _parse_genres(raw) -> list:
    """Parse genres from JSON string, list, or comma-separated string."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return [g.strip() for g in raw.split(",") if g.strip()]
    return []


def _isoformat(val) -> Optional[str]:
    """Safely convert datetime or string to ISO format string."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, str):
        return val
    return str(val)


def _bool_or_none(val):
    """Convert to bool, returning None if val is None."""
    if val is None:
        return None
    return bool(val)


def filter_fields(data: dict, fields: Optional[Set[str]]) -> dict:
    """If fields set is provided, return only those keys."""
    if not fields:
        return data
    return {k: v for k, v in data.items() if k in fields}


# ── Library Entity Serializers ────────────────────────────────


def serialize_artist(obj, fields: Optional[Set[str]] = None) -> dict:
    """Full artist serialization — all columns."""
    d = _to_dict(obj)
    result = {
        "id": d.get("id"),
        "name": d.get("name"),
        "thumb_url": d.get("thumb_url"),
        "banner_url": d.get("banner_url"),
        "genres": _parse_genres(d.get("genres")),
        "summary": d.get("summary"),
        "style": d.get("style"),
        "mood": d.get("mood"),
        "label": d.get("label"),
        "server_source": d.get("server_source"),
        "created_at": _isoformat(d.get("created_at")),
        "updated_at": _isoformat(d.get("updated_at")),
        # Cross-install identity (soulid_worker). Exposed because it is the
        # only id in the row that means the same thing on another install.
        "soul_id": d.get("soul_id"),
        # External IDs
        "musicbrainz_id": d.get("musicbrainz_id"),
        "spotify_artist_id": d.get("spotify_artist_id"),
        "itunes_artist_id": d.get("itunes_artist_id"),
        "audiodb_id": d.get("audiodb_id"),
        "deezer_id": d.get("deezer_id"),
        "tidal_id": d.get("tidal_id"),
        "qobuz_id": d.get("qobuz_id"),
        "genius_id": d.get("genius_id"),
        # Match statuses
        "musicbrainz_match_status": d.get("musicbrainz_match_status"),
        "spotify_match_status": d.get("spotify_match_status"),
        "itunes_match_status": d.get("itunes_match_status"),
        "audiodb_match_status": d.get("audiodb_match_status"),
        "deezer_match_status": d.get("deezer_match_status"),
        "lastfm_match_status": d.get("lastfm_match_status"),
        "genius_match_status": d.get("genius_match_status"),
        "tidal_match_status": d.get("tidal_match_status"),
        "qobuz_match_status": d.get("qobuz_match_status"),
        # Last attempted timestamps
        "musicbrainz_last_attempted": _isoformat(d.get("musicbrainz_last_attempted")),
        "spotify_last_attempted": _isoformat(d.get("spotify_last_attempted")),
        "itunes_last_attempted": _isoformat(d.get("itunes_last_attempted")),
        "audiodb_last_attempted": _isoformat(d.get("audiodb_last_attempted")),
        "deezer_last_attempted": _isoformat(d.get("deezer_last_attempted")),
        "lastfm_last_attempted": _isoformat(d.get("lastfm_last_attempted")),
        "genius_last_attempted": _isoformat(d.get("genius_last_attempted")),
        "tidal_last_attempted": _isoformat(d.get("tidal_last_attempted")),
        "qobuz_last_attempted": _isoformat(d.get("qobuz_last_attempted")),
        # Last.fm metadata
        "lastfm_listeners": d.get("lastfm_listeners"),
        "lastfm_playcount": d.get("lastfm_playcount"),
        "lastfm_tags": d.get("lastfm_tags"),
        "lastfm_similar": d.get("lastfm_similar"),
        "lastfm_bio": d.get("lastfm_bio"),
        "lastfm_url": d.get("lastfm_url"),
        # Genius metadata
        "genius_description": d.get("genius_description"),
        "genius_alt_names": d.get("genius_alt_names"),
        "genius_url": d.get("genius_url"),
    }
    # Preserve extra keys from enriched queries (album_count, track_count, is_watched)
    for extra_key in ("album_count", "track_count", "is_watched", "image_url"):
        if extra_key in d:
            result[extra_key] = d[extra_key]
    return filter_fields(result, fields)


def serialize_album(obj, fields: Optional[Set[str]] = None) -> dict:
    """Full album serialization — all columns."""
    d = _to_dict(obj)
    result = {
        "id": d.get("id"),
        "artist_id": d.get("artist_id"),
        "title": d.get("title"),
        "year": d.get("year"),
        "thumb_url": d.get("thumb_url"),
        "genres": _parse_genres(d.get("genres")),
        "track_count": d.get("track_count"),
        "duration": d.get("duration"),
        "style": d.get("style"),
        "mood": d.get("mood"),
        "label": d.get("label"),
        "explicit": _bool_or_none(d.get("explicit")),
        "record_type": d.get("record_type"),
        "server_source": d.get("server_source"),
        "created_at": _isoformat(d.get("created_at")),
        "updated_at": _isoformat(d.get("updated_at")),
        "upc": d.get("upc"),
        "copyright": d.get("copyright"),
        # Cross-install identity (soulid_worker).
        "soul_id": d.get("soul_id"),
        # External IDs
        "musicbrainz_release_id": d.get("musicbrainz_release_id"),
        "spotify_album_id": d.get("spotify_album_id"),
        "itunes_album_id": d.get("itunes_album_id"),
        "audiodb_id": d.get("audiodb_id"),
        "deezer_id": d.get("deezer_id"),
        "tidal_id": d.get("tidal_id"),
        "qobuz_id": d.get("qobuz_id"),
        # Match statuses
        "musicbrainz_match_status": d.get("musicbrainz_match_status"),
        "spotify_match_status": d.get("spotify_match_status"),
        "itunes_match_status": d.get("itunes_match_status"),
        "audiodb_match_status": d.get("audiodb_match_status"),
        "deezer_match_status": d.get("deezer_match_status"),
        "lastfm_match_status": d.get("lastfm_match_status"),
        "tidal_match_status": d.get("tidal_match_status"),
        "qobuz_match_status": d.get("qobuz_match_status"),
        # Last attempted timestamps
        "musicbrainz_last_attempted": _isoformat(d.get("musicbrainz_last_attempted")),
        "spotify_last_attempted": _isoformat(d.get("spotify_last_attempted")),
        "itunes_last_attempted": _isoformat(d.get("itunes_last_attempted")),
        "audiodb_last_attempted": _isoformat(d.get("audiodb_last_attempted")),
        "deezer_last_attempted": _isoformat(d.get("deezer_last_attempted")),
        "lastfm_last_attempted": _isoformat(d.get("lastfm_last_attempted")),
        "tidal_last_attempted": _isoformat(d.get("tidal_last_attempted")),
        "qobuz_last_attempted": _isoformat(d.get("qobuz_last_attempted")),
        # Last.fm metadata
        "lastfm_listeners": d.get("lastfm_listeners"),
        "lastfm_playcount": d.get("lastfm_playcount"),
        "lastfm_tags": d.get("lastfm_tags"),
        "lastfm_wiki": d.get("lastfm_wiki"),
        "lastfm_url": d.get("lastfm_url"),
    }
    return filter_fields(result, fields)


def serialize_track(obj, fields: Optional[Set[str]] = None) -> dict:
    """Full track serialization — all columns."""
    d = _to_dict(obj)
    result = {
        "id": d.get("id"),
        "album_id": d.get("album_id"),
        "artist_id": d.get("artist_id"),
        "title": d.get("title"),
        "track_number": d.get("track_number"),
        "duration": d.get("duration"),
        "file_path": d.get("file_path"),
        "bitrate": d.get("bitrate"),
        "bpm": d.get("bpm"),
        "explicit": _bool_or_none(d.get("explicit")),
        "style": d.get("style"),
        "mood": d.get("mood"),
        "repair_status": d.get("repair_status"),
        "repair_last_checked": _isoformat(d.get("repair_last_checked")),
        "server_source": d.get("server_source"),
        "created_at": _isoformat(d.get("created_at")),
        "updated_at": _isoformat(d.get("updated_at")),
        "isrc": d.get("isrc"),
        "copyright": d.get("copyright"),
        # Cross-install identity (soulid_worker). ``soul_id`` is the song
        # (artist + title); ``album_soul_id`` pins this specific release.
        "soul_id": d.get("soul_id"),
        "album_soul_id": d.get("album_soul_id"),
        # External IDs
        "musicbrainz_recording_id": d.get("musicbrainz_recording_id"),
        "spotify_track_id": d.get("spotify_track_id"),
        "itunes_track_id": d.get("itunes_track_id"),
        "audiodb_id": d.get("audiodb_id"),
        "deezer_id": d.get("deezer_id"),
        "tidal_id": d.get("tidal_id"),
        "qobuz_id": d.get("qobuz_id"),
        "genius_id": d.get("genius_id"),
        # Match statuses
        "musicbrainz_match_status": d.get("musicbrainz_match_status"),
        "spotify_match_status": d.get("spotify_match_status"),
        "itunes_match_status": d.get("itunes_match_status"),
        "audiodb_match_status": d.get("audiodb_match_status"),
        "deezer_match_status": d.get("deezer_match_status"),
        "lastfm_match_status": d.get("lastfm_match_status"),
        "genius_match_status": d.get("genius_match_status"),
        "tidal_match_status": d.get("tidal_match_status"),
        "qobuz_match_status": d.get("qobuz_match_status"),
        # Last attempted timestamps
        "musicbrainz_last_attempted": _isoformat(d.get("musicbrainz_last_attempted")),
        "spotify_last_attempted": _isoformat(d.get("spotify_last_attempted")),
        "itunes_last_attempted": _isoformat(d.get("itunes_last_attempted")),
        "audiodb_last_attempted": _isoformat(d.get("audiodb_last_attempted")),
        "deezer_last_attempted": _isoformat(d.get("deezer_last_attempted")),
        "lastfm_last_attempted": _isoformat(d.get("lastfm_last_attempted")),
        "genius_last_attempted": _isoformat(d.get("genius_last_attempted")),
        "tidal_last_attempted": _isoformat(d.get("tidal_last_attempted")),
        "qobuz_last_attempted": _isoformat(d.get("qobuz_last_attempted")),
        # Last.fm metadata
        "lastfm_listeners": d.get("lastfm_listeners"),
        "lastfm_playcount": d.get("lastfm_playcount"),
        "lastfm_tags": d.get("lastfm_tags"),
        "lastfm_url": d.get("lastfm_url"),
        # Genius metadata
        "genius_lyrics": d.get("genius_lyrics"),
        "genius_description": d.get("genius_description"),
        "genius_url": d.get("genius_url"),
    }
    # Preserve extra keys from joined queries (artist_name, album_title)
    for extra_key in ("artist_name", "album_title"):
        if extra_key in d:
            result[extra_key] = d[extra_key]
    return filter_fields(result, fields)


# ── Watchlist / Wishlist Serializers ──────────────────────────


def _watchlist_artist_source(d: dict) -> Optional[str]:
    """Which provider actually owns this row's artist id.

    ``watchlist_artists`` has no ``source`` column, so reading one returned
    ``None`` and the field silently degraded to ``preferred_metadata_source``
    — a user metadata override that is NULL on nearly every row and means
    something else entirely (R2-12). The answer is in the data: exactly one id
    column is populated for a given provider, so report that one, in the same
    lookup order the database uses.
    """
    from core.watchlist_sources import SOURCE_COLUMNS

    for source, column in SOURCE_COLUMNS.items():
        if d.get(column):
            return source
    return d.get("preferred_metadata_source")


def serialize_watchlist_artist(obj, fields: Optional[Set[str]] = None) -> dict:
    """Full watchlist artist serialization — all columns including all content filters."""
    d = _to_dict(obj)
    result = {
        "id": d.get("id"),
        # Every provider id column, plus the provider that owns the id. A native
        # client must be able to tell a Deezer artist from an iTunes one instead
        # of inferring it from the id's shape (P1-05).
        "source": _watchlist_artist_source(d),
        "spotify_artist_id": d.get("spotify_artist_id"),
        "itunes_artist_id": d.get("itunes_artist_id"),
        "deezer_artist_id": d.get("deezer_artist_id"),
        "discogs_artist_id": d.get("discogs_artist_id"),
        "musicbrainz_artist_id": d.get("musicbrainz_artist_id"),
        "amazon_artist_id": d.get("amazon_artist_id"),
        "preferred_metadata_source": d.get("preferred_metadata_source"),
        "artist_name": d.get("artist_name"),
        "image_url": d.get("image_url"),
        "date_added": _isoformat(d.get("date_added")),
        "last_scan_timestamp": _isoformat(d.get("last_scan_timestamp")),
        "created_at": _isoformat(d.get("created_at")),
        "updated_at": _isoformat(d.get("updated_at")),
        "profile_id": d.get("profile_id"),
        "quality_profile_id": d.get("quality_profile_id"),
        # Content type filters — ALL of them
        "include_albums": bool(d.get("include_albums", True)),
        "include_eps": bool(d.get("include_eps", True)),
        "include_singles": bool(d.get("include_singles", True)),
        "include_live": bool(d.get("include_live", False)),
        "include_remixes": bool(d.get("include_remixes", False)),
        "include_acoustic": bool(d.get("include_acoustic", False)),
        "include_compilations": bool(d.get("include_compilations", False)),
    }
    return filter_fields(result, fields)


def serialize_wishlist_track(obj, fields: Optional[Set[str]] = None) -> dict:
    """Standardized wishlist track serialization."""
    d = _to_dict(obj)
    track_data = d.get("track_data", d.get("spotify_data", {}))
    if isinstance(track_data, str):
        try:
            track_data = json.loads(track_data)
        except (json.JSONDecodeError, TypeError):
            track_data = {}

    source_info = d.get("source_info")
    if isinstance(source_info, str):
        try:
            source_info = json.loads(source_info)
        except (json.JSONDecodeError, TypeError):
            source_info = None

    result = {
        "id": d.get("id"),
        "track_id": d.get("track_id") or d.get("spotify_track_id") or d.get("id"),
        "spotify_track_id": d.get("spotify_track_id"),
        "track_name": (
            track_data.get("name", "Unknown") if isinstance(track_data, dict) else d.get("track_name", "Unknown")
        ),
        "artist_name": ", ".join(
            a.get("name", "") if isinstance(a, dict) else str(a)
            for a in track_data.get("artists", [])
        ) if isinstance(track_data, dict) and isinstance(track_data.get("artists"), list) else "",
        "album_name": (
            track_data.get("album", {}).get("name")
            if isinstance(track_data, dict) and isinstance(track_data.get("album"), dict)
            else None
        ),
        "track_data": track_data,
        "spotify_data": track_data,
        "provider": track_data.get("provider") if isinstance(track_data, dict) else d.get("provider"),
        "failure_reason": d.get("failure_reason"),
        "retry_count": d.get("retry_count", 0),
        "last_attempted": _isoformat(d.get("last_attempted")),
        "date_added": _isoformat(d.get("date_added")),
        "source_type": d.get("source_type"),
        "source_info": source_info,
        "profile_id": d.get("profile_id"),
        # The Quality Profile this item will be downloaded/imported against.
        # A native client has to be able to READ BACK what was actually stored,
        # otherwise it cannot verify its own write (P1-02).
        "quality_profile_id": d.get("quality_profile_id"),
    }
    return filter_fields(result, fields)


# ── Discovery Serializers ─────────────────────────────────────


def serialize_discovery_track(obj, fields: Optional[Set[str]] = None) -> dict:
    """Discovery pool track serialization."""
    d = _to_dict(obj)
    result = {
        "id": d.get("id"),
        "spotify_track_id": d.get("spotify_track_id"),
        "spotify_album_id": d.get("spotify_album_id"),
        "spotify_artist_id": d.get("spotify_artist_id"),
        "itunes_track_id": d.get("itunes_track_id"),
        "itunes_album_id": d.get("itunes_album_id"),
        "itunes_artist_id": d.get("itunes_artist_id"),
        "source": d.get("source"),
        "track_name": d.get("track_name"),
        "artist_name": d.get("artist_name"),
        "album_name": d.get("album_name"),
        "album_cover_url": d.get("album_cover_url"),
        "duration_ms": d.get("duration_ms"),
        "popularity": d.get("popularity"),
        "release_date": d.get("release_date"),
        "is_new_release": bool(d.get("is_new_release", False)),
        "artist_genres": _parse_genres(d.get("artist_genres")),
        "added_date": _isoformat(d.get("added_date")),
    }
    return filter_fields(result, fields)


def serialize_similar_artist(obj, fields: Optional[Set[str]] = None) -> dict:
    """Similar artist serialization."""
    d = _to_dict(obj)
    result = {
        "id": d.get("id"),
        "source_artist_id": d.get("source_artist_id"),
        "similar_artist_spotify_id": d.get("similar_artist_spotify_id"),
        "similar_artist_itunes_id": d.get("similar_artist_itunes_id"),
        "similar_artist_musicbrainz_id": d.get("similar_artist_musicbrainz_id"),
        "similar_artist_name": d.get("similar_artist_name"),
        "similarity_rank": d.get("similarity_rank"),
        "occurrence_count": d.get("occurrence_count"),
        "last_updated": _isoformat(d.get("last_updated")),
        "last_featured": _isoformat(d.get("last_featured")),
    }
    return filter_fields(result, fields)


def serialize_recent_release(obj, fields: Optional[Set[str]] = None) -> dict:
    """Recent release serialization."""
    d = _to_dict(obj)
    result = {
        "id": d.get("id"),
        "watchlist_artist_id": d.get("watchlist_artist_id"),
        "album_spotify_id": d.get("album_spotify_id"),
        "album_itunes_id": d.get("album_itunes_id"),
        "source": d.get("source"),
        "album_name": d.get("album_name"),
        "release_date": d.get("release_date"),
        "album_cover_url": d.get("album_cover_url"),
        "track_count": d.get("track_count"),
        "added_date": _isoformat(d.get("added_date")),
    }
    return filter_fields(result, fields)


# ── MetaSync export serializers ───────────────────────────────

# ALLOWLIST, not blocklist. Each field is named explicitly so that a column
# added to artists/albums/tracks next year cannot silently join the export.
# serialize_track/_album/_artist are deliberately NOT reused: they return
# file_path, thumb_url, genius_lyrics and other things that must never leave
# the box.
#
# Never exported, and why:
#   id / artist_id / album_id ....... media-server primary keys, install-local
#   file_path / file_size / bitrate . local filesystem layout
#   thumb_url / banner_url .......... leak the media server's address
#   lyrics, summaries, bios, wikis .. publisher-owned prose, re-fetchable by id
#   play_count / last_played ........ listening behaviour
#   server_source / verification_status / repair_* / quality_profile_id
#                                     local state, meaningless to a peer
#
# soul_id is the network KEY, never a claimable payload id — which is why it
# is not listed among the provider ids. Serving it as both would let
# network-sourced identity be laundered back in as a local observation.

_METASYNC_ARTIST_IDS = (
    "musicbrainz_id", "spotify_artist_id", "itunes_artist_id", "deezer_id",
    "discogs_id", "amazon_id", "tidal_id", "qobuz_id", "audiodb_id",
    "genius_id", "jiosaavn_id",
)

_METASYNC_ALBUM_IDS = (
    "musicbrainz_release_id", "spotify_album_id", "itunes_album_id",
    "deezer_id", "discogs_id", "amazon_id", "tidal_id", "qobuz_id",
    "audiodb_id", "jiosaavn_id", "bandcamp_url", "upc",
)

_METASYNC_TRACK_IDS = (
    "musicbrainz_recording_id", "spotify_track_id", "itunes_track_id",
    "deezer_id", "amazon_id", "tidal_id", "qobuz_id", "audiodb_id",
    "genius_id", "jiosaavn_id", "bandcamp_url",
)

# The provider whose *_match_status governs each id column. MetaSync only
# publishes ids whose status is 'matched' and drops the rest, so these are
# load-bearing, not decoration.
_METASYNC_ID_PROVIDER = {
    "musicbrainz_id": "musicbrainz", "musicbrainz_release_id": "musicbrainz",
    "musicbrainz_recording_id": "musicbrainz",
    "spotify_artist_id": "spotify", "spotify_album_id": "spotify",
    "spotify_track_id": "spotify",
    "itunes_artist_id": "itunes", "itunes_album_id": "itunes",
    "itunes_track_id": "itunes",
    "deezer_id": "deezer", "discogs_id": "discogs", "amazon_id": "amazon",
    "tidal_id": "tidal", "qobuz_id": "qobuz", "audiodb_id": "audiodb",
    "genius_id": "genius", "jiosaavn_id": "jiosaavn",
    "bandcamp_url": "bandcamp",
}


def _metasync_ids(d: dict, id_columns) -> dict:
    """Each permitted provider id plus the match status that governs it."""
    out = {}
    for column in id_columns:
        out[column] = d.get(column)
        provider = _METASYNC_ID_PROVIDER.get(column)
        if provider:
            status_key = f"{provider}_match_status"
            out[status_key] = d.get(status_key)
    return out


def serialize_metasync_artist(obj, fields: Optional[Set[str]] = None) -> dict:
    d = _to_dict(obj)
    result = {
        "soul_id": d.get("soul_id"),
        # 'canonical' | 'album' | 'name' | None. Only 'canonical' is
        # reproducible on another install (see the artists migration).
        "soul_id_path": d.get("soul_id_path"),
        "name": d.get("name"),
        "genres": _parse_genres(d.get("genres")),
        "updated_at": _isoformat(d.get("updated_at")),
    }
    result.update(_metasync_ids(d, _METASYNC_ARTIST_IDS))
    return filter_fields(result, fields)


def serialize_metasync_album(obj, fields: Optional[Set[str]] = None) -> dict:
    d = _to_dict(obj)
    result = {
        "soul_id": d.get("soul_id"),
        "title": d.get("title"),
        "artist_name": d.get("artist_name"),
        "year": d.get("year"),
        "release_date": d.get("release_date"),
        "track_count": d.get("track_count"),
        "record_type": d.get("record_type"),
        "label": d.get("label"),
        "genres": _parse_genres(d.get("genres")),
        "updated_at": _isoformat(d.get("updated_at")),
        "canonical_source": d.get("canonical_source"),
        "canonical_album_id": d.get("canonical_album_id"),
        "canonical_score": d.get("canonical_score"),
    }
    result.update(_metasync_ids(d, _METASYNC_ALBUM_IDS))
    return filter_fields(result, fields)


def serialize_metasync_track(obj, fields: Optional[Set[str]] = None) -> dict:
    d = _to_dict(obj)
    result = {
        "soul_id": d.get("soul_id"),
        "album_soul_id": d.get("album_soul_id"),
        "title": d.get("title"),
        "artist_name": d.get("artist_name"),
        "album_title": d.get("album_title"),
        "track_number": d.get("track_number"),
        "disc_number": d.get("disc_number"),
        "duration": d.get("duration"),
        "bpm": d.get("bpm"),
        "explicit": _bool_or_none(d.get("explicit")),
        "year": d.get("year"),
        "isrc": d.get("isrc"),
        "updated_at": _isoformat(d.get("updated_at")),
    }
    result.update(_metasync_ids(d, _METASYNC_TRACK_IDS))
    return filter_fields(result, fields)
