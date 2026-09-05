"""Read queries + status computation for the Library v2 API layer."""

from __future__ import annotations

import json

from core.library2 import queries as Q
from core.library2.metadata_overrides import clear_field_override, set_field_override
from core.library2.status import (
    compute_metadata_gaps,
    file_status,
    metadata_scan_status,
    quality_tier,
)


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
    assert file_status(
        {"path": "/x.flac", "file_state": "missing_suspected"}, None
    ) == "missing_suspected"


def test_metadata_gaps_uses_db_and_tags():
    # No file: the track is missing, not badly tagged. Retag gaps only make
    # sense for a physical file we can actually repair.
    gaps = compute_metadata_gaps(None)
    assert gaps == []
    # A scanned missing-tag snapshot is authoritative when present.
    gaps2 = compute_metadata_gaps({"missing_tags_json": '["cover"]'})
    assert gaps2 == ["cover"]


def test_metadata_gaps_never_scanned_file_is_not_reported_gap_free():
    """LV2-TAG-STATUS-01: a file whose tags_json/missing_tags_json are still
    the untouched schema defaults ('{}' / '[]') has never actually been read
    by the canonical tag reader — it must not be reported as having zero
    gaps (a false "tags ✓"), even though the raw missing_tags_json decodes
    to an empty list indistinguishable-by-value from a real scan result."""
    never_scanned = {"path": "/m/01.flac", "tags_json": "{}", "missing_tags_json": "[]"}
    assert compute_metadata_gaps(never_scanned) == []
    assert metadata_scan_status(never_scanned) == "pending"


def test_metadata_gaps_unreadable_file_does_not_fall_back_to_db_cover():
    """A failed read persists tags_json='{}' + missing_tags_json='null' (the
    explicit unknown sentinel). This must surface as 'unreadable', not fall
    back to DB fields like a provider image_url standing in for an embedded
    cover that was never confirmed to actually be in the file."""
    unreadable = {"path": "/m/01.flac", "tags_json": "{}", "missing_tags_json": "null"}
    assert compute_metadata_gaps(unreadable) == []
    assert metadata_scan_status(unreadable) == "unreadable"


def test_metadata_gaps_real_scan_with_zero_gaps_is_scanned():
    scanned = {
        "path": "/m/01.flac",
        "tags_json": '{"title": "T", "artist": "A", "album": "Al", '
        '"albumartist": "A", "track_number": 1, "disc_number": 1, '
        '"year": 2020, "genre": "Pop", "cover": true}',
        "missing_tags_json": "[]",
    }
    assert compute_metadata_gaps(scanned) == []
    assert metadata_scan_status(scanned) == "scanned"


def test_metadata_scan_status_no_file_is_pending():
    assert metadata_scan_status(None) == "pending"
    assert metadata_scan_status({"path": None}) == "pending"


# --- query layer -------------------------------------------------------------

def test_list_artists_stats(imported_conn):
    artists, total = Q.list_artists(imported_conn)
    by_name = {a["name"]: a for a in artists}
    # Drake (legacy) + Wizkid (featured) both surface.
    assert total == 2
    drake = by_name["Drake"]
    assert drake["album_count"] == 1
    assert drake["single_count"] == 1
    # §16.2: 'Views' is only partially owned (Hotline Bling has no file), so the
    # album is no longer blanket-monitored and its missing track is not wanted —
    # the index stat therefore counts only owned-or-wanted tracks (both present
    # 'One Dance' cuts), not the un-wanted missing one.
    assert drake["track_count"] == 2
    assert drake["tracks_present"] == 2
    assert drake["tracks_missing"] == 0
    # Wizkid shows the one track it's credited on (multi-artist via junction).
    assert by_name["Wizkid"]["track_count"] == 1
    assert by_name["Wizkid"]["album_count"] == 1
    # I8: disk-space roll-up — both of Drake's present files (5000 bytes each
    # per the legacy seed), Hotline Bling has no file so contributes nothing.
    assert drake["total_size_bytes"] == 10000


def test_queries_project_media_server_recognition_badges(imported_conn):
    artist = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()[0]
    track = imported_conn.execute(
        "SELECT t.id,t.album_id FROM lib2_tracks t "
        "JOIN lib2_track_artists ta ON ta.track_id=t.id WHERE ta.artist_id=? LIMIT 1",
        (artist,),
    ).fetchone()
    imported_conn.execute(
        "INSERT INTO lib2_media_server_mappings"
        "(entity_type,entity_id,server_source,server_id) VALUES"
        "('artist',?,'plex','artist-p'),('artist',?,'jellyfin','artist-j'),"
        "('track',?,'plex','track-p')",
        (artist, artist, track[0]),
    )

    artists, _total = Q.list_artists(imported_conn)
    drake = next(row for row in artists if row["id"] == artist)
    album = Q.get_album(imported_conn, track[1])
    projected_track = next(row for row in album["tracks"] if row["id"] == track[0])

    assert drake["media_server_sources"] == ["jellyfin", "plex"]
    assert projected_track["media_server_sources"] == ["plex"]


