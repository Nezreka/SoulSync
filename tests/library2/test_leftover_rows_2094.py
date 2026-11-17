"""#2094: Aesop Rock "Labor Days" read 14 of 24 on 3.6.6.

a soulseek rip tagged "Labor Days - Daylight" and so on got a track row per
song under that name. a retag moved the files onto the proper rows and left
ten rows behind with no file. 3.6.6 could fold them, but only the one-time
library upgrade ran the sweep, so the deep scan, the artist's Refresh & Scan
and Update Discography the reporter ran never touched them. and a row with no
file had no way to be removed by hand.
"""

from __future__ import annotations

from contextlib import closing
from types import SimpleNamespace

import pytest

from tests.lib2_seed import row_conn
from tests.library2.test_api_routes import _wait_for_job, api  # noqa: F401 - the route fixture

# what the reporter's screenshots show: the 14 songs with their files, and
# the rip's rows at the same positions with no file. "No rEgrets" is the
# provider's spelling, the rip said "No Regrets". lengths are a second apart
# on four of them, and the 9-5ers leftover picked up the real song's isrc.
_SONGS = [
    ("Labor", 153000, 152300), ("Daylight", 266000, 266000), ("Save Yourself", 300000, None),
    ("Flashflood", 234000, 234000), ("No rEgrets", 271000, 271000), ("One Brick", 272000, 272000),
    ("The Tugboat Complex Pt. 3", 227000, None), ("Coma", 237000, 236300),
    ("Battery", 307000, 307000), ("Boombox Apocalypse", 305000, None),
    ("Bent Life", 290000, 289300), ("The Yes & The Y'all", 245000, None),
    ("9-5ers Anthem", 279000, 278300), ("Shovel", 286000, 285300),
]
_RIP_TITLE = {"No rEgrets": "No Regrets"}


def _labor_days(conn, *, artist_id=None):
    """the album as the reporter has it. returns (album, kept ids, leftover ids)"""
    if artist_id is None:
        artist_id = conn.execute(
            "INSERT INTO lib2_artists(name, sort_name) VALUES('Aesop Rock','Aesop Rock')").lastrowid
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, origin) "
        "VALUES(?, 'Labor Days', 'album', 'library')", (artist_id,)).lastrowid
    conn.execute("INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role) "
                 "VALUES(?, ?, 'primary')", (album, artist_id))
    kept, leftovers = [], []
    for number, (title, kept_ms, rip_ms) in enumerate(_SONGS, start=1):
        if rip_ms is not None:
            leftover = conn.execute(
                "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, duration, "
                "play_count, isrc) VALUES(?, ?, ?, 1, ?, 1, ?)",
                (album, f"Labor Days - {_RIP_TITLE.get(title, title)}", number, rip_ms,
                 "USA4T0401313" if number == 13 else None)).lastrowid
            # the file it had, gone when the retag moved it
            conn.execute("INSERT INTO lib2_track_files(track_id, path, file_state) "
                         "VALUES(?, ?, 'missing_confirmed')",
                         (leftover, f"/m/Aesop Rock/Labor Days/Labor Days - {number:02d}.flac"))
            leftovers.append(leftover)
        track = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, duration, "
            "play_count, isrc) VALUES(?, ?, ?, 1, ?, 2, ?)",
            (album, title, number, kept_ms, f"USA4T04013{number:02d}")).lastrowid
        conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?, ?)",
                     (track, f"/m/Aesop Rock/Labor Days/{number:02d} - {title}.flac"))
        kept.append(track)
    conn.commit()
    return album, kept, leftovers


def _album_rows(conn, album):
    return [r[0] for r in conn.execute(
        "SELECT title FROM lib2_tracks WHERE album_id=? ORDER BY track_number, id", (album,))]


@pytest.mark.parametrize("deep", [True, False])
def test_a_scan_folds_the_rows_the_retag_left(legacy_db, imported_conn, monkeypatch, deep):
    """the deep scan and the regular scan the reporter ran end in the post-scan
    hook. on 3.6.6 nothing in it looked for leftovers, so 24 stayed 24."""
    import api.database_admin as da
    album, kept, _leftovers = _labor_days(imported_conn)
    phases = []
    monkeypatch.setattr(da, "_run_own_library_scans", lambda *a, **k: None)
    monkeypatch.setattr(da, "_reconcile_after_scan", None)
    monkeypatch.setattr(da, "_db_update_phase_callback", phases.append)

    da._post_scan_hook_with_own_libraries("plex", deep=deep)(SimpleNamespace(database=legacy_db))

    with closing(row_conn(legacy_db.path)) as conn:
        assert _album_rows(conn, album) == [title for title, _k, _r in _SONGS]
        assert [r[0] for r in conn.execute(
            "SELECT id FROM lib2_tracks WHERE album_id=? ORDER BY track_number", (album,))] == kept
        # the leftover's play moved onto the song
        assert conn.execute("SELECT play_count FROM lib2_tracks WHERE id=?", (kept[0],)).fetchone()[0] == 3
    assert "Finishing up: folding leftover track rows..." in phases


