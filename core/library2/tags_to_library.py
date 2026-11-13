"""Use the files' own tags as the Library's truth (#2070).

Everything else here writes one way: the Library's metadata into the file
(retag.py). This is the other direction, on request, for the person who fixed
their tags by hand outside SoulSync and wants the Library to say what the
files say.

What it takes from a file:

- the track's title, track number and disc number,
- the album's year, release date and genre,
- which album the track is on.

Fields are saved as manual edits (metadata_overrides), the same store the Edit
dialogs write, so enrichment does not put the old value back and Write Tags
shows them as hand-set.

A track whose tag names another album is MOVED: its file link goes to that
album under the same album artist, created when the Library has no such
album. When every file of an album names the same other title, that is a
rename of the album, not a move of each track. Nothing on disk is touched;
Reorganize refiles from the corrected Library afterwards.

Artist names are left alone. A track or album artist that differs is reported
in the plan and nothing is done about it: re-crediting an artist changes whose
page the music sits on and wants its own tool.

``plan`` reads and decides, ``apply`` does what a plan says. The plan is the
preview the page shows, so what is shown is what happens.
"""

from __future__ import annotations

import re
from collections import Counter
from contextlib import closing
from typing import Any, Dict, Iterable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.tags_to_library")

#: tracks one request may plan or apply (the retag preview's ceiling too)
MAX_TRACKS = 1000

_TRACK_FIELDS = (
    # (plan field, file tag key, db_data key, label)
    ("title", "title", "title", "Title"),
    ("track_number", "track_number", "track_number", "Track #"),
    ("disc_number", "disc_number", "disc_number", "Disc #"),
)


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _same_text(a: Any, b: Any) -> bool:
    return _text(a).casefold() == _text(b).casefold()


def _number(value: Any) -> Optional[int]:
    """A track or disc number out of a tag: "3", "03", "3/12" are all 3."""
    match = re.match(r"\s*(\d+)", str(value if value is not None else ""))
    return int(match.group(1)) if match else None


def _year(value: Any) -> Optional[int]:
    match = re.match(r"\s*(\d{4})", str(value if value is not None else ""))
    if not match:
        return None
    year = int(match.group(1))
    return year if 1000 <= year <= 9999 else None


def _release_date(value: Any) -> Optional[str]:
    """A full date when the tag carries one ("2005-04-12"), else None."""
    match = re.match(r"\s*(\d{4}-\d{2}-\d{2})", str(value if value is not None else ""))
    return match.group(1) if match else None


def _genres(value: Any) -> List[str]:
    if isinstance(value, (list, tuple)):
        parts = [_text(v) for v in value]
    else:
        parts = [_text(v) for v in re.split(r"[;/\x00]|,\s", str(value or ""))]
    seen, out = set(), []
    for part in parts:
        if part and part.casefold() not in seen:
            seen.add(part.casefold())
            out.append(part)
    return out


def _most_common(values: Iterable[Any]) -> Any:
    """What most of the files say. A tie goes to the value seen first."""
    counted = Counter(v for v in values if v not in (None, "", ()))
    return counted.most_common(1)[0][0] if counted else None


def _read(context: Dict[str, Any], read_tags) -> Optional[Dict[str, Any]]:
    from core.library2.paths import resolve_lib2_path
    path = resolve_lib2_path(context.get("file_path") or "")
    if not path:
        return None
    try:
        tags = read_tags(path)
    except Exception as exc:  # noqa: BLE001 - one unreadable file is its own row's problem
        logger.debug("tags_to_library: could not read %s: %s", path, exc)
        return None
    if not isinstance(tags, dict) or tags.get("error"):
        return None
    return tags


def _files_per_album(conn, album_ids: Iterable[int]) -> Dict[int, int]:
    """How many tracks of each album have a file. A rename needs all of them."""
    ids = sorted({int(a) for a in album_ids if a})
    out: Dict[int, int] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" for _ in chunk)
        for row in conn.execute(
                "SELECT t.album_id, COUNT(DISTINCT t.id) FROM lib2_tracks t "
                "JOIN lib2_track_files f ON f.track_id = t.id "
                "AND COALESCE(f.file_state,'active') <> 'deleted' "
                f"WHERE t.album_id IN ({marks}) GROUP BY t.album_id", chunk):
            out[int(row[0])] = int(row[1])
    return out