def test_list_artists_materializes_requested_page_before_catalog_rollups(
    imported_conn,
):
    statements = []
    imported_conn.set_trace_callback(statements.append)
    try:
        artists, total = Q.list_artists(imported_conn, limit=1)
    finally:
        imported_conn.set_trace_callback(None)

    rollup_sql = next(
        statement for statement in statements
        if "page_artists AS MATERIALIZED" in statement
    )
    assert len(artists) == 1
    assert total == 2
    assert rollup_sql.index("page_artists AS MATERIALIZED") < rollup_sql.index(
        "artist_albums AS"
    )
    # Every roll-up must be reachable only through the page-scoped CTEs. The
    # check used to count `JOIN page_artists` occurrences, which stopped being
    # the mechanism: `canonical_members` is itself page-scoped (its WHERE
    # restricts to artists on the page), and the roll-ups now CROSS JOIN from
    # it so SQLite cannot reorder the junction table to the front (PERF-06).
    # What matters is the property, so assert the PLAN: no full scan of the
    # big junction/file tables.
    plan = "\n".join(
        row[3] for row in imported_conn.execute(
            "EXPLAIN QUERY PLAN " + rollup_sql,
            {"limit": 1, "offset": 0},
        )
    )
    for table in ("lib2_track_artists", "lib2_album_artists", "lib2_track_files"):
        assert f"SCAN {table}" not in plan, (
            f"{table} is being scanned in full:\n{plan}"
        )


def test_list_artists_aggregate_sorts_choose_page_before_rollups(imported_conn):
    by_tracks, _ = Q.list_artists(imported_conn, sort="tracks", limit=1)
    assert [artist["name"] for artist in by_tracks] == ["Drake"]

    artist_id = imported_conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES('Many Albums', 'Many Albums')"
    ).lastrowid
    for number in range(2):
        album_id = imported_conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, ?)",
            (artist_id, f"Album {number}"),
        ).lastrowid
        imported_conn.execute(
            "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?, ?)",
            (album_id, artist_id),
        )

    by_albums, _ = Q.list_artists(imported_conn, sort="albums", limit=1)
    assert [artist["name"] for artist in by_albums] == ["Many Albums"]
    assert by_albums[0]["album_count"] == 2


def test_size_rollup_counts_each_track_once_despite_historical_file_rows(imported_conn):
    """I8: a track can accumulate multiple lib2_track_files rows over its
    history (replaced/lossy-copy/deleted). The disk-space roll-up must count
    each track's current primary file exactly once — never fan out with the
    unrelated files_present/track_count joins, never count deleted rows."""
    conn = imported_conn
    track_id = conn.execute(
        "SELECT id FROM lib2_tracks WHERE title='One Dance' AND album_id="
        "(SELECT id FROM lib2_albums WHERE title='Views')"
    ).fetchone()["id"]
    album_id = conn.execute(
        "SELECT album_id FROM lib2_tracks WHERE id=?", (track_id,)
    ).fetchone()["album_id"]
    artist_id = conn.execute(
        "SELECT primary_artist_id FROM lib2_albums WHERE id=?", (album_id,)
    ).fetchone()["primary_artist_id"]

    before_album = Q.get_album(conn, album_id)["total_size_bytes"]
    before_artists = {a["id"]: a["total_size_bytes"] for a in Q.list_artists(conn)[0]}

    # A stale, deleted historical file row for the SAME track — must be
    # excluded entirely, not summed alongside the live one.
    conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, size, file_state) "
        "VALUES(?, '/old/one-dance.flac', 999999, 'deleted')", (track_id,))
    conn.commit()

    after_album = Q.get_album(conn, album_id)["total_size_bytes"]
    after_artists = {a["id"]: a["total_size_bytes"] for a in Q.list_artists(conn)[0]}

    assert after_album == before_album
    assert after_artists[artist_id] == before_artists[artist_id]

    # get_artist's per-album totals must add up to the same artist-wide total,
    # and match get_album/list_artists exactly (one source of truth).
    artist_detail = Q.get_artist(conn, artist_id)
    assert artist_detail["total_size_bytes"] == after_artists[artist_id]
    matching_album = next(a for a in artist_detail["albums"] if a["id"] == album_id)
    assert matching_album["total_size_bytes"] == after_album


