"""Same-named releases kept apart by their tagged year and folder (#2110).

Weezer has three albums called "Weezer" (1994, 2001, 2008). A file known only
by its tags (a media-server scan adopting it, a standalone refresh) carries no
provider or MusicBrainz release id, so the import writer matched it to the
first album of that name and all three landed on one row: three track 1s,
three track 2s.

The tags still tell them apart: each album sits in its own folder with its
own year. A same-titled row whose files all live in other folders under
another year is another release. Same folder, or the same year (a CD1/CD2
split), or no year to compare: still the same album, as before.

``find_split_plans`` / ``apply_split_plan`` heal rows merged before the fix.
The heal reads the tag snapshots (``tags_json``), which fill in and change as
files are scanned, so it runs on every start and after every scan: a row it
could not prove on one pass splits on a later one. A row matched to a release
of a year none of its folders carry (the 2016 "Weezer" over the 1994, 2001 and
2008 ones) hands that match back, and ``refuses_release`` keeps a provider
from putting one release on two of them again.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.release_split")

_YEAR = re.compile(r"\s*(\d{4})")


def tag_year(value: Any) -> Optional[str]:
    """The four digit year a tag or column holds ("1994-05-10" -> "1994")."""
    match = _YEAR.match(str(value or ""))
    if not match or match.group(1) == "0000":
        return None
    return match.group(1)


def folder_key(path: Any) -> str:
    text = str(path or "").replace("\\", "/").rstrip("/")
    return text.casefold()


_DISC_FOLDER = re.compile(r"^(cd|disc|disk)\s*[-._ ]*\d+\b", re.IGNORECASE)


def album_folder(folder: Any) -> str:
    """The folder a release lives in: a disc folder (CD1, Disc 2 - Bonus)
    counts as the album folder above it."""
    key = folder_key(folder)
    head, _, tail = key.rpartition("/")
    return head if head and _DISC_FOLDER.match(tail) else key


def same_album_folder(a: str, b: str) -> bool:
    """Two album folders that are one, or one inside the other (a deluxe
    whose bonus tracks sit in a subfolder)."""
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _title_key(value: Any) -> str:
    from core.library2.importer import release_title_key
    return release_title_key(str(value or ""))


def _tags(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _album_files(conn, album_id: int) -> List[Any]:
    return conn.execute(
        "SELECT t.id AS track_id, t.title, t.track_number, t.disc_number, f.path, f.tags_json"
        "  FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id = t.id"
        " WHERE t.album_id = ? AND COALESCE(f.file_state, 'active') = 'active'"
        "   AND TRIM(COALESCE(f.path, '')) <> ''",
        (int(album_id),),
    ).fetchall()


def is_other_release_by_year(conn, album_id: int, year: Any,
                             folder: Optional[str]) -> bool:
    """True when a same-titled row is another release than this file's.

    Every one of the row's files sits in another album folder, and none of
    them (or the row itself, when no file has a year) carries this file's
    year. A disc folder or a subfolder of the album's own is the same album.
    """
    year = tag_year(year)
    if not year or not folder:
        return False
    rows = _album_files(conn, album_id)
    if not rows:
        return False  # an empty row (a discography release) takes the file
    target = album_folder(folder)
    years = set()
    for row in rows:
        if same_album_folder(album_folder(os.path.dirname(str(row["path"]))), target):
            return False
        found = tag_year(_tags(row["tags_json"]).get("year"))
        if found:
            years.add(found)
    if not years:
        held = conn.execute("SELECT year FROM lib2_albums WHERE id=?",
                            (int(album_id),)).fetchone()
        found = tag_year(held[0]) if held else None
        if found:
            years.add(found)
    return bool(years) and year not in years


def _candidate_album_ids(conn, album_ids: Optional[Iterable[int]] = None) -> List[int]:
    """albums showing the merge: the same position held by two track rows.

    one pass over the album/position index (0.4 s on 320k tracks). asking
    here whether each track has a file took a minute on the same library;
    ``plan_for_album`` reads the files of the few albums this returns.
    """
    scope, params = "", []
    if album_ids is not None:
        ids = sorted({int(a) for a in album_ids if a})
        if not ids:
            return []
        scope = f" AND album_id IN ({','.join('?' for _ in ids)})"
        params = ids
    return [int(r[0]) for r in conn.execute(
        "SELECT DISTINCT album_id FROM ("
        "  SELECT album_id FROM lib2_tracks"
        f"   WHERE album_id IS NOT NULL AND track_number IS NOT NULL{scope}"
        "   GROUP BY album_id, COALESCE(disc_number, 1), track_number"
        "  HAVING COUNT(*) > 1)", params).fetchall()]


def _provider_ids(album: Any) -> Dict[str, str]:
    """every provider id an album row holds, by service."""
    ids: Dict[str, str] = {}
    for service, value in _tags(album["external_ids"]).items():
        if str(value or "").strip():
            ids[str(service).strip().lower()] = str(value).strip()
    for column, service in (("spotify_id", "spotify"), ("musicbrainz_id", "musicbrainz")):
        if str(album[column] or "").strip():
            ids.setdefault(service, str(album[column]).strip())
    return ids


def plan_for_album(conn, album_id: int) -> Optional[Dict[str, Any]]:
    """How one merged row splits, or None when it is one release.

    Proof, all of it or nothing happens:

    * every file names this album in its tags (an unread file waits for the
      scan that reads it, a single's file filed here is another problem);
    * every album folder is one tagged year. Disc folders and subfolders
      belong to the album folder above them, and folders of one year are one
      release, so a multi-disc album or a deluxe with a bonus folder stays;
    * no track has files in two of the releases;
    * each release holds a position (disc and track number) another one holds
      too. That collision is the merge; discs that fit together are one album;
    * the releases are different songs. A second copy of an album's own track
      filed elsewhere under another year is a duplicate, not another album.
    """
    album = conn.execute(
        "SELECT id, title, year, release_date, spotify_id, musicbrainz_id, external_ids"
        "  FROM lib2_albums WHERE id=?", (int(album_id),)).fetchone()
    if not album:
        return None
    title = _title_key(album["title"])
    if not title:
        return None
    folders: Dict[str, Dict[str, Any]] = {}
    track_folder: Dict[int, str] = {}
    for row in _album_files(conn, album_id):
        folder = album_folder(os.path.dirname(str(row["path"])))
        tags = _tags(row["tags_json"])
        if _title_key(tags.get("album")) != title:
            return None
        track_id = int(row["track_id"])
        if track_folder.setdefault(track_id, folder) != folder:
            return None
        group = folders.setdefault(folder, {"years": set(), "tracks": {}})
        group["years"].add(tag_year(tags.get("year")))
        group["tracks"][track_id] = (
            (int(row["disc_number"] or 1), row["track_number"]), _title_key(row["title"]))
    # a folder inside another of this album's folders is part of it
    for folder in sorted(folders, key=len, reverse=True):
        parent = next((other for other in folders
                       if other != folder and folder.startswith(other + "/")), None)
        if parent:
            inner = folders.pop(folder)
            folders[parent]["years"] |= inner["years"]
            folders[parent]["tracks"].update(inner["tracks"])
    releases: Dict[str, Dict[int, tuple]] = {}
    for group in folders.values():
        if len(group["years"]) != 1 or None in group["years"]:
            return None
        releases.setdefault(next(iter(group["years"])), {}).update(group["tracks"])
    if len(releases) < 2:
        return None
    years = sorted(releases)
    for year in years:
        positions = {slot for slot, _t in releases[year].values() if slot[1] is not None}
        titles = {t for _slot, t in releases[year].values() if t}
        collides = False
        for other in years:
            if other == year:
                continue
            other_titles = {t for _slot, t in releases[other].values() if t}
            if len(titles & other_titles) * 4 > min(len(titles), len(other_titles)):
                return None
            collides = collides or bool(positions & {
                slot for slot, _t in releases[other].values() if slot[1] is not None})
        if not collides:
            return None
    # the release the row's own match names keeps the row and its ids
    matched_year = tag_year(album["release_date"])
    held_year = matched_year or tag_year(album["year"])
    ordered = sorted(years, key=lambda y: (y != held_year, -len(releases[y]), y))
    keep = ordered[0]
    return {
        "album_id": int(album_id),
        "keep": {"year": keep},
        # matched to a year none of the folders carry: nobody's release
        "rematch": bool(matched_year and matched_year not in releases
                        and _provider_ids(album)),
        "split": [{"year": year, "track_ids": sorted(releases[year])} for year in ordered[1:]],
    }


def find_split_plans(conn, album_ids: Optional[Iterable[int]] = None) -> List[Dict[str, Any]]:
    """Read only: every merged row that splits cleanly."""
    plans = []
    for album_id in _candidate_album_ids(conn, album_ids):
        plan = plan_for_album(conn, album_id)
        if plan:
            plans.append(plan)
    return plans


def _hand_back_match(conn, album_id: int, year: str) -> None:
    """The row was matched to a release none of its files are: every provider
    id goes back to the queue, and the dates, art and tracklist that came with
    the match go with it. Does not commit."""
    from core.library2.match_status import set_library_v2_match
    album = conn.execute(
        "SELECT spotify_id, musicbrainz_id, external_ids FROM lib2_albums WHERE id=?",
        (int(album_id),)).fetchone()
    for service in _provider_ids(album):
        try:
            set_library_v2_match(conn, "album", int(album_id), service, None,
                                 actor="release_split", ask_again=True)
        except (ValueError, LookupError):
            # an id of a provider the match ledger does not know
            ids = _tags(conn.execute("SELECT external_ids FROM lib2_albums WHERE id=?",
                                     (int(album_id),)).fetchone()[0])
            ids.pop(service, None)
            conn.execute("UPDATE lib2_albums SET external_ids=? WHERE id=?",
                         (json.dumps(ids, sort_keys=True, separators=(",", ":")), int(album_id)))
    conn.execute(
        "UPDATE lib2_albums SET year=?, release_date=NULL, expected_track_count=NULL,"
        "       musicbrainz_release_group_id=NULL, upc=NULL,"
        "       image_url=CASE WHEN COALESCE(art_locked,0)=1 THEN image_url ELSE NULL END,"
        "       canonical_source=NULL, canonical_album_id=NULL, canonical_score=NULL,"
        "       canonical_locked=NULL, canonical_resolved_at=NULL, canonical_track_count=NULL,"
        "       tracklist_json=NULL, tracklist_status='idle', tracklist_error=NULL,"
        "       tracklist_retry_at=NULL, updated_at=CURRENT_TIMESTAMP"
        " WHERE id=?", (int(year), int(album_id)))
    try:
        from core.library2.editions import sync_default_edition
        sync_default_edition(conn.cursor(), int(album_id))
    except Exception as exc:  # noqa: BLE001 - a schema without editions
        logger.debug("default edition sync skipped for album %s: %s", album_id, exc)


def _claim_stub(conn, album_id: int, year: str) -> bool:
    """A release the provider already listed for this year (a fileless
    discography row of the same name) becomes this album instead of sitting
    beside it as "0 of 10": its ids, date and art come over. Only when exactly
    one such row exists. Does not commit."""
    from core.library2.dedup_repair import (
        _album_rows_for_artist, _bucket, _reviewed, _years_disagree)
    from core.library2.mb_reconcile import _fold_duplicate, _is_pristine
    row = conn.execute("SELECT primary_artist_id, title FROM lib2_albums WHERE id=?",
                       (int(album_id),)).fetchone()
    if not row or row["primary_artist_id"] is None:
        return False
    members = _album_rows_for_artist(conn, int(row["primary_artist_id"]))
    mine = next((m for m in members if int(m["id"]) == int(album_id)), None)
    if mine is None:
        return False
    cursor = conn.cursor()
    title = _title_key(row["title"])
    stubs = [m for m in members
             if int(m["id"]) != int(album_id) and _title_key(m["title"]) == title
             and _bucket(m["album_type"]) == _bucket(mine["album_type"])
             and tag_year(m.get("release_date") or m.get("year")) == year
             and _is_pristine(cursor, m)]
    if len(stubs) != 1:
        return False
    stub = stubs[0]
    mine = {**mine, "year": int(year), "release_date": None}
    listed = stub.get("expected_track_count")
    if (_years_disagree(mine, stub) or _reviewed(conn, "album", album_id, stub["id"])
            # more songs here than that release has: not it
            or (listed and int(mine.get("track_rows") or 0) > int(listed))):
        return False
    facts = conn.execute("SELECT release_date, image_url, expected_track_count,"
                         " musicbrainz_release_group_id FROM lib2_albums WHERE id=?",
                         (int(stub["id"]),)).fetchone()
    _fold_duplicate(cursor, mine, stub)
    cursor.execute(
        "UPDATE lib2_albums SET release_date=COALESCE(release_date, ?),"
        "       image_url=COALESCE(NULLIF(image_url, ''), ?),"
        "       expected_track_count=COALESCE(expected_track_count, ?),"
        "       musicbrainz_release_group_id=COALESCE(NULLIF(musicbrainz_release_group_id, ''), ?),"
        "       spotify_id=COALESCE(spotify_id, json_extract(external_ids, '$.spotify')),"
        "       updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (facts["release_date"], facts["image_url"], facts["expected_track_count"],
         facts["musicbrainz_release_group_id"], int(album_id)))
    return True


def apply_split_plan(conn, plan: Dict[str, Any]) -> List[int]:
    """Give each other release its own album row. Does not commit.

    The new row copies the artist, title, kind and profile. Its provider ids
    are left for enrichment (the merged row's ids can belong to one release at
    most), unless the provider already listed a release of that name and year.
    """
    from core.library2 import locks
    from core.library2.editions import prune_edition_rows_for_track
    source_id = int(plan["album_id"])
    if locks.blocks("album", source_id, "match", "Same-named release split", conn=conn):
        return []
    made = []
    cursor = conn.cursor()
    for part in plan["split"]:
        cursor.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, album_type, album_type_known,"
            "                        quality_profile_id, monitored, year, origin)"
            " SELECT primary_artist_id, title, album_type, album_type_known,"
            "        quality_profile_id, monitored, ?, 'library'"
            "   FROM lib2_albums WHERE id=?",
            (int(part["year"]), source_id))
        new_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role)"
            " SELECT ?, artist_id, role FROM lib2_album_artists WHERE album_id=?",
            (new_id, source_id))
        # what each profile chose for the merged row holds for every release
        # in it, or a profile's watched album comes back unwatched
        conn.execute(
            "INSERT OR IGNORE INTO lib2_monitor_rules(entity_type, entity_id, profile_id,"
            "                                        monitored, provenance)"
            " SELECT 'album', ?, profile_id, monitored, provenance FROM lib2_monitor_rules"
            "  WHERE entity_type='album' AND entity_id=?",
            (new_id, source_id))
        track_ids = [int(t) for t in part["track_ids"]]
        for track_id in track_ids:
            # its place in the merged row's edition is not its place any more
            prune_edition_rows_for_track(cursor, track_id)
        conn.execute(
            f"UPDATE lib2_tracks SET album_id=?, updated_at=CURRENT_TIMESTAMP"
            f" WHERE album_id=? AND id IN ({','.join('?' * len(track_ids))})",
            (new_id, source_id, *track_ids))
        made.append(new_id)
    if not made:
        return made
    keep_year = str(plan["keep"]["year"])
    if plan.get("rematch"):
        _hand_back_match(conn, source_id, keep_year)
    for album_id, year in [(source_id, keep_year)] + [
            (new_id, str(part["year"])) for new_id, part in zip(made, plan["split"], strict=True)]:
        if album_id != source_id or plan.get("rematch"):
            try:
                _claim_stub(conn, album_id, year)
            except Exception as exc:  # noqa: BLE001 - the split stands without it
                logger.debug("no provider release claimed for album %s: %s", album_id, exc)
        conn.execute(
            "UPDATE lib2_albums SET track_count=(SELECT COUNT(*) FROM lib2_tracks"
            " WHERE album_id=?), updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (album_id, album_id))
    logger.info("Album %s held %d releases of one name, split into %s%s",
                source_id, len(made) + 1, made,
                " (its match was another release's, handed back)" if plan.get("rematch") else "")
    return made


def split_same_named_releases(conn, album_ids: Optional[Iterable[int]] = None) -> Dict[str, int]:
    """Heal every merged row (or the ones in ``album_ids``). Does not commit.
    Safe to run again: a split row no longer shows the merge."""
    stats = {"albums_split": 0, "albums_made": 0}
    for plan in find_split_plans(conn, album_ids):
        made = apply_split_plan(conn, plan)
        if made:
            stats["albums_split"] += 1
            stats["albums_made"] += len(made)
    return stats


def split_same_named_after_scan(database: Any, album_ids: Optional[Iterable[int]] = None) -> int:
    """the heal as a scan's last step: a scan is what reads the tags the
    proof needs. best effort, a scan never fails on it. answers the albums made."""
    from contextlib import closing
    try:
        with closing(database._get_connection()) as conn:
            stats = split_same_named_releases(conn, album_ids)
            if stats["albums_made"]:
                conn.commit()
        if stats["albums_made"]:
            try:
                from core.library2.mirror_outbox import drain
                drain(database)
            except Exception as exc:  # noqa: BLE001
                logger.debug("post-split outbox drain failed: %s", exc)
        return stats["albums_made"]
    except Exception as exc:  # noqa: BLE001 - the scan itself is done
        logger.warning("same-named releases not split after the scan: %s", exc)
        return 0


# --- keeping a provider from putting one release on two of them --------------


def own_years(conn, album_id: int) -> set:
    """The years this album's files are tagged with (the row's own year when
    no file names one)."""
    years = {tag_year(_tags(row["tags_json"]).get("year")) for row in _album_files(conn, album_id)}
    years.discard(None)
    if not years:
        held = conn.execute("SELECT year FROM lib2_albums WHERE id=?", (int(album_id),)).fetchone()
        found = tag_year(held[0]) if held else None
        if found:
            years.add(found)
    return years


def same_named_siblings(conn, album_id: int) -> List[int]:
    """Other albums of the same artist and name that hold files of other
    years: proven other releases, not twins of this one."""
    row = conn.execute("SELECT primary_artist_id, title FROM lib2_albums WHERE id=?",
                       (int(album_id),)).fetchone()
    if not row or row["primary_artist_id"] is None:
        return []
    title = _title_key(row["title"])
    if not title:
        return []
    others = [int(r["id"]) for r in conn.execute(
        "SELECT id, title FROM lib2_albums WHERE primary_artist_id=? AND id<>?",
        (row["primary_artist_id"], int(album_id))) if _title_key(r["title"]) == title]
    if not others:
        return []
    mine = own_years(conn, album_id)
    if not mine or not _album_files(conn, album_id):
        return []
    return [other for other in others
            if _album_files(conn, other) and (theirs := own_years(conn, other))
            and not (mine & theirs)]


def refuses_release(conn, album_id: int, service: str, provider_id: Any, year: Any = None) -> bool:
    """True when an automatic match must not put this provider release on
    this album: the artist has another album of the same name and another
    year, and either that one already holds the id or the release's year is
    not the year this album's files carry. A name match can't tell weezer
    1994 from weezer 2016; left unmatched it can still be matched by hand."""
    siblings = same_named_siblings(conn, album_id)
    if not siblings:
        return False
    year = tag_year(year)
    if year and year not in own_years(conn, album_id):
        return True
    service = str(service or "").strip().lower()
    wanted = str(provider_id or "").strip()
    if not service or not wanted:
        return False
    marks = ",".join("?" for _ in siblings)
    for row in conn.execute(
            f"SELECT spotify_id, musicbrainz_id, external_ids FROM lib2_albums WHERE id IN ({marks})",
            siblings):
        if _provider_ids(row).get(service) == wanted:
            return True
    return False
