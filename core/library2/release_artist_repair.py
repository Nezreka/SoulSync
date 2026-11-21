"""Releases filed under a track's lead artist instead of their album artist (#2142).

Up to 3.6.8 a download that didn't start from a Library v2 row filed its
release under the track's first artist, while the import tagged and foldered
it under the album artist. Track 8 of 16BL's dj mix made a one-track
"Anjunadeep Open Air Prague" of Rampa's, a PhaseOne single fetched for 12th
Planet became PhaseOne's, and a 61-track compilation became one album per
performer. Each of them was then a release artist with a card on the Artists
list.

This puts each such release back under the artist its files name as album
artist: onto that artist's release of the same name when there is one, else
the row itself moves. The performer keeps the track credit, so they stay in
search and on the track rows, and the release is under Appears On on their
page.

Only when everything agrees: every file of the album that names an album
artist names the same one, it is not the filed artist under another
spelling or with company ("A & B" stays A's), the filed artist is a lead
credit on the album's tracks (the shape the old filing left), and nobody
locked the album.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.release_artist_repair")

#: file reads one pass may make for albums without a tag snapshot
MAX_FILE_READS = 5000


def _tag_album_artist(tags_json: Any) -> str:
    try:
        tags = json.loads(tags_json) if tags_json else None
    except (TypeError, ValueError):
        return ""
    if not isinstance(tags, dict):
        return ""
    value = tags.get("albumartist")
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


def _read_file_album_artist(path: str) -> str:
    from core.imports.track_number import read_embedded_album_artist
    return str(read_embedded_album_artist(path) or "").strip()


def _locked(conn: Any, album_id: int) -> bool:
    try:
        return conn.execute("SELECT 1 FROM lib2_entity_locks WHERE entity_type='album'"
                            " AND entity_id=? LIMIT 1", (album_id,)).fetchone() is not None
    except Exception:  # noqa: BLE001 - no locks table, nothing is locked
        return False


def _leads_a_track(conn: Any, album_id: int, group: List[int]) -> bool:
    marks = ",".join("?" for _ in group)
    return conn.execute(
        f"SELECT 1 FROM lib2_tracks t JOIN lib2_track_artists ta ON ta.track_id=t.id"
        f" WHERE t.album_id=? AND ta.artist_id IN ({marks}) AND ta.role='primary' LIMIT 1",
        (album_id, *group)).fetchone() is not None


def _same_named_release(conn: Any, album: Any, group: List[int]) -> Optional[int]:
    """the target artist's one release of this name, or None (none, or two:
    which one is a guess)"""
    from core.library2.importer import release_title_key
    marks = ",".join("?" for _ in group)
    key = release_title_key(album["title"])
    found = [row for row in conn.execute(
        f"SELECT id, title, spotify_id FROM lib2_albums WHERE primary_artist_id IN ({marks})",
        tuple(group)) if release_title_key(row["title"]) == key]
    if len(found) != 1:
        return None
    other = found[0]
    if album["spotify_id"] and other["spotify_id"] and album["spotify_id"] != other["spotify_id"]:
        return None  # same name, another release
    return int(other["id"])


def _rehome(conn: Any, album: Any, old_group: List[int], target_id: int,
            old_name: str) -> str:
    """move one album to ``target_id``; answers 'merged' or 'moved'."""
    from core.library2.artist_aliases import resolve_alias_group
    album_id = int(album["id"])
    target_group = resolve_alias_group(conn, target_id) or [target_id]
    # the performer stays on the tracks they lead, by name where the row
    # had none (the player and the track rows read it)
    marks = ",".join("?" for _ in old_group)
    conn.execute(
        f"UPDATE lib2_tracks SET track_artist=? WHERE album_id=?"
        f" AND COALESCE(track_artist,'')=''"
        f" AND id IN (SELECT track_id FROM lib2_track_artists WHERE artist_id IN ({marks})"
        f"             AND role='primary')", (old_name, album_id, *old_group))
    from core.library2.release_kind import settle_shape_kind
    keep = _same_named_release(conn, album, target_group)
    if keep is not None and keep != album_id:
        from core.library2.album_versions import merge_album_into
        merge_album_into(conn, keep, album_id)
        settle_shape_kind(conn, keep)
        return "merged"
    # the kind its folder took, when no source ever said one (#2240)
    settle_shape_kind(conn, album_id)
    conn.execute("UPDATE lib2_albums SET primary_artist_id=?, updated_at=CURRENT_TIMESTAMP"
                 " WHERE id=?", (target_id, album_id))
    conn.execute(f"UPDATE lib2_album_artists SET role='featured' WHERE album_id=?"
                 f" AND artist_id IN ({marks})", (album_id, *old_group))
    conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES(?,?,'primary')"
                 " ON CONFLICT(album_id, artist_id) DO UPDATE SET role='primary'",
                 (album_id, target_id))
    return "moved"


def rehome_track_lead_releases(
    conn: Any, *, read_album_artist: Callable[[str], str] = _read_file_album_artist,
    max_file_reads: int = MAX_FILE_READS,
) -> Dict[str, int]:
    """put releases filed under a track's lead back under their tagged album
    artist. does not commit."""
    from core.imports.collab_artist import listed_primary_of
    from core.library2.artist_aliases import resolve_alias_group
    from core.library2.artist_merges import _alias_names, _artist_folder, _name_key, _same_act
    from core.library2.autolink import _find_or_create_artist

    stats = {"albums_checked": 0, "albums_moved": 0, "albums_merged": 0, "file_reads": 0}
    said: Dict[int, set] = defaultdict(set)
    untagged: Dict[int, str] = {}
    albums: Dict[int, Any] = {}
    for row in conn.execute(
            """SELECT al.id, al.title, al.spotify_id, al.primary_artist_id,
                      ar.name AS artist_name, ar.aliases, f.path, f.tags_json
                 FROM lib2_track_files f
                 JOIN lib2_tracks t ON t.id = f.track_id
                 JOIN lib2_albums al ON al.id = t.album_id
                 JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                WHERE f.path IS NOT NULL AND TRIM(f.path) <> ''
                  AND COALESCE(f.file_state, 'active') = 'active'"""):
        album_id = int(row["id"])
        albums.setdefault(album_id, row)
        tagged = _tag_album_artist(row["tags_json"])
        if tagged:
            said[album_id].add(tagged)
        elif _name_key(_artist_folder(row["path"])) not in ("", _name_key(row["artist_name"])):
            # no snapshot yet, and the folder names someone else: the file says
            untagged.setdefault(album_id, row["path"])

    for album_id, path in untagged.items():
        if said.get(album_id) or stats["file_reads"] >= max_file_reads:
            continue
        stats["file_reads"] += 1
        tagged = read_album_artist(path)
        if tagged:
            said[album_id].add(tagged)

    moved = set()
    for album_id, names in said.items():
        album = albums[album_id]
        artist_name = str(album["artist_name"] or "")
        aliases = _alias_names(album["aliases"])
        if any(_same_act(artist_name, name, aliases) or listed_primary_of(name, artist_name)
               for name in names):
            continue  # the filed artist, as written somewhere
        if len({_name_key(name) for name in names}) != 1:
            continue  # the files don't agree whose release it is
        # #2240: a tag naming the whole credit ('all artists combined')
        # is its first artist's release, never an artist of its own
        target_name = sorted(names)[0]
        target_name = _credit_lead(conn, target_name, album_id, artist_name) or target_name
        if _same_act(artist_name, target_name, aliases):
            continue  # the filed artist leads the credit
        stats["albums_checked"] += 1
        old_group = resolve_alias_group(conn, int(album["primary_artist_id"])) or [
            int(album["primary_artist_id"])]
        if _locked(conn, album_id) or not _leads_a_track(conn, album_id, old_group):
            continue
        target_id = _find_or_create_artist(conn, target_name)
        if target_id is None or int(target_id) in old_group:
            continue
        # one album at a time: a merge that fails half way leaves nothing
        conn.execute("SAVEPOINT rehome_release")
        try:
            outcome = _rehome(conn, album, old_group, int(target_id), artist_name)
        except Exception as exc:  # noqa: BLE001 - this album stays where it is
            conn.execute("ROLLBACK TO rehome_release")
            conn.execute("RELEASE rehome_release")
            logger.warning("#2142: %r not moved to %s: %s", album["title"], sorted(names)[0], exc)
            continue
        conn.execute("RELEASE rehome_release")
        moved.add(album_id)
        stats["albums_merged" if outcome == "merged" else "albums_moved"] += 1
        logger.info("#2142: %r was filed under %s, its files name %s (%s)",
                    album["title"], artist_name, target_name, outcome)

    for album_id, album in albums.items():
        if album_id not in moved:
            _rehome_whole_credit(conn, album, said.get(album_id) or set(), stats)
    return stats


def _library_has(conn: Any) -> Callable[[str], bool]:
    def has(name: str) -> bool:
        return conn.execute("SELECT 1 FROM lib2_artists WHERE LOWER(name)=LOWER(?) LIMIT 1",
                            (name,)).fetchone() is not None
    return has


def _credit_lead(conn: Any, credit: str, album_id: int, *others: str) -> str:
    """the first artist of ``credit`` when it is a whole credit line: the
    album's credited artists and the library's own prove it a list. ''
    when it is one artist's name (Earth, Wind & Fire)."""
    from core.imports.collab_artist import credit_lead_artist
    credited = [row[0] for row in conn.execute(
        "SELECT DISTINCT ar.name FROM lib2_tracks t"
        "  JOIN lib2_track_artists ta ON ta.track_id = t.id"
        "  JOIN lib2_artists ar ON ar.id = ta.artist_id WHERE t.album_id = ?",
        (int(album_id),)) if row[0]]
    return credit_lead_artist(credit, [*credited, *others], _library_has(conn))