def test_list_artist_track_files_scopes_paginates_and_excludes_deleted(imported_conn):
    """C2 (Manage Track Files): a flat, paginated per-artist file list that
    mirrors ADR-05's own artist scope (primary_artist_id, non-deleted)."""
    artist_id = imported_conn.execute(
        "INSERT INTO lib2_artists(name) VALUES('File List Artist')"
    ).lastrowid
    album_id = imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'File List Album')",
        (artist_id,),
    ).lastrowid
    imported_conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?, ?)",
        (album_id, artist_id),
    )
    track_ids = []
    for n in range(1, 4):
        track_id = imported_conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?, ?, ?)",
            (album_id, f"Track {n}", n),
        ).lastrowid
        track_ids.append(track_id)
        imported_conn.execute(
            "INSERT INTO lib2_track_files(track_id, path, size, format, quality_tier, "
            "file_state, is_primary) VALUES(?, ?, ?, 'flac', 'lossless', 'active', 1)",
            (track_id, f"/music/track{n}.flac", 1000 * n),
        )
    # A soft-deleted file must never surface in the list.
    imported_conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, size, format, quality_tier, file_state) "
        "VALUES(?, '/music/gone.flac', 1, 'flac', 'lossless', 'deleted')",
        (track_ids[0],),
    )
    imported_conn.commit()

    all_files, total = Q.list_artist_track_files(imported_conn, artist_id, limit=500)
    assert total == 3
    assert {f["path"] for f in all_files} == {
        "/music/track1.flac", "/music/track2.flac", "/music/track3.flac",
    }

    page1, total_page1 = Q.list_artist_track_files(imported_conn, artist_id, page=1, limit=2)
    page2, total_page2 = Q.list_artist_track_files(imported_conn, artist_id, page=2, limit=2)
    assert total_page1 == total_page2 == 3
    assert len(page1) == 2
    assert len(page2) == 1
    assert {f["file_id"] for f in page1} | {f["file_id"] for f in page2} == \
        {f["file_id"] for f in all_files}

    filtered, filtered_total = Q.list_artist_track_files(
        imported_conn, artist_id, search="Track 2")
    assert filtered_total == 1
    assert filtered[0]["track_title"] == "Track 2"


def test_play_queue_follows_credits_and_names_each_track_artist(imported_conn):
    """The artist Play button asks a different question than the Files tab.

    ``list_artist_track_files`` is scoped to albums whose PRIMARY artist is this
    one, deliberately, so a Manage-Track-Files selection matches what the delete
    preview sees. The artist PAGE shows releases reached through track credits
    too, so a play queue built from the Files-tab scope silently omits exactly
    those songs — and labels the rest with the page's artist."""
    guest_id = imported_conn.execute(
        "INSERT INTO lib2_artists(name) VALUES('Guest Artist')").lastrowid
    host_id = imported_conn.execute(
        "INSERT INTO lib2_artists(name) VALUES('Host Artist')").lastrowid

    own_album = imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Their Own Record')",
        (guest_id,)).lastrowid
    own_track = imported_conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, duration) "
        "VALUES(?, 'Own Song', 1, 200000)", (own_album,)).lastrowid
    imported_conn.execute(
        "INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
        "VALUES(?, ?, 'primary', 0)", (own_track, guest_id))
    imported_conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, format, file_state, is_primary) "
        "VALUES(?, '/music/own.flac', 'flac', 'active', 1)", (own_track,))

    # A record by someone else that this artist is only credited ON.
    other_album = imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, image_url) "
        "VALUES(?, 'Somebody Else Record', '/art/else.jpg')", (host_id,)).lastrowid
    feature = imported_conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number) "
        "VALUES(?, 'The Feature', 1)", (other_album,)).lastrowid
    imported_conn.execute(
        "INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
        "VALUES(?, ?, 'primary', 0)", (feature, host_id))
    imported_conn.execute(
        "INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
        "VALUES(?, ?, 'featured', 1)", (feature, guest_id))
    imported_conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, format, file_state, is_primary) "
        "VALUES(?, '/music/feature.flac', 'flac', 'active', 1)", (feature,))
    imported_conn.commit()

    files, total = Q.list_artist_playback_files(imported_conn, guest_id, limit=500)
    by_path = {f["path"]: f for f in files}
    assert total == 2
    # The Files-tab scope sees only the first of these.
    assert set(by_path) == {"/music/own.flac", "/music/feature.flac"}
    assert Q.list_artist_track_files(imported_conn, guest_id, limit=500)[1] == 1

    # Each row names the artist actually credited on that track.
    assert by_path["/music/own.flac"]["artist_name"] == "Guest Artist"
    assert by_path["/music/feature.flac"]["artist_name"] == "Host Artist"
    assert by_path["/music/feature.flac"]["artist_id"] == host_id
    assert by_path["/music/feature.flac"]["album_image_url"] == "/art/else.jpg"
    assert by_path["/music/own.flac"]["duration"] == 200000


