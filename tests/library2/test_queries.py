"""Read queries + status computation for the Library v2 API layer."""

from __future__ import annotations

from core.library2 import queries as Q
from core.library2.status import compute_metadata_gaps, file_status, quality_tier


# --- pure status helpers -----------------------------------------------------

def test_quality_tier():
    assert quality_tier("flac", None, 24) == "lossless_hi"
    assert quality_tier("flac", None, 16) == "lossless"
    assert quality_tier("mp3", 320, None) == "lossy_high"
    assert quality_tier("mp3", 128, None) == "lossy"
    assert quality_tier("mp3", 320000, None) == "lossy_high"   # bps normalized
    assert quality_tier(None, None, None) == "unknown"


def test_file_status():
    assert file_status(None, None) == "missing"
    assert file_status({"path": "/x.flac"}, None) == "present"
    assert file_status({"path": "/x.flac"}, 42) == "duplicate_single"


def test_metadata_gaps_uses_db_and_tags():
    track = {"title": "T", "track_number": 1, "disc_number": 1}
    # No file: the track is missing, not badly tagged. Retag gaps only make
    # sense for a physical file we can actually repair.
    gaps = compute_metadata_gaps(track, None, artist_count=1)
    assert gaps == []
    # A scanned missing-tag snapshot is authoritative when present.
    gaps2 = compute_metadata_gaps(track, {"missing_tags_json": '["cover"]'}, artist_count=1)
    assert gaps2 == ["cover"]


# --- query layer -------------------------------------------------------------

def test_list_artists_stats(imported_conn):
    artists, total = Q.list_artists(imported_conn)
    by_name = {a["name"]: a for a in artists}
    # Drake (legacy) + Wizkid (featured) both surface.
    assert total == 2
    drake = by_name["Drake"]
    assert drake["album_count"] == 1
    assert drake["single_count"] == 1
    assert drake["track_count"] == 3
    assert drake["tracks_present"] == 2
    assert drake["tracks_missing"] == 1
    # Wizkid shows the one track it's credited on (multi-artist via junction).
    assert by_name["Wizkid"]["track_count"] == 1


def test_get_artist_groups_albums_and_singles(imported_conn):
    drake_id = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()[0]
    data = Q.get_artist(imported_conn, drake_id)
    assert [a["title"] for a in data["albums"]] == ["Views"]
    assert [s["title"] for s in data["singles"]] == ["One Dance"]


def test_get_album_track_status(imported_conn):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'").fetchone()[0]
    album = Q.get_album(imported_conn, views_id)
    assert album["album_type"] == "album"
    assert album["track_count"] == 2
    assert album["tracks_present"] == 1
    assert album["tracks_missing"] == 1
    by_title = {t["title"]: t for t in album["tracks"]}
    one_dance = by_title["One Dance"]
    assert [a["name"] for a in one_dance["artists"]] == ["Drake", "Wizkid"]
    assert one_dance["file_status"] == "present"
    assert one_dance["file"]["quality_tier"] == "lossless"
    # The track with no file_path is reported missing.
    assert by_title["Hotline Bling"]["file_status"] == "missing"


def test_get_album_shows_expected_missing_track_rows(imported_conn):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'").fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_albums SET expected_track_count=4 WHERE id=?", (views_id,)
    )

    album = Q.get_album(imported_conn, views_id)

    assert album["track_count"] == 4
    assert album["tracks_missing"] == 3
    assert len(album["tracks"]) == 4
    missing_rows = [track for track in album["tracks"] if track["file_status"] == "missing"]
    assert [track["track_number"] for track in missing_rows] == [2, 3, 4]
    assert missing_rows[1]["title"] is None
    assert missing_rows[1]["metadata_gaps"] == []


def test_get_album_single_is_duplicate(imported_conn):
    single_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='One Dance'").fetchone()[0]
    album = Q.get_album(imported_conn, single_id)
    assert album["album_type"] == "single"
    assert album["tracks"][0]["file_status"] == "duplicate_single"


def test_get_track_detail(imported_conn):
    tid = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=100").fetchone()[0]
    track = Q.get_track(imported_conn, tid)
    assert track["album"]["title"] == "Views"
    assert [a["name"] for a in track["artists"]] == ["Drake", "Wizkid"]
