"""§62.6 Stufe 4: one-shot repair for already-duplicated artists/albums.

The Sawano DB state (§62.1): artist 31 (name-only, created by an early
wishlist materialize) next to artist 32 (legacy import, full ids), each
holding overlapping album rows. The repair folds no-conflict same-name
artists together, re-homes their albums, then folds/flags same-title album
pairs inside the merged artist.
"""

from __future__ import annotations

import json

import pytest

from core.library2.dedup_repair import repair_duplicate_artists
from tests.lib2_seed import row_conn


def _conn(legacy_db):
    conn = row_conn(legacy_db.path)
    return conn


def _artist(conn, name, **cols):
    keys = ", ".join(["name", "sort_name", *cols.keys()])
    marks = ", ".join("?" for _ in range(2 + len(cols)))
    cur = conn.execute(
        f"INSERT INTO lib2_artists({keys}) VALUES({marks})",
        (name, name, *cols.values()))
    return cur.lastrowid


def _album(conn, artist_id, title, *, origin="library", monitored=0,
           album_type="album", external_ids="{}", with_track=False, **cols):
    keys = ", ".join(["primary_artist_id", "title", "album_type", "origin",
                      "monitored", "external_ids", *cols.keys()])
    marks = ", ".join("?" for _ in range(6 + len(cols)))
    cur = conn.execute(
        f"INSERT INTO lib2_albums({keys}) VALUES({marks})",
        (artist_id, title, album_type, origin, monitored, external_ids,
         *cols.values()))
    album_id = cur.lastrowid
    conn.execute(
        "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role) "
        "VALUES(?,?, 'primary')", (album_id, artist_id))
    if with_track:
        conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number) "
            "VALUES(?, 'Cut', 1)", (album_id,))
    return album_id


@pytest.fixture
def repair_db(imported_conn, legacy_db):
    """imported_conn gives a full lib2 schema; hand back both handles."""
    return legacy_db, imported_conn


def test_no_conflict_same_name_artists_are_merged(repair_db):
    legacy_db, conn = repair_db
    bare = _artist(conn, "Hiroyuki Sawano")
    rich = _artist(conn, "Hiroyuki Sawano", spotify_id="0Riv2KnFcLZA3JSVryRg4y",
                   legacy_artist_id=730596583,
                   external_ids=json.dumps({"deezer": "1315147"}))
    kept_album = _album(conn, bare, "Sengoku BASARA 2 Ongaku Emaki",
                        origin="discography")
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["artists_merged"] == 1
    rows = conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Hiroyuki Sawano'").fetchall()
    assert [r["id"] for r in rows] == [rich]
    # The bare row's album moved over to the survivor.
    moved = conn.execute(
        "SELECT primary_artist_id FROM lib2_albums WHERE id=?",
        (kept_album,)).fetchone()
    assert moved["primary_artist_id"] == rich
    link = conn.execute(
        "SELECT artist_id FROM lib2_album_artists WHERE album_id=?",
        (kept_album,)).fetchall()
    assert [r["artist_id"] for r in link] == [rich]


def test_merge_prefers_the_legacy_linked_richer_row(repair_db):
    legacy_db, conn = repair_db
    rich = _artist(conn, "Hiroyuki Sawano", spotify_id="0Riv2KnFcLZA3JSVryRg4y",
                   legacy_artist_id=730596583)
    bare = _artist(conn, "hiroyuki  sawano")
    conn.commit()

    repair_duplicate_artists(legacy_db)

    survivor = conn.execute(
        "SELECT id, spotify_id FROM lib2_artists WHERE lower(name) LIKE 'hiroyuki%'"
    ).fetchall()
    assert len(survivor) == 1
    assert survivor[0]["id"] == rich


def test_conflicting_ids_become_alias_link_not_merge(repair_db):
    legacy_db, conn = repair_db
    first = _artist(conn, "Hiroyuki Sawano", spotify_id="sp-one")
    second = _artist(conn, "Hiroyuki Sawano", spotify_id="sp-two")
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["artists_merged"] == 0
    assert stats["alias_linked"] == 1
    rows = {r["id"]: r["canonical_artist_id"] for r in conn.execute(
        "SELECT id, canonical_artist_id FROM lib2_artists "
        "WHERE name='Hiroyuki Sawano'")}
    assert len(rows) == 2
    assert rows[second] == first
    assert rows[first] is None


def test_post_merge_same_title_pristine_album_is_folded(repair_db):
    legacy_db, conn = repair_db
    bare = _artist(conn, "Hiroyuki Sawano")
    rich = _artist(conn, "Hiroyuki Sawano", spotify_id="0Riv2KnFcLZA3JSVryRg4y")
    # artist 31 flavor: pristine discography row.
    dup = _album(conn, bare, "Sengoku BASARA Digital Original Director's Special Edition",
                 origin="discography", album_type="ep")
    # artist 32 flavor: same release, also provider-only.
    keeper = _album(conn, rich, "Sengoku BASARA Digital Original Director's Special Edition",
                    origin="discography", album_type="ep",
                    external_ids=json.dumps({"deezer": "398610457"}))
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["artists_merged"] == 1
    assert stats["albums_folded"] == 1
    titles = conn.execute(
        "SELECT id FROM lib2_albums WHERE title LIKE 'Sengoku BASARA Digital%'"
    ).fetchall()
    assert len(titles) == 1


