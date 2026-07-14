"""Multiple copies/versions of one movie or episode: both adapters emit EVERY
version, the writer stores a row per version, and the detail payloads surface
them (movie files[] rail; per-episode versions + best resolution)."""

from __future__ import annotations

import pytest

from core.video.sources import JellyfinVideoSource, PlexVideoSource
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _v(path, res, size):
    return {"relative_path": path, "resolution": res, "size_bytes": size,
            "video_codec": "hevc"}


# ── DB: a row per version, detail payloads surface them ─────────────────────
def test_movie_versions_stored_and_surfaced(db):
    mid = db.upsert_movie("plex", {
        "server_id": "m1", "title": "Dune", "tmdb_id": 1,
        "files": [_v("/dune.4k.mkv", "2160p", 30 * 1024**3),
                  _v("/dune.1080.mkv", "1080p", 8 * 1024**3)],
        "file": _v("/dune.4k.mkv", "2160p", 30 * 1024**3)})
    d = db.movie_detail(mid)
    assert d["owned"] is True
    assert [f["resolution"] for f in d["files"]] == ["2160p", "1080p"]   # size-desc
    assert d["file"]["resolution"] == "2160p"                            # best (compat)
    # A rescan with ONE version replaces the set (no stale rows).
    db.upsert_movie("plex", {"server_id": "m1", "title": "Dune", "tmdb_id": 1,
                             "files": [_v("/dune.1080.mkv", "1080p", 8 * 1024**3)]})
    assert len(db.movie_detail(mid)["files"]) == 1


def test_episode_versions_in_show_detail(db):
    sid = db.upsert_show_tree("plex", {
        "server_id": "s1", "title": "Show", "tmdb_id": 9, "seasons": [
            {"season_number": 1, "episodes": [
                {"episode_number": 1, "title": "Two Copies", "server_id": "e1",
                 "files": [_v("/e1.1080.mkv", "1080p", 4 * 1024**3),
                           _v("/e1.720.mkv", "720p", 1024**3)]},
                {"episode_number": 2, "title": "One Copy", "server_id": "e2",
                 "file": _v("/e2.mkv", "720p", 1024**3)},
                {"episode_number": 3, "title": "None", "server_id": "e3"}]}]})
    eps = {e["episode_number"]: e for e in db.show_detail(sid)["seasons"][0]["episodes"]}
    assert eps[1]["owned"] and eps[1]["versions"] == 2 and eps[1]["resolution"] == "1080p"
    assert eps[2]["owned"] and eps[2]["versions"] == 1 and eps[2]["resolution"] == "720p"
    assert not eps[3]["owned"] and eps[3]["versions"] == 0


# ── Plex adapter: every media entry = a version; parts sum into its size ────
class _P:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_plex_part_files_emits_every_version():
    obj = _P(duration=7_200_000, media=[
        _P(videoResolution="4k", aspectRatio=1.78, videoCodec="hevc", audioCodec="truehd",
           parts=[_P(file="/m/4k.p1.mkv", size=15 * 1024**3),
                  _P(file="/m/4k.p2.mkv", size=15 * 1024**3)]),          # 2-part copy
        _P(videoResolution="1080", aspectRatio=1.78, videoCodec="h264", audioCodec="ac3",
           parts=[_P(file="/m/1080.mkv", size=8 * 1024**3)]),
    ])
    files = PlexVideoSource._part_files(obj)
    assert len(files) == 2
    assert files[0]["relative_path"] == "/m/4k.p1.mkv"
    assert files[0]["size_bytes"] == 30 * 1024**3                        # parts summed
    assert files[1]["resolution"] == "1080"
    assert PlexVideoSource._part_file(obj)["video_codec"] == "hevc"      # compat: first
    assert PlexVideoSource._part_files(_P(duration=None, media=[])) == []


# ── Jellyfin adapter: every MediaSource = a version ──────────────────────────
def test_jellyfin_files_emits_every_source():
    item = {"RunTimeTicks": 72_000_000_000, "MediaSources": [
        {"Path": "/m/4k.mkv", "Size": 30, "MediaStreams": [
            {"Type": "Video", "Codec": "hevc", "Height": 2160, "Width": 3840},
            {"Type": "Audio", "Codec": "truehd"}]},
        {"Path": "/m/1080.mkv", "Size": 8, "MediaStreams": [
            {"Type": "Video", "Codec": "h264", "Height": 1080, "Width": 1920}]},
    ]}
    files = JellyfinVideoSource._files(item)
    assert len(files) == 2
    assert files[0]["resolution"] == "2160p" and files[1]["resolution"] == "1080p"
    assert JellyfinVideoSource._file(item)["relative_path"] == "/m/4k.mkv"
    # Bare Path (no sources) still yields the minimal single entry.
    assert JellyfinVideoSource._files({"Path": "/x.mkv"}) == [{"relative_path": "/x.mkv"}]
    assert JellyfinVideoSource._files({}) == []