def test_play_queue_keeps_one_file_per_track(imported_conn):
    """A lossless master and its retained lossy companion are ONE recording.

    Deduping here rather than in the client is what makes pagination safe: the
    two copies can land on different pages, where no client can see both."""
    artist_id = imported_conn.execute(
        "INSERT INTO lib2_artists(name) VALUES('Retained Copies')").lastrowid
    album_id = imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Record')",
        (artist_id,)).lastrowid
    track_id = imported_conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?, 'Song', 1)",
        (album_id,)).lastrowid
    imported_conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, format, file_state, is_primary) "
        "VALUES(?, '/music/song.flac', 'flac', 'active', 1)", (track_id,))
    imported_conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, format, file_state, is_primary) "
        "VALUES(?, '/music/song.mp3', 'mp3', 'active', 0)", (track_id,))
    # ...and a deleted copy is not playable at all.
    imported_conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, format, file_state, is_primary) "
        "VALUES(?, '/music/gone.flac', 'flac', 'deleted', 0)", (track_id,))
    imported_conn.commit()

    files, total = Q.list_artist_playback_files(imported_conn, artist_id, limit=500)
    assert total == 1
    assert [f["path"] for f in files] == ["/music/song.flac"]


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


def test_featured_artist_detail_includes_its_library_appearance(imported_conn):
    """A credited artist must not be an empty/dead detail page while its
    overview card already counts the credited track."""
    wizkid_id = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Wizkid'"
    ).fetchone()[0]

    data = Q.get_artist(imported_conn, wizkid_id)

    assert [album["title"] for album in data["albums"]] == ["Views"]


def test_featured_artist_read_model_repairs_pre_fix_missing_album_junction(imported_conn):
    """Existing production imports had only track credits.  Reads must expose
    their appearance immediately, before the next re-import backfills the
    durable album junction."""
    wizkid_id = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Wizkid'"
    ).fetchone()[0]
    imported_conn.execute(
        "DELETE FROM lib2_album_artists WHERE artist_id=?", (wizkid_id,)
    )

    artists, _total = Q.list_artists(imported_conn)
    wizkid = next(artist for artist in artists if artist["id"] == wizkid_id)
    data = Q.get_artist(imported_conn, wizkid_id)

    assert wizkid["album_count"] == 1
    assert [album["title"] for album in data["albums"]] == ["Views"]


# --- §40 alias registry -------------------------------------------------------

def _link_alias(conn, artist_id: int, alias_of_id: int) -> None:
    from core.library2.artist_aliases import link_artist_alias
    link_artist_alias(conn, artist_id, alias_of_id)


def test_list_artists_hides_alias_member_rows(imported_conn):
    drake_id = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()[0]
    cur = imported_conn.execute("INSERT INTO lib2_artists(name) VALUES('Drake (Alias)')")
    alias_id = cur.lastrowid
    _link_alias(imported_conn, alias_id, drake_id)

    artists, total = Q.list_artists(imported_conn)

    names = {a["name"] for a in artists}
    assert "Drake" in names
    assert "Drake (Alias)" not in names
    assert alias_id not in {a["id"] for a in artists}
    assert total == 2  # Drake + Wizkid, same as before linking — alias not counted twice