def test_post_merge_two_track_bearing_albums_become_review_finding(repair_db):
    legacy_db, conn = repair_db
    bare = _artist(conn, "Hiroyuki Sawano")
    rich = _artist(conn, "Hiroyuki Sawano", spotify_id="0Riv2KnFcLZA3JSVryRg4y")
    a1 = _album(conn, bare, "TVアニメ「進撃の巨人」Season 2 オリジナルサウンドトラック",
                with_track=True)
    a2 = _album(conn, rich, "TVアニメ「進撃の巨人」Season 2 オリジナルサウンドトラック",
                with_track=True)
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["albums_folded"] == 0
    assert stats["album_review"] == 1
    assert conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE id IN (?,?)", (a1, a2)
    ).fetchone()["c"] == 2
    finding = conn.execute(
        "SELECT reason FROM lib2_release_group_review").fetchone()
    assert finding["reason"] == "duplicate_title_unmerged"


def test_album_twins_are_folded_without_any_artist_merge(repair_db):
    """A library whose artists are all distinct still grows album twins.

    Real-DB catch (26 July): the album pass only ever visited artists that a
    merge had just touched, so an artist with a single clean row — the normal
    case — never had its duplicate albums looked at at all.
    """
    legacy_db, conn = repair_db
    solo = _artist(conn, "Justin Bieber", spotify_id="1uNFoZAHBGtllmzznpCI3s")
    keeper = _album(conn, solo, "SWAG II", origin="discography",
                    external_ids=json.dumps({"deezer": "816518541"}))
    _album(conn, solo, "SWAG II", origin="discography")
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["artists_merged"] == 0
    assert stats["albums_folded"] == 1
    remaining = conn.execute(
        "SELECT id FROM lib2_albums WHERE title='SWAG II'").fetchall()
    assert [r["id"] for r in remaining] == [keeper]


def test_file_bearing_twins_of_an_unmerged_artist_become_a_review_finding(
        repair_db):
    """Both sides own files, so the pass reports instead of merging — but it
    has to report, which it could not do while it never ran."""
    legacy_db, conn = repair_db
    solo = _artist(conn, "Hiroyuki Sawano")
    first = _album(conn, solo, "TV Anime \"Attack on Titan\" Original Soundtrack",
                   with_track=True)
    second = _album(conn, solo, "TV Anime \"Attack on Titan\" Original Soundtrack",
                    with_track=True)
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["albums_folded"] == 0
    assert stats["album_review"] == 1
    assert conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE id IN (?,?)", (first, second)
    ).fetchone()["c"] == 2
    finding = conn.execute(
        "SELECT reason FROM lib2_release_group_review").fetchone()
    assert finding["reason"] == "duplicate_title_unmerged"


def test_an_album_and_its_same_named_single_are_not_twins(repair_db):
    """DD-G1's bucket separation must survive the wider scan: a single named
    after its parent album is a different release, never a duplicate."""
    legacy_db, conn = repair_db
    solo = _artist(conn, "Justin Bieber")
    _album(conn, solo, "DAISIES", origin="discography")
    _album(conn, solo, "DAISIES", origin="discography", album_type="single")
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["albums_folded"] == 0
    assert stats["album_review"] == 0
    assert conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE title='DAISIES'"
    ).fetchone()["c"] == 2


def test_untitled_albums_are_never_grouped_together(repair_db):
    """An empty title normalizes to an empty key; two of them are not twins."""
    legacy_db, conn = repair_db
    solo = _artist(conn, "Various Artists")
    _album(conn, solo, "", origin="discography")
    _album(conn, solo, "", origin="discography")
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["albums_folded"] == 0
    assert stats["album_review"] == 0


def test_repair_is_idempotent(repair_db):
    legacy_db, conn = repair_db
    _artist(conn, "Hiroyuki Sawano")
    _artist(conn, "Hiroyuki Sawano", spotify_id="0Riv2KnFcLZA3JSVryRg4y")
    conn.commit()

    repair_duplicate_artists(legacy_db)
    stats2 = repair_duplicate_artists(legacy_db)

    assert stats2["artists_merged"] == 0
    assert stats2["alias_linked"] == 0


def test_different_names_with_same_catalog_id_are_merged(repair_db):
    legacy_db, conn = repair_db
    real = _artist(
        conn, "Odetari", spotify_id="7ITMCzIU9uII8gwRg8JAhc",
        legacy_artist_id=42,
    )
    fragment = _artist(conn, "Odetari w", spotify_id="7ITMCzIU9uII8gwRg8JAhc")
    fragment_album = _album(conn, fragment, "I LOVE YOU HOE", with_track=True)
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["artists_merged"] == 1
    assert conn.execute(
        "SELECT COUNT(*) c FROM lib2_artists "
        "WHERE spotify_id='7ITMCzIU9uII8gwRg8JAhc'"
    ).fetchone()["c"] == 1
    assert conn.execute(
        "SELECT primary_artist_id FROM lib2_albums WHERE id=?", (fragment_album,)
    ).fetchone()["primary_artist_id"] == real


def test_shared_catalog_id_does_not_merge_conflicting_other_ids(repair_db):
    legacy_db, conn = repair_db
    _artist(
        conn, "Name One", spotify_id="7ITMCzIU9uII8gwRg8JAhc",
        external_ids=json.dumps({"deezer": "111"}),
    )
    _artist(
        conn, "Name Two", spotify_id="7ITMCzIU9uII8gwRg8JAhc",
        external_ids=json.dumps({"deezer": "222"}),
    )
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["artists_merged"] == 0
    assert conn.execute(
        "SELECT COUNT(*) c FROM lib2_artists "
        "WHERE spotify_id='7ITMCzIU9uII8gwRg8JAhc'"
    ).fetchone()["c"] == 2


# ---------------------------------------------------------------------------
# §62.6 Stufe 5: heal foreign-shaped ids already sitting in spotify_id columns
# ---------------------------------------------------------------------------

