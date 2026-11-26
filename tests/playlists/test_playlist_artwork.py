"""playlist artwork on every playlist type (#2279, #2280, #2281).

mirrors, user playlists and request lists are all rows of mirrored_playlists.
image_url is what shows; source_image_url is the source's own poster, kept
current so restore always has it; custom_cover means the user picked
image_url and a refresh must leave it alone.

the parts that would quietly break: a refresh echoing a custom cover back as
the "original", restore forgetting the original, a request-list copy losing
its cover when the parent goes, and a restored cover never reaching the
media server because it isn't custom any more.
"""

import io
import os
import sqlite3
import types

import pytest
from flask import Flask, g

from core.playlists import user_playlists as up
from database.music_database import MusicDatabase


@pytest.fixture()
def mdb(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


TRACKS = [{"track_name": "One", "artist_name": "Artist", "source_track_id": "t1"}]


def _mirror(db, image="https://src/a.jpg", **kw):
    return db.mirror_playlist("spotify", "sp-1", "Night Drive", TRACKS, profile_id=1,
                              image_url=image, **kw)


def _row(db, pid):
    return db.get_mirrored_playlist(pid)


def _png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (80, 80), (10, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


# ── the source's poster is kept (#2279) ──────────────────────────────────────


def test_new_mirror_keeps_the_source_poster_as_the_original(mdb):
    pid = _mirror(mdb)
    row = _row(mdb, pid)
    assert row["image_url"] == "https://src/a.jpg"
    assert row["source_image_url"] == "https://src/a.jpg"
    assert row["custom_cover"] is False


def test_a_local_cover_is_never_the_original(mdb):
    pid = _mirror(mdb, image="/api/user-playlists/9/cover?v=1")
    assert _row(mdb, pid)["source_image_url"] is None


def test_refresh_with_a_new_poster_updates_cover_and_original(mdb):
    pid = _mirror(mdb)
    _mirror(mdb, image="https://src/b.jpg")
    row = _row(mdb, pid)
    assert row["image_url"] == "https://src/b.jpg"
    assert row["source_image_url"] == "https://src/b.jpg"


def test_refresh_under_a_custom_cover_updates_only_the_original(mdb):
    pid = _mirror(mdb)
    up.set_cover_url(mdb, _row(mdb, pid), "https://mine/x.jpg")
    _mirror(mdb, image="https://src/b.jpg")
    row = _row(mdb, pid)
    assert row["image_url"] == "https://mine/x.jpg"
    assert row["custom_cover"] is True
    assert row["source_image_url"] == "https://src/b.jpg"


def test_an_echoed_custom_cover_never_becomes_the_original(mdb):
    """a caller that hands the row's own image back isn't the source talking."""
    pid = _mirror(mdb)
    up.set_cover_url(mdb, _row(mdb, pid), "https://mine/x.jpg")
    _mirror(mdb, image="https://mine/x.jpg")
    assert _row(mdb, pid)["source_image_url"] == "https://src/a.jpg"


def test_refresh_without_a_poster_keeps_both(mdb):
    pid = _mirror(mdb)
    _mirror(mdb, image=None)
    row = _row(mdb, pid)
    assert row["image_url"] == "https://src/a.jpg"
    assert row["source_image_url"] == "https://src/a.jpg"


def test_refresh_handler_hands_over_the_sources_current_poster(mdb, monkeypatch):
    from core.automation.handlers import refresh_mirrored as rm
    pid = _mirror(mdb)
    pl = _row(mdb, pid)
    from unittest.mock import MagicMock
    deps = MagicMock()
    monkeypatch.setattr(rm.auto_identify, "request_if_unmatched", lambda *a, **k: None,
                        raising=False)
    rm._commit_refresh(pl, "spotify", "sp-1", list(TRACKS), mdb, deps, None,
                       {"source_image_url": "https://src/new.jpg"}, identify=False)
    row = _row(mdb, pid)
    assert row["image_url"] == "https://src/new.jpg"
    assert row["source_image_url"] == "https://src/new.jpg"


def test_spotify_playlist_carries_its_image():
    from core.spotify_client import Playlist
    pl = Playlist.from_spotify_playlist({
        "id": "p", "name": "P", "owner": {"display_name": "o"}, "public": True,
        "collaborative": False, "tracks": {"total": 3},
        "images": [{"url": "https://i.scdn.co/big.jpg"}, {"url": "https://i.scdn.co/small.jpg"}],
    }, [])
    assert pl.image_url == "https://i.scdn.co/big.jpg"
    no_art = Playlist.from_spotify_playlist({
        "id": "p", "name": "P", "owner": {"display_name": "o"}, "public": True,
        "collaborative": False, "images": None,
    }, [])
    assert no_art.image_url is None


def test_adapters_put_the_poster_in_their_meta():
    from core.playlists.sources.deezer import DeezerPlaylistSource as DeezerSource
    from core.playlists.sources.spotify import SpotifyPlaylistSource as SpotifySource
    from core.playlists.sources.tidal import TidalPlaylistSource as TidalSource
    pl = types.SimpleNamespace(id="1", name="N", owner="o", description=None, total_tracks=0,
                               public=True, collaborative=False, tracks=[],
                               image_url="https://img/p.jpg", external_urls={})
    for cls in (SpotifySource, TidalSource, DeezerSource):
        meta = cls.__new__(cls)._meta_from_playlist(pl)
        assert meta.image_url == "https://img/p.jpg", cls.__name__


# ── a cover of your own on any playlist (#2280) ──────────────────────────────


def test_a_mirror_takes_an_uploaded_cover(mdb, tmp_path):
    pid = _mirror(mdb)
    f = types.SimpleNamespace(read=lambda n: _png())
    url = up.set_cover_upload(mdb, _row(mdb, pid), f, base_dir=str(tmp_path))
    row = _row(mdb, pid)
    assert row["image_url"] == url
    assert row["custom_cover"] is True
    assert row["source_image_url"] == "https://src/a.jpg"
    assert os.path.isfile(up.cover_path(pid, str(tmp_path), mkdir=False))


def test_a_track_cover_on_a_mirror_is_custom(mdb):
    pid = mdb.mirror_playlist("spotify", "sp-1", "N",
                              [dict(TRACKS[0], image_url="https://alb/1.jpg")],
                              profile_id=1, image_url="https://src/a.jpg")
    up.set_cover_from_track(mdb, _row(mdb, pid), 1)
    row = _row(mdb, pid)
    assert row["image_url"] == "https://alb/1.jpg"
    assert row["custom_cover"] is True


def _client(db, monkeypatch, tmp_path, profile_id=1):
    import api.user_playlists as mod
    monkeypatch.setattr(mod, "get_database", lambda: db)
    monkeypatch.setattr(mod, "get_current_profile_id", lambda: profile_id)
    monkeypatch.setattr(mod, "config_manager", types.SimpleNamespace(base_dir=str(tmp_path)))
    app = Flask(__name__)

    @app.before_request
    def _stamp():
        g.is_admin = True

    app.register_blueprint(mod.create_blueprint())
    return app.test_client()


def test_cover_endpoints_work_on_a_mirror_and_a_request_list(mdb, monkeypatch, tmp_path):
    c = _client(mdb, monkeypatch, tmp_path)
    pid = _mirror(mdb)
    req = mdb.copy_to_request_list(pid, profile_id=1)
    for target in (pid, req):
        r = c.put(f"/api/user-playlists/{target}/cover", json={"image_url": "https://mine/y.jpg"})
        assert r.status_code == 200, (target, r.data)
        r = c.post(f"/api/user-playlists/{target}/cover",
                   data={"file": (io.BytesIO(_png()), "c.png")},
                   content_type="multipart/form-data")
        assert r.status_code == 200, (target, r.data)
        assert c.get(f"/api/user-playlists/{target}/cover").status_code == 200
        # DELETE is restore: back to the source's poster, the file goes
        assert c.delete(f"/api/user-playlists/{target}/cover").status_code == 200
        row = _row(mdb, target)
        assert row["image_url"] == "https://src/a.jpg"
        assert row["custom_cover"] is False
        assert c.get(f"/api/user-playlists/{target}/cover").status_code == 404


def test_cover_endpoints_stay_profile_scoped(mdb, monkeypatch, tmp_path):
    pid = _mirror(mdb)
    c = _client(mdb, monkeypatch, tmp_path, profile_id=2)
    r = c.put(f"/api/user-playlists/{pid}/cover", json={"image_url": "https://mine/y.jpg"})
    assert r.status_code == 404
    assert _row(mdb, pid)["image_url"] == "https://src/a.jpg"


# ── restore (#2281) ──────────────────────────────────────────────────────────


def test_restore_goes_back_and_keeps_the_original(mdb, tmp_path):
    pid = _mirror(mdb)
    up.set_cover_url(mdb, _row(mdb, pid), "https://mine/x.jpg")
    up.clear_cover(mdb, _row(mdb, pid), base_dir=str(tmp_path))
    row = _row(mdb, pid)
    assert row["image_url"] == "https://src/a.jpg"
    assert row["source_image_url"] == "https://src/a.jpg"
    assert row["custom_cover"] is False
    # and again: the original is still there for a second round
    up.set_cover_url(mdb, _row(mdb, pid), "https://mine/z.jpg")
    up.clear_cover(mdb, _row(mdb, pid), base_dir=str(tmp_path))
    assert _row(mdb, pid)["image_url"] == "https://src/a.jpg"


def test_restore_with_no_original_falls_back_to_the_collage(mdb, tmp_path):
    pid = up.create_playlist(mdb, 1, "Mine")
    up.set_cover_url(mdb, _row(mdb, pid), "https://mine/x.jpg")
    up.clear_cover(mdb, _row(mdb, pid), base_dir=str(tmp_path))
    assert _row(mdb, pid)["image_url"] is None


def test_a_cover_change_waits_for_the_media_server(mdb, tmp_path):
    pid = _mirror(mdb)
    assert not _row(mdb, pid)["cover_push_pending"]
    up.set_cover_url(mdb, _row(mdb, pid), "https://mine/x.jpg")
    assert _row(mdb, pid)["cover_push_pending"]
    mdb.clear_mirrored_playlist_cover_push_pending(pid)
    up.clear_cover(mdb, _row(mdb, pid), base_dir=str(tmp_path))
    # restored: not custom any more, but the server still has the custom one
    row = _row(mdb, pid)
    assert not row["custom_cover"]
    assert row["cover_push_pending"]


def test_sync_pushes_a_restored_cover_then_clears_the_flag(mdb, monkeypatch, tmp_path):
    from core.discovery import sync as ds
    pid = _mirror(mdb)
    up.set_cover_url(mdb, _row(mdb, pid), "https://mine/x.jpg")
    up.clear_cover(mdb, _row(mdb, pid), base_dir=str(tmp_path))
    monkeypatch.setattr("database.music_database.get_database", lambda: mdb)
    assert ds._sync_cover_is_users(f"auto_mirror_{pid}", 1) is True
    # the row's cover wins over whatever the caller passed
    assert ds._sync_cover_row_image(f"auto_mirror_{pid}", 1, "https://other.jpg") == "https://src/a.jpg"
    ds._sync_cover_pushed(f"auto_mirror_{pid}")
    assert ds._sync_cover_is_users(f"auto_mirror_{pid}", 1) is False


# ── request lists keep their cover ───────────────────────────────────────────


def test_request_list_copy_keeps_cover_state(mdb):
    pid = _mirror(mdb)
    up.set_cover_url(mdb, _row(mdb, pid), "https://mine/x.jpg")
    req = mdb.copy_to_request_list(pid, profile_id=1)
    row = _row(mdb, req)
    assert row["image_url"] == "https://mine/x.jpg"
    assert row["custom_cover"] is True
    assert row["source_image_url"] == "https://src/a.jpg"


def test_request_list_copy_gets_its_own_cover_file(mdb, tmp_path):
    pid = _mirror(mdb)
    f = types.SimpleNamespace(read=lambda n: _png())
    up.set_cover_upload(mdb, _row(mdb, pid), f, base_dir=str(tmp_path))
    req = mdb.copy_to_request_list(pid, profile_id=1)
    up.carry_cover_file(mdb, pid, _row(mdb, req), base_dir=str(tmp_path))
    row = _row(mdb, req)
    assert row["image_url"].startswith(f"/api/user-playlists/{req}/cover")
    assert row["custom_cover"] is True
    assert os.path.isfile(up.cover_path(req, str(tmp_path), mkdir=False))
    # the parent going back to its original leaves the copy's cover alone
    up.clear_cover(mdb, _row(mdb, pid), base_dir=str(tmp_path))
    assert os.path.isfile(up.cover_path(req, str(tmp_path), mkdir=False))


def test_request_list_copy_with_a_missing_file_restores(mdb, tmp_path):
    pid = _mirror(mdb)
    mdb.set_mirrored_playlist_custom_cover(pid, f"/api/user-playlists/{pid}/cover?v=1")
    req = mdb.copy_to_request_list(pid, profile_id=1)
    up.carry_cover_file(mdb, pid, _row(mdb, req), base_dir=str(tmp_path))
    assert _row(mdb, req)["image_url"] == "https://src/a.jpg"


# ── existing rows ────────────────────────────────────────────────────────────


def test_old_mirrors_get_their_original_backfilled(tmp_path):
    path = str(tmp_path / "music.db")
    db = MusicDatabase(database_path=path)
    pid = _mirror(db)
    uid = up.create_playlist(db, 1, "Mine")
    conn = sqlite3.connect(path)
    conn.execute("UPDATE mirrored_playlists SET source_image_url = NULL")
    conn.execute("UPDATE mirrored_playlists SET image_url = 'https://picked/u.jpg' WHERE id = ?", (uid,))
    conn.commit()
    conn.close()
    # setup runs once per path per process: a restart is a fresh process
    from database import music_database as mdbmod
    mdbmod._database_initialized_paths.discard(str(db.database_path.resolve()))
    db = MusicDatabase(database_path=path)
    assert _row(db, pid)["source_image_url"] == "https://src/a.jpg"
    # a user playlist's old cover was the user's pick, not an original
    assert _row(db, uid)["source_image_url"] is None