def test_list_artists_search_and_totals_include_alias_members(imported_conn):
    drake_id = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()[0]
    alias_id = imported_conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) "
        "VALUES('Secret Alias Name', 'Secret Alias Name')"
    ).lastrowid
    album_id = imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) "
        "VALUES(?, 'Alias-Owned Album')", (alias_id,),
    ).lastrowid
    imported_conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?, ?)",
        (album_id, alias_id),
    )
    track_id = imported_conn.execute(
        "INSERT INTO lib2_tracks(album_id, title) VALUES(?, 'Alias-Owned Track')",
        (album_id,),
    ).lastrowid
    imported_conn.execute(
        "INSERT INTO lib2_track_artists(track_id, artist_id) VALUES(?, ?)",
        (track_id, alias_id),
    )
    imported_conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, size, is_primary) "
        "VALUES(?, '/m/alias-owned.flac', 777, 1)", (track_id,),
    )
    _link_alias(imported_conn, alias_id, drake_id)

    artists, total = Q.list_artists(imported_conn, search="Secret Alias")

    assert total == 1
    assert [artist["id"] for artist in artists] == [drake_id]
    assert artists[0]["album_count"] == 2
    assert artists[0]["single_count"] == 1
    assert artists[0]["track_count"] == 3
    assert artists[0]["tracks_present"] == 3
    assert artists[0]["total_size_bytes"] == 10777


def test_get_artist_merges_albums_across_alias_group(imported_conn):
    drake_id = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()[0]
    cur = imported_conn.execute("INSERT INTO lib2_artists(name) VALUES('Drake (Alias)')")
    alias_id = cur.lastrowid
    cur = imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
        "VALUES(?, 'Alias-Only Album', 'album')", (alias_id,))
    alias_album_id = cur.lastrowid
    imported_conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
        (alias_album_id, alias_id))
    _link_alias(imported_conn, alias_id, drake_id)

    data = Q.get_artist(imported_conn, drake_id)

    titles = {a["title"] for a in data["albums"]}
    assert "Views" in titles          # canonical's own album
    assert "Alias-Only Album" in titles  # merged in from the linked alias


def test_get_artist_on_alias_id_resolves_to_canonical_header(imported_conn):
    drake_id = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()[0]
    cur = imported_conn.execute("INSERT INTO lib2_artists(name) VALUES('Drake (Alias)')")
    alias_id = cur.lastrowid
    _link_alias(imported_conn, alias_id, drake_id)

    data = Q.get_artist(imported_conn, alias_id)

    # Opening an old deep link to the alias id shows the CANONICAL header...
    assert data["id"] == drake_id
    assert data["name"] == "Drake"
    # ...but still the merged album set.
    assert [a["title"] for a in data["albums"]] == ["Views"]


def test_artist_track_files_include_alias_owned_releases(imported_conn):
    drake_id = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()[0]
    alias_id = imported_conn.execute(
        "INSERT INTO lib2_artists(name) VALUES('Drake Alias')"
    ).lastrowid
    album_id = imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Alias Album')",
        (alias_id,),
    ).lastrowid
    track_id = imported_conn.execute(
        "INSERT INTO lib2_tracks(album_id, title) VALUES(?, 'Alias Track')",
        (album_id,),
    ).lastrowid
    imported_conn.execute(
        "INSERT INTO lib2_track_files(track_id, path) VALUES(?, '/m/alias.flac')",
        (track_id,),
    )
    _link_alias(imported_conn, alias_id, drake_id)

    files, total = Q.list_artist_track_files(imported_conn, drake_id, limit=500)

    assert total >= 2
    assert "/m/alias.flac" in {item["path"] for item in files}


def test_get_album_track_status(imported_conn):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'").fetchone()[0]
    album = Q.get_album(imported_conn, views_id)
    assert album["album_type"] == "album"
    assert album["track_count"] == 2
    assert album["tracks_present"] == 1
    # "Missing" means monitored-and-absent. 'Hotline Bling' is a legacy row the
    # import left unmonitored (no file, no wishlist rule) — known, but nobody
    # is waiting for it. Monitor it and it becomes a gap again, below.
    assert album["tracks_missing"] == 0
    by_title = {t["title"]: t for t in album["tracks"]}
    one_dance = by_title["One Dance"]
    assert [a["name"] for a in one_dance["artists"]] == ["Drake", "Wizkid"]
    assert one_dance["file_status"] == "present"
    assert one_dance["file"]["quality_tier"] == "lossless"
    # B6: bulk row-selection reuses the ADR-05 file_ids-scoped delete, which
    # needs the lib2_track_files row id, not just the path.
    assert isinstance(one_dance["file"]["file_id"], int)
    # The track with no file_path is reported missing.
    assert by_title["Hotline Bling"]["file_status"] == "missing"

    # Want it again — through the wanted projection, which is what both the
    # album list and this detail read — and it counts as a gap once more.
    imported_conn.execute(
        "UPDATE lib2_tracks SET monitored=1 WHERE id=?",
        (by_title["Hotline Bling"]["id"],),
    )
    imported_conn.execute(
        "DELETE FROM lib2_wanted_tracks WHERE track_id=?",
        (by_title["Hotline Bling"]["id"],),
    )
    assert Q.get_album(imported_conn, views_id)["tracks_missing"] == 1


