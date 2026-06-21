"""ffprobe-backed media verification — the pure parsing seam.

We trust the FILE over the scene name: real dimensions → resolution, real duration to
catch samples, real codec. ffprobe (the subprocess) is injected, so these run without
ffmpeg installed.
"""

from __future__ import annotations

import json

from core.video.mediainfo import parse_ffprobe, probe, resolution_from_dimensions


def test_resolution_buckets_use_the_long_axis():
    assert resolution_from_dimensions(3840, 2160) == "2160p"
    assert resolution_from_dimensions(1920, 1080) == "1080p"
    # a letterboxed 1080p movie is 1920x800 — must NOT read as 720p by its short side
    assert resolution_from_dimensions(1920, 800) == "1080p"
    assert resolution_from_dimensions(1280, 720) == "720p"
    assert resolution_from_dimensions(720, 480) == "480p"
    assert resolution_from_dimensions(0, 0) is None


def _ffprobe_json(width, height, duration, vcodec="hevc", acodec="eac3"):
    return json.dumps({
        "streams": [
            {"codec_type": "video", "codec_name": vcodec, "width": width, "height": height},
            {"codec_type": "audio", "codec_name": acodec},
        ],
        "format": {"duration": str(duration)},
    })


def test_parse_ffprobe_extracts_real_info():
    info = parse_ffprobe(json.loads(_ffprobe_json(1920, 1080, 7200.0)))
    assert info["ok"] is True
    assert info["resolution"] == "1080p"
    assert info["duration_sec"] == 7200.0
    assert info["video_codec"] == "hevc"
    assert info["audio_codec"] == "eac3"


def test_parse_ffprobe_no_video_stream_is_not_ok():
    data = {"streams": [{"codec_type": "audio", "codec_name": "mp3"}], "format": {"duration": "200"}}
    assert parse_ffprobe(data)["ok"] is False
    assert parse_ffprobe({})["ok"] is False


def test_probe_runs_injected_runner():
    info = probe("/x/movie.mkv", runner=lambda p: _ffprobe_json(3840, 2160, 8000))
    assert info["ok"] and info["resolution"] == "2160p"


def test_probe_returns_none_when_unverifiable():
    assert probe("/x/movie.mkv", runner=lambda p: None) is None          # ffprobe couldn't run
    assert probe("/x/movie.mkv", runner=lambda p: "not json") is None     # garbage output
    def boom(_p):
        raise OSError("ffprobe exploded")
    assert probe("/x/movie.mkv", runner=boom) is None                     # crash → unverified
