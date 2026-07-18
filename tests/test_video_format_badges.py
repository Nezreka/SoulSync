"""Detail BIC P4 — format facts on media files (4K · HDR · Atmos · 5.1 badges).

media_files gains audio_channels / dynamic_range / atmos (schema v46). Plex
gives a real channel count but stream-level HDR would be an N+1 reload, so
HDR/Atmos come from release-name tokens (Radarr-style); Jellyfin overrides
with real MediaStream facts (VideoRangeType, Channels, Atmos profile).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.video.sources import _format_from_name
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video.db"))


class TestNameTokens:
    def test_full_stack_release(self):
        got = _format_from_name("/m/Dune.2021.2160p.UHD.BluRay.DV.HDR10Plus.Atmos.TrueHD.7.1.mkv")
        assert got == {"dynamic_range": "DV HDR10+", "atmos": True}

    def test_plain_hdr_and_hlg(self):
        assert _format_from_name("/m/x.hdr.hevc.mkv") == {"dynamic_range": "HDR10"}
        assert _format_from_name("/m/x.hlg.mkv") == {"dynamic_range": "HLG"}

    def test_dolby_vision_spelled_out(self):
        assert _format_from_name("/m/x.Dolby.Vision.mkv") == {"dynamic_range": "DV"}

    def test_no_false_positives_inside_words(self):
        # 'dv' in 'adverse', 'hdr' nowhere, 'atmos' in 'atmosphere' guarded by \b
        assert _format_from_name("/tv/adverse.effects.s01e01.mkv") == {}
        assert _format_from_name("/m/atmosphere.2020.mkv") == {}

    def test_sdr_release_is_empty(self):
        assert _format_from_name("/m/Oppenheimer.2023.1080p.WEB-DL.DDP5.1.H.264.mkv") == {}


class TestIngestRoundTrip:
    def test_schema_and_migrations(self, db):
        conn = db._get_connection()
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(media_files)")}
        conn.close()
        assert {"audio_channels", "dynamic_range", "atmos"} <= cols
        src = (_ROOT / "database" / "video_database.py").read_text(encoding="utf-8", errors="replace")
        for entry in ('("media_files", "audio_channels", "INTEGER")',
                      '("media_files", "dynamic_range", "TEXT")',
                      '("media_files", "atmos", "INTEGER")'):
            assert entry in src, f"missing migration {entry}"

    def test_movie_file_carries_the_facts(self, db):
        mid = db.upsert_movie("plex", {
            "server_id": "m1", "title": "Dune", "year": 2021, "tmdb_id": 438631,
            "file": {"relative_path": "/m/dune.mkv", "size_bytes": 10, "resolution": "2160p",
                     "audio_codec": "truehd", "audio_channels": 8,
                     "dynamic_range": "DV HDR10+", "atmos": True}})
        f = db.movie_detail(mid)["file"]
        assert f["audio_channels"] == 8
        assert f["dynamic_range"] == "DV HDR10+"
        assert f["atmos"] == 1

    def test_plain_file_stays_clean(self, db):
        mid = db.upsert_movie("plex", {
            "server_id": "m2", "title": "F", "year": 2020, "tmdb_id": 7,
            "file": {"relative_path": "/m/f.mkv", "size_bytes": 10, "resolution": "1080p"}})
        f = db.movie_detail(mid)["file"]
        assert f["audio_channels"] is None and f["dynamic_range"] is None and f["atmos"] == 0


def test_source_maps_wire_the_fields():
    src = (_ROOT / "core" / "video" / "sources.py").read_text(encoding="utf-8", errors="replace")
    # plex: real channel count + name-derived HDR/Atmos on every version
    assert 'int(getattr(media, "audioChannels", 0) or 0) or None' in src
    assert src.count("_format_from_name(") >= 3      # def + plex + jellyfin
    # jellyfin: real stream facts override the name tokens
    assert '"audio_channels": aud.get("Channels")' in src
    assert 'vid.get("VideoRangeType")' in src
    assert '"DOVI": "DV"' in src
    assert 'aud.get("DisplayTitle")' in src


def test_detail_js_renders_the_badges():
    js = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(
        encoding="utf-8", errors="replace")
    assert 'function formatBadges(f)' in js
    assert 'formatBadges(d.file)' in js               # hero meta (owned movies)
    assert 'function channelsLabel(n)' in js
    assert "rows.push(['Dynamic range', f.dynamic_range])" in js
    css = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(
        encoding="utf-8", errors="replace")
    assert '.vd-fmt' in css
