"""#2126: subtitles that only live inside the video.

thibs searched french subs for Primal and got 'none found': the french track
was inside the file, the subtitle sites had none. the search said nothing
about it. now the manual search reports the tracks the file already has in
that language (sidecar or inside it, and whether it is a picture track that
can't become a .srt), and an untagged inside track whose title names its
language ('Français') counts as that language instead of as nothing.
"""

from __future__ import annotations

import pytest

from api.video.subtitles import tracks_in_file
from core.video.subtitles import existing as ex
from core.video.subtitles.providers.embedded import _stream_language


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    ex.clear_probe_cache()
    monkeypatch.setattr(ex, "_probe_streams", lambda path: [])


@pytest.fixture()
def video(tmp_path):
    p = tmp_path / "Primal.S02E03.mkv"
    p.write_bytes(b"x" * 1024)
    return str(p)


def _stream(codec, lang=None, title="", forced=0):
    tags = {"title": title}
    if lang is not None:
        tags["language"] = lang
    return {"codec_name": codec, "tags": tags, "disposition": {"forced": forced}}


@pytest.mark.parametrize("stream, code", [
    (_stream("subrip", "fre"), "fr"),
    (_stream("subrip", None, "Français"), "fr"),
    (_stream("subrip", "und", "French Forced"), "fr"),
    (_stream("subrip", None, "English SDH"), "en"),
    (_stream("subrip", None, "Commentary"), None),
    (_stream("subrip", "und", ""), None),
])
def test_untagged_track_takes_its_language_from_the_title(stream, code):
    assert _stream_language(stream) == code


def test_an_untagged_french_track_covers_french(video):
    probe = lambda p: [_stream("subrip", None, "Français")]  # noqa: E731
    assert ex.covered(ex.embedded_tracks(video, {}, probe=probe), "fr")


def test_search_reports_what_the_file_already_has(video, monkeypatch):
    monkeypatch.setattr(ex, "_probe_streams", lambda p: [
        _stream("hdmv_pgs_subtitle", "fre"), _stream("subrip", "eng"),
        _stream("subrip", "fre", forced=1)])
    got = tracks_in_file(video, "fr", False, lambda k, d=None: d)
    assert got == [{"language": "fr", "where": "embedded", "forced": False, "hi": False,
                    "codec": "hdmv_pgs_subtitle", "bitmap": True}]
    forced = tracks_in_file(video, "fr", True, lambda k, d=None: d)
    assert [t["codec"] for t in forced] == ["subrip"]
    assert tracks_in_file(video, "de", False, lambda k, d=None: d) == []


def test_no_file_no_tracks(tmp_path):
    assert tracks_in_file(str(tmp_path / "gone.mkv"), "fr", False, lambda k, d=None: d) == []
    assert tracks_in_file(None, "fr", False, lambda k, d=None: d) == []