def test_album_detail_query_count_does_not_scale_with_tracks(imported_conn):
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    drake_id = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()[0]

    def select_count():
        statements = []
        imported_conn.set_trace_callback(statements.append)
        try:
            Q.get_album(imported_conn, views_id)
        finally:
            imported_conn.set_trace_callback(None)
        return sum(
            statement.lstrip().upper().startswith(("SELECT", "WITH"))
            for statement in statements
        )

    before = select_count()
    for number in range(20):
        track_id = imported_conn.execute(
            """INSERT INTO lib2_tracks(
                   album_id, title, track_number, monitored, quality_profile_id)
               VALUES(?, ?, ?, 1, 1)""",
            (views_id, f"Scale Track {number}", number + 10),
        ).lastrowid
        imported_conn.execute(
            "INSERT INTO lib2_track_artists(track_id, artist_id) VALUES(?, ?)",
            (track_id, drake_id),
        )
        imported_conn.execute(
            """INSERT INTO lib2_track_files(
                   track_id, path, format, bitrate, sample_rate, bit_depth)
               VALUES(?, ?, 'flac', 1000, 44100, 16)""",
            (track_id, f"/m/scale-{number}.flac"),
        )

    after = select_count()

    assert before == after


def test_album_detail_batches_legacy_download_provenance(imported_conn):
    imported_conn.execute(
        """CREATE TABLE track_downloads(
               id INTEGER PRIMARY KEY,
               file_path TEXT,
               source_service TEXT,
               spotify_track_id TEXT,
               musicbrainz_recording_id TEXT,
               isrc TEXT,
               track_title TEXT,
               track_artist TEXT,
               track_album TEXT,
               bitrate INTEGER,
               sample_rate INTEGER,
               bit_depth INTEGER)"""
    )
    imported_conn.execute(
        """INSERT INTO track_downloads(
               id, file_path, source_service, track_title, track_artist,
               track_album, sample_rate, bit_depth)
           VALUES(1, '/other/01.flac', 'wrong-suffix', 'One Dance', 'Drake',
                  'Views', 48000, 24)"""
    )
    imported_conn.execute(
        """INSERT INTO track_downloads(
               id, file_path, source_service, track_title, track_artist,
               track_album, sample_rate, bit_depth)
           VALUES(2, '/m/01.flac', 'exact-source', 'One Dance', 'Drake',
                  'Views', 96000, 24)"""
    )
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]

    album = Q.get_album(imported_conn, views_id)
    track = next(item for item in album["tracks"] if item["title"] == "One Dance")

    assert track["file"]["source"] == "exact-source"
    assert track["file"]["sample_rate"] == 96000
    assert track["file"]["bit_depth"] == 24


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
    # One Dance is monitored (it had a file until this scan confirmed it gone),
    # so it counts; the unmonitored legacy row next to it does not.
    assert album["tracks_missing"] == 1
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
    # Two slots the provider promised and no row exists for yet. The third
    # absent track has a row and is unmonitored, so it is not a gap.
    assert album["tracks_missing"] == 2
    assert len(album["tracks"]) == 4
    missing_rows = [track for track in album["tracks"] if track["file_status"] == "missing"]
    assert [track["track_number"] for track in missing_rows] == [2, 3, 4]
    assert missing_rows[1]["title"] is None
    assert missing_rows[1]["metadata_gaps"] == []


def test_a_monitored_album_counts_each_unmaterialized_slot_once(imported_conn):
    """ARCH-02: the placeholders for promised-but-unmaterialized slots are
    appended to `tracks` and inherit the album's monitored flag, so on a
    MONITORED album the missing sum counted them and `total - known_count`
    counted them again. Two expected tracks with no rows yet reported four
    missing — more missing than the album has tracks at all."""
    artist_id = imported_conn.execute(
        "SELECT id FROM lib2_artists LIMIT 1").fetchone()[0]
    album_id = imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, "
        "monitored, expected_track_count) VALUES(?, 'Pending Tracklist', "
        "'album', 1, 2)", (artist_id,)).lastrowid
    imported_conn.commit()

    album = Q.get_album(imported_conn, album_id)

    assert album["track_count"] == 2
    assert album["tracks_present"] == 0
    assert album["tracks_missing"] == 2
    assert album["tracks_missing"] <= album["track_count"]


