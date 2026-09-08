"""Tests for core/podcast_post_processor.py — in-file tag embedding & media server sidecars."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from mutagen.id3 import ID3
from mutagen.flac import FLAC

from core.podcast_client import PodcastEpisode
from core.podcast_post_processor import (
    build_podcast_episode_json,
    build_podcast_episode_nfo,
    build_podcast_show_json,
    build_podcast_show_nfo,
    embed_podcast_tags,
    ensure_podcast_show_assets,
    fetch_artwork_bytes,
    find_show_dir,
    post_process_podcast_episode,
    strip_html,
    write_podcast_episode_sidecars,
)

_HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _create_dummy_mp3(path: Path) -> None:
    """Create a minimal playable MP3 file using ffmpeg."""
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            "0.1",
            str(path),
        ],
        check=True,
    )


def _create_dummy_flac(path: Path) -> None:
    """Create a minimal playable FLAC file using ffmpeg."""
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            "0.1",
            str(path),
        ],
        check=True,
    )


# ---------------------------------------------------------------------------
# Unit Tests: Pure Helpers
# ---------------------------------------------------------------------------

def test_strip_html():
    assert strip_html(None) == ""
    assert strip_html("") == ""
    raw = "<p>Welcome to <b>The Show</b>!<br>Visit <a href='https://example.com'>our site</a>.</p>"
    cleaned = strip_html(raw)
    assert "<b>" not in cleaned
    assert "<a" not in cleaned
    assert "Welcome to The Show!" in cleaned
    assert "our site." in cleaned

    # Entities
    assert strip_html("Rock &amp; Roll &quot;Quotes&#39;") == "Rock & Roll \"Quotes'"


# ---------------------------------------------------------------------------
# Unit Tests: XML / NFO Builders
# ---------------------------------------------------------------------------

def test_build_podcast_episode_nfo():
    ep = PodcastEpisode(
        guid="test-guid-123",
        title="Episode 42: The & Answer",
        enclosure_url="https://cdn.example.com/ep42.mp3",
        enclosure_type="audio/mpeg",
        enclosure_length=1234567,
        pub_date=datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc),
        duration_seconds=3600,
        description="<p>An amazing & deep discussion.</p>",
        show_notes="",
        season=2,
        episode_number=42,
        episode_type="full",
        artwork_url="https://cdn.example.com/ep42.jpg",
        chapter_url=None,
        transcript_url=None,
        show_title="Universe Podcast",
        author="Douglas Adams",
    )

    nfo = build_podcast_episode_nfo(ep)
    assert '<?xml version="1.0" encoding="UTF-8"?>' in nfo
    assert "<episodedetails>" in nfo
    assert "<title>Episode 42: The &amp; Answer</title>" in nfo
    assert "<showtitle>Universe Podcast</showtitle>" in nfo
    assert "<season>2</season>" in nfo
    assert "<episode>42</episode>" in nfo
    assert "<plot>An amazing &amp; deep discussion.</plot>" in nfo
    assert "<aired>2026-03-15</aired>" in nfo
    assert "<runtime>60</runtime>" in nfo
    assert "<studio>Douglas Adams</studio>" in nfo
    assert '<uniqueid type="guid" default="true">test-guid-123</uniqueid>' in nfo
    assert "</episodedetails>" in nfo


def test_build_podcast_show_nfo():
    show_meta = {
        "title": "History & Lore",
        "author": "Dan Carlin",
        "description": "<p>A detailed narrative & analysis of history.</p>",
        "premiered": "2006-10-29",
        "categories": ["Podcasts", "History", "Society & Culture"],
        "feed_url": "https://feeds.example.com/history.xml",
        "itunes_id": 123456789,
    }

    nfo = build_podcast_show_nfo(show_meta)
    assert '<?xml version="1.0" encoding="UTF-8"?>' in nfo
    assert "<tvshow>" in nfo
    assert "<title>History &amp; Lore</title>" in nfo
    assert "<plot>A detailed narrative &amp; analysis of history.</plot>" in nfo
    assert "<studio>Dan Carlin</studio>" in nfo
    assert "<premiered>2006-10-29</premiered>" in nfo
    # "Podcasts" noise category stripped
    assert "<genre>Podcasts</genre>" not in nfo
    assert "<genre>History</genre>" in nfo
    assert "<genre>Society &amp; Culture</genre>" in nfo
    assert '<uniqueid type="feed" default="true">https://feeds.example.com/history.xml</uniqueid>' in nfo
    assert '<uniqueid type="itunes">123456789</uniqueid>' in nfo
    assert "</tvshow>" in nfo


# ---------------------------------------------------------------------------
# Unit Tests: JSON Builders
# ---------------------------------------------------------------------------

def test_build_podcast_episode_json():
    ep = PodcastEpisode(
        guid="ep-999",
        title="Breaking News",
        enclosure_url="https://cdn.example.com/audio.mp3",
        enclosure_type="audio/mpeg",
        enclosure_length=500000,
        pub_date=datetime(2026, 4, 1, 8, 30, tzinfo=timezone.utc),
        duration_seconds=1800,
        description="Daily roundup",
        show_notes="<p>Full show notes with links</p>",
        season=1,
        episode_number=10,
        episode_type="full",
        artwork_url="https://cdn.example.com/art.jpg",
        chapter_url="https://cdn.example.com/chapters.json",
        transcript_url=None,
        show_title="Morning News",
        author="Reporter Jane",
    )

    data = build_podcast_episode_json(ep, {"categories": ["News"]})
    assert data["id"] == "ep-999"
    assert data["title"] == "Breaking News"
    assert data["show"] == "Morning News"
    assert data["author"] == "Reporter Jane"
    assert data["duration"] == 1800
    assert data["season"] == 1
    assert data["episode_number"] == 10
    assert data["pub_date"] == "2026-04-01T08:30:00+00:00"
    assert data["release_year"] == "2026"
    assert data["chapter_url"] == "https://cdn.example.com/chapters.json"
    assert data["categories"] == ["News"]


def test_build_podcast_show_json():
    show_meta = {
        "title": "Tech Talk",
        "author": "Tech Corp",
        "description": "Tech discussions",
        "feed_url": "https://example.com/rss",
        "itunes_id": 4567,
        "language": "en",
        "explicit": False,
        "categories": ["Technology"],
        "episode_count": 120,
    }

    data = build_podcast_show_json(show_meta)
    assert data["title"] == "Tech Talk"
    assert data["author"] == "Tech Corp"
    assert data["feed_url"] == "https://example.com/rss"
    assert data["itunes_id"] == 4567
    assert data["episode_count"] == 120


# ---------------------------------------------------------------------------
# Unit Tests: Show Directory Resolution
# ---------------------------------------------------------------------------

def test_find_show_dir(tmp_path: Path):
    dest = tmp_path / "Podcasts"
    show_dir = dest / "Hardcore History"
    season_dir = show_dir / "Season 01"
    season_dir.mkdir(parents=True)
    audio_file = season_dir / "Episode 1.mp3"
    audio_file.write_text("test")

    found = find_show_dir(audio_file, "Hardcore History", dest_root=dest)
    assert found == show_dir

    # Flat layout: dest / "Episode 1.mp3" -> direct parent
    flat_file = dest / "Episode 1.mp3"
    flat_found = find_show_dir(flat_file, "Hardcore History", dest_root=dest)
    assert flat_found == dest


# ---------------------------------------------------------------------------
# Unit Tests: Sidecar Writers
# ---------------------------------------------------------------------------

def test_ensure_podcast_show_assets(tmp_path: Path):
    show_dir = tmp_path / "My Podcast"
    show_dir.mkdir()

    dummy_art = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 100
    meta = {
        "title": "My Podcast",
        "author": "Host",
        "description": "About my podcast",
        "feed_url": "https://example.com/rss",
    }
    settings = {"save_artwork": True, "write_nfo": True, "write_json": True}

    created = ensure_podcast_show_assets(show_dir, meta, settings, art_bytes=dummy_art)
    assert "cover.jpg" in created
    assert "poster.jpg" in created
    assert "tvshow.nfo" in created
    assert "show.info.json" in created

    assert (show_dir / "cover.jpg").is_file()
    assert (show_dir / "poster.jpg").is_file()
    assert (show_dir / "tvshow.nfo").is_file()
    assert (show_dir / "show.info.json").is_file()

    # Second run should be idempotent (create nothing new)
    second_run = ensure_podcast_show_assets(show_dir, meta, settings, art_bytes=dummy_art)
    assert second_run == []


def test_write_podcast_episode_sidecars(tmp_path: Path):
    ep_dir = tmp_path / "Season 01"
    ep_dir.mkdir()
    audio_path = ep_dir / "Ep 01.mp3"
    audio_path.write_text("audio")

    ep = PodcastEpisode(
        guid="guid-1",
        title="Ep 01",
        enclosure_url="https://example.com/ep1.mp3",
        enclosure_type="audio/mpeg",
        enclosure_length=1000,
        pub_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        duration_seconds=600,
        description="Description",
        show_notes="",
        season=1,
        episode_number=1,
        episode_type="full",
        artwork_url=None,
        chapter_url=None,
        transcript_url=None,
        show_title="Test Show",
        author="Host",
    )
    dummy_art = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 100
    settings = {"save_artwork": True, "write_nfo": True, "write_json": True}

    created = write_podcast_episode_sidecars(
        audio_path,
        ep,
        show_meta={"title": "Test Show"},
        settings=settings,
        art_bytes=dummy_art,
        art_mime="image/jpeg",
    )

    assert "Ep 01.nfo" in created
    assert "Ep 01.info.json" in created
    assert "Ep 01.jpg" in created
    assert "Ep 01-thumb.jpg" in created

    assert (ep_dir / "Ep 01.nfo").is_file()
    assert (ep_dir / "Ep 01.info.json").is_file()
    assert (ep_dir / "Ep 01.jpg").is_file()
    assert (ep_dir / "Ep 01-thumb.jpg").is_file()


# ---------------------------------------------------------------------------
# Integration Tests: Audio Tag Embedding (MP3 & FLAC)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg required to generate audio files")
def test_embed_podcast_tags_id3_roundtrip(tmp_path: Path):
    mp3_file = tmp_path / "test_ep.mp3"
    _create_dummy_mp3(mp3_file)

    dummy_art = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 500
    ep = PodcastEpisode(
        guid="unique-guid-999",
        title="The Big Interview",
        enclosure_url="https://example.com/ep.mp3",
        enclosure_type="audio/mpeg",
        enclosure_length=12345,
        pub_date=datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc),
        duration_seconds=1200,
        description="A great talk with our guest.",
        show_notes="",
        season=3,
        episode_number=15,
        episode_type="full",
        artwork_url=None,
        chapter_url=None,
        transcript_url=None,
        show_title="Great Show",
        author="Alice Smith",
    )
    show_meta = {
        "title": "Great Show",
        "author": "Alice Smith",
        "categories": ["Technology", "Podcasts"],
        "feed_url": "https://example.com/feed.xml",
    }

    ok = embed_podcast_tags(
        audio_path=mp3_file,
        episode=ep,
        show_meta=show_meta,
        embed_artwork=True,
        art_bytes=dummy_art,
        art_mime="image/jpeg",
    )
    assert ok is True

    # Read back tags using Mutagen ID3
    tags = ID3(str(mp3_file))
    assert tags.get("TIT2").text[0] == "The Big Interview"
    assert tags.get("TALB").text[0] == "Great Show"
    assert tags.get("TPE1").text[0] == "Alice Smith"
    assert tags.get("TPE2").text[0] == "Great Show"  # Album Artist
    assert tags.get("TRCK").text[0] == "15"
    assert tags.get("TPOS").text[0] == "3"
    assert str(tags.get("TDRC").text[0]) == "2026-02-20"
    assert tags.get("TCON").text[0] == "Technology"
    assert tags.get("COMM:Description:eng").text[0] == "A great talk with our guest."
    assert tags.get("PCST") is not None  # Podcast frame
    assert tags.get("WFED").url == "https://example.com/feed.xml"
    assert tags.get("TGID").text[0] == "unique-guid-999"

    # Embedded artwork
    apic = tags.get("APIC:Cover")
    assert apic is not None
    assert apic.mime == "image/jpeg"
    assert apic.data == dummy_art


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg required to generate audio files")
def test_embed_podcast_tags_vorbis_roundtrip(tmp_path: Path):
    flac_file = tmp_path / "test_ep.flac"
    _create_dummy_flac(flac_file)

    dummy_art = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 300
    ep = PodcastEpisode(
        guid="flac-guid-1",
        title="Flac Episode",
        enclosure_url="https://example.com/ep.flac",
        enclosure_type="audio/flac",
        enclosure_length=50000,
        pub_date=datetime(2026, 5, 10, tzinfo=timezone.utc),
        duration_seconds=900,
        description="Lossless podcast episode",
        show_notes="",
        season=1,
        episode_number=5,
        episode_type="full",
        artwork_url=None,
        chapter_url=None,
        transcript_url=None,
        show_title="HiFi Podcasts",
        author="Audio Host",
    )
    show_meta = {
        "title": "HiFi Podcasts",
        "author": "Audio Host",
        "categories": ["Music Commentary"],
        "feed_url": "https://example.com/hifi.xml",
    }

    ok = embed_podcast_tags(
        audio_path=flac_file,
        episode=ep,
        show_meta=show_meta,
        embed_artwork=True,
        art_bytes=dummy_art,
        art_mime="image/jpeg",
    )
    assert ok is True

    # Read back tags using Mutagen FLAC
    audio = FLAC(str(flac_file))
    assert audio["title"] == ["Flac Episode"]
    assert audio["album"] == ["HiFi Podcasts"]
    assert audio["artist"] == ["Audio Host"]
    assert audio["albumartist"] == ["HiFi Podcasts"]
    assert audio["tracknumber"] == ["5"]
    assert audio["discnumber"] == ["1"]
    assert audio["date"] == ["2026-05-10"]
    assert audio["genre"] == ["Music Commentary"]
    assert audio["podcast"] == ["1"]
    assert audio["podcasturl"] == ["https://example.com/hifi.xml"]
    assert audio["podcastid"] == ["flac-guid-1"]
    assert len(audio.pictures) == 1
    assert audio.pictures[0].data == dummy_art


# ---------------------------------------------------------------------------
# Integration Tests: End-to-End Workflow & Resilience
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg required to generate audio files")
def test_post_process_podcast_episode_full_workflow(tmp_path: Path):
    dest_root = tmp_path / "Podcasts"
    show_dir = dest_root / "Science Daily"
    season_dir = show_dir / "Season 01"
    season_dir.mkdir(parents=True)

    audio_file = season_dir / "Science Daily - S01E01 - Quantum.mp3"
    _create_dummy_mp3(audio_file)

    ep = PodcastEpisode(
        guid="quantum-ep-01",
        title="Quantum Mysteries",
        enclosure_url="https://example.com/quantum.mp3",
        enclosure_type="audio/mpeg",
        enclosure_length=100000,
        pub_date=datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc),
        duration_seconds=2400,
        description="A journey into quantum physics.",
        show_notes="Full show notes",
        season=1,
        episode_number=1,
        episode_type="full",
        artwork_url="https://example.com/quantum.jpg",
        chapter_url=None,
        transcript_url=None,
        show_title="Science Daily",
        author="Dr. Scientist",
    )
    show_meta = {
        "title": "Science Daily",
        "author": "Dr. Scientist",
        "description": "Daily science show",
        "feed_url": "https://example.com/science.xml",
        "itunes_id": 987654,
        "categories": ["Science", "Natural Sciences"],
        "artwork_url": "https://example.com/science_cover.jpg",
    }

    dummy_art = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 200
    with patch("core.podcast_post_processor.fetch_artwork_bytes", return_value=(dummy_art, "image/jpeg")):
        res = post_process_podcast_episode(
            audio_path=audio_file,
            episode=ep,
            show_meta=show_meta,
            dest_root=dest_root,
        )

    assert res["success"] is True
    assert res["tags_embedded"] is True

    # Show root assets
    assert (show_dir / "cover.jpg").is_file()
    assert (show_dir / "poster.jpg").is_file()
    assert (show_dir / "tvshow.nfo").is_file()
    assert (show_dir / "show.info.json").is_file()

    # Episode sidecars
    stem = audio_file.stem
    assert (season_dir / f"{stem}.nfo").is_file()
    assert (season_dir / f"{stem}.info.json").is_file()
    assert (season_dir / f"{stem}.jpg").is_file()
    assert (season_dir / f"{stem}-thumb.jpg").is_file()

    # Verify NFO and JSON content validity
    nfo_content = (season_dir / f"{stem}.nfo").read_text(encoding="utf-8")
    assert "<title>Quantum Mysteries</title>" in nfo_content
    assert "<studio>Dr. Scientist</studio>" in nfo_content

    json_content = json.loads((season_dir / f"{stem}.info.json").read_text(encoding="utf-8"))
    assert json_content["title"] == "Quantum Mysteries"
    assert json_content["author"] == "Dr. Scientist"
    assert json_content["duration"] == 2400


def test_post_process_error_resilience(tmp_path: Path):
    non_existent = tmp_path / "does_not_exist.mp3"
    ep = PodcastEpisode(
        guid="g1",
        title="Ghost Ep",
        enclosure_url="https://example.com/audio.mp3",
        enclosure_type="audio/mpeg",
        enclosure_length=10,
        pub_date=None,
        duration_seconds=None,
        description="",
        show_notes="",
        season=None,
        episode_number=None,
        episode_type="full",
        artwork_url=None,
        chapter_url=None,
        transcript_url=None,
    )

    # Should not raise any exception, returns gracefully
    res = post_process_podcast_episode(
        audio_path=non_existent,
        episode=ep,
        show_meta={},
    )
    assert res["success"] is True
    assert res["tags_embedded"] is False


# ---------------------------------------------------------------------------
# Unit Tests: Static MP4 Video Remux (TV Media Server Support)
# ---------------------------------------------------------------------------

def test_find_ffmpeg_binary():
    from core.podcast_post_processor import find_ffmpeg_binary
    # Should return either a path string or None, without raising exceptions
    bin_path = find_ffmpeg_binary()
    assert bin_path is None or isinstance(bin_path, str)


def test_convert_audio_to_static_mp4_no_ffmpeg(tmp_path: Path):
    from core.podcast_post_processor import convert_audio_to_static_mp4
    audio = tmp_path / "ep.mp3"
    audio.write_bytes(b"dummy audio")
    out_mp4 = tmp_path / "ep.mp4"

    # With invalid ffmpeg path, should return False gracefully
    ok = convert_audio_to_static_mp4(
        audio_path=audio,
        output_mp4_path=out_mp4,
        ffmpeg_bin=str(tmp_path / "non_existent_ffmpeg"),
    )
    assert ok is False
    assert not out_mp4.exists()


def test_convert_audio_to_static_mp4_mocked_ffmpeg(tmp_path: Path, monkeypatch):
    import subprocess
    from mutagen.mp4 import MP4
    from core.podcast_post_processor import convert_audio_to_static_mp4

    audio = tmp_path / "ep.mp3"
    audio.write_bytes(b"audio data")
    out_mp4 = tmp_path / "ep.mp4"
    img = tmp_path / "ep.jpg"
    img.write_bytes(b"image data")

    ep = PodcastEpisode(
        guid="ep-101",
        title="Deep Space",
        enclosure_url="https://example.com/ep.mp3",
        enclosure_type="audio/mpeg",
        enclosure_length=1000,
        pub_date=None,
        duration_seconds=120,
        description="Exploring the cosmos",
        show_notes="",
        season=2,
        episode_number=7,
        episode_type="full",
        artwork_url=None,
        chapter_url=None,
        transcript_url=None,
    )
    meta = {"title": "Space Podcast", "author": "Astronomer"}

    def fake_run(cmd, *args, **kwargs):
        # Create a valid minimal MP4 file using Mutagen or writing bytes
        out_mp4.write_bytes(b"\x00\x00\x00 ftypisom\x00\x00\x02\x00isomiso2mp41\x00\x00\x00\x08free")
        return subprocess.CompletedProcess(cmd, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    ok = convert_audio_to_static_mp4(
        audio_path=audio,
        output_mp4_path=out_mp4,
        image_path=img,
        episode=ep,
        show_meta=meta,
        ffmpeg_bin="mock_ffmpeg",
    )
    assert ok is True
    assert out_mp4.is_file()


def test_post_process_media_format_audio_mode(tmp_path: Path):
    audio_file = tmp_path / "Ep1.mp3"
    audio_file.write_bytes(b"audio content")

    ep = PodcastEpisode(
        guid="ep-1",
        title="Audio Ep",
        enclosure_url="https://example.com/audio.mp3",
        enclosure_type="audio/mpeg",
        enclosure_length=10,
        pub_date=None,
        duration_seconds=60,
        description="Audio only",
        show_notes="",
        season=1,
        episode_number=1,
        episode_type="full",
        artwork_url=None,
        chapter_url=None,
        transcript_url=None,
    )

    res = post_process_podcast_episode(
        audio_path=audio_file,
        episode=ep,
        show_meta={"title": "Test Show"},
        settings={"media_format": "audio", "embed_metadata": False, "save_artwork": False},
        dest_root=tmp_path,
    )
    assert res["audio_path"] == str(audio_file)
    assert res["video_path"] is None
    assert res["converted_to_video"] is False
    assert audio_file.is_file()
    assert not (tmp_path / "Ep1.mp4").exists()


def test_post_process_media_format_video_mode(tmp_path: Path, monkeypatch):
    audio_file = tmp_path / "Ep2.mp3"
    audio_file.write_bytes(b"audio content")
    mp4_file = tmp_path / "Ep2.mp4"

    ep = PodcastEpisode(
        guid="ep-2",
        title="Video Ep",
        enclosure_url="https://example.com/video.mp3",
        enclosure_type="audio/mpeg",
        enclosure_length=10,
        pub_date=None,
        duration_seconds=60,
        description="Video only",
        show_notes="",
        season=1,
        episode_number=2,
        episode_type="full",
        artwork_url=None,
        chapter_url=None,
        transcript_url=None,
    )

    def mock_convert(*args, **kwargs):
        mp4_file.write_bytes(b"dummy mp4 video")
        return True

    from core import podcast_post_processor
    monkeypatch.setattr(podcast_post_processor, "convert_audio_to_static_mp4", mock_convert)

    res = post_process_podcast_episode(
        audio_path=audio_file,
        episode=ep,
        show_meta={"title": "Test Show"},
        settings={"media_format": "video", "embed_metadata": False, "save_artwork": False},
        dest_root=tmp_path,
    )

    # In "video" mode, original audio is replaced and deleted
    assert res["converted_to_video"] is True
    assert res["audio_path"] == str(mp4_file)
    assert res["video_path"] == str(mp4_file)
    assert mp4_file.is_file()
    assert not audio_file.exists()


def test_post_process_media_format_both_mode(tmp_path: Path, monkeypatch):
    audio_file = tmp_path / "Ep3.mp3"
    audio_file.write_bytes(b"audio content")
    mp4_file = tmp_path / "Ep3.mp4"

    ep = PodcastEpisode(
        guid="ep-3",
        title="Both Formats Ep",
        enclosure_url="https://example.com/both.mp3",
        enclosure_type="audio/mpeg",
        enclosure_length=10,
        pub_date=None,
        duration_seconds=60,
        description="Both audio and video",
        show_notes="",
        season=1,
        episode_number=3,
        episode_type="full",
        artwork_url=None,
        chapter_url=None,
        transcript_url=None,
    )

    def mock_convert(*args, **kwargs):
        mp4_file.write_bytes(b"dummy mp4 video")
        return True

    from core import podcast_post_processor
    monkeypatch.setattr(podcast_post_processor, "convert_audio_to_static_mp4", mock_convert)

    res = post_process_podcast_episode(
        audio_path=audio_file,
        episode=ep,
        show_meta={"title": "Test Show"},
        settings={"media_format": "both", "embed_metadata": False, "save_artwork": False},
        dest_root=tmp_path,
    )

    # In "both" mode, both files must exist side-by-side
    assert res["converted_to_video"] is True
    assert res["audio_path"] == str(audio_file)
    assert res["video_path"] == str(mp4_file)
    assert audio_file.is_file()
    assert mp4_file.is_file()

