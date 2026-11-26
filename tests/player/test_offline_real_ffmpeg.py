"""a real conversion through ffmpeg: a 1080p hevc clip with a text subtitle
and chapters becomes a 720p h264 mp4 that keeps both, and a song becomes
aac at the asked bitrate. skipped where ffmpeg isn't installed."""

import json
import shutil
import subprocess

import pytest

from core.player import offline_jobs as J

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="needs ffmpeg and ffprobe")


def _probe(path):
    out = subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-show_chapters", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _run(cmd):
    subprocess.run(cmd, capture_output=True, check=True, timeout=300)


def test_a_movie_becomes_a_phone_copy_with_subtitles_and_chapters(tmp_path):
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:02,000\nhello\n", encoding="utf-8")
    meta = tmp_path / "m.txt"
    meta.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1500\ntitle=Intro\n"
                    "[CHAPTER]\nTIMEBASE=1/1000\nSTART=1500\nEND=3000\ntitle=Story\n", encoding="utf-8")
    src = tmp_path / "src.mkv"
    _run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24:duration=3",
          "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-i", str(srt), "-i", str(meta),
          "-map", "0:v", "-map", "1:a", "-map", "2:s", "-map_chapters", "3",
          "-c:v", "libx265", "-preset", "ultrafast", "-c:a", "ac3", "-c:s", "srt", str(src)])
    out = tmp_path / "out.mp4"
    job = J.Job(id="t", kind="convert", media="video", quality="phone", out=str(out), item_type="movie",
                item_id="1", input=str(src), duration=3.0,
                video={"video_codec": "hevc", "audio_codec": "ac3", "height": 1080}, text_subs=[0])
    _run(J._video_cmd(FFMPEG, job, str(out)))
    info = _probe(out)
    kinds = {s["codec_type"]: s for s in info["streams"]}
    assert kinds["video"]["codec_name"] == "h264"
    assert int(kinds["video"]["height"]) <= 720
    assert kinds["audio"]["codec_name"] == "aac"
    assert kinds["subtitle"]["codec_name"] == "mov_text"
    assert [c["tags"]["title"] for c in info["chapters"]] == ["Intro", "Story"]


def test_a_song_becomes_aac_at_the_asked_bitrate(tmp_path):
    src = tmp_path / "song.flac"
    _run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=4", str(src)])
    out = tmp_path / "song.m4a"
    _run(J._audio_cmd(FFMPEG, str(src), str(out), "128", ".m4a"))
    audio = _probe(out)["streams"][0]
    assert audio["codec_name"] == "aac"
    assert 90_000 < int(audio["bit_rate"]) < 170_000