def test_sanitize_moves_itunes_id_out_of_spotify_column(repair_db):
    """The 1229 shape: spotify_id + external_ids.spotify hold the iTunes id."""
    legacy_db, conn = repair_db
    artist = _artist(conn, "Hiroyuki Sawano")
    album = _album(conn, artist, "TVアニメ「進撃の巨人」Season 2 OST",
                   spotify_id="1239706770",
                   external_ids=json.dumps({
                       "deezer": "196470602", "itunes": "1239706770",
                       "spotify": "1239706770"}))
    conn.commit()

    repair_duplicate_artists(legacy_db)

    row = conn.execute(
        "SELECT spotify_id, external_ids FROM lib2_albums WHERE id=?",
        (album,)).fetchone()
    assert row["spotify_id"] is None
    ids = json.loads(row["external_ids"])
    assert "spotify" not in ids
    assert ids["itunes"] == "1239706770"
    assert ids["deezer"] == "196470602"


def test_sanitize_does_not_infer_namespace_via_legacy_table(repair_db):
    """P3 never assigns a provider namespace from a legacy-table coincidence."""
    legacy_db, conn = repair_db
    conn.execute("ALTER TABLE albums ADD COLUMN deezer_id TEXT")
    conn.execute("UPDATE albums SET deezer_id='42388621' WHERE id=10")
    artist = _artist(conn, "Hiroyuki Sawano")
    album = _album(conn, artist, 'TV Anime "Attack on Titan" OST',
                   spotify_id="42388621")
    conn.commit()

    repair_duplicate_artists(legacy_db)

    row = conn.execute(
        "SELECT spotify_id, external_ids FROM lib2_albums WHERE id=?",
        (album,)).fetchone()
    assert row["spotify_id"] is None
    ids = json.loads(row["external_ids"])
    assert "deezer" not in ids
    assert ids["legacy_unknown"] == "42388621"


def test_sanitize_uuid_shaped_spotify_id_is_musicbrainz(repair_db):
    legacy_db, conn = repair_db
    artist = _artist(conn, "Sawano",
                     spotify_id="60d2ea34-1912-425f-bf9c-fc544e4448cd")
    conn.commit()

    repair_duplicate_artists(legacy_db)

    row = conn.execute(
        "SELECT spotify_id, musicbrainz_id, external_ids FROM lib2_artists "
        "WHERE id=?", (artist,)).fetchone()
    assert row["spotify_id"] is None
    assert row["musicbrainz_id"] == "60d2ea34-1912-425f-bf9c-fc544e4448cd"


def test_sanitize_parks_unresolvable_numeric_id(repair_db):
    legacy_db, conn = repair_db
    artist = _artist(conn, "Sawano")
    album = _album(conn, artist, "Mystery Album", spotify_id="777000111")
    conn.commit()

    repair_duplicate_artists(legacy_db)

    row = conn.execute(
        "SELECT spotify_id, external_ids FROM lib2_albums WHERE id=?",
        (album,)).fetchone()
    assert row["spotify_id"] is None
    # Value survives for value-based matching, but under no provider's name.
    assert json.loads(row["external_ids"])["legacy_unknown"] == "777000111"


def test_a_file_imported_under_two_servers_keeps_one_track(repair_db, monkeypatch):
    """The legacy library kept a row set per media server; a library that once
    lived on another server imports every file twice. The active server's track
    keeps the file, the stale twin release folds into it and a wish that hung on
    the twin moves over instead of dying with it."""
    legacy_db, conn = repair_db
    monkeypatch.setattr("core.library2.dedup_repair._active_server", lambda: "plex")
    artist = _artist(conn, "Two Servers")
    live = _album(conn, artist, "Same Album")
    stale = _album(conn, artist, "Same Album")
    for album, server in ((live, "plex"), (stale, "jellyfin")):
        track = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, server_source) "
            "VALUES(?, 'Song', 1, ?)", (album, server)).lastrowid
        conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?, '/m/song.flac')",
                     (track,))
    conn.execute("INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?, 'Wish', 2)",
                 (stale,))
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert (stats["shared_files_folded"], stats["server_albums_folded"]) == (1, 1)
    assert conn.execute("SELECT COUNT(*) FROM lib2_albums WHERE id IN (?,?)",
                        (live, stale)).fetchone()[0] == 1
    assert sorted(r[0] for r in conn.execute(
        "SELECT title FROM lib2_tracks WHERE album_id=?", (live,))) == ["Song", "Wish"]
    assert conn.execute("SELECT COUNT(*) FROM lib2_track_files WHERE path='/m/song.flac'"
                        ).fetchone()[0] == 1


def _shared_file_pair(conn, live_artist, stale_artist, stale_title):
    live = _album(conn, live_artist, "Same Album")
    stale = _album(conn, stale_artist, stale_title)
    for album, server in ((live, "plex"), (stale, "jellyfin")):
        track = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, server_source) "
            "VALUES(?, 'Song', 1, ?)", (album, server)).lastrowid
        conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?, '/m/song.flac')",
                     (track,))
    conn.commit()
    return live, stale


def test_a_server_twin_under_another_artist_row_still_folds(repair_db, monkeypatch):
    """the other server's copy can be filed under its own artist row (an alias
    twin). its emptied release still folds into the live one."""
    from core.library2.dedup_repair import fold_shared_file_tracks
    _legacy_db, conn = repair_db
    monkeypatch.setattr("core.library2.dedup_repair._active_server", lambda: "plex")
    live, stale = _shared_file_pair(conn, _artist(conn, "Live Row"),
                                    _artist(conn, "Stale Row"), "Same Album")

    stats = fold_shared_file_tracks(conn)

    assert (stats["shared_files_folded"], stats["server_albums_folded"]) == (1, 1)
    assert conn.execute("SELECT COUNT(*) FROM lib2_albums WHERE id=?",
                        (stale,)).fetchone()[0] == 0


