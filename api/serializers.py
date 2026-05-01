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


def serialize_watchlist_artist(obj, fields: Optional[Set[str]] = None) -> dict:
    """Full watchlist artist serialization — all columns including all content filters."""
    d = _to_dict(obj)
    result = {
        "id": d.get("id"),
        "spotify_artist_id": d.get("spotify_artist_id"),
        "itunes_artist_id": d.get("itunes_artist_id"),
        "artist_name": d.get("artist_name"),
        "image_url": d.get("image_url"),
        "date_added": _isoformat(d.get("date_added")),
        "last_scan_timestamp": _isoformat(d.get("last_scan_timestamp")),
        "created_at": _isoformat(d.get("created_at")),
        "updated_at": _isoformat(d.get("updated_at")),
        "profile_id": d.get("profile_id"),
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
