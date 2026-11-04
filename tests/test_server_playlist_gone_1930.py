"""#1930: a playlist deleted on the server came back only in replace mode.

the smart skip saw the same tracks, all matched, and stopped there. switching
to replace looked like the fix only because a mode change breaks the skip.
the skip now also asks the server whether the playlist is still there.
"""

import hashlib
import threading
from unittest.mock import MagicMock

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def mdb(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _mirror(db):
    return db.mirror_playlist(
        source="spotify", source_playlist_id="p1", name="Mix",
        tracks=[{"track_name": "Song", "artist_name": "Artist", "source_track_id": "sp1"}],
        profile_id=1,
    )


def _run(db, pk, exists, monkeypatch):
    """the real handler with a status that matches the tracks in the same mode,
    so only the server check can stop the skip."""
    from core.automation.handlers.sync_playlist import auto_sync_playlist

    h = hashlib.md5("sp1".encode()).hexdigest()
    status = {"tracks_hash": h, "mirror_tracks_hash": h, "matched_tracks": 1,
              "quality_profile_id": db.get_mirrored_playlist(pk).get("quality_profile_id"),
              "sync_mode": "reconcile"}
    asked = []
    deps = MagicMock()
    deps.get_database.return_value = db
    deps.load_sync_status_file.return_value = {f"auto_mirror_{pk}": status}
    deps.config_manager.get.side_effect = (
        lambda key, default=None: "reconcile" if key == "playlist_sync.mode" else default)
    deps.config_manager.get_active_media_server.return_value = "plex"
    deps.server_playlist_exists = lambda name, profile_id=None: asked.append(name) or exists
    started = threading.Event()
    deps.run_sync_task.side_effect = lambda *a, **k: started.set()
    monkeypatch.setattr("core.sync.library_presence.matched_tracks_still_present",
                        lambda *_a, **_k: True)
    return auto_sync_playlist({"playlist_id": str(pk)}, deps), asked


def test_a_playlist_gone_from_the_server_syncs_again(mdb, monkeypatch):
    pk = _mirror(mdb)

    result, asked = _run(mdb, pk, False, monkeypatch)

    assert result["status"] == "started"
    assert asked == ["Mix"]


def test_a_playlist_still_on_the_server_still_skips(mdb, monkeypatch):
    pk = _mirror(mdb)

    result, _ = _run(mdb, pk, True, monkeypatch)

    assert result["status"] == "skipped"


def test_a_server_that_cant_say_keeps_the_skip(mdb, monkeypatch):
    pk = _mirror(mdb)

    result, _ = _run(mdb, pk, None, monkeypatch)

    assert result["status"] == "skipped"


def test_the_server_check_uses_the_lookup_by_name(monkeypatch):
    import web_server

    client = MagicMock()
    client.get_playlist_by_name.side_effect = lambda name: None if name == "Gone" else object()
    service = MagicMock()
    service._get_active_media_client.return_value = (client, "plex")
    monkeypatch.setattr(web_server, "sync_service", service)

    assert web_server._server_playlist_exists("Gone") is False
    assert web_server._server_playlist_exists("Here") is True
    service._get_active_media_client.return_value = (client, "soulsync")
    assert web_server._server_playlist_exists("Gone") is None
