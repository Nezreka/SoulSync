"""Read queries + status computation for the Library v2 API layer."""

from __future__ import annotations

from core.library2 import queries as Q
from core.library2.metadata_overrides import clear_field_override, set_field_override
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
    assert file_status(
        {"path": "/x.flac", "file_state": "missing_confirmed"}, None
    ) == "missing"


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


def test_artist_index_and_detail_query_counts_do_not_scale_with_rows(imported_conn):
    def select_count(call):
        statements = []
        imported_conn.set_trace_callback(statements.append)
        try:
            call()
        finally:
            imported_conn.set_trace_callback(None)
        return sum(
            statement.lstrip().upper().startswith(("SELECT", "WITH"))
            for statement in statements
        )

    drake_id = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()[0]
    index_before = select_count(lambda: Q.list_artists(imported_conn, limit=500))
    detail_before = select_count(lambda: Q.get_artist(imported_conn, drake_id))

    for number in range(20):
        artist_id = imported_conn.execute(
            "INSERT INTO lib2_artists(name) VALUES(?)",
            (f"Scale Artist {number}",),
        ).lastrowid
        album_id = imported_conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, ?)",
            (drake_id, f"Scale Album {number}"),
        ).lastrowid
        imported_conn.execute(
            "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?, ?)",
            (album_id, drake_id),
        )
        imported_conn.execute(
            "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?, ?)",
            (album_id, artist_id),
        )

    index_after = select_count(lambda: Q.list_artists(imported_conn, limit=500))
    detail_after = select_count(lambda: Q.get_artist(imported_conn, drake_id))

    assert index_before == index_after
    assert detail_before == detail_after


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


def test_confirmed_missing_file_is_not_counted_as_present(imported_conn):
    album_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    imported_conn.execute(
        """UPDATE lib2_track_files SET file_state='missing_confirmed'
            WHERE track_id=(
                SELECT id FROM lib2_tracks
                 WHERE album_id=? AND title='One Dance'
            )""",
        (album_id,),
    )

    album = Q.get_album(imported_conn, album_id)

    assert album["tracks_present"] == 0
    assert album["tracks_missing"] == 2
    one_dance = next(track for track in album["tracks"] if track["title"] == "One Dance")
    assert one_dance["file_status"] == "missing"
    assert one_dance["file"]["file_state"] == "missing_confirmed"
    assert one_dance["meets_profile"] is None


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


def test_get_album_preserves_unknown_present_file_quality(imported_conn):
    track_id = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=100"
    ).fetchone()[0]
    album_id = imported_conn.execute(
        "SELECT album_id FROM lib2_tracks WHERE id=?", (track_id,)
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_track_files SET format='unknown' WHERE track_id=?",
        (track_id,),
    )

    album = Q.get_album(imported_conn, album_id)
    track = next(item for item in album["tracks"] if item["id"] == track_id)

    assert track["file_status"] == "present"
    assert track["meets_profile"] is None
    assert track["upgrade_candidate"] is None


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


def test_effective_reads_project_user_metadata_without_rewriting_provider(imported_conn):
    artist_id = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()[0]
    album_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    track_id = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE title='One Dance' AND album_id=?",
        (album_id,),
    ).fetchone()[0]
    for entity_type, entity_id, field, value in (
        ("artist", artist_id, "name", "Drake Corrected"),
        ("artist", artist_id, "genres", ["Hip-Hop"]),
        ("release_group", album_id, "title", "Views Corrected"),
        ("release_group", album_id, "album_type", "ep"),
        ("release_group", album_id, "year", 2024),
        ("release_group", album_id, "genres", ["Rap"]),
        ("track", track_id, "title", "One Dance Corrected"),
        ("track", track_id, "track_number", 7),
        ("track", track_id, "duration", 123),
    ):
        set_field_override(
            imported_conn,
            entity_type=entity_type,
            entity_id=entity_id,
            field_name=field,
            value=value,
        )

    artists, _total = Q.list_artists(imported_conn)
    corrected = next(row for row in artists if row["id"] == artist_id)
    assert corrected["name"] == "Drake Corrected"
    assert corrected["genres"] == ["Hip-Hop"]
    assert corrected["user_overrides"]["name"] == "Drake Corrected"

    artist = Q.get_artist(imported_conn, artist_id)
    assert artist["name"] == "Drake Corrected"
    assert [row["title"] for row in artist["eps"]] == ["Views Corrected"]
    assert artist["eps"][0]["user_overrides"]["album_type"] == "ep"

    album = Q.get_album(imported_conn, album_id)
    assert (album["title"], album["album_type"], album["year"]) == (
        "Views Corrected", "ep", 2024,
    )
    assert album["genres"] == ["Rap"]
    assert album["primary_artist"]["name"] == "Drake Corrected"
    track = next(row for row in album["tracks"] if row["id"] == track_id)
    assert (track["title"], track["track_number"], track["duration"]) == (
        "One Dance Corrected", 7, 123,
    )
    assert [row["name"] for row in track["artists"]] == [
        "Drake Corrected", "Wizkid",
    ]

    direct = Q.get_track(imported_conn, track_id)
    assert direct["title"] == "One Dance Corrected"
    assert direct["album"]["title"] == "Views Corrected"

    # A provider refresh changes only its baseline; user intent keeps winning.
    imported_conn.execute(
        "UPDATE lib2_albums SET title='Provider Refresh' WHERE id=?", (album_id,)
    )
    assert Q.get_album(imported_conn, album_id)["title"] == "Views Corrected"
    clear_field_override(
        imported_conn,
        entity_type="release_group",
        entity_id=album_id,
        field_name="title",
    )
    assert Q.get_album(imported_conn, album_id)["title"] == "Provider Refresh"


def test_track_reads_expose_effective_wanted_projection(imported_conn):
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule
    from core.library2.wanted import recompute_wanted

    track_id = imported_conn.execute(
        "SELECT id FROM lib2_tracks ORDER BY id LIMIT 1"
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_tracks SET monitored=0 WHERE id=?", (track_id,)
    )
    album_id = imported_conn.execute(
        "SELECT album_id FROM lib2_tracks WHERE id=?", (track_id,)
    ).fetchone()[0]
    record_rule(imported_conn, "track", track_id, True, PROVENANCE_USER)
    recompute_wanted(imported_conn, track_ids=[track_id])

    assert Q.get_track(imported_conn, track_id)["monitored"] is True
    album_track = next(
        row for row in Q.get_album(imported_conn, album_id)["tracks"]
        if row["id"] == track_id
    )
    assert album_track["monitored"] is True
