"""Shared validation for canonical links and single/album file moves.

Both commands mutate the same logical relationship, so they must use one
validator. This is deliberately validation only: canonical choice and file
movement remain with their existing command paths.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict


class DuplicateRelationshipError(ValueError):
    """A proposed duplicate relationship is missing or internally unsafe."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _normalized_title(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(part for part in re.split(r"\W+", text) if part)


def _artist_ids(conn, track_id: int, primary_artist_id: int) -> set[int]:
    ids = {int(primary_artist_id)}
    ids.update(
        int(row[0])
        for row in conn.execute(
            "SELECT artist_id FROM lib2_track_artists WHERE track_id=?",
            (track_id,),
        ).fetchall()
    )
    return ids


def validate_duplicate_pair(
    conn,
    from_track_id: int,
    to_track_id: int,
    *,
    allow_reverse_existing: bool = False,
) -> Dict[str, Any]:
    """Return source/target rows when a duplicate relationship is credible.

    Validation is intentionally conservative: the rows must share an artist,
    normalized title and compatible duration; hard recording identifiers may
    be absent, but when both sides have one in the same namespace they must
    agree. A canonical root cannot itself become a duplicate because that
    would create a chain and make file ownership ambiguous.
    """
    if int(from_track_id) == int(to_track_id):
        raise DuplicateRelationshipError("Source and target are the same track")

    def _track(track_id: int, label: str):
        row = conn.execute(
            """SELECT t.id, t.title, t.duration, t.isrc, t.musicbrainz_id,
                      t.spotify_id, t.canonical_track_id,
                      al.primary_artist_id
                 FROM lib2_tracks t
                 JOIN lib2_albums al ON al.id=t.album_id
                WHERE t.id=?""",
            (int(track_id),),
        ).fetchone()
        if not row:
            raise DuplicateRelationshipError(f"{label} track not found", status=404)
        return row

    source = _track(from_track_id, "Source")
    target = _track(to_track_id, "Target")
    reverse_existing = (
        allow_reverse_existing
        and target["canonical_track_id"] == int(from_track_id)
    )
    if target["canonical_track_id"] is not None and not reverse_existing:
        raise DuplicateRelationshipError(
            "Target is itself a duplicate — link to its canonical instead"
        )
    dependents = conn.execute(
        "SELECT id FROM lib2_tracks WHERE canonical_track_id=?",
        (int(from_track_id),),
    ).fetchall()
    if dependents and not (
        reverse_existing
        and {int(row[0]) for row in dependents} == {int(to_track_id)}
    ):
        raise DuplicateRelationshipError(
            "Source is already a canonical target and cannot become a duplicate"
        )

    source_artists = _artist_ids(
        conn, int(source["id"]), int(source["primary_artist_id"])
    )
    target_artists = _artist_ids(
        conn, int(target["id"]), int(target["primary_artist_id"])
    )
    if source_artists.isdisjoint(target_artists):
        raise DuplicateRelationshipError("Tracks do not share an artist")

    if _normalized_title(source["title"]) != _normalized_title(target["title"]):
        raise DuplicateRelationshipError("Track titles do not match")

    if source["duration"] is not None and target["duration"] is not None:
        source_duration = int(source["duration"])
        target_duration = int(target["duration"])
        tolerance = max(5_000, round(max(source_duration, target_duration) * 0.03))
        if abs(source_duration - target_duration) > tolerance:
            raise DuplicateRelationshipError("Track durations differ too much")

    for column, label in (
        ("isrc", "ISRC"),
        ("musicbrainz_id", "MusicBrainz recording ID"),
        ("spotify_id", "Spotify track ID"),
    ):
        source_id = str(source[column] or "").strip()
        target_id = str(target[column] or "").strip()
        if source_id and target_id and source_id.casefold() != target_id.casefold():
            raise DuplicateRelationshipError(f"Tracks have conflicting {label}s")

    return {
        "source": dict(source),
        "target": dict(target),
        "reverse_existing": reverse_existing,
    }


__all__ = ["DuplicateRelationshipError", "validate_duplicate_pair"]