def test_an_unmonitored_album_still_reports_its_expected_slots(imported_conn):
    """The intent the double count was hiding behind: expected slots are shown
    on an unmonitored album too, and still exactly once."""
    artist_id = imported_conn.execute(
        "SELECT id FROM lib2_artists LIMIT 1").fetchone()[0]
    album_id = imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, "
        "monitored, expected_track_count) VALUES(?, 'Unwatched', 'album', 0, 3)",
        (artist_id,)).lastrowid
    imported_conn.commit()

    album = Q.get_album(imported_conn, album_id)

    assert album["tracks_missing"] == 3
    assert len(album["tracks"]) == 3


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
    # Fresh installs default to upgrades disabled, so unknown quality remains
    # visible without becoming an automatic replacement candidate.
    assert track["upgrade_candidate"] is False


def test_get_album_evaluates_explicit_track_profile_not_album_profile(imported_conn):
    profile_id = imported_conn.execute(
        """INSERT INTO quality_profiles(
               name, ranked_targets, upgrade_policy, upgrade_cutoff_index)
           VALUES('Track Hi-Res', ?, 'until_cutoff', 0)""",
        (json.dumps([{
            "label": "FLAC 24/96", "format": "flac",
            "bit_depth": 24, "min_sample_rate": 96000,
        }]),),
    ).lastrowid
    track_id = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=100"
    ).fetchone()[0]
    album_id = imported_conn.execute(
        "SELECT album_id FROM lib2_tracks WHERE id=?", (track_id,)
    ).fetchone()[0]
    imported_conn.execute(
        """UPDATE lib2_tracks
              SET quality_profile_id=?, quality_profile_explicit=1 WHERE id=?""",
        (profile_id, track_id),
    )
    imported_conn.execute(
        """UPDATE lib2_track_files
              SET format='flac', bit_depth=16, sample_rate=44100
            WHERE track_id=?""",
        (track_id,),
    )

    album = Q.get_album(imported_conn, album_id)
    track = next(item for item in album["tracks"] if item["id"] == track_id)

    assert track["quality_profile_id"] == profile_id
    assert track["quality_profile_source"] == "track"
    assert track["upgrade_candidate"] is True


def test_get_album_surfaces_first_missing_scan_without_marking_track_missing(imported_conn):
    track_id = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=100"
    ).fetchone()[0]
    album_id = imported_conn.execute(
        "SELECT album_id FROM lib2_tracks WHERE id=?", (track_id,)
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_track_files SET file_state='missing_suspected', "
        "missing_scan_count=1 WHERE track_id=?",
        (track_id,),
    )

    album = Q.get_album(imported_conn, album_id)
    track = next(item for item in album["tracks"] if item["id"] == track_id)

    assert track["file_status"] == "missing_suspected"
    assert track["file"]["file_state"] == "missing_suspected"
    # First miss is diagnostic only: it still counts as present and cannot
    # activate the wanted/redownload projection before confirmation.
    assert album["tracks_present"] == 1
    # The suspected-missing track still counts as present, and the album's other
    # row is an unmonitored legacy one — nothing is waiting to be filled.
    assert album["tracks_missing"] == 0


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


def test_has_lyrics_recognizes_unsyncedlyrics_tag(imported_conn):
    """G5: the Lyrics tab and missing_lyrics repair job both read/write
    'unsyncedlyrics' (USLT-only files, .lrc sidecars), not just 'lyrics' — the
    LR badge must agree with what the tab actually shows."""
    import json
    tid = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=100").fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_track_files SET tags_json=? WHERE track_id=?",
        (json.dumps({"unsyncedlyrics": "la la la"}), tid),
    )
    imported_conn.commit()
    track = Q.get_track(imported_conn, tid)
    assert track["file"]["has_lyrics"] is True


def test_track_read_exposes_acoustid_status_and_pipeline_result(imported_conn):
    """A7/C4: the autolink callback now persists these onto the file row —
    the read side must surface them so the Info-tab lifecycle UI can show
    more than the coarse verification_status badge."""
    import json
    tid = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=100").fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_track_files SET acoustid_status='skip', "
        "pipeline_result_json=? WHERE track_id=?",
        (json.dumps({"acoustid_message": "no confident match",
                     "quality_fallback": ["downsample"]}), tid),
    )
    imported_conn.commit()
    track = Q.get_track(imported_conn, tid)
    assert track["file"]["acoustid_status"] == "skip"
    assert track["file"]["pipeline_result"]["acoustid_message"] == "no confident match"
    assert track["file"]["pipeline_result"]["quality_fallback"] == ["downsample"]