def _rehome_whole_credit(conn: Any, album: Any, tagged: set, stats: Dict[str, int]) -> None:
    """#2240: a release filed under an artist named after its whole credit
    ("12th Planet, BARELY ALIVE & PhaseOne") goes to the credit's first
    artist, the one its folder names under 'first listed'. only when the
    credit is provably a list, the files name that credit or its first
    artist (or nothing), and nobody locked the album."""
    from core.imports.collab_artist import credit_segments
    from core.library2.artist_aliases import resolve_alias_group
    from core.library2.artist_merges import _name_key
    from core.library2.autolink import _find_or_create_artist

    artist_name = str(album["artist_name"] or "")
    if len(credit_segments(artist_name)) < 2:
        return
    album_id = int(album["id"])
    lead = _credit_lead(conn, artist_name, album_id)
    if not lead or _name_key(lead) == _name_key(artist_name):
        return
    if any(_name_key(name) not in (_name_key(artist_name), _name_key(lead)) for name in tagged):
        return  # the files name someone else: the pass above decides those
    if _locked(conn, album_id):
        return
    old_group = resolve_alias_group(conn, int(album["primary_artist_id"])) or [
        int(album["primary_artist_id"])]
    target_id = _find_or_create_artist(conn, lead)
    if target_id is None or int(target_id) in old_group:
        return
    stats["albums_checked"] += 1
    conn.execute("SAVEPOINT rehome_release")
    try:
        outcome = _rehome(conn, album, old_group, int(target_id), artist_name)
    except Exception as exc:  # noqa: BLE001 - this album stays where it is
        conn.execute("ROLLBACK TO rehome_release")
        conn.execute("RELEASE rehome_release")
        logger.warning("#2240: %r not moved to %s: %s", album["title"], lead, exc)
        return
    conn.execute("RELEASE rehome_release")
    stats["albums_merged" if outcome == "merged" else "albums_moved"] += 1
    logger.info("#2240: %r was filed under the whole credit %s, now %s's (%s)",
                album["title"], artist_name, lead, outcome)


__all__ = ["rehome_track_lead_releases"]
