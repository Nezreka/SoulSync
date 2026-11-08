"""review 5 R5-X3-3: /api/library/play streams a file that is not on
SoulSync's disk by the active server's own id for the catalogue track, not
by whatever id the page sent (a 3.6.1 plex ratingKey after a move to
navidrome, or a pre-rekey navidrome id)."""

from __future__ import annotations

import pytest

web_server = pytest.importorskip("web_server")

from core.library2.media_mappings import upsert_mapping  # noqa: E402
from database.music_database import MusicDatabase  # noqa: E402
from tests import lib2_seed  # noqa: E402


class _Navidrome:
    asked: list = []

    def build_stream_url(self, song_id, max_bitrate=0):
        self.asked.append(str(song_id))
        return f"http://nav.example/rest/stream?id={song_id}"


@pytest.fixture
def streamed(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    conn = lib2_seed.row_conn(str(tmp_path / "m.db"))
    track = lib2_seed.track(conn, "Artist", "Album", "Song", path="/mnt/plexonly/Song.flac")
    conn.execute("UPDATE lib2_tracks SET legacy_track_id=584278, server_source='plex', "
                 "server_id='584278' WHERE id=?", (track,))
    conn.commit()
    monkeypatch.setattr(web_server, "get_database", lambda: db)
    monkeypatch.setattr(web_server, "_resolve_library_file_path", lambda p: None)
    monkeypatch.setattr(web_server.config_manager, "get_active_media_server", lambda: "navidrome")
    monkeypatch.setattr(web_server.media_server_engine, "client", lambda name: _Navidrome())
    _Navidrome.asked.clear()
    return conn, track


def _play(track, **ids):
    body = {"file_path": "/mnt/plexonly/Song.flac", "title": "Song", "lib2_track_id": track, **ids}
    return web_server.app.test_client().post("/api/library/play", json=body)


def test_play_streams_the_active_servers_own_id_over_a_sent_legacy_id(streamed):
    conn, track = streamed
    upsert_mapping(conn.cursor(), "track", track, "navidrome", "0IOchBY6UeMfO9StCNxMil")
    conn.commit()

    r = _play(track, track_id=584278, legacy_track_id=584278, server_track_id=584278)

    assert r.status_code == 200, r.get_json()
    assert _Navidrome.asked == ["0IOchBY6UeMfO9StCNxMil"]


def test_play_without_a_mapping_still_uses_the_sent_id(streamed):
    _conn, track = streamed

    r = _play(track, track_id=584278, legacy_track_id=584278, server_track_id=584278)

    assert r.status_code == 200, r.get_json()
    assert _Navidrome.asked == ["584278"]