def test_a_differently_titled_release_under_another_artist_is_left_alone(repair_db, monkeypatch):
    from core.library2.dedup_repair import fold_shared_file_tracks
    _legacy_db, conn = repair_db
    monkeypatch.setattr("core.library2.dedup_repair._active_server", lambda: "plex")
    live, stale = _shared_file_pair(conn, _artist(conn, "Live Row"),
                                    _artist(conn, "Stale Row"), "Other Album")

    stats = fold_shared_file_tracks(conn)

    assert stats["server_albums_folded"] == 0
    assert conn.execute("SELECT COUNT(*) FROM lib2_albums WHERE id=?",
                        (stale,)).fetchone()[0] == 1


def test_startup_heals_a_fallback_id_and_lets_spotify_ask_again(repair_db):
    """Bad 25th Anniversary: Spotify's fallback handed over the iTunes id as
    Spotify's, and a "matched" Spotify attempt meant nothing looked again."""
    from core.library2.schema import run_library_v2_backfills
    _legacy_db, conn = repair_db
    artist = _artist(conn, "Michael Jackson")
    album = _album(conn, artist, "Bad 25th Anniversary", spotify_id="551515699",
                   external_ids=json.dumps({"itunes": "551515699", "spotify": "551515699"}))
    conn.execute("INSERT INTO lib2_provider_attempts(entity_type, entity_id, service, status) "
                 "VALUES('album', ?, 'spotify', 'matched')", (album,))
    conn.commit()

    run_library_v2_backfills(conn)

    row = conn.execute("SELECT spotify_id, external_ids FROM lib2_albums WHERE id=?", (album,)).fetchone()
    assert row["spotify_id"] is None
    assert json.loads(row["external_ids"]) == {"itunes": "551515699"}
    assert conn.execute("SELECT COUNT(*) FROM lib2_provider_attempts WHERE entity_id=? "
                        "AND service='spotify'", (album,)).fetchone()[0] == 0


def test_a_folded_server_twin_hands_over_its_plays_edits_and_wishes(repair_db, monkeypatch):
    """the twin track the other server held is deleted by the fold: its play
    count, a hand edit and a wishlist link move to the track that keeps the
    file instead of dying with it"""
    from core.library2.dedup_repair import fold_shared_file_tracks
    from core.library2.metadata_overrides import ensure_metadata_overrides_schema
    _legacy_db, conn = repair_db
    monkeypatch.setattr("core.library2.dedup_repair._active_server", lambda: "plex")
    artist = _artist(conn, "Two Servers")
    live, stale = _album(conn, artist, "Same Album"), _album(conn, artist, "Same Album")
    ids = {}
    for album, server, plays in ((live, "plex", 2), (stale, "jellyfin", 5)):
        ids[server] = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, server_source, play_count) "
            "VALUES(?, 'Song', 1, ?, ?)", (album, server, plays)).lastrowid
        conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?, '/m/song.flac')",
                     (ids[server],))
    ensure_metadata_overrides_schema(conn.cursor())
    conn.execute("INSERT INTO lib2_metadata_overrides(entity_type, entity_id, field_name, value_json) "
                 "VALUES('track', ?, 'title', '\"Song (fixed)\"')", (ids["jellyfin"],))
    conn.execute("CREATE TABLE IF NOT EXISTS wishlist_tracks (id INTEGER PRIMARY KEY, "
                 "spotify_track_id TEXT, spotify_data TEXT, source_info TEXT, source_type TEXT, "
                 "profile_id INTEGER DEFAULT 1, quality_profile_id INTEGER, request_status TEXT)")
    wished = conn.execute(
        "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, source_info, profile_id) "
        "VALUES('sp-song', '{}', json_object('lib2_track_id', ?), 1)", (ids["jellyfin"],)).lastrowid
    conn.commit()

    fold_shared_file_tracks(conn)
    conn.commit()

    keep = ids["plex"]
    assert conn.execute("SELECT play_count FROM lib2_tracks WHERE id=?", (keep,)).fetchone()[0] == 7
    assert conn.execute("SELECT entity_id FROM lib2_metadata_overrides").fetchone()[0] == keep
    info = conn.execute("SELECT source_info FROM wishlist_tracks WHERE id=?", (wished,)).fetchone()
    assert json.loads(info[0])["lib2_track_id"] == keep


def test_a_finding_on_the_merged_twin_follows_the_survivor(repair_db):
    """third #1062 review P2A-6: a dismissal carried onto the jellyfin copy
    of an artist named a deleted id after the merge, and the next scan
    raised the finding again on the survivor"""
    legacy_db, conn = repair_db
    bare = _artist(conn, "Hiroyuki Sawano")
    rich = _artist(conn, "Hiroyuki Sawano", spotify_id="0Riv2KnFcLZA3JSVryRg4y",
                   legacy_artist_id=730596583)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS repair_findings (id INTEGER PRIMARY KEY, job_id TEXT,"
        " finding_type TEXT, entity_type TEXT, entity_id TEXT, status TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO repair_findings(job_id, finding_type, entity_type, entity_id, status)"
                 " VALUES('genre_enrichment', 'genre_enrichment', 'artist', ?, 'dismissed')",
                 (f"lib2:{bare}",))
    conn.commit()

    repair_duplicate_artists(legacy_db)

    assert conn.execute("SELECT entity_id FROM repair_findings").fetchone()[0] == f"lib2:{rich}"


# ── #1985: the same release listed by several media servers ──────────────────