def test_artist_refresh_and_scan_folds_them(api, monkeypatch):  # noqa: F811
    """the artist page's Refresh & Scan rescans the files and stopped there"""
    client, db, ids = api
    monkeypatch.setattr("core.library2.scan.rescan_files",
                        lambda *a, **k: {"scanned": 14, "updated": 0})
    with closing(db._get_connection()) as conn:
        artist = conn.execute(
            "INSERT INTO lib2_artists(name, sort_name) VALUES('Aesop Rock','Aesop Rock')").lastrowid
        album, _kept, _leftovers = _labor_days(conn, artist_id=artist)
        # another artist's album with a leftover is not this refresh's business
        _other, _k2, other_leftovers = _labor_days(conn, artist_id=ids["artist"])
    started = client.post(f"/api/library/v2/artists/{artist}/refresh").get_json()
    state = _wait_for_job(client, started["job_id"])
    assert state["error"] is None
    assert state["result"]["leftover_tracks_folded"] == 10
    with closing(db._get_connection()) as conn:
        assert len(_album_rows(conn, album)) == 14
        assert conn.execute(
            f"SELECT COUNT(*) FROM lib2_tracks WHERE id IN ({','.join('?' for _ in other_leftovers)})",
            other_leftovers).fetchone()[0] == 10


def test_update_discography_folds_before_the_tracklist_pass(legacy_db, imported_conn, monkeypatch):
    """two rows at one position read as a numbering fault and got a tracklist
    pass, which keeps both rows. they are a leftover and its song."""
    from core.library2 import completeness
    from core.library2.discography import repair_track_number_collisions
    album, _kept, _leftovers = _labor_days(imported_conn)
    artist = imported_conn.execute(
        "SELECT primary_artist_id FROM lib2_albums WHERE id=?", (album,)).fetchone()[0]
    resolved = []
    monkeypatch.setattr(completeness, "resolve_tracklist",
                        lambda _cfg, _conn, album_id, **_k: resolved.append(album_id))

    repair_track_number_collisions(legacy_db, None, artist)

    assert resolved == []
    with closing(row_conn(legacy_db.path)) as conn:
        assert len(_album_rows(conn, album)) == 14


def test_a_reviewed_album_is_left_to_the_person(imported_conn):
    """the sweep is automation: an album locked after review (#2046) keeps
    its rows until someone removes them"""
    from core.library2 import locks
    from core.library2.dedup_repair import fold_leftover_rows
    album, _kept, _leftovers = _labor_days(imported_conn)
    locks.set_lock(imported_conn, "album", album)
    imported_conn.commit()
    locks.invalidate()
    try:
        with locks.automation():
            assert fold_leftover_rows(imported_conn) == {"leftover_tracks_folded": 0}
        assert len(_album_rows(imported_conn, album)) == 24
    finally:
        locks.invalidate()


def test_remove_a_leftover_row_by_hand(api):  # noqa: F811
    """a row with no file had no remove at all: the track delete answers
    "Track has no file in the library" for it"""
    client, db, _ids = api
    with closing(db._get_connection()) as conn:
        album, kept, leftovers = _labor_days(conn)
        # a lone missing track: nothing to fold it into
        lone = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number) "
            "VALUES(?, 'Bonus', 15, 1)", (album,)).lastrowid
        conn.commit()

    resp = client.post(f"/api/library/v2/tracks/{leftovers[1]}/remove-leftover")
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["kept_track_id"] == kept[1] and body["kept_title"] == "Daylight"

    assert client.post(f"/api/library/v2/tracks/{lone}/remove-leftover").status_code == 409
    assert client.post(f"/api/library/v2/tracks/{kept[0]}/remove-leftover").status_code == 409
    assert client.post("/api/library/v2/tracks/999999/remove-leftover").status_code == 404
    with closing(db._get_connection()) as conn:
        assert conn.execute("SELECT 1 FROM lib2_tracks WHERE id=?", (leftovers[1],)).fetchone() is None
        assert conn.execute("SELECT 1 FROM lib2_tracks WHERE id=?", (lone,)).fetchone() is not None
        # its old file record is history under the song now
        assert conn.execute("SELECT COUNT(*) FROM lib2_track_files WHERE track_id=?",
                            (kept[1],)).fetchone()[0] == 2
        assert len(_album_rows(conn, album)) == 24

    db.active_profile = 2
    assert client.post(f"/api/library/v2/tracks/{leftovers[0]}/remove-leftover").status_code == 403
