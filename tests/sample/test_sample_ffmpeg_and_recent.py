"""discord report (Specialmed): sample studio's library panel said "search
failed" before any search, and every m4a said "analysis failed".

- the empty panel asked /api/library/recently-added for tracks, but that path
  is the dashboard's album rail and never answered with tracks
- decoding mp3/m4a/opus ran a bare "ffmpeg", which only works where ffmpeg is
  on PATH, not with the copy soulsync keeps in tools/
"""

from __future__ import annotations

import os

import pytest

from core.sample import analyze


def test_ffmpeg_on_path_wins(monkeypatch):
    monkeypatch.setattr(analyze.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert analyze.ffmpeg_bin() == "/usr/bin/ffmpeg"


def test_ffmpeg_falls_back_to_the_tools_copy(monkeypatch):
    monkeypatch.setattr(analyze.shutil, "which", lambda name: None)
    tools = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(analyze.__file__)))), "tools")
    want = os.path.join(tools, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    monkeypatch.setattr(analyze.os.path, "isfile", lambda p: p == want)
    assert analyze.ffmpeg_bin() == want


def test_no_ffmpeg_says_so(monkeypatch):
    monkeypatch.setattr(analyze.shutil, "which", lambda name: None)
    monkeypatch.setattr(analyze.os.path, "isfile", lambda p: False)
    with pytest.raises(RuntimeError, match="ffmpeg isn't installed"):
        analyze.ffmpeg_bin()


def test_lossy_decode_runs_the_found_ffmpeg(monkeypatch, tmp_path):
    song = tmp_path / "song.m4a"
    song.write_bytes(b"x")
    monkeypatch.setattr(analyze, "ffmpeg_bin", lambda: "/opt/soulsync/tools/ffmpeg")
    ran = {}

    class _Proc:
        returncode = 1
        stdout = b""
        stderr = b"nope"

    def fake_run(cmd, **kw):
        ran["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(analyze.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError):
        analyze.decode_mono(str(song))
    assert ran["cmd"][0] == "/opt/soulsync/tools/ffmpeg"


def test_render_decodes_with_the_found_ffmpeg(monkeypatch, tmp_path):
    from core.sample import render

    song = tmp_path / "song.m4a"
    song.write_bytes(b"not audio")
    monkeypatch.setattr(render, "ffmpeg_bin", lambda: "/opt/soulsync/tools/ffmpeg")
    ran = []

    class _Proc:
        returncode = 1
        stdout = b""
        stderr = b"nope"

    monkeypatch.setattr(render.subprocess, "run", lambda cmd, **kw: ran.append(cmd) or _Proc())
    with pytest.raises(RuntimeError):
        render.decode_stereo(str(song))
    with pytest.raises(RuntimeError):
        render.decode_region(str(song), 0, 1)
    assert [cmd[0] for cmd in ran] == ["/opt/soulsync/tools/ffmpeg"] * 2


def test_recent_library_tracks_are_serialized_tracks(monkeypatch):
    import api.sample as sample_api

    asked = {}

    class _DB:
        def api_get_recently_added(self, entity_type, limit):
            asked.update(entity_type=entity_type, limit=limit)
            return [{"id": 7, "title": "ISSA", "artist_name": "Sido", "duration": 144000}]

    monkeypatch.setattr(sample_api, "get_database", lambda: _DB())
    tracks = sample_api.recent_library_tracks(50)
    assert asked == {"entity_type": "tracks", "limit": 50}
    assert tracks[0]["id"] == 7 and tracks[0]["title"] == "ISSA"