def _server_album(conn, artist, title, server, tracks, *, path, year=None, plays=0, **cols):
    """one server's listing of a release: an album row, its tracks and files."""
    import uuid
    key = uuid.uuid4().hex[:8]
    album = _album(conn, artist, title, server_source=server, server_id=f"{server}-{key}",
                   **({"year": year} if year else {}), **cols)
    ids = []
    for number, name in enumerate(tracks, start=1):
        track = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, server_source, "
            "server_id, play_count) VALUES(?,?,?,1,?,?,?)",
            (album, name, number, server, f"{server}-{key}-{number}", plays)).lastrowid
        conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?,?)",
                     (track, path.format(n=number, name=name)))
        conn.execute("INSERT INTO lib2_media_server_mappings(entity_type, entity_id, server_source, "
                     "server_id) VALUES('track', ?, ?, ?)", (track, server, f"{server}-{key}-{number}"))
        ids.append(track)
    conn.execute("INSERT INTO lib2_media_server_mappings(entity_type, entity_id, server_source, "
                 "server_id) VALUES('album', ?, ?, ?)", (album, server, f"{server}-{key}"))
    conn.commit()
    return album, ids


_SONGS = ["Intro", "Second Song", "Third Song"]


def _three_servers(conn, monkeypatch, artist_name="Three Servers"):
    monkeypatch.setattr("core.library2.dedup_repair._active_server", lambda: "plex")
    artist = _artist(conn, artist_name)
    who = artist_name.replace(" ", "")
    plex, plex_tracks = _server_album(
        conn, artist, "Same Album", "plex", _SONGS,
        path="/data/music/" + who + "/Same Album/{n:02d} - {name}.flac", plays=2)
    jelly, _ = _server_album(conn, artist, "Same Album", "jellyfin", _SONGS,
                             path="/media/" + who + "/Same Album/{n:02d} - {name}.flac", plays=5)
    # navidrome reports paths under its own music folder, from an older layout
    navi, _ = _server_album(conn, artist, "Same Album", "navidrome", _SONGS,
                            path=who + "/Same Album/01-{n:02d} - {name}.flac")
    return artist, plex, plex_tracks, jelly, navi


def _titles(conn, album):
    return [r[0] for r in conn.execute(
        "SELECT title FROM lib2_tracks WHERE album_id=? ORDER BY track_number, id", (album,))]


def test_one_release_listed_by_three_servers_is_one_album(repair_db, monkeypatch):
    """an artist with 85 tracks showed 290 after the upgrade: each server's
    listing of the same files, under its own paths, became its own tracks."""
    from core.library2.dedup_repair import fold_server_twins
    _legacy_db, conn = repair_db
    artist, plex, plex_tracks, jelly, navi = _three_servers(conn, monkeypatch)

    stats = fold_server_twins(conn, resolve=lambda path: None)

    assert stats == {"server_twins_folded": 6, "server_twin_copies_kept": 0,
                     "server_twin_albums_folded": 2}
    albums = [r[0] for r in conn.execute(
        "SELECT id FROM lib2_albums WHERE primary_artist_id=?", (artist,))]
    assert albums == [plex]
    assert _titles(conn, plex) == _SONGS
    # one file per track, the active server's
    files = [r[0] for r in conn.execute(
        "SELECT f.path FROM lib2_track_files f JOIN lib2_tracks t ON t.id=f.track_id "
        "WHERE t.album_id=? ORDER BY f.path", (plex,))]
    assert files == [f"/data/music/ThreeServers/Same Album/{n:02d} - {name}.flac"
                     for n, name in enumerate(_SONGS, start=1)]
    # every server still knows the kept rows as its own
    mapped = {r[0]: r[1] for r in conn.execute(
        "SELECT server_source, COUNT(*) FROM lib2_media_server_mappings "
        "WHERE entity_type='track' AND entity_id IN (?,?,?) GROUP BY 1", plex_tracks)}
    assert mapped == {"plex": 3, "jellyfin": 3, "navidrome": 3}
    assert {r[0] for r in conn.execute(
        "SELECT server_source FROM lib2_media_server_mappings "
        "WHERE entity_type='album' AND entity_id=?", (plex,))} == {"plex", "jellyfin", "navidrome"}
    # two servers counted their own plays
    assert conn.execute("SELECT play_count FROM lib2_tracks WHERE id=?",
                        (plex_tracks[0],)).fetchone()[0] == 7
    # nothing left to do
    assert not any(fold_server_twins(conn, resolve=lambda path: None).values())


def test_a_second_real_file_stays_as_another_copy(repair_db, monkeypatch, tmp_path):
    from core.library2.dedup_repair import fold_server_twins
    _legacy_db, conn = repair_db
    monkeypatch.setattr("core.library2.dedup_repair._active_server", lambda: "plex")
    artist = _artist(conn, "Two Copies")
    first, second = tmp_path / "flac" / "song.flac", tmp_path / "mp3" / "song.mp3"
    for path in (first, second):
        path.parent.mkdir()
        path.write_bytes(b"x")
    plex, (kept,) = _server_album(conn, artist, "Album", "plex", ["Song"], path=str(first))
    _server_album(conn, artist, "Album", "jellyfin", ["Song"], path=str(second))

    stats = fold_server_twins(conn, resolve=lambda path: path)

    assert stats["server_twins_folded"] == 1 and stats["server_twin_copies_kept"] == 1
    rows = conn.execute("SELECT path, is_primary FROM lib2_track_files WHERE track_id=? "
                        "ORDER BY path", (kept,)).fetchall()
    assert [r["path"] for r in rows] == sorted([str(first), str(second)])
    assert sum(r["is_primary"] for r in rows) == 1


