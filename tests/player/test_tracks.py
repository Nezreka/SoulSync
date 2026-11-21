"""a video's tracks: read from the file and the subtitles beside it,
subtitles as WebVTT (made once), and the converter switching audio and
burning in picture subtitles."""

from __future__ import annotations

import pytest
from flask import Flask

from core.player import tracks as K
from core.player import transcode as T
from core.player.playable import Source

PROBE = {"streams": [
    {"index": 0, "codec_type": "video", "codec_name": "h264"},
    {"index": 1, "codec_type": "audio", "codec_name": "eac3", "channels": 6, "channel_layout": "5.1",
     "tags": {"language": "eng", "title": "Surround"}, "disposition": {"default": 1}},
    {"index": 2, "codec_type": "audio", "codec_name": "aac", "channels": 2, "tags": {"language": "jpn"}},
    {"index": 3, "codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "eng"},
     "disposition": {"forced": 1}},
    {"index": 4, "codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle", "tags": {"LANGUAGE": "und"}},
]}


def test_the_probe_becomes_tracks():
    t = K.parse_probe(PROBE)
    assert [(a["index"], a["language"], a["channels"], a["plays"]) for a in t["audio"]] == [
        (0, "eng", 6, False), (1, "jpn", 2, True)]
    assert t["audio"][0]["title"] == "Surround" and t["audio"][0]["default"] is True
    assert [(x["id"], x["kind"], x["language"], x["forced"]) for x in t["subtitles"]] == [
        ("s0", "text", "eng", True), ("s1", "image", None, False)]


def test_subtitle_files_beside_the_video(tmp_path):
    video = tmp_path / "Movie (2020).mkv"
    video.write_bytes(b"")
    for name in ("Movie (2020).en.srt", "Movie (2020).es.forced.srt", "Movie (2020).srt",
                 "Movie (2020) Extras.en.srt", "Other.en.srt", "Movie (2020).nfo"):
        (tmp_path / name).write_text("x")
    found = K.sidecars(str(video))
    assert [(x["language"], x["forced"]) for x in found] == [("en", False), ("es", True), (None, False)]
    assert all(x["external"] and x["kind"] == "text" for x in found)
    assert "path" not in str(K.public({"audio": [], "subtitles": found})["subtitles"])


def test_webvtt_is_made_once(tmp_path, monkeypatch):
    runs = []

    class Done:
        returncode = 0
        stdout = b"WEBVTT\n\n00:00.000 --> 00:01.000\nhello\n"
        stderr = b""

    monkeypatch.setattr(K.subprocess, "run", lambda cmd, **k: runs.append(cmd) or Done())
    a = K.to_vtt("ffmpeg", "/m/a.mkv", 2, ("file", "/m/a.mkv", 1, 1), tmp_path)
    b = K.to_vtt("ffmpeg", "/m/a.mkv", 2, ("file", "/m/a.mkv", 1, 1), tmp_path)
    assert a == b and a.startswith("WEBVTT") and len(runs) == 1
    assert runs[0][runs[0].index("-map") + 1] == "0:s:2"

    class Failed:
        returncode = 1
        stdout = b""
        stderr = b"Subtitle codec 94213 is not supported"

    monkeypatch.setattr(K.subprocess, "run", lambda cmd, **k: Failed())
    with pytest.raises(RuntimeError):
        K.to_vtt("ffmpeg", "/m/b.mkv", 0, "other", tmp_path)


def test_the_converter_switches_audio_and_burns_in_pictures():
    cpu = ("libx264", "CPU", [], "")
    jpn = T.plan("h264", "aac", audio=1)
    cmd = T.command("ffmpeg", "x", jpn, 0, cpu)
    assert "0:a:1?" in cmd and cmd[cmd.index("-c:v") + 1] == "copy" and cmd[cmd.index("-c:a") + 1] == "copy"
    burn = T.plan("h264", "aac", burn=1)
    assert burn.copy_video is False and "drawn in" in burn.reasons[0]
    bcmd = T.command("ffmpeg", "x", burn, 0, cpu)
    graph = bcmd[bcmd.index("-filter_complex") + 1]
    assert graph.startswith("[0:v:0][0:s:1]overlay,") and graph.endswith("[v]")
    assert bcmd[bcmd.index("-map") + 1] == "[v]" and "-vf" not in bcmd


@pytest.fixture
def client(monkeypatch, tmp_path):
    import api.player_tracks as R
    import core.player.playable as P
    src = Source("file", "video/x-matroska", path=str(tmp_path / "a.mkv"), extra={"file_id": 3})
    monkeypatch.setattr(P, "resolve", lambda t, i, part=None: src)
    tracks = K.parse_probe(PROBE)
    tracks["subtitles"].append({"id": "x0", "language": "fr", "kind": "text", "external": True,
                                "path": "/secret/a.fr.srt", "codec": "srt"})
    monkeypatch.setattr(R, "source_tracks", lambda s: (tracks, "/m/a.mkv", "ffmpeg", "k"))
    made = []
    monkeypatch.setattr(K, "to_vtt", lambda ff, source, stream, key: made.append((source, stream)) or "WEBVTT\n")
    app = Flask(__name__)
    app.register_blueprint(R.create_blueprint())
    c = app.test_client()
    c.made = made
    return c


def test_track_routes(client):
    body = client.get("/api/player/tracks/movie/1").get_json()
    assert [a["language"] for a in body["audio"]] == ["eng", "jpn"]
    assert "/secret" not in str(body)
    sub = client.get("/api/player/subtitle/movie/1?sub=s0")
    assert sub.status_code == 200 and sub.mimetype == "text/vtt"
    assert client.get("/api/player/subtitle/movie/1?sub=x0").status_code == 200
    assert client.made == [("/m/a.mkv", 0), ("/secret/a.fr.srt", None)]
    assert client.get("/api/player/subtitle/movie/1?sub=s1").get_json()["error"] == "image"
    assert client.get("/api/player/subtitle/movie/1?sub=s9").status_code == 404
    assert client.get("/api/player/tracks/track/1").status_code == 400
