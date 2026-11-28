"""tests for Subsonic on-the-fly virtual tracks (#2341)."""

from __future__ import annotations

import time
from pathlib import Path

from flask import Flask, send_file

from api import subsonic as API
from core.connect import device_tokens as T
from core.playlists import user_playlists as up
from core.subsonic import on_the_fly as OTF
from core.subsonic import store as S


class _Cfg:
    def __init__(self, data):
        self.data = data

    def get(self, key, default=None):
        return self.data.get(key, default)

    def _decrypt_value(self, value):
        return value


def _make_app(mdb, cfg_dict, *, downloader=None, ext_search=None):
    from flask import g

    app = Flask(__name__)
    app.secret_key = "test-otf"

    def _serve(path):
        return send_file(path, conditional=True)

    API.configure(
        get_database=lambda: mdb,
        config_manager=_Cfg(cfg_dict),
        serve_file=_serve,
        otf_downloader=downloader,
        otf_search=ext_search,
    )

    @app.before_request
    def _auth():
        dev, refusal = API.authenticate(mdb)
        if refusal is not None:
            return refusal
        if dev is not None:
            g.device_auth = True
            g.device = dev
            g.profile_id = dev["profile_id"]

    app.register_blueprint(API.create_blueprint())
    return app


def _make_auth(mdb):
    pid = mdb.create_profile("Listener")
    with mdb._get_connection() as conn:
        conn.execute("UPDATE profiles SET is_admin = 1 WHERE id = ?", (pid,))
        conn.commit()
    token, did = T.create(
        mdb,
        name="Symfonium",
        platform="android",
        app="Symfonium",
        kind=T.KIND_MUSIC,
        ip="127.0.0.1",
    )
    T.pick_profile(mdb, did, pid)
    S.save_app_password(mdb, did, pid, "apppass123", lambda v: v)
    return pid, token


def test_otf_disabled_by_default_hides_unowned_tracks(mdb):
    cfg = {}
    pid, token = _make_auth(mdb)
    pl_id = up.create_playlist(mdb, pid, "Discover Weekly")
    p = mdb.get_mirrored_playlist(pl_id, profile_id=pid)
    up.add_tracks(
        mdb,
        p,
        [{"name": "Ghost Song", "artist": "Phantom Band", "album": "Mist", "duration_ms": 210000}],
    )
    app = _make_app(mdb, cfg)
    c = app.test_client()

    r = c.get(f"/rest/getPlaylist?id=pl-{pl_id}&f=json&v=1.16.1&c=test&apiKey={token}")
    assert r.status_code == 200
    body = r.get_json()["subsonic-response"]
    assert body["status"] == "ok", body
    assert body["playlist"]["entry"] == []


def test_otf_playlist_search_stream_promote_and_prune(mdb, tmp_path):
    cfg = {
        "music_apps.on_the_fly_enabled": True,
        "music_apps.on_the_fly_ttl_days": 7,
        "music_apps.on_the_fly_promote_wishlist": True,
    }
    pid, token = _make_auth(mdb)

    # grant download rights so wishlist promotion fires
    tmpls = mdb.list_permission_templates()
    if tmpls:
        mdb.set_profile_permission_layers(pid, tmpls[0]["id"], '{"music.download": true}')
        from core import permission_store
        permission_store.recompute(mdb, [pid])

    pl_id = up.create_playlist(mdb, pid, "Discover Weekly")
    p = mdb.get_mirrored_playlist(pl_id, profile_id=pid)
    up.add_tracks(
        mdb,
        p,
        [{"name": "Neon Drift", "artist": "Synth Wave", "album": "Night", "duration_ms": 195000}],
    )

    downloads = []

    def fake_downloader(row, cache_dir):
        downloads.append(row["id"])
        out = Path(cache_dir) / f"otf_{row['id']}.mp3"
        out.write_bytes(b"ID3FAKEAUDIO" * 64)
        return str(out)

    def fake_ext_search(q, limit):
        return [{"title": "Starlight Echo", "artist": "External Band", "album": "Orbit", "duration_ms": 240000}]

    app = _make_app(mdb, cfg, downloader=fake_downloader, ext_search=fake_ext_search)
    c = app.test_client()

    # 1. getPlaylist returns the unowned track with otf-<id>
    r = c.get(f"/rest/getPlaylist?id=pl-{pl_id}&f=json&v=1.16.1&c=test&apiKey={token}")
    entries = r.get_json()["subsonic-response"]["playlist"]["entry"]
    assert len(entries) == 1
    otf_id = entries[0]["id"]
    assert otf_id.startswith("otf-")
    assert entries[0]["title"] == "Neon Drift"

    # 2. search3 returns unowned tracks from mirrored playlists and external search
    sr = c.get(f"/rest/search3?query=Neon&f=json&v=1.16.1&c=test&apiKey={token}")
    found = sr.get_json()["subsonic-response"]["searchResult3"]["song"]
    assert any(s["title"] == "Neon Drift" for s in found)
    assert any(s["title"] == "Starlight Echo" for s in found)

    # 3. first stream downloads on the fly, does not promote yet
    st1 = c.get(f"/rest/stream?id={otf_id}&f=json&v=1.16.1&c=test&apiKey={token}")
    assert st1.status_code == 200
    assert st1.data.startswith(b"ID3FAKEAUDIO")
    assert len(downloads) == 1
    num_id = int(otf_id.split("-")[1])
    row1 = OTF.get_track(mdb, num_id)
    assert row1["play_count"] == 1
    assert row1["promoted"] == 0

    # 4. second stream reuses cached file and promotes to wishlist on replay
    st2 = c.get(f"/rest/stream?id={otf_id}&f=json&v=1.16.1&c=test&apiKey={token}")
    assert st2.status_code == 200
    assert len(downloads) == 1  # cached!
    row2 = OTF.get_track(mdb, num_id)
    assert row2["play_count"] == 2
    assert row2["promoted"] == 1

    # 5. starring another virtual track promotes it immediately
    ext_song = next(s for s in found if s["title"] == "Starlight Echo")
    ext_num = int(ext_song["id"].split("-")[1])
    star_r = c.get(f"/rest/star?id={ext_song['id']}&f=json&v=1.16.1&c=test&apiKey={token}")
    assert star_r.get_json()["subsonic-response"]["status"] == "ok"
    assert OTF.get_track(mdb, ext_num)["promoted"] == 1

    # 6. adding a virtual track to another playlist promotes it too
    third = OTF.register_track(mdb, title="Deep Signal", artist="Radio Wave", album="Pulse")
    pl2_r = c.get(f"/rest/createPlaylist?name=Keepers&songId=otf-{third['id']}&f=json&v=1.16.1&c=test&apiKey={token}")
    assert pl2_r.get_json()["subsonic-response"]["status"] == "ok"
    assert OTF.get_track(mdb, third["id"])["promoted"] == 1

    # 7. TTL pruning deletes expired unpromoted files while keeping promoted ones
    temp_row = OTF.register_track(mdb, title="Transient", artist="One Play", album="Tmp")
    tmp_file = tmp_path / "transient.mp3"
    tmp_file.write_bytes(b"TEMP")
    with mdb._get_connection() as conn:
        conn.execute(
            "UPDATE subsonic_otf_tracks SET file_path = ?, file_size = 4, last_played_at = ? WHERE id = ?",
            (str(tmp_file), time.time() - 10 * 86400, temp_row["id"]),
        )
        conn.commit()
    removed = OTF.prune_expired(mdb, ttl=7)
    assert removed == 1
    assert not tmp_file.exists()
    # promoted track's file stays intact
    assert Path(row2["file_path"]).exists()