def plan(database, track_ids: List[int], *, read_tags=None) -> Dict[str, Any]:
    """What using the file tags would change, without changing anything.

    Returns ``{"tracks": [...], "albums": [...], "counts": {...}}``. A track
    entry carries its field ``changes`` and, when its tag names another album,
    a ``move``. An album entry carries the album-wide ``changes`` (year,
    release date, genre) and a ``rename`` when every file agrees on a new
    title. ``unreadable`` tracks have a file whose tags could not be read.
    """
    from core.library2 import retag
    if read_tags is None:
        from core.tag_writer import read_file_tags as read_tags

    ids = [int(t) for t in track_ids][:MAX_TRACKS]
    with closing(database._get_connection()) as conn:
        contexts = [c for c in retag.track_contexts(conn, ids, lyrics=False) if c.get("file_path")]
        with_files = _files_per_album(conn, [c.get("album_id") for c in contexts])

    by_album: Dict[int, List[Dict[str, Any]]] = {}
    tracks: List[Dict[str, Any]] = []
    for context in contexts:
        data = context.get("db_data") or {}
        entry: Dict[str, Any] = {
            "track_id": int(context["id"]),
            "title": data.get("title") or context.get("title"),
            "album_id": context.get("album_id"),
            "album_title": data.get("album_title") or context.get("album_title"),
            "file_path": context.get("file_path"),
            "changes": [], "move": None, "notes": [],
            "_album_now": {key: data.get(key) for key in ("year", "release_date", "genres")},
            "_album_artist_id": context.get("album_artist_id"),
        }
        tags = _read(context, read_tags)
        if tags is None:
            entry["unreadable"] = True
            tracks.append(entry)
            continue
        entry["_tags"] = tags
        for field, tag_key, data_key, label in _TRACK_FIELDS:
            if field == "title":
                wanted, current = _text(tags.get(tag_key)), _text(data.get(data_key))
                differs = bool(wanted) and wanted != current
            else:
                wanted, current = _number(tags.get(tag_key)), _number(data.get(data_key))
                differs = wanted is not None and wanted != current
            if differs:
                entry["changes"].append(
                    {"field": field, "label": label, "library": current, "file": wanted})
        # reported, never applied: see the module docstring
        for tag_key, data_key, label in (("artist", "track_artist", "artist"),
                                         ("album_artist", "artist_name", "album artist")):
            said = _text(tags.get(tag_key))
            have = _text(data.get(data_key) or data.get("artist_name"))
            if said and have and not _same_text(said, have):
                entry["notes"].append(f"The file's {label} is {said}; the Library keeps {have}.")
        tracks.append(entry)
        by_album.setdefault(int(context["album_id"]), []).append(entry)

    albums: List[Dict[str, Any]] = []
    for album_id, entries in by_album.items():
        current_title = _text(entries[0]["album_title"])
        tagged = [_text(e["_tags"].get("album")) for e in entries]
        elsewhere = [bool(t) and not _same_text(t, current_title) for t in tagged]
        album: Dict[str, Any] = {"album_id": album_id, "title": current_title,
                                 "changes": [], "rename": None}
        whole_album = len(entries) >= with_files.get(album_id, len(entries))
        renamed = (whole_album and all(elsewhere)
                   and len({t.casefold() for t in tagged}) == 1)
        if renamed:
            album["rename"] = tagged[0]
        else:
            for entry, title, moves in zip(entries, tagged, elsewhere, strict=True):
                if moves:
                    entry["move"] = {"album_title": title}
        staying = [e for e in entries if not e["move"]]
        album["changes"] = _album_changes(staying, entries[0])
        if album["rename"] or album["changes"]:
            albums.append(album)

    # what a moved track's tags say about the album it is going to
    for title_key, movers in _group_moves(tracks).items():
        first = movers[0]
        changes = _album_changes(movers, first, into_other_album=True)
        for mover in movers:
            mover["move"]["album_changes"] = changes
        logger.debug("tags_to_library: %d track(s) to %r", len(movers), title_key)

    for entry in tracks:
        for private in ("_tags", "_album_now", "_album_artist_id"):
            entry.pop(private, None)
    return {
        "tracks": tracks,
        "albums": albums,
        "counts": {
            "tracks": len(tracks),
            "changed_tracks": sum(1 for t in tracks if t["changes"]),
            "moves": sum(1 for t in tracks if t["move"]),
            "albums": len(albums),
            "unreadable": sum(1 for t in tracks if t.get("unreadable")),
        },
        "truncated": len(track_ids) > MAX_TRACKS,
    }


def _group_moves(tracks: List[Dict[str, Any]]) -> Dict[tuple, List[Dict[str, Any]]]:
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for entry in tracks:
        if entry.get("move"):
            key = (entry["album_id"], _text(entry["move"]["album_title"]).casefold())
            groups.setdefault(key, []).append(entry)
    return groups


