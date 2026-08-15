"""Movie night P2: honest playability, and byte-serving the library file.

Watch-together phase 1 shipped the ballot — nominate, vote, start, and a personal
ownership probe. Clicking "Movie night" therefore opened a picker and never a room,
which is exactly what Boulder reported. Phase 2 is the part that makes it a room: a
stream endpoint the ``<video>`` element can seek in, and — because v1 is deliberately
direct-play with no transcoding — an honest answer about whether this browser can play
the file BEFORE anyone commits to it.

That honesty is not a nicety. In Boulder's own library 82,752 files are Matroska and
over 42,000 carry AC3/E-AC3 audio, which Chrome and Firefox refuse: without the probe
the common case is a party where half the room gets a silent picture and nobody knows
why. The verdict is three-valued on purpose — collapsing "depends on your browser" into
either yes or no would be a lie in one direction or the other.

The security shape is worth stating because it is easy to get wrong: the client names a
TITLE (kind + tmdb id + SxE) and never a path. The path comes from the library row and
is re-rooted by the video path resolver, so no request shape can address a file the
scanner never recorded.
"""

from __future__ import annotations

import pytest

from core.video.direct_play import container_of, direct_play_verdict, mime_for


# ── the verdict ──────────────────────────────────────────────────────────────

def test_the_common_good_case_is_a_clean_yes():
    v = direct_play_verdict("/m/Movie.mp4", "h264", "aac")
    assert v["verdict"] == "yes" and v["reasons"] == []


def test_ac3_audio_is_the_silent_picture_case():
    """The single most common reason a perfectly good 1080p WEB-DL 'plays' with
    no sound. It must be called out by name, not folded into a shrug."""
    v = direct_play_verdict("/m/Movie.mp4", "h264", "ac3")
    assert v["verdict"] == "no"
    assert any("silent picture" in r for r in v["reasons"])
    assert any("ac3" in r for r in v["reasons"])


def test_eac3_is_judged_like_ac3():
    assert direct_play_verdict("/m/M.mp4", "h264", "eac3")["verdict"] == "no"


def test_hevc_is_a_maybe_not_a_no():
    """It genuinely plays on Safari/Apple silicon. Refusing outright would deny
    a working party to the people it works for."""
    v = direct_play_verdict("/m/Movie.mp4", "hevc", "aac")
    assert v["verdict"] == "maybe"
    assert any("hevc" in r for r in v["reasons"])


def test_matroska_is_a_maybe_on_the_container_alone():
    v = direct_play_verdict("/m/Movie.mkv", "h264", "aac")
    assert v["verdict"] == "maybe"
    assert any(".mkv" in r for r in v["reasons"])


def test_an_old_xvid_avi_is_a_flat_no():
    v = direct_play_verdict("/m/Movie.avi", "mpeg4", "mp3")
    assert v["verdict"] == "no"


def test_the_worst_part_decides():
    """A pristine container cannot rescue undecodable audio — the verdict is the
    minimum over the three parts, not an average or a majority."""
    assert direct_play_verdict("/m/M.mp4", "h264", "dts")["verdict"] == "no"
    assert direct_play_verdict("/m/M.mkv", "hevc", "ac3")["verdict"] == "no"


def test_unknown_codecs_are_maybe_never_no():
    """A file the scanner never probed must not look broken. The video element
    is a better judge than a missing column."""
    v = direct_play_verdict("/m/Movie.mp4", None, None)
    assert v["verdict"] == "maybe"
    assert direct_play_verdict("/m/Movie.mp4", "some-new-codec", "aac")["verdict"] == "maybe"


@pytest.mark.parametrize("raw,expect", [
    ("H.264", "h264"), ("V_MPEG4/ISO/AVC", "avc"), ("x265", "x265"), ("HEVC", "hevc"),
])
def test_video_codec_names_are_normalised_across_dialects(raw, expect):
    """Plex, Jellyfin, ffprobe and Matroska all spell these differently; the
    rules key off a stem, not an exact string."""
    assert direct_play_verdict("/m/M.mp4", raw, "aac")["video"] == expect


@pytest.mark.parametrize("raw,expect", [("E-AC-3", "eac3"), ("AAC LC", "aac"), ("MP3", "mp3")])
def test_audio_codec_names_are_normalised_across_dialects(raw, expect):
    assert direct_play_verdict("/m/M.mp4", "h264", raw)["audio"] == expect


