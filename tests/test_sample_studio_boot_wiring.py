"""sample studio resolves track files with the same resolver playback uses.

core.sample.worker.configure() was never called at boot, so the worker ran
with no config manager and the shared resolver had no base dirs to walk. a
track whose stored path wasn't literal on disk (docker/plex/navidrome layout)
played fine but failed analysis, peaks, preview, chop and stems with "audio
file not reachable on disk". the tests in tests/sample inject configure()
themselves, which is how the missing wiring hid.
"""

import os
from unittest.mock import patch

import pytest


@pytest.fixture
def ws():
    with patch("web_server.SpotifyClient"), patch("core.tidal_client.TidalClient"):
        import web_server
    return web_server


def test_boot_injects_the_playback_resolver(ws):
    from core.sample import worker as sample_worker

    assert sample_worker._resolve_path_fn is ws._resolve_library_file_path
    assert sample_worker._config_manager is ws.config_manager


def test_sample_source_resolves_where_playback_does(ws, tmp_path, monkeypatch):
    """end to end through the real seam: api.sample -> worker -> web_server
    resolver. the db says "Don't" and disk says "Don’t": only the playback
    resolver's confusable-tolerant scan (#833) finds it, so this fails if
    sample studio falls back to the shared resolver."""
    import api.sample as sample_api
    from core.sample import store as sample_store

    music = tmp_path / "music"
    track = music / "Virtual Mage" / "Aether" / "01 - Don’t Stop.flac"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"x")

    real_get = ws.config_manager.get

    def fake_get(key, default=None):
        if key == "library.music_paths":
            return [str(music)]
        if key in ("soulseek.transfer_path", "soulseek.download_path"):
            return str(tmp_path / "empty")
        return real_get(key, default)

    monkeypatch.setattr(ws.config_manager, "get", fake_get)
    monkeypatch.setattr(ws.media_server_engine, "client", lambda name: None)
    stored = "/mnt/musicBackup/Virtual Mage/Aether/01 - Don't Stop.flac"
    from database.music_database import MusicDatabase
    from core.library_scope import library_scope
    from tests.lib2_seed import track as seed_track

    db = MusicDatabase(str(tmp_path / "catalogue.db"))
    with db._get_connection() as conn:
        track_id = seed_track(conn, "Virtual Mage", "Aether", "Don't Stop", path=stored)
        conn.commit()
    monkeypatch.setattr(sample_api, "get_database", lambda: db)
    monkeypatch.setattr(sample_store, "get_database", lambda: db)
    monkeypatch.setattr("core.library_scope.any_own_library_exists", lambda: True)

    assert not os.path.isfile(stored)
    assert ws._resolve_library_file_path(stored) == str(track)
    with library_scope("shared"):
        assert sample_api._resolve_source_path(track_id, None) == str(track)


def test_web_peaks_route_passes_the_stem(ws, monkeypatch):
    """the session-auth peaks wrapper dropped ?stem=, so a stem's waveform
    drew the full mix."""
    import api.sample as sample_api

    calls = []

    def fake_fetch_peaks(track_id, buckets, stem=None):
        calls.append((track_id, buckets, stem))
        return {"buckets": buckets, "duration_s": 1.0, "min": [], "max": []}, 200

    monkeypatch.setattr(sample_api, "fetch_peaks", fake_fetch_peaks)
    ws.app.config["TESTING"] = True
    client = ws.app.test_client()
    r = client.get("/api/sample/peaks?track_id=7&buckets=100&stem=drums")
    assert r.status_code == 200, r.get_data(as_text=True)
    r = client.get("/api/sample/peaks?track_id=7&buckets=100")
    assert calls == [(7, 100, "drums"), (7, 100, None)]
