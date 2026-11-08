"""the kids guard's explicit lookup (core/library2/explicit.py)."""

from __future__ import annotations

from core.library2.explicit import library_track_is_explicit
from core.library2.media_mappings import upsert_mapping


def _album(conn, title="Guard Album", explicit=0):
    artist = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES('Guard Artist','Guard Artist')"
    ).lastrowid
    return conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, explicit) "
        "VALUES(?, ?, 'album', ?)", (artist, title, explicit)).lastrowid


def _track(conn, album, title, explicit, **cols):
    names = ["album_id", "title", "explicit", *cols]
    values = [album, title, explicit, *cols.values()]
    return conn.execute(
        f"INSERT INTO lib2_tracks({','.join(names)}) VALUES({','.join('?' * len(values))})",
        values).lastrowid


def _world(conn):
    album = _album(conn)
    explicit = {
        "int_legacy": _track(conn, album, "a", 1, legacy_track_id=595766),
        "text_legacy": _track(conn, album, "b", 1, legacy_track_id="plexKeyAbc"),
        "server": _track(conn, album, "c", 1, server_source="plex", server_id="p-77"),
        "mapped": _track(conn, album, "d", 1),
        "path": _track(conn, album, "e", 1),
    }
    upsert_mapping(conn.cursor(), "track", explicit["mapped"], "navidrome", "nav0IOch")
    conn.execute("INSERT INTO lib2_track_files(track_id, path, is_primary, file_state) "
                 "VALUES(?, '/music/Guard/e.flac', 1, 'active')", (explicit["path"],))
    clean = _track(conn, album, "clean", 0, legacy_track_id=111222)
    conn.commit()
    return explicit, clean


def test_every_identity_the_player_sends_still_finds_its_track(imported_conn):
    """review 5 R5-E-4: the lookup is one indexed query per identity now;
    each identity must still reach the track the old OR reached."""
    explicit, clean = _world(imported_conn)
    check = lambda **kw: library_track_is_explicit(imported_conn, **kw)  # noqa: E731

    assert check(lib2_track_id=explicit["mapped"])
    assert check(lib2_track_id=str(explicit["mapped"]))
    assert check(track_id="595766")
    assert check(track_id=595766)
    assert check(track_id="plexKeyAbc")
    assert check(track_id="p-77")
    assert check(track_id="nav0IOch")
    assert check(file_path="/music/Guard/e.flac")
    assert check(file_path="/other/root/Guard/e.flac")       # re-rooted path tail
    assert not check(track_id="111222")
    assert not check(lib2_track_id=clean)
    assert not check(lib2_track_id="0" + str(explicit["mapped"]))  # not that id's text
    assert not check(track_id="no-such-id")


def test_a_guarded_play_never_scans_the_catalogue(imported_conn):
    """review 5 R5-E-4 / R5-X3-2: CAST(t.id AS TEXT) and
    CAST(t.legacy_track_id AS TEXT) in one OR made every guarded play a
    full scan of lib2_tracks (seconds on a big library)."""
    explicit, _clean = _world(imported_conn)
    seen = []
    imported_conn.set_trace_callback(seen.append)
    try:
        assert library_track_is_explicit(
            imported_conn, file_path="/music/Guard/e.flac",
            lib2_track_id=explicit["mapped"], track_id="595766")
    finally:
        imported_conn.set_trace_callback(None)

    statements = [s for s in seen if s.lstrip().upper().startswith("SELECT")
                  and "sqlite_master" not in s]
    assert statements
    big = ("lib2_tracks", " t", "lib2_track_files", "lib2_media_server_mappings")
    for sql in statements:
        plan = [r[3] for r in imported_conn.execute("EXPLAIN QUERY PLAN " + sql)]
        scans = [p for p in plan if p.startswith("SCAN")
                 and any(p == f"SCAN {name.strip()}" or p.startswith(f"SCAN {name.strip()} ")
                         for name in big)]
        assert not scans, (sql, plan)
