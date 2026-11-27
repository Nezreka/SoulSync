"""loudness in the player's answers: a song's gains with its playable
answer and on its bytes (for the pre-cache), a queue's at once, a video's
measurement, and the paired app's access to all of it."""

from __future__ import annotations

import json

import pytest
from flask import Flask

from core.player import playable as P
from core.player.playable import Source

LOUD = {"track_gain": -7.31, "track_peak": 0.988, "album_gain": -8.0, "album_peak": 1.0,
        "reference_lufs": -18.0, "source": "tags"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    import api.player_stream as S
    f = tmp_path / "song.flac"
    f.write_bytes(bytes(range(256)) * 8)
    sources = {
        "42": Source("file", "audio/flac", path=str(f), duration=200.0,
                     private={"file_id": 7, "track_id": 42}),
        "43": Source("file", "audio/flac", path=str(f), duration=200.0,
                     private={"file_id": 8, "track_id": 43}),
        "9": Source("file", "video/mp4", path=str(tmp_path / "Film.mp4"), duration=60.0,
                    extra={"file_id": 3, "file": {"size_bytes": 1234}},
                    private={"file_name": "Film.mp4", "size_bytes": 1234}),
    }
    monkeypatch.setattr(P, "resolve", lambda t, i, part=None: sources[i])
    from core.player import transcode as T
    monkeypatch.setattr(T, "ffmpeg_path", lambda: None)

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Db:
        def _get_connection(self):
            return Conn()

    monkeypatch.setattr("database.music_database.get_database", lambda: Db())
    bumped = []
    monkeypatch.setattr("core.loudness.for_file",
                        lambda conn, file_id, path=None, **kw: LOUD if file_id == 7 else None)
    monkeypatch.setattr("core.library2.loudness_worker.bump_track",
                        lambda conn, track_id, file_id: bumped.append((track_id, file_id)))
    monkeypatch.setattr("core.video.loudness.for_source",
                        lambda source: {"integrated_lufs": -27.5, "true_peak_db": -6.0, "lra": 14.0,
                                        "audio_stream": 0} if source.private.get("file_name") else None)
    app = Flask(__name__)
    app.register_blueprint(S.create_blueprint())
    c = app.test_client()
    c.bumped = bumped
    return c


def test_a_song_brings_its_loudness_and_a_song_without_is_queued(client):
    body = client.get("/api/player/playable/track/42").get_json()
    assert body["loudness"] == LOUD
    assert "file_id" not in json.dumps(body) or body.get("file_id") is None
    body = client.get("/api/player/playable/track/43").get_json()
    assert body["loudness"] is None
    assert client.bumped == [(43, 8)]


def test_a_whole_song_fetched_ahead_carries_its_loudness_in_headers(client):
    res = client.get("/api/player/stream/track/42")
    assert res.status_code == 200
    assert res.headers["X-ReplayGain-Track-Gain"] == "-7.31"
    assert res.headers["X-ReplayGain-Album-Peak"] == "1.000000"
    assert res.headers["X-Loudness-Reference"] == "-18.00"
    # a seek (a range) is bytes only
    ranged = client.get("/api/player/stream/track/42", headers={"Range": "bytes=0-9"})
    assert ranged.status_code == 206 and "X-ReplayGain-Track-Gain" not in ranged.headers


def test_a_video_brings_its_measured_loudness(client):
    body = client.get("/api/player/playable/movie/9").get_json()
    assert body["loudness"] == {"integrated_lufs": -27.5, "true_peak_db": -6.0, "lra": 14.0, "audio_stream": 0}
    # its file name and size stay on the server
    assert "Film.mp4" not in json.dumps(body)


def test_a_queue_asks_for_many_songs_at_once(monkeypatch):
    import api.player_tracks as T
    seen = {}

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Db:
        def _get_connection(self):
            return Conn()

    monkeypatch.setattr("database.music_database.get_database", lambda: Db())
    monkeypatch.setattr("core.loudness.for_tracks",
                        lambda conn, ids, **kw: seen.setdefault("ids", ids) and {1: LOUD, 2: None})
    monkeypatch.setattr("core.loudness.reference_lufs", lambda cm=None: -18.0)
    app = Flask(__name__)
    app.register_blueprint(T.create_blueprint())
    c = app.test_client()
    body = c.get("/api/player/loudness?ids=1,2,x").get_json()
    assert seen["ids"] == [1, 2]
    assert body == {"success": True, "reference_lufs": -18.0, "tracks": {"1": LOUD, "2": None}}
    assert c.get("/api/player/loudness").status_code == 400
    assert c.get("/api/player/loudness?ids=" + ",".join(str(i) for i in range(501))).status_code == 400


def test_the_loudness_route_declares_its_access():
    import api.player_tracks as T
    from core.connect.device_access import PLAYBACK, device_access
    from core.permissions import declared_access
    assert device_access(T.loudness) == PLAYBACK
    assert declared_access(T.loudness)[0] == "perm_any"
