"""m3u entries relative to the playlist file.

a container writes /app/Transfer/Artist/song.flac into the m3u, which nothing
reading the same folder over a network share can resolve. with
m3u_export.relative_paths on, an m3u saved into the transfer folder points at
../Artist/song.flac instead, which works from anywhere the folder is mounted.
"""

import os
import types

import pytest

from core.library.m3u_export import build_m3u, relative_m3u_entry, write_library_m3u


def _posix_only():
    if os.sep != "/":
        pytest.skip("posix paths")


def test_entry_is_relative_to_the_playlist_folder():
    _posix_only()
    assert relative_m3u_entry("/app/Transfer/Artist/Singles/song.flac",
                              "/app/Transfer/Playlists") == "../Artist/Singles/song.flac"
    assert relative_m3u_entry("/app/Transfer/Playlists/x.flac",
                              "/app/Transfer/Playlists") == "x.flac"


@pytest.mark.parametrize("track", [
    "/mnt/musicBackup/Artist/song.flac",      # only "/" in common: a media-server path
    "H:\\Music\\Artist\\song.flac",           # windows path on linux
    "Artist/song.flac",                       # not absolute
    "",
])
def test_entry_that_cannot_be_relative_says_so(track):
    _posix_only()
    assert relative_m3u_entry(track, "/app/Transfer/Playlists") is None


def test_build_falls_back_to_base_and_rewrite_for_unrelatable_tracks():
    _posix_only()
    out = build_m3u(
        [{"path": "/app/Transfer/A/one.flac", "title": "one"},
         {"path": "/media/B/two.flac", "title": "two"}],
        rewrite_from="/media", rewrite_to="M:/media",
        relative_to="/app/Transfer/Playlists",
    )
    lines = [ln for ln in out.splitlines() if not ln.startswith("#")]
    assert lines == ["../A/one.flac", "M:/media/B/two.flac"]


def test_build_without_relative_is_unchanged():
    entries = [{"path": "/app/Transfer/A/one.flac"}]
    assert build_m3u(entries) == build_m3u(entries, relative_to="")


def test_library_m3u_entries_resolve_back_to_the_real_files(tmp_path):
    track = tmp_path / "Transfer" / "Artist" / "Album" / "01 - song.flac"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"")
    folder = tmp_path / "Transfer" / "Playlists"

    written = write_library_m3u([{"path": str(track)}], str(folder), relative=True)
    entry = [ln for ln in open(written, encoding="utf-8").read().splitlines()
             if not ln.startswith("#")][0]
    assert not os.path.isabs(entry)
    assert os.path.normpath(os.path.join(folder, entry)) == str(track)

    off = write_library_m3u([{"path": str(track)}], str(folder))
    assert str(track) in open(off, encoding="utf-8").read()


# ---- the real writers in web_server ----

class _Cfg:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)

    def get_active_media_server(self):
        return "plex"


@pytest.fixture
def ws(monkeypatch, tmp_path):
    import web_server

    transfer = tmp_path / "Transfer"
    track = transfer / "Artist" / "Singles" / "song.flac"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"")
    playlists = transfer / "Playlists"

    cfg = _Cfg({
        "m3u_export.enabled": True,
        "m3u_export.relative_paths": True,
        "soulseek.transfer_path": str(transfer),
    })
    monkeypatch.setattr(web_server, "config_manager", cfg)
    monkeypatch.setattr(web_server, "docker_resolve_path", lambda p: p)
    monkeypatch.setattr(web_server, "_compute_m3u_folder", lambda *a, **k: str(playlists))
    real_db = web_server.get_database()

    class _Db:
        # the real db for everything else (the profile gate reads it), only
        # the track lookups are faked
        def __getattr__(self, name):
            return getattr(real_db, name)

        def get_tracks_for_m3u_resolution(self, server_source=None):
            return [{"artist": "Artist", "title": "Song", "file_path": str(track)}]

        def search_tracks(self, artist=None, limit=None, server_source=None):
            return [types.SimpleNamespace(title="Song", file_path=str(track))]

    monkeypatch.setattr(web_server, "get_database", lambda: _Db())
    return types.SimpleNamespace(mod=web_server, cfg=cfg, track=track, playlists=playlists)


def _client(ws):
    client = ws.mod.app.test_client()
    with client.session_transaction() as sess:
        sess.setdefault("profile_id", 1)
    return client


def _entries(text):
    return [ln for ln in text.splitlines() if ln and not ln.startswith("#")]


def test_saved_playlist_m3u_uses_relative_entries(ws):
    client = _client(ws)
    resp = client.post("/api/generate-playlist-m3u", json={
        "playlist_name": "Mix", "save_to_disk": True, "force": True,
        "tracks": [{"name": "Song", "artist": "Artist", "duration_ms": 1000}],
    })
    body = resp.get_json()
    assert body.get("success"), (resp.status_code, body)
    saved = open(body["path"], encoding="utf-8").read()
    assert _entries(saved) == ["../Artist/Singles/song.flac"]
    assert body["m3u_content"] == saved


def test_content_only_export_stays_absolute(ws):
    # no file on disk = nothing to be relative to
    client = _client(ws)
    body = client.post("/api/generate-playlist-m3u", json={
        "playlist_name": "Mix",
        "tracks": [{"name": "Song", "artist": "Artist", "duration_ms": 1000}],
    }).get_json()
    assert _entries(body["m3u_content"]) == [str(ws.track).replace("\\", "/")]


def test_setting_off_keeps_full_paths(ws):
    ws.cfg.values["m3u_export.relative_paths"] = False
    client = _client(ws)
    body = client.post("/api/generate-playlist-m3u", json={
        "playlist_name": "Mix", "save_to_disk": True, "force": True,
        "tracks": [{"name": "Song", "artist": "Artist", "duration_ms": 1000}],
    }).get_json()
    assert _entries(body["m3u_content"]) == [str(ws.track).replace("\\", "/")]


def test_batch_complete_regen_uses_relative_entries(ws):
    ws.mod._regenerate_batch_m3u(
        {"playlist_name": "Mix"},
        [{"name": "Song", "artist": "Artist", "duration_ms": 1000}],
    )
    saved = open(ws.playlists / "Mix.m3u", encoding="utf-8").read()
    assert _entries(saved) == ["../Artist/Singles/song.flac"]


def test_scan_sync_library_m3u_passes_the_setting():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "api" / "database_admin.py").read_text(encoding="utf-8")
    assert "relative=bool(config_manager.get('m3u_export.relative_paths', False))" in src


def test_settings_ui_round_trip_wiring():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    js = (root / "webui" / "static" / "settings.js").read_text(encoding="utf-8")
    html = (root / "webui" / "index.html").read_text(encoding="utf-8")
    assert 'id="m3u-relative-paths"' in html
    assert "relative_paths: _cfgBool('m3u-relative-paths')" in js
    assert "settings.m3u_export?.relative_paths === true" in js