def test_the_same_file_under_two_mounts_is_one_file(repair_db, monkeypatch, tmp_path):
    from core.library2.dedup_repair import fold_server_twins
    _legacy_db, conn = repair_db
    monkeypatch.setattr("core.library2.dedup_repair._active_server", lambda: "plex")
    artist = _artist(conn, "One File")
    real = tmp_path / "song.flac"
    real.write_bytes(b"x")
    plex, (kept,) = _server_album(conn, artist, "Album", "plex", ["Song"], path="/data/song.flac")
    _server_album(conn, artist, "Album", "jellyfin", ["Song"], path="/media/song.flac")

    stats = fold_server_twins(conn, resolve=lambda path: str(real))

    assert stats["server_twin_copies_kept"] == 0
    assert [r[0] for r in conn.execute(
        "SELECT path FROM lib2_track_files WHERE track_id=?", (kept,))] == ["/data/song.flac"]


def test_two_releases_of_one_name_stay_two(repair_db, monkeypatch):
    """#1299: same server, or a different year, or too little in common."""
    from core.library2.dedup_repair import fold_server_twins
    _legacy_db, conn = repair_db
    monkeypatch.setattr("core.library2.dedup_repair._active_server", lambda: "plex")
    artist = _artist(conn, "Self Titled")
    _server_album(conn, artist, "Same Server", "plex", _SONGS, path="/a/{n}.flac")
    _server_album(conn, artist, "Same Server", "plex", _SONGS, path="/b/{n}.flac")
    _server_album(conn, artist, "Peter Gabriel", "plex", _SONGS, path="/c/{n}.flac", year=1977)
    _server_album(conn, artist, "Peter Gabriel", "jellyfin", _SONGS, path="/d/{n}.flac", year=1980)
    _server_album(conn, artist, "Greatest Hits", "plex",
                  ["One", "Two", "Three", "Four"], path="/e/{n}.flac")
    _server_album(conn, artist, "Greatest Hits", "jellyfin",
                  ["One", "Five", "Six", "Seven"], path="/f/{n}.flac")
    _server_album(conn, artist, "A Single", "plex", ["A Single"], path="/g/{n}.flac",
                  album_type="single")
    _server_album(conn, artist, "A Single", "jellyfin", _SONGS, path="/h/{n}.flac")

    assert not any(fold_server_twins(conn, resolve=lambda path: None).values())
    assert conn.execute("SELECT COUNT(*) FROM lib2_albums WHERE primary_artist_id=?",
                        (artist,)).fetchone()[0] == 8


def test_tracks_only_the_other_server_lists_stay_where_they_are(repair_db, monkeypatch):
    from core.library2.dedup_repair import fold_server_twins
    _legacy_db, conn = repair_db
    monkeypatch.setattr("core.library2.dedup_repair._active_server", lambda: "plex")
    artist = _artist(conn, "Bonus Track")
    plex, _ = _server_album(conn, artist, "Album", "plex", _SONGS, path="/p/{n}.flac")
    jelly, _ = _server_album(conn, artist, "Album", "jellyfin", [*_SONGS, "Bonus"],
                             path="/j/{n}.flac")

    stats = fold_server_twins(conn, resolve=lambda path: None)

    assert stats["server_twins_folded"] == 3 and stats["server_twin_albums_folded"] == 0
    assert _titles(conn, plex) == _SONGS and _titles(conn, jelly) == ["Bonus"]


def test_a_repeated_title_pairs_by_its_number(repair_db, monkeypatch):
    from core.library2.dedup_repair import fold_server_twins
    _legacy_db, conn = repair_db
    monkeypatch.setattr("core.library2.dedup_repair._active_server", lambda: "plex")
    artist = _artist(conn, "Interludes")
    songs = ["Interlude", "Song", "Interlude"]
    plex, kept = _server_album(conn, artist, "Album", "plex", songs, path="/p/{n}.flac")
    _server_album(conn, artist, "Album", "jellyfin", songs, path="/j/{n}.flac", plays=1)

    fold_server_twins(conn, resolve=lambda path: None)

    assert _titles(conn, plex) == songs
    assert [r[0] for r in conn.execute(
        "SELECT play_count FROM lib2_tracks WHERE album_id=? ORDER BY track_number", (plex,))] == [1, 1, 1]


def test_another_profiles_own_library_is_not_a_server_twin(repair_db, monkeypatch):
    from core.library2.dedup_repair import fold_server_twins
    _legacy_db, conn = repair_db
    monkeypatch.setattr("core.library2.dedup_repair._active_server", lambda: "plex")
    artist = _artist(conn, "Kim Has It Too")
    _server_album(conn, artist, "Album", "plex", _SONGS, path="/house/{n}.flac")
    kim, kim_tracks = _server_album(conn, artist, "Album", "jellyfin", _SONGS, path="/kim/{n}.flac")
    conn.execute("UPDATE lib2_track_files SET owner_profile_id=7 WHERE track_id IN (?,?,?)",
                 kim_tracks)
    conn.commit()
    assert not any(fold_server_twins(conn, resolve=lambda path: None).values())


def test_the_fold_commits_artist_by_artist(repair_db, monkeypatch):
    from core.library2.dedup_repair import fold_server_twins
    legacy_db, conn = repair_db
    _three_servers(conn, monkeypatch, "First")
    _three_servers(conn, monkeypatch, "Second")
    commits = []
    fold_server_twins(conn, resolve=lambda path: None, commit=lambda: commits.append(1))
    assert len(commits) == 2


