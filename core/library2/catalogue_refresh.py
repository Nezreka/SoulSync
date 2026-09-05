"""Provider → catalogue: the half of Re-tag that Library v2 was missing.

The legacy job pulled a fresh tracklist from an album's matched source and
wrote it into the files. Its lib2 replacement only ever went the other way —
catalogue → file tags — so nothing on this branch could get a corrected title
INTO the catalogue at all. A manual match set an id and stopped there:
``enrich_native_entity_for_service`` writes provider ids and artwork, never
titles or track numbers. Which is why "match the right release, then
reorganize" changed nothing: the catalogue still held the old values, and
both the page and the path read the catalogue.

Three steps, each of them visible:

    Manual match   → identity
    Refresh        → provider → catalogue     (this module)
    Re-tag         → catalogue → file tags

The rules are the tag side's rules, because they are the same rules. A field a
person set by hand is reported, never silently replaced. An empty provider
value is not a proposal — a blank must not erase what the library has. And the
preview only computes; nothing is written until it is applied.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("library2.catalogue_refresh")

#: Below this, two titles are different songs rather than one spelled two ways.
#: Only consulted when the disc/track position did not already answer.
_TITLE_THRESHOLD = 0.6

#: Catalogue fields this module proposes, and the override field that guards
#: each one. Track numbers are in here because a source correcting a tracklist
#: is the ordinary reason a library's numbering is wrong.
_TRACK_FIELDS = ("title", "track_number", "disc_number")
_ALBUM_FIELDS = ("title", "release_date", "year")


def _get(obj: Any, *keys: str, default=None):
    for key in keys:
        value = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
        if value not in (None, ""):
            return value
    return default


def _int_or_none(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _norm_title(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[\(\[].*?[\)\]]", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _year_of(value: Any) -> Optional[int]:
    match = re.search(r"\d{4}", str(value or ""))
    return int(match.group(0)) if match else None


def album_source(conn, album_id: int) -> Tuple[Optional[str], Optional[str]]:
    """The provider this album is matched to, in configured priority order.

    Returns ``(source, external_id)`` or ``(None, None)``. Priority matters:
    an album can carry ids from several providers, and refreshing from a
    different one each run would make the catalogue oscillate.
    """
    row = conn.execute(
        "SELECT spotify_id, musicbrainz_id, external_ids FROM lib2_albums WHERE id=?",
        (int(album_id),),
    ).fetchone()
    if row is None:
        return None, None
    from core.library2.provider_ids import source_ids_from_values

    ids = source_ids_from_values(
        spotify_id=row["spotify_id"],
        musicbrainz_id=row["musicbrainz_id"],
        external_ids=row["external_ids"],
    )
    try:
        from core.metadata_service import get_primary_source, get_source_priority
        order = list(get_source_priority(get_primary_source()))
    except Exception:  # noqa: BLE001 - a refresh is not the place to fail on config
        order = []
    for source in order + ["spotify", "musicbrainz", "deezer", "itunes"]:
        value = ids.get(source)
        if value:
            return source, str(value)
    return None, None


def _effective(conn, entity_type: str, entity_id: Any,
               fields: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    from core.library2.metadata_overrides import project_metadata

    try:
        return project_metadata(conn, entity_type=entity_type,
                                entity_id=int(entity_id), provider_fields=fields)
    except Exception as exc:  # noqa: BLE001
        logger.debug("override projection skipped for %s %s: %s",
                     entity_type, entity_id, exc)
        return dict(fields), {}


def _match(source_tracks: List[Any],
           library_tracks: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], Any]]:
    """Pair each catalogue track with a source track, or None.

    Disc+track position is authoritative and title similarity is the fallback;
    a source track is consumed once so two library tracks cannot both claim it.
    Unmatched library tracks come back as ``None`` rather than being dropped —
    "the source does not have this track" is information, not an absence.
    """
    by_position: Dict[Tuple[int, int], int] = {}
    for index, track in enumerate(source_tracks):
        number = _int_or_none(_get(track, "track_number"))
        if number is None:
            continue
        disc = _int_or_none(_get(track, "disc_number", default=1)) or 1
        by_position.setdefault((disc, number), index)

    used: set = set()
    pairs: List[Tuple[Dict[str, Any], Any]] = []
    for track in library_tracks:
        number = _int_or_none(track.get("track_number"))
        disc = _int_or_none(track.get("disc_number")) or 1
        index = by_position.get((disc, number)) if number is not None else None
        if index is not None and index not in used:
            used.add(index)
            pairs.append((track, source_tracks[index]))
            continue
        wanted = _norm_title(track.get("title"))
        best_index, best_score = None, 0.0
        if wanted:
            for candidate, source_track in enumerate(source_tracks):
                if candidate in used:
                    continue
                score = SequenceMatcher(
                    None, wanted,
                    _norm_title(_get(source_track, "name", "title", "track_name")),
                ).ratio()
                if score > best_score:
                    best_score, best_index = score, candidate
        if best_index is not None and best_score >= _TITLE_THRESHOLD:
            used.add(best_index)
            pairs.append((track, source_tracks[best_index]))
        else:
            pairs.append((track, None))
    return pairs


def _change(field: str, current: Any, proposed: Any,
            overrides: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One proposal, or None when there is nothing to propose.

    An empty provider value is not a proposal: the library's value is not worse
    for the provider having lost it, and a blank would erase something real.
    """
    if proposed in (None, ""):
        return None
    if str(proposed) == str(current if current is not None else ""):
        return None
    return {
        "field": field,
        "current": current,
        "proposed": proposed,
        # Reported, not applied. Settling it is the user's call, exactly as it
        # is on the tag side.
        "manual": field in overrides,
    }