def _album_changes(entries: List[Dict[str, Any]], sample: Dict[str, Any],
                   *, into_other_album: bool = False) -> List[Dict[str, Any]]:
    """The album-wide values most of these files agree on, where they differ
    from the Library's. For tracks moving to another album there is no current
    value to compare with: the tags' values are simply what that album gets."""
    if not entries:
        return []
    tags = [e["_tags"] for e in entries]
    year = _most_common(_year(t.get("year")) for t in tags)
    date = _most_common(_release_date(t.get("year") or t.get("date")) for t in tags)
    genres = _most_common(tuple(_genres(t.get("genre"))) for t in tags)
    current = {} if into_other_album else (sample.get("_album_now") or {})
    out = []
    if year is not None and year != _year(current.get("year")):
        out.append({"field": "year", "label": "Year", "library": _year(current.get("year")), "file": year})
    if date and date != _text(current.get("release_date"))[:10]:
        out.append({"field": "release_date", "label": "Release date",
                    "library": _text(current.get("release_date"))[:10] or None, "file": date})
    if genres and [g.casefold() for g in genres] != [
            g.casefold() for g in _genres(current.get("genres"))]:
        out.append({"field": "genres", "label": "Genre",
                    "library": _genres(current.get("genres")), "file": list(genres)})
    return out


def _set(conn, entity_type: str, entity_id: int, change: Dict[str, Any], profile_id: int) -> None:
    from core.library2.metadata_overrides import set_field_override
    set_field_override(conn, entity_type=entity_type, entity_id=int(entity_id),
                       field_name=change["field"], value=change["file"],
                       profile_id=profile_id, reason="file tags")


def apply(database, track_ids: List[int], *, profile_id: int = 1, read_tags=None) -> Dict[str, Any]:
    """Do what ``plan`` says for these tracks. Returns what was done.

    The plan is worked out again here rather than taken from the caller: a
    posted plan could name any album or value, and the tags may have changed
    since the preview was drawn.
    """
    from core.library2.autolink import _find_or_create_album, _find_or_create_track
    from core.library2.track_file_move import MoveError, move_track_file

    the_plan = plan(database, track_ids, read_tags=read_tags)
    done = {"tracks_changed": 0, "albums_changed": 0, "albums_renamed": 0,
            "moved": 0, "albums_created": 0, "failed": []}

    with closing(database._get_connection()) as conn:
        for album in the_plan["albums"]:
            if album["rename"]:
                _set(conn, "release_group", album["album_id"],
                     {"field": "title", "file": album["rename"]}, profile_id)
                done["albums_renamed"] += 1
            for change in album["changes"]:
                _set(conn, "release_group", album["album_id"], change, profile_id)
            if album["changes"]:
                done["albums_changed"] += 1
        for entry in the_plan["tracks"]:
            if entry["move"] or not entry["changes"]:
                continue
            for change in entry["changes"]:
                _set(conn, "track", entry["track_id"], change, profile_id)
            done["tracks_changed"] += 1
        conn.commit()

        for entry in the_plan["tracks"]:
            move = entry["move"]
            if not move:
                continue
            try:
                source = conn.execute(
                    "SELECT t.title, t.track_number, t.disc_number, a.primary_artist_id, a.album_type "
                    "FROM lib2_tracks t JOIN lib2_albums a ON a.id = t.album_id WHERE t.id=?",
                    (entry["track_id"],)).fetchone()
                if not source or not source["primary_artist_id"]:
                    raise MoveError("The track's album has no artist to file the new album under")
                wanted = {c["field"]: c["file"] for c in entry["changes"]}
                before = conn.execute("SELECT COALESCE(MAX(id), 0) FROM lib2_albums").fetchone()[0]
                album_id = _find_or_create_album(
                    conn, int(source["primary_artist_id"]), move["album_title"],
                    album_type=source["album_type"] or "album")
                target = _find_or_create_track(
                    conn, album_id, int(source["primary_artist_id"]),
                    wanted.get("title") or source["title"],
                    track_number=wanted.get("track_number", source["track_number"]),
                    disc_number=wanted.get("disc_number", source["disc_number"]))
                conn.commit()
                if int(target) != int(entry["track_id"]):
                    move_track_file(database, conn, int(entry["track_id"]), int(target),
                                    wishlist_profile_id=profile_id)
                for change in entry["changes"]:
                    _set(conn, "track", target, change, profile_id)
                for change in move.get("album_changes") or []:
                    _set(conn, "release_group", album_id, change, profile_id)
                conn.commit()
                done["moved"] += 1
                if int(album_id) > int(before):
                    done["albums_created"] += 1
            except Exception as exc:  # noqa: BLE001 - one track's move must not stop the rest
                conn.rollback()
                logger.warning("tags_to_library: could not move track %s to %r: %s",
                               entry["track_id"], move["album_title"], exc)
                done["failed"].append({"track_id": entry["track_id"], "title": entry["title"],
                                       "error": str(exc)})
    done["unreadable"] = the_plan["counts"]["unreadable"]
    logger.info("Use file tags: %s", {k: v for k, v in done.items() if k != "failed"})
    return done