def test_the_repair_folds_server_twins(repair_db, monkeypatch):
    legacy_db, conn = repair_db
    artist, plex, *_rest = _three_servers(conn, monkeypatch, "Third")
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda path, **_k: None)
    stats = repair_duplicate_artists(legacy_db)
    assert stats["server_twins_folded"] == 6 and stats["server_twin_albums_folded"] == 2
    assert conn.execute("SELECT COUNT(*) FROM lib2_albums WHERE primary_artist_id=?",
                        (artist,)).fetchone()[0] == 1


def test_every_artist_with_server_twins_is_folded_in_one_run(repair_db, monkeypatch):
    legacy_db, conn = repair_db
    kept = [_three_servers(conn, monkeypatch, name)[:2] for name in ("First", "Second", "Third")]
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda path, **_k: None)
    stats = repair_duplicate_artists(legacy_db)
    assert stats["server_twins_folded"] == 18 and stats["server_twin_albums_folded"] == 6
    for artist, plex in kept:
        assert [r[0] for r in conn.execute(
            "SELECT id FROM lib2_albums WHERE primary_artist_id=?", (artist,))] == [plex]
        assert _titles(conn, plex) == _SONGS


# ── #1985: removing one copy of a song leaves no "missing" track behind ──────

def _two_copies(conn, artist_name="Copies"):
    artist = _artist(conn, artist_name)
    album = _album(conn, artist, "Album")
    ids = []
    for folder, plays in (("old", 3), ("new", 4)):
        track = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, play_count) "
            "VALUES(?, 'Song', 1, 1, ?)", (album, plays)).lastrowid
        conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?,?)",
                     (track, f"/m/{artist_name}/{folder}/01 - Song.flac"))
        conn.execute("INSERT INTO lib2_media_server_mappings(entity_type, entity_id, server_source, "
                     "server_id) VALUES('track', ?, 'plex', ?)", (track, f"{artist_name}-{folder}"))
        ids.append(track)
    conn.commit()
    return album, ids


def test_a_removed_copys_row_folds_into_the_copy_that_is_left(repair_db):
    """two copies of a song in one album are two rows. removing one left its
    row as a missing track: 1 of 2, on an album with one song."""
    from core.library2.dedup_repair import fold_fileless_twins
    _legacy_db, conn = repair_db
    album, (old, new) = _two_copies(conn)
    conn.execute("UPDATE lib2_track_files SET file_state='deleted' WHERE track_id=?", (old,))

    assert fold_fileless_twins(conn, [old]) == 1

    assert [r[0] for r in conn.execute("SELECT id FROM lib2_tracks WHERE album_id=?", (album,))] == [new]
    assert conn.execute("SELECT play_count FROM lib2_tracks WHERE id=?", (new,)).fetchone()[0] == 7
    # the removed file stays on record; no mapping is left naming the gone row
    assert {(r[0], r[1]) for r in conn.execute(
        "SELECT path, file_state FROM lib2_track_files WHERE track_id=?", (new,))} == {
        ("/m/Copies/old/01 - Song.flac", "deleted"), ("/m/Copies/new/01 - Song.flac", "active")}
    assert conn.execute("SELECT COUNT(*) FROM lib2_media_server_mappings "
                        "WHERE entity_type='track' AND entity_id=?", (old,)).fetchone()[0] == 0
    assert fold_fileless_twins(conn, [old, new]) == 0


def test_a_missing_track_with_no_twin_stays_missing(repair_db):
    from core.library2.dedup_repair import fold_fileless_twins
    _legacy_db, conn = repair_db
    artist = _artist(conn, "Real Gap")
    album = _album(conn, artist, "Album")
    first = conn.execute("INSERT INTO lib2_tracks(album_id, title, track_number) "
                         "VALUES(?, 'One', 1)", (album,)).lastrowid
    second = conn.execute("INSERT INTO lib2_tracks(album_id, title, track_number) "
                          "VALUES(?, 'Two', 2)", (album,)).lastrowid
    # same number, another song: a deluxe disc numbered from 1 again
    other = conn.execute("INSERT INTO lib2_tracks(album_id, title, track_number) "
                         "VALUES(?, 'Bonus', 1)", (album,)).lastrowid
    for track in (second, other):
        conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?,?)",
                     (track, f"/m/{track}.flac"))
    conn.commit()
    assert fold_fileless_twins(conn, [first]) == 0
    assert conn.execute("SELECT COUNT(*) FROM lib2_tracks WHERE album_id=?", (album,)).fetchone()[0] == 3


def test_a_track_that_still_has_a_file_is_never_folded(repair_db):
    from core.library2.dedup_repair import fold_fileless_twins
    _legacy_db, conn = repair_db
    album, ids = _two_copies(conn, "Both Kept")
    assert fold_fileless_twins(conn, ids) == 0
    assert conn.execute("SELECT COUNT(*) FROM lib2_tracks WHERE album_id=?", (album,)).fetchone()[0] == 2


def test_three_copies_with_two_left_keep_their_rows(repair_db):
    """two rows still hold a file: which one the leftover belongs to is not
    ours to guess."""
    from core.library2.dedup_repair import fold_fileless_twins
    _legacy_db, conn = repair_db
    album, (old, new) = _two_copies(conn, "Three")
    third = conn.execute("INSERT INTO lib2_tracks(album_id, title, track_number, disc_number) "
                         "VALUES(?, 'Song', 1, 1)", (album,)).lastrowid
    conn.commit()
    assert fold_fileless_twins(conn, [third]) == 0


# ── an artist two servers listed is one artist ───────────────────────────────

def _ids(**ids):
    return json.dumps(ids, sort_keys=True)


