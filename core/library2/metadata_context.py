"""Purpose-explicit native metadata contexts, without writes or provider I/O.

Provider lookups use catalogue identity. Display and write values retain the
existing override/edition contract; track writes reuse Retag's projection.
These values are not write authorization: callers still recheck file ownership,
hand tags and protected fields at the existing writer boundary.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from .editions import album_release_ids
from .metadata_overrides import project_metadata
from .provider_ids import source_ids_from_values

_PURPOSES = frozenset({"display", "provider_lookup", "write"})


def validate_metadata_purpose(purpose: str) -> None:
    if purpose not in _PURPOSES:
        raise ValueError(f"Unknown metadata context purpose: {purpose!r}")


def _source_ids(row) -> Dict[str, str]:
    data = dict(row) if row is not None else {}
    return source_ids_from_values(spotify_id=data.get("spotify_id"),
                                  musicbrainz_id=data.get("musicbrainz_id"),
                                  external_ids=data.get("external_ids"),
                                  isrc=data.get("isrc"), upc=data.get("upc"))


def album_metadata_context(conn, album_id: int, *, purpose: str,
                           edition_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    validate_metadata_purpose(purpose)
    row = conn.execute("SELECT * FROM lib2_albums WHERE id=?", (int(album_id),)).fetchone()
    if row is None:
        return None
    album = dict(row)
    artist = conn.execute("SELECT * FROM lib2_artists WHERE id=?", (album["primary_artist_id"],)).fetchone()
    if edition_id is None:
        edition = conn.execute("SELECT * FROM lib2_release_editions WHERE release_group_id=? AND is_default=1",
                               (int(album_id),)).fetchone()
    else:
        edition = conn.execute("SELECT * FROM lib2_release_editions WHERE id=? AND release_group_id=?",
                               (int(edition_id), int(album_id))).fetchone()
        if edition is None:
            raise ValueError("Edition does not belong to this release group")
    provider = dict(album)
    # A concrete edition owns its identity; the group pin is only the fallback.
    album_ids = _source_ids(edition) if edition is not None else {}
    if edition is None or bool(edition["is_default"]):
        # The current group IDs and canonical pin define the default release.
        # A not-yet-synchronized default edition must neither hide newer native
        # IDs nor substitute a stale release for a user's pin.
        album_ids.update(album_release_ids(album))
    if edition is not None:
        for field in ("title", "release_date", "track_count"):
            if edition[field] is not None:
                provider[field] = edition[field]
    effective = dict(provider)
    manual = {}
    if purpose != "provider_lookup":
        if edition is not None:
            effective, overrides = project_metadata(conn, entity_type="release_edition",
                                                    entity_id=edition["id"], provider_fields=effective)
            manual.update({key: provider.get(key) for key in overrides})
        effective, overrides = project_metadata(conn, entity_type="release_group",
                                                entity_id=int(album_id), provider_fields=effective)
        manual.update({key: provider.get(key) for key in overrides})
    artist_data = dict(artist) if artist is not None else {}
    if artist and purpose != "provider_lookup":
        artist_data = project_metadata(conn, entity_type="artist", entity_id=artist["id"],
                                       provider_fields=artist_data)[0]
    name = artist_data.get("name") or ""
    result = dict(effective)
    result.update(album_id=int(album_id), album_title=effective["title"],
                  artist_id=album["primary_artist_id"], artist_name=name,
                  album_source_ids=album_ids, artist_source_ids=_source_ids(artist),
                  artist_metadata=artist_data,
                  edition_id=edition["id"] if edition else None,
                  canonical_locked=bool(album.get("canonical_locked")),
                  _manual_fields=manual, metadata_purpose=purpose)
    return result


def track_metadata_contexts(conn, track_ids: Iterable[int], *, purpose: str) -> Dict[int, Dict[str, Any]]:
    """Materialize native contexts once per track, including fileless tracks."""
    validate_metadata_purpose(purpose)
    from .retag import _track_rows, _db_data_for_row, MAX_TRACKS
    from .validation import edition_reference, edition_reference_rows

    ids = sorted({int(value) for value in track_ids})
    result = {}
    albums = {}
    for start in range(0, len(ids), MAX_TRACKS):
        batch = ids[start:start + MAX_TRACKS]
        marks = ','.join('?' for _ in batch)
        editions, bound = edition_reference_rows(conn, batch) if purpose == 'provider_lookup' else ({}, set())
        credits = {}
        for credit in conn.execute(
            "SELECT ta.track_id, ar.* FROM lib2_track_artists ta JOIN lib2_artists ar ON ar.id=ta.artist_id "
            f"WHERE ta.track_id IN ({marks}) ORDER BY CASE ta.role WHEN 'primary' THEN 0 ELSE 1 END,ta.position,ar.id", batch):
            credits.setdefault(int(credit['track_id']), credit)
        for row in _track_rows(conn, batch):
            tid = int(row["id"])
            if purpose == "provider_lookup":
                data = {"title": row["title"], "album_title": row["album_title"],
                        "duration": row['duration'], "track_number": row["track_number"],
                        "disc_number": row["disc_number"], "bpm": row["bpm"],
                        "year": row["year"], "release_date": row["release_date"]}
                reference = edition_reference(conn, tid, dict(data), rows=editions.get(tid, []),
                                              has_edition=tid in bound, lookup_only=True)
                # Lookup identity may use the concrete release's title, but its
                # write positions are a different contract. Native positions
                # remain useful even when release slots are absent or stale.
                for field in ("title", "album_title", "edition_id", "edition_status"):
                    if field in reference:
                        data[field] = reference[field]
                data["_manual_fields"] = {}
            else:
                data = _db_data_for_row(conn, row)
                data["duration"] = project_metadata(conn, entity_type="track", entity_id=tid,
                                                  provider_fields={"duration": row['duration']})[0]["duration"]
                if purpose == "display":
                    for field in ("track_number", "disc_number"):
                        if data.get(field) is None:
                            data[field] = row[field]
            key = (row["album_id"], data.get("edition_id"))
            if key not in albums:
                albums[key] = album_metadata_context(conn, row["album_id"], purpose=purpose,
                                                     edition_id=data.get("edition_id"))
            album = albums[key]
            credited = credits.get(tid)
            artist_data = dict(credited) if credited is not None else album["artist_metadata"]
            if credited and purpose != "provider_lookup":
                artist_data = project_metadata(conn, entity_type="artist", entity_id=credited["id"],
                                               provider_fields=artist_data)[0]
            name = artist_data.get("name") or ""
            # Retag uses artist_name for ALBUMARTIST and track_artist for ARTIST.
            # Preserve that existing write contract; lookup/display use the lead credit.
            if purpose != "write":
                data["artist_name"] = name
            data.update(track_id=tid, album_id=row["album_id"],
                        artist_id=credited["id"] if credited else album["artist_id"],
                        album_artist_id=album["artist_id"], album_artist_name=album["artist_name"],
                        track_source_ids=_source_ids(row), album_source_ids=album["album_source_ids"],
                        artist_source_ids=_source_ids(credited) if credited else album["artist_source_ids"],
                        album_artist_source_ids=album["artist_source_ids"],
                        album_metadata=album, artist_metadata=artist_data,
                        canonical_locked=album["canonical_locked"], metadata_purpose=purpose)
            result[tid] = data
    return result


def track_metadata_context(conn, track_id: int, *, purpose: str) -> Optional[Dict[str, Any]]:
    return track_metadata_contexts(conn, [track_id], purpose=purpose).get(int(track_id))


__all__ = ["album_metadata_context", "track_metadata_context", "track_metadata_contexts",
           "validate_metadata_purpose"]
