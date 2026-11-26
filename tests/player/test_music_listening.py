"""the music page's listening rows: plays as things in the library you can
open, only what the profile may see."""

from core.player import music_listening as ml


def _db(tmp_path):
    from core.library2.schema import ensure_library_v2_schema
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / "m.db"))
    conn = db._get_connection()
    ensure_library_v2_schema(conn)
    return db, conn


def _play(conn, track_id, when, title="x", times=1):
    for i in range(times):
        conn.execute(
            "INSERT INTO listening_history (title, artist, album, played_at, lib2_track_id, track_id, server_source) "
            "VALUES (?, 'a', 'b', datetime('now', ?), ?, ?, 'test')",
            (title, f"{when} minutes", track_id, f"{track_id}-{when}-{i}"))


def test_plays_come_back_as_library_albums_artists_and_tracks(tmp_path):
    from tests import lib2_seed
    db, conn = _db(tmp_path)
    blue = lib2_seed.track(conn, "Miles Davis", "Kind of Blue", "So What")
    blue2 = lib2_seed.track(conn, "Miles Davis", "Kind of Blue", "Blue in Green")
    hell = lib2_seed.track(conn, "Run DMC", "Raising Hell", "Peter Piper")
    wanted = lib2_seed.track(conn, "Run DMC", "Tougher Than Leather", "Mary, Mary", owned=False)
    _play(conn, blue, -5)                       # the newest play
    _play(conn, blue2, -50, times=2)
    _play(conn, hell, -20, times=5)
    _play(conn, wanted, -1)                     # not owned: nothing to open
    _play(conn, None, -2)                       # never matched to the library
    _play(conn, hell, -60 * 24 * 90, times=40)  # ninety days ago: all-time only
    conn.commit()
    conn.close()

    out = ml.read(db, None)
    assert [a["title"] for a in out["recent"]] == ["Kind of Blue", "Raising Hell"]
    assert out["recent"][0]["image_url"].startswith("/api/library/v2/artwork/album/")
    assert [(a["name"], a["plays"]) for a in out["heavy_rotation"]] == [("Run DMC", 5), ("Miles Davis", 3)]
    assert [(a["title"], a["plays"]) for a in out["top_albums"]] == [("Raising Hell", 5), ("Kind of Blue", 3)]
    assert [(t["title"], t["plays"]) for t in out["top_tracks"]][:2] == [("Peter Piper", 5), ("Blue in Green", 2)]
    assert out["top_tracks"][0]["track_id"] == hell
    assert [(a["name"], a["plays"]) for a in out["all_time_artists"]] == [("Run DMC", 45), ("Miles Davis", 3)]
    assert all(a["artist_id"] for a in out["heavy_rotation"])


def test_an_empty_history_is_empty_rows(tmp_path):
    db, conn = _db(tmp_path)
    conn.close()
    assert ml.read(db, None) == {"recent": [], "heavy_rotation": [], "top_albums": [],
                                 "top_tracks": [], "all_time_artists": []}


def test_a_profile_that_hides_explicit_music_gets_rows_without_it(tmp_path):
    from tests import lib2_seed
    db, conn = _db(tmp_path)
    clean = lib2_seed.track(conn, "Miles Davis", "Kind of Blue", "So What")
    rude = lib2_seed.track(conn, "Run DMC", "Raising Hell", "Peter Piper")
    conn.execute("UPDATE lib2_tracks SET explicit=1 WHERE id=?", (rude,))
    _play(conn, clean, -5)
    _play(conn, rude, -3, times=4)
    conn.commit()
    conn.close()
    assert [a["title"] for a in ml.read(db, None)["recent"]] == ["Raising Hell", "Kind of Blue"]
    out = ml.read(db, None, hide_explicit=True)
    assert [a["title"] for a in out["recent"]] == ["Kind of Blue"]
    assert [a["name"] for a in out["heavy_rotation"]] == ["Miles Davis"]
    assert [t["title"] for t in out["top_tracks"]] == ["So What"]