def _twin_artists(conn, monkeypatch, *, theirs):
    monkeypatch.setattr("core.library2.dedup_repair._active_server", lambda: "plex")
    mine = _artist(conn, "Twin Artist", spotify_id="twin-sp", musicbrainz_id="twin-mb",
                   external_ids=_ids(deezer="twin-dz", spotify="twin-sp", musicbrainz="twin-mb", tidal="111"),
                   server_source="plex")
    other = _artist(conn, "Twin Artist", external_ids=_ids(**theirs), server_source="navidrome")
    conn.commit()
    return mine, other


def test_one_lesser_catalogue_disagreeing_does_not_keep_an_artist_in_two(repair_db, monkeypatch):
    """tidal keeps two pages for bbno$. the navidrome row carried the other
    one, so the two rows stayed apart and the artist page showed every release
    twice: once owned, once as 0 of 14."""
    database, conn = repair_db
    mine, other = _twin_artists(conn, monkeypatch, theirs={
        "deezer": "twin-dz", "spotify": "twin-sp", "musicbrainz": "twin-mb", "tidal": "999"})
    owned, _ = _server_album(conn, mine, "Same Album", "plex", _SONGS,
                             path="/data/music/Twin/Same Album/{n:02d} - {name}.flac")
    shell = _album(conn, other, "Same Album")
    for number, name in enumerate(_SONGS, start=1):
        conn.execute("INSERT INTO lib2_tracks(album_id, title, track_number, disc_number) "
                     "VALUES(?,?,?,1)", (shell, name, number))
    conn.commit()
    files = conn.execute("SELECT COUNT(*) FROM lib2_track_files").fetchone()[0]

    stats = repair_duplicate_artists(database)

    assert stats["artists_merged"] == 1 and stats["alias_linked"] == 0
    assert [r[0] for r in conn.execute("SELECT id FROM lib2_artists WHERE name='Twin Artist'")] == [mine]
    assert [r[0] for r in conn.execute("SELECT id FROM lib2_albums WHERE title='Same Album'")] == [owned]
    assert _titles(conn, owned) == _SONGS
    assert conn.execute("SELECT COUNT(*) FROM lib2_track_files").fetchone()[0] == files
    # and nothing more to do the second time
    again = repair_duplicate_artists(database)
    assert not any(again.values()), again


def test_a_strong_id_disagreeing_still_keeps_two_artists(repair_db, monkeypatch):
    database, conn = repair_db
    mine, other = _twin_artists(conn, monkeypatch, theirs={
        "deezer": "twin-dz2", "spotify": "twin-sp", "musicbrainz": "twin-mb", "tidal": "111"})
    stats = repair_duplicate_artists(database)
    assert stats["artists_merged"] == 0 and stats["alias_linked"] == 1
    assert conn.execute("SELECT canonical_artist_id FROM lib2_artists WHERE id=?",
                        (other,)).fetchone()[0] == mine


def test_one_strong_id_is_not_enough_to_overrule_a_disagreement(repair_db, monkeypatch):
    database, conn = repair_db
    _twin_artists(conn, monkeypatch, theirs={"spotify": "twin-sp", "tidal": "999"})
    assert repair_duplicate_artists(database)["artists_merged"] == 0


def test_an_id_a_row_collected_twice_agrees_with_either(repair_db, monkeypatch):
    database, conn = repair_db
    mine, _ = _twin_artists(conn, monkeypatch, theirs={
        "deezer": "twin-dz", "musicbrainz": "twin-mb, twin-mb9", "tidal": "111"})
    assert repair_duplicate_artists(database)["artists_merged"] == 1
    assert [r[0] for r in conn.execute("SELECT id FROM lib2_artists WHERE name='Twin Artist'")] == [mine]


def test_a_listing_with_no_files_and_no_twin_is_left_alone(repair_db, monkeypatch):
    database, conn = repair_db
    monkeypatch.setattr("core.library2.dedup_repair._active_server", lambda: "plex")
    artist = _artist(conn, "Shell Only")
    _server_album(conn, artist, "Owned One", "plex", _SONGS,
                  path="/data/music/Shell/Owned One/{n:02d} - {name}.flac")
    shell = _album(conn, artist, "Another One", with_track=True)
    conn.commit()
    repair_duplicate_artists(database)
    assert conn.execute("SELECT COUNT(*) FROM lib2_tracks WHERE album_id=?", (shell,)).fetchone()[0] == 1


def test_an_upgraded_library_gets_the_artist_merge_once(repair_db, monkeypatch):
    from core.library2 import bootstrap
    database, conn = repair_db
    monkeypatch.setattr(bootstrap, "get_state",
                        lambda _db: {"status": "done", "finished_at": "2026-11-01"})
    mine, _ = _twin_artists(conn, monkeypatch, theirs={
        "deezer": "twin-dz", "spotify": "twin-sp", "musicbrainz": "twin-mb", "tidal": "999"})
    first = bootstrap._fold_artist_twins_once(database, conn)
    assert first["artists_merged"] == 1
    assert [r[0] for r in conn.execute("SELECT id FROM lib2_artists WHERE name='Twin Artist'")] == [mine]
    # marked: a later start does not walk every artist again
    assert bootstrap._fold_artist_twins_once(database, conn) == {}


def test_the_artist_merge_waits_for_a_finished_import(repair_db, monkeypatch):
    from core.library2 import bootstrap
    database, conn = repair_db
    monkeypatch.setattr(bootstrap, "get_state", lambda _db: {"status": "running"})
    _twin_artists(conn, monkeypatch, theirs={
        "deezer": "twin-dz", "spotify": "twin-sp", "musicbrainz": "twin-mb", "tidal": "999"})
    assert bootstrap._fold_artist_twins_once(database, conn) == {}
    assert conn.execute("SELECT COUNT(*) FROM lib2_artists WHERE name='Twin Artist'").fetchone()[0] == 2
