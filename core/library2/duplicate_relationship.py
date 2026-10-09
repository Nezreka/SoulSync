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


# A dash-form provenance tail names the release a recording was taken from,
# not the song: ``Rabbit Run - From "8 Mile" Soundtrack`` is ``Rabbit Run``
# (upstream #1315, the rule core/matching/audio_verification.py has long
# applied). Dash form after a space only: ``(from the vault)`` stays a version
# qualifier and ``Far-from Home`` stays a title.
_PROVENANCE_TAIL_RE = re.compile(r"\s+-\s*from\s+.+$", re.IGNORECASE)


def strip_provenance_tail(title: Any) -> str:
    """``title`` without a trailing `` - From …`` naming its source release."""
    return _PROVENANCE_TAIL_RE.sub("", str(title or ""))


def _normalized_title(value: Any) -> str:
    # A feat credit names who is on it, not which version (#1568): the same
    # rule the importer's single/album link uses. (Remix) etc. still count.
    from core.library2.importer import _FEAT_IN_TITLE_RE, _FEAT_TITLE_TAIL_RE

    text = strip_provenance_tail(value)
    text = _FEAT_TITLE_TAIL_RE.sub("", _FEAT_IN_TITLE_RE.sub("", text))
    text = unicodedata.normalize("NFKC", text).casefold()
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


# The release-independent recording identifiers. A Spotify track id is NOT
# one: a single and its album cut carry different Spotify ids for the same
# recording, which is why the automatic link leaves it out.
RECORDING_IDS = (
    ("isrc", "ISRC"),
    ("musicbrainz_id", "MusicBrainz recording ID"),
)


def durations_compatible(source_ms: Any, target_ms: Any) -> bool:
    """Within 3% (at least 5 s) of each other, or one side unknown."""
    if source_ms is None or target_ms is None:
        return True
    source_ms, target_ms = int(source_ms), int(target_ms)
    tolerance = max(5_000, round(max(source_ms, target_ms) * 0.03))
    return abs(source_ms - target_ms) <= tolerance


def conflicting_recording_id(source: Any, target: Any,
                             namespaces=RECORDING_IDS) -> str | None:
    """The label of the first identifier both sides carry and disagree on."""
    for column, label in namespaces:
        source_id = str(source[column] or "").strip()
        target_id = str(target[column] or "").strip()
        if source_id and target_id and source_id.casefold() != target_id.casefold():
            return label
    return None


def same_recording(source: Any, target: Any) -> bool:
    """Whether an automatic single↔album link may treat two rows as one
    recording: compatible durations and no conflicting ISRC/MBID
    (feature-parity A02). Title and artist are the caller's grouping."""
    return (durations_compatible(source["duration"], target["duration"])
            and conflicting_recording_id(source, target) is None)


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

    if not durations_compatible(source["duration"], target["duration"]):
        raise DuplicateRelationshipError("Track durations differ too much")

    conflict = conflicting_recording_id(
        source, target, (*RECORDING_IDS, ("spotify_id", "Spotify track ID")))
    if conflict:
        raise DuplicateRelationshipError(f"Tracks have conflicting {conflict}s")

    return {
        "source": dict(source),
        "target": dict(target),
        "reverse_existing": reverse_existing,
    }


__all__ = ["DuplicateRelationshipError", "conflicting_recording_id",
           "durations_compatible", "same_recording", "validate_duplicate_pair"]
