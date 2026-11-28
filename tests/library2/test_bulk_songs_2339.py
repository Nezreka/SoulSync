"""#2339: cross-page song selection (/api/library/v2/songs/ids) and bulk song
deletion (/api/library/v2/songs/bulk-delete) with automatic sidecar, empty
folder, empty album and empty artist cleanup."""

from __future__ import annotations

import pytest
from flask import Flask

from database.music_database import MusicDatabase
from tests import lib2_seed


@pytest.fixture()
def app_and_db(tmp_path, monkeypatch):
    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    db = MusicDatabase(str(tmp_path / "music.db"))

    import api.library_v2 as lib2_api
    from core.settings import config_manager
    orig_get = config_manager.get
    monkeypatch.setattr(
        config_manager, "get",
        lambda key, default=None: str(transfer) if key == "soulseek.transfer_path" else orig_get(key, default),
    )
    monkeypatch.setattr("database.music_database.get_database", lambda: db)
    monkeypatch.setattr("core.library2.library_roots.configured_roots", lambda _db=None: {str(transfer): 0})

    app = Flask(__name__)
    lib2_api.register_library_v2_routes(
        app,
        get_database=lambda: db,
        config_get=lambda key, default=None: str(transfer) if key == "soulseek.transfer_path" else default,
    )

    @app.before_request
    def _admin():
        from flask import g
        g.profile_id = 1
        g.is_admin = True
        g.can_download = True

    return app.test_client(), db, transfer


def test_songs_ids_endpoint_matches_filters_2339(app_and_db):
    c, db, transfer = app_and_db
    f1 = transfer / "Alpha" / "Album A" / "01 - Alpha Song.flac"
    f2 = transfer / "Beta" / "Album B" / "01 - Beta Song.mp3"
    f1.parent.mkdir(parents=True)
    f2.parent.mkdir(parents=True)
    f1.write_bytes(b"flac")
    f2.write_bytes(b"mp3")

    with db._get_connection() as conn:
        t1 = lib2_seed.track(conn, "Alpha", "Album A", "Alpha Song", path=str(f1))
        t2 = lib2_seed.track(conn, "Beta", "Album B", "Beta Song", path=str(f2))
        conn.commit()

    r_all = c.get("/api/library/v2/songs/ids").get_json()
    assert r_all["success"] is True
    assert set(r_all["ids"]) == {t1, t2}
    assert r_all["total"] == 2

    r_lossless = c.get("/api/library/v2/songs/ids?quality=lossless").get_json()
    assert r_lossless["ids"] == [t1]

    r_letter = c.get("/api/library/v2/songs/ids?letter=b").get_json()
    assert r_letter["ids"] == [t2]


def test_bulk_delete_songs_cleans_sidecars_empty_folders_albums_and_artists_2339(app_and_db):
    c, db, transfer = app_and_db
    # Artist 1 has 1 album with 2 songs + cover.jpg + lyrics.lrc -> delete BOTH -> album, artist, folder, sidecars all gone
    a1_dir = transfer / "Doomed Artist" / "Only Album"
    a1_dir.mkdir(parents=True)
    s1 = a1_dir / "01 - One.mp3"
    s2 = a1_dir / "02 - Two.mp3"
    cover = a1_dir / "cover.jpg"
    lrc = a1_dir / "01 - One.lrc"
    for p in (s1, s2, cover, lrc):
        p.write_bytes(b"data")

    # Artist 2 has 1 album with 2 songs -> delete ONLY ONE -> album, artist, folder, sidecars STAY
    a2_dir = transfer / "Kept Artist" / "Partial Album"
    a2_dir.mkdir(parents=True)
    s3 = a2_dir / "01 - Gone.mp3"
    s4 = a2_dir / "02 - Stays.mp3"
    a2_cover = a2_dir / "cover.jpg"
    for p in (s3, s4, a2_cover):
        p.write_bytes(b"data")

    with db._get_connection() as conn:
        t1 = lib2_seed.track(conn, "Doomed Artist", "Only Album", "One", path=str(s1))
        t2 = lib2_seed.track(conn, "Doomed Artist", "Only Album", "Two", path=str(s2))
        t3 = lib2_seed.track(conn, "Kept Artist", "Partial Album", "Gone", path=str(s3))
        t4 = lib2_seed.track(conn, "Kept Artist", "Partial Album", "Stays", path=str(s4))
        conn.commit()

    r = c.post("/api/library/v2/songs/bulk-delete", json={
        "ids": [t1, t2, t3],
        "delete_files": True,
        "blacklist": True,
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert body["deleted_count"] == 3
    assert body["files_deleted"] == 3
    assert body["albums_removed"] == 1
    assert body["artists_removed"] == 1
    assert body["folders_removed"] >= 2  # album folder + artist folder
    assert body["blacklisted"] == 3

    # Doomed Artist folder and sidecars are gone; Transfer root stays
    assert not a1_dir.exists()
    assert not (transfer / "Doomed Artist").exists()
    assert transfer.exists()

    # Kept Artist's remaining track, album, folder and cover.jpg stay untouched
    assert s4.exists()
    assert a2_cover.exists()
    assert not s3.exists()
    with db._get_connection() as conn:
        artists = [r[0] for r in conn.execute("SELECT name FROM lib2_artists").fetchall()]
        albums = [r[0] for r in conn.execute("SELECT title FROM lib2_albums").fetchall()]
        assert artists == ["Kept Artist"]
        assert albums == ["Partial Album"]
        assert conn.execute("SELECT COUNT(*) FROM lib2_tracks WHERE id=?", (t4,)).fetchone()[0] == 1
