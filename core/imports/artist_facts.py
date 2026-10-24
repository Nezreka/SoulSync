"""What the library knows about an artist, for naming templates (#1859).

two artists can share a name (nirvana the us band, nirvana the uk band) and
land in one folder. musicbrainz tells them apart with a disambiguation note,
a country and tags. this reads those from what enrichment already stored (the
artists row and the cached musicbrainz artist payload). it never calls out to
musicbrainz: a path is built for every imported track, and an artist that
isn't enriched yet simply has no facts until it is.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Optional

from utils.logging_config import get_logger

logger = get_logger("imports.artist_facts")

_EMPTY = {"disambiguation": "", "country": "", "genre": "", "clash": False}
_CACHE_TTL = 30.0
_cache: dict = {}
_cache_lock = threading.Lock()


def _get_db():
    from database.music_database import get_database
    return get_database()


def _resolve_mbid(cursor, name: str, mbid: str, source: str, source_id: str) -> str:
    """the artist's musicbrainz id: given, else via the source's artist id,
    else by name when the name belongs to only one musicbrainz artist."""
    if mbid:
        # a compilation's context can name a track artist while carrying the
        # album artist's id: only trust the id when it is this name's
        cursor.execute("SELECT name FROM artists WHERE musicbrainz_id = ? LIMIT 1", (mbid,))
        row = cursor.fetchone()
        if not row or str(row[0] or "").casefold() == name.casefold():
            return mbid
    if source and source_id:
        from core.source_ids import id_column
        column = id_column(source, "artist")
        if column:
            try:
                cursor.execute(
                    f"SELECT name, musicbrainz_id FROM artists WHERE {column} = ? "  # noqa: S608 - column from a fixed map
                    "AND musicbrainz_id IS NOT NULL AND musicbrainz_id != '' LIMIT 1",
                    (str(source_id),))
                row = cursor.fetchone()
                # an id that points at someone else isn't this artist
                if row and str(row[0] or "").casefold() == name.casefold():
                    return str(row[1])
            except Exception as e:  # noqa: BLE001 - an old schema just means no id route
                logger.debug("artist id lookup failed: %s", e)
    ids = _mbids_named(cursor, name)
    return ids[0] if len(ids) == 1 else ""


def _mbids_named(cursor, name: str) -> list:
    cursor.execute(
        "SELECT DISTINCT musicbrainz_id FROM artists WHERE name = ? COLLATE NOCASE "
        "AND musicbrainz_id IS NOT NULL AND musicbrainz_id != ''", (name,))
    return [str(r[0]) for r in cursor.fetchall()]


def _first_genre(cursor, mbid: str, payload: dict) -> str:
    try:
        cursor.execute(
            "SELECT genres FROM artists WHERE musicbrainz_id = ? AND genres IS NOT NULL "
            "AND genres != '' LIMIT 1", (mbid,))
        row = cursor.fetchone()
        genres = json.loads(row[0]) if row and row[0] else []
        if isinstance(genres, list):
            for genre in genres:
                if isinstance(genre, str) and genre.strip():
                    return genre.strip().title()
    except Exception as e:  # noqa: BLE001 - a malformed genres column falls through to tags
        logger.debug("artist genres unreadable: %s", e)
    tags = [t for t in (payload.get("tags") or []) if isinstance(t, dict) and t.get("name")]
    tags.sort(key=lambda t: (-int(t.get("count") or 0), t["name"]))
    return tags[0]["name"].strip().title() if tags else ""


def _country(payload: dict) -> str:
    country = str(payload.get("country") or "").strip().upper()
    if len(country) == 2:
        return country
    area = payload.get("area") if isinstance(payload.get("area"), dict) else {}
    codes = area.get("iso-3166-1-codes") or []
    return str(codes[0]).upper() if codes else ""


def _lookup(name: str, mbid: str, source: str, source_id: str) -> dict:
    db = _get_db()
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        mbid = _resolve_mbid(cursor, name, mbid, source, source_id)
        if not mbid:
            return dict(_EMPTY)
        payload = {}
        cursor.execute(
            "SELECT metadata_json FROM musicbrainz_cache WHERE entity_type = 'artist' "
            "AND musicbrainz_id = ? AND metadata_json IS NOT NULL LIMIT 1", (mbid,))
        row = cursor.fetchone()
        if row and row[0]:
            try:
                loaded = json.loads(row[0])
                payload = loaded if isinstance(loaded, dict) else {}
            except (TypeError, ValueError):
                payload = {}
        others = [m for m in _mbids_named(cursor, name) if m != mbid]
        return {
            "disambiguation": str(payload.get("disambiguation") or "").strip(),
            "country": _country(payload),
            "genre": _first_genre(cursor, mbid, payload),
            "clash": bool(others),
        }
    finally:
        conn.close()


def artist_facts(name, *, mbid=None, source=None, source_id=None) -> dict:
    """``{disambiguation, country, genre, clash}`` for an artist, empty when
    the library doesn't know. ``clash`` is True when another musicbrainz
    artist in the library has the same name."""
    name = str(name or "").strip()
    if not name:
        return dict(_EMPTY)
    key = (name.casefold(), str(mbid or ""), str(source or ""), str(source_id or ""))
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL:
            return dict(hit[1])
    try:
        facts = _lookup(name, str(mbid or ""), str(source or ""), str(source_id or ""))
    except Exception as e:  # noqa: BLE001 - naming must never fail over a lookup
        logger.debug("artist facts lookup failed for %r: %s", name, e)
        facts = dict(_EMPTY)
    with _cache_lock:
        if len(_cache) > 2048:
            _cache.clear()
        _cache[key] = (now, facts)
    return dict(facts)


def info_chain(facts: dict) -> str:
    """the first thing that tells this artist apart: the disambiguation
    note, else the country, else the genre."""
    return facts.get("disambiguation") or facts.get("country") or facts.get("genre") or ""


def composite_chain(facts: dict) -> str:
    """the disambiguation note, else country and genre together ("US Rock"),
    else whichever of the two exists (QT3496's addon to #1859)."""
    if facts.get("disambiguation"):
        return facts["disambiguation"]
    return " ".join(part for part in (facts.get("country"), facts.get("genre")) if part)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


__all__ = ["artist_facts", "info_chain", "composite_chain", "clear_cache"]