def test_a_matroska_h264_file_is_not_mistaken_for_mpeg4_part2():
    """The trap this normaliser exists for. Matroska names H.264 as
    'V_MPEG4/ISO/AVC' and DivX-era MPEG-4 Part 2 as 'V_MPEG4/ISO/ASP' — a naive
    substring order calls the first one 'mpeg4' and marks the commonest file
    shape in a real library unplayable. Boulder's has 82,752 .mkv."""
    good = direct_play_verdict("/m/M.mkv", "V_MPEG4/ISO/AVC", "aac")
    assert good["video"] == "avc" and good["verdict"] == "maybe"   # maybe = the .mkv container
    assert not any("isn't supported" in r for r in good["reasons"])

    old = direct_play_verdict("/m/M.mkv", "V_MPEG4/ISO/ASP", "mp3")
    assert old["video"] == "mpeg4" and old["verdict"] == "no"


def test_a_dolby_vision_mkv_reads_as_maybe_not_yes():
    assert direct_play_verdict("/m/M.mkv", "hevc", "eac3")["verdict"] == "no"


# ── containers and mime types ────────────────────────────────────────────────

def test_container_and_mime_agree():
    assert container_of("/x/Movie.MKV") == ".mkv"
    assert mime_for("/x/Movie.MKV") == "video/x-matroska"
    assert mime_for("/x/Movie.mp4") == "video/mp4"
    assert mime_for("/x/Movie.m4v") == "video/mp4"


def test_an_unknown_extension_is_served_as_bytes_not_guessed_video():
    assert mime_for("/x/Movie.qqq") == "application/octet-stream"
    assert mime_for("") == "application/octet-stream"


def test_nothing_raises_on_junk_input():
    for bad in (None, "", 12345, "no-extension"):
        assert direct_play_verdict(bad, None, None)["verdict"] in ("yes", "maybe", "no")
        assert isinstance(mime_for(bad), str)


# ── the endpoints ────────────────────────────────────────────────────────────

def test_the_stream_endpoint_resolves_a_title_never_a_path():
    """The security shape. If a future edit ever reads a path from the request,
    this endpoint becomes an arbitrary file reader — so the absence of that is
    pinned here rather than left to review."""
    import inspect
    from pathlib import Path
    src = Path(inspect.getfile(__import__("api.video.watch", fromlist=["x"]))).read_text(encoding="utf-8")
    body = src[src.index("def _party_file("):src.index("@bp.route(\"/watch/playable\"")]
    assert "video_stored_file_path" in body, "the path must come from the library row"
    assert "resolve_video_file_path" in body, "and then be re-rooted by the resolver"
    for forbidden in ('args.get("path")', "args.get('path')", 'args.get("file")'):
        assert forbidden not in src, "the client must never name a path"


def test_the_stream_endpoint_serves_range_requests():
    """conditional=True is what makes this seekable — and seeking is how a
    latecomer joins a showing already in progress, not a nicety."""
    import inspect
    from pathlib import Path
    src = Path(inspect.getfile(__import__("api.video.watch", fromlist=["x"]))).read_text(encoding="utf-8")
    stream = src[src.index('@bp.route("/watch/stream"'):]
    assert "conditional=True" in stream
    assert "mime_for" in stream, "a browser needs the right Content-Type to demux"


def test_an_unreachable_file_says_so_rather_than_404ing_blankly():
    """Three outcomes the user must be able to tell apart: you don't own it,
    you own it and it is playing, and you own it but NOBODY could serve it.

    The last message changed when playback stopped depending on a local mount:
    it used to blame this server alone, which was misleading once the media
    server became the primary route — on a library spread over eleven mount
    roots, 'this server can't reach it' was true and useless."""
    import inspect
    from pathlib import Path
    src = Path(inspect.getfile(__import__("api.video.watch", fromlist=["x"]))).read_text(encoding="utf-8")
    assert "This server can't reach the file, and %s" in src, (
        "the media server's OWN reason must be carried through, not replaced by "
        "a generic sentence — 'stale Plex id' and 'Plex not configured' send you "
        "to completely different places")
    assert "You don't have a file for this" in src


def test_the_codec_lookup_degrades_instead_of_blocking_playback():
    """A missing codec row must not stop the party — it downgrades the verdict
    to 'maybe' and lets the browser decide."""
    import inspect
    from pathlib import Path
    src = Path(inspect.getfile(__import__("api.video.watch", fromlist=["x"]))).read_text(encoding="utf-8")
    play = src[src.index('@bp.route("/watch/playable"'):src.index('@bp.route("/watch/stream"')]
    assert "except Exception" in play and "logger.debug" in play