def refresh_preview(conn, album_id: int, *, source: Optional[str] = None) -> Dict[str, Any]:
    """What the matched provider would change in the catalogue. Writes nothing."""
    from core.metadata.album_tracks import get_album_for_source, get_album_tracks_for_source

    album_row = conn.execute(
        "SELECT id, title, year, release_date FROM lib2_albums WHERE id=?",
        (int(album_id),),
    ).fetchone()
    if album_row is None:
        return {"success": False, "status": "no_album", "source": None,
                "album": None, "tracks": [], "has_manual_conflict": False}

    resolved_source, external_id = album_source(conn, album_id)
    if source:
        resolved_source = source
    if not resolved_source or not external_id:
        # Its own outcome, not an empty diff: the UI can send the user to the
        # match dialog instead of showing "nothing to do" for a missing link.
        return {"success": False, "status": "no_source", "source": None,
                "album": None, "tracks": [], "has_manual_conflict": False}

    source_tracks = get_album_tracks_for_source(resolved_source, external_id) or []
    source_album = get_album_for_source(resolved_source, external_id) or {}

    album_fields = {"title": album_row["title"], "year": album_row["year"],
                    "release_date": album_row["release_date"]}
    album_effective, album_overrides = _effective(
        conn, "release_group", album_row["id"], album_fields)
    proposed_album = {
        "title": (_get(source_album, "name", "title", "album_name") or "").strip(),
        "release_date": _get(source_album, "release_date", "releaseDate", "date"),
        "year": _year_of(_get(source_album, "release_date", "releaseDate",
                              "date", "year")),
    }
    album_changes = [
        change for change in (
            _change(field, album_effective.get(field), proposed_album.get(field),
                    album_overrides)
            for field in _ALBUM_FIELDS
        ) if change
    ]

    rows = conn.execute(
        "SELECT id, title, track_number, disc_number FROM lib2_tracks "
        "WHERE album_id=? ORDER BY COALESCE(disc_number,1), track_number, id",
        (int(album_id),),
    ).fetchall()
    library_tracks = []
    override_by_track: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        effective, overrides = _effective(conn, "track", row["id"], {
            "id": row["id"], "title": row["title"],
            "track_number": row["track_number"], "disc_number": row["disc_number"],
        })
        override_by_track[int(row["id"])] = overrides
        library_tracks.append(effective)

    tracks: List[Dict[str, Any]] = []
    for track, source_track in _match(source_tracks, library_tracks):
        overrides = override_by_track.get(int(track["id"]), {})
        entry = {"track_id": track["id"], "title": track.get("title"),
                 "track_number": track.get("track_number"),
                 "matched": source_track is not None, "changes": []}
        if source_track is not None:
            proposed = {
                "title": (_get(source_track, "name", "title", "track_name") or "").strip(),
                "track_number": _int_or_none(_get(source_track, "track_number")),
                "disc_number": _int_or_none(_get(source_track, "disc_number", default=1)),
            }
            entry["changes"] = [
                change for change in (
                    _change(field, track.get(field), proposed.get(field), overrides)
                    for field in _TRACK_FIELDS
                ) if change
            ]
        tracks.append(entry)

    conflicts = any(c["manual"] for c in album_changes) or any(
        c["manual"] for t in tracks for c in t["changes"])
    return {
        "success": True,
        "status": "planned",
        "source": resolved_source,
        "album": {"album_id": album_row["id"], "changes": album_changes},
        "tracks": tracks,
        "has_manual_conflict": conflicts,
    }


def apply_refresh(conn, album_id: int, *, source: Optional[str] = None,
                  overwrite_manual: bool = False) -> Dict[str, Any]:
    """Write the preview's proposals into the catalogue.

    Does not commit — the caller owns the transaction, so a refresh and
    whatever it triggers land together or not at all.

    Releasing a hand-set field CLEARS its override rather than only writing the
    base row. Writing the base row alone would change nothing anyone can see:
    the override still wins on every read, and the user would be looking at
    their old value wondering why the refresh did nothing.
    """
    from core.library2.metadata_overrides import clear_field_override

    plan = refresh_preview(conn, album_id, source=source)
    stats = {"status": plan["status"], "source": plan.get("source"),
             "tracks_updated": 0, "album_updated": 0, "kept_manual": 0}
    if not plan.get("success"):
        return stats

    def _apply(entity_type: str, entity_id: int, table: str,
               changes: List[Dict[str, Any]]) -> bool:
        applied: Dict[str, Any] = {}
        for change in changes:
            if change["manual"] and not overwrite_manual:
                stats["kept_manual"] += 1
                continue
            if change["manual"]:
                try:
                    clear_field_override(conn, entity_type=entity_type,
                                         entity_id=int(entity_id),
                                         field_name=change["field"])
                except Exception as exc:  # noqa: BLE001
                    logger.debug("could not clear override %s on %s %s: %s",
                                 change["field"], entity_type, entity_id, exc)
                    continue
            applied[change["field"]] = change["proposed"]
        if not applied:
            return False
        assignments = ", ".join(f"{field}=?" for field in applied)
        conn.execute(
            f"UPDATE {table} SET {assignments}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (*applied.values(), int(entity_id)),
        )
        return True

    for track in plan["tracks"]:
        if track["changes"] and _apply("track", track["track_id"], "lib2_tracks",
                                       track["changes"]):
            stats["tracks_updated"] += 1
    album = plan.get("album") or {}
    if album.get("changes") and _apply("release_group", album["album_id"],
                                       "lib2_albums", album["changes"]):
        stats["album_updated"] = 1
    return stats


__all__ = ["album_source", "apply_refresh", "refresh_preview"]