def test_track_read_defaults_pipeline_result_to_empty_dict(imported_conn):
    """The DDL default is the '{}' string — reads must hand back {} , not the
    raw string, so the UI can index into it unconditionally."""
    tid = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=100").fetchone()[0]
    track = Q.get_track(imported_conn, tid)
    assert track["file"]["pipeline_result"] == {}
    assert track["file"]["acoustid_status"] is None


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


def test_rich_metadata_fields_are_projected_in_reads(imported_conn):
    """§48: style/mood/label (artist), +explicit/label/style/mood (album),
    +bpm/explicit/style/mood (track) must round-trip through the read
    projections used by the edit modals, not just live in the DB column."""
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

    imported_conn.execute(
        "UPDATE lib2_albums SET explicit=1, label='Provider Label', "
        "style='Provider Style', mood='Provider Mood' WHERE id=?", (album_id,)
    )
    imported_conn.execute(
        "UPDATE lib2_tracks SET bpm=90.0, explicit=0, style='Provider T-Style', "
        "mood='Provider T-Mood' WHERE id=?", (track_id,)
    )

    artist = Q.get_artist(imported_conn, artist_id)
    assert artist["style"] is None and artist["mood"] is None and artist["label"] is None
    album_entry = next(row for row in artist["albums"] + artist["singles"] + artist["eps"]
                        if row["id"] == album_id)
    assert album_entry["explicit"] is True
    assert album_entry["label"] == "Provider Label"
    assert album_entry["style"] == "Provider Style"
    assert album_entry["mood"] == "Provider Mood"

    album = Q.get_album(imported_conn, album_id)
    assert album["explicit"] is True
    assert album["label"] == "Provider Label"
    assert album["style"] == "Provider Style"
    assert album["mood"] == "Provider Mood"

    track = next(row for row in album["tracks"] if row["id"] == track_id)
    assert track["bpm"] == 90.0
    assert track["explicit"] is False
    assert track["style"] == "Provider T-Style"
    assert track["mood"] == "Provider T-Mood"

    set_field_override(
        imported_conn, entity_type="artist", entity_id=artist_id,
        field_name="style", value="User Style",
    )
    set_field_override(
        imported_conn, entity_type="release_group", entity_id=album_id,
        field_name="explicit", value=False,
    )
    set_field_override(
        imported_conn, entity_type="track", entity_id=track_id,
        field_name="bpm", value=104.5,
    )

    artist = Q.get_artist(imported_conn, artist_id)
    assert artist["style"] == "User Style"
    album = Q.get_album(imported_conn, album_id)
    assert album["explicit"] is False
    track = next(row for row in Q.get_album(imported_conn, album_id)["tracks"]
                 if row["id"] == track_id)
    assert track["bpm"] == 104.5


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


def test_list_artists_can_skip_the_disk_space_rollup(imported_conn):
    """perf25-03: the size roll-up is the heaviest part of the statement and its
    column is opt-in, so it must be omitted — not merely hidden — on request."""
    statements = []
    imported_conn.set_trace_callback(statements.append)
    try:
        artists, total = Q.list_artists(imported_conn, include_size=False)
    finally:
        imported_conn.set_trace_callback(None)

    rollup_sql = next(
        statement for statement in statements
        if "page_artists AS MATERIALIZED" in statement
    )
    assert "ROW_NUMBER() OVER" not in rollup_sql
    assert "artist_size" not in rollup_sql

    by_name = {a["name"]: a for a in artists}
    assert total == 2
    # Everything the collapsed list actually renders is unchanged.
    assert by_name["Drake"]["album_count"] == 1
    assert by_name["Drake"]["single_count"] == 1
    assert by_name["Drake"]["track_count"] == 2
    assert by_name["Drake"]["tracks_present"] == 2
    assert by_name["Drake"]["total_size_bytes"] == 0


def test_list_artists_folds_only_page_aliases(imported_conn):
    """perf25-03: the alias-fold CTE is scoped to the requested page, so the
    result is identical while the scan no longer grows with library size."""
    statements = []
    imported_conn.set_trace_callback(statements.append)
    try:
        artists, _total = Q.list_artists(imported_conn, limit=1)
    finally:
        imported_conn.set_trace_callback(None)

    rollup_sql = next(
        statement for statement in statements
        if "page_artists AS MATERIALIZED" in statement
    )
    assert rollup_sql.index("page_artists AS MATERIALIZED") < rollup_sql.index(
        "canonical_members AS MATERIALIZED"
    )
    assert "IN (SELECT id FROM page_artists)" in rollup_sql
    assert artists[0]["album_count"] == 1
