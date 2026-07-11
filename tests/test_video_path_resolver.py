"""Video path resolver — server-view paths (Plex part.file / Jellyfin Path)
re-rooted to folders that exist HERE, and the upgrade-in-place wiring: an
owned item's resolved real folder overrides the template destination so a
better copy replaces the file where it lives instead of forking a second one."""

from __future__ import annotations

import json

import pytest

from core.video.path_resolver import resolve_video_file_path, video_base_dirs
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


# ── the resolver (pure; exists() injected) ───────────────────────────────────
def test_raw_path_wins_when_it_exists():
    assert resolve_video_file_path("/data/movies/m.mkv", ["/mnt"],
                                   exists=lambda p: p == "/data/movies/m.mkv") \
        == "/data/movies/m.mkv"


def test_tail_reroot_probes_deeper_segments():
    # Server sees /data/movies/The Matrix (1999)/matrix.mkv; here it's /mnt/media/...
    have = {"/mnt/media/The Matrix (1999)/matrix.mkv"}
    got = resolve_video_file_path("/data/movies/The Matrix (1999)/matrix.mkv",
                                  ["/nope", "/mnt/media"], exists=lambda p: p in have)
    assert got == "/mnt/media/The Matrix (1999)/matrix.mkv"
    # Bare-basename match works too (flat library).
    have = {"/mnt/flat/matrix.mkv"}
    assert resolve_video_file_path("/data/movies/The Matrix (1999)/matrix.mkv",
                                   ["/mnt/flat"], exists=lambda p: p in have) \
        == "/mnt/flat/matrix.mkv"


def test_resolver_never_matches_beyond_probe_depth_or_junk():
    assert resolve_video_file_path("", ["/mnt"], exists=lambda p: True) is None
    assert resolve_video_file_path(None, ["/mnt"], exists=lambda p: False) is None
    assert resolve_video_file_path("/a/b/c.mkv", [], exists=lambda p: False) is None
    assert resolve_video_file_path("/a/b/c.mkv", ["/mnt"], exists=lambda p: False) is None


def test_video_base_dirs_reads_settings(db):
    assert video_base_dirs(db) == []
    db.set_setting("movies_path", "/mnt/movies")
    db.set_setting("tv_path", "/mnt/tv")
    assert video_base_dirs(db) == ["/mnt/movies", "/mnt/tv"]


# ── DB: the stored (server-view) path for an owned item ─────────────────────
def test_video_stored_file_path(db):
    db.upsert_movie("plex", {"server_id": "m1", "tmdb_id": 603, "title": "The Matrix",
                             "file": {"relative_path": "/data/movies/mx/matrix.mkv",
                                      "size_bytes": 5}})
    db.upsert_show_tree("plex", {"server_id": "s1", "tmdb_id": 9, "title": "Show", "seasons": [
        {"season_number": 1, "episodes": [
            {"server_id": "e1", "episode_number": 1, "title": "E1",
             "file": {"relative_path": "/data/tv/show/s01e01.mkv", "size_bytes": 5}}]}]})
    assert db.video_stored_file_path("movie", tmdb_id=603) == "/data/movies/mx/matrix.mkv"
    assert db.video_stored_file_path("episode", tmdb_id=9, season=1, episode=1) \
        == "/data/tv/show/s01e01.mkv"
    assert db.video_stored_file_path("movie", tmdb_id=999) is None
    assert db.video_stored_file_path("episode", tmdb_id=9, season=1, episode=2) is None
    assert db.video_stored_file_path("movie", tmdb_id=None) is None


# ── importer: library_dir overrides the template destination ────────────────
def test_plan_import_upgrades_in_the_real_folder():
    from tests.test_video_importer import FakeFS, _movie_dl
    from core.video.importer import plan_import
    fs = FakeFS({"/mnt/media/The Matrix (1999)": ["The.Matrix.1999.720p.mkv"]})
    plan = plan_import(_movie_dl("The.Matrix.1999.1080p.BluRay.x264"),
                       "/dl/The.Matrix.1999.1080p.BluRay.x264.mkv",
                       list_dir=fs.list_dir,
                       library_dir="/mnt/media/The Matrix (1999)")
    assert plan["action"] == "upgrade"
    assert plan["dest"]["dir"] == "/mnt/media/The Matrix (1999)"
    assert plan["replace_path"] == "/mnt/media/The Matrix (1999)/The.Matrix.1999.720p.mkv"
    # Without library_dir the same plan would have used the template location.
    plan2 = plan_import(_movie_dl("The.Matrix.1999.1080p.BluRay.x264"),
                        "/dl/The.Matrix.1999.1080p.BluRay.x264.mkv",
                        list_dir=fs.list_dir)
    assert plan2["dest"]["dir"].startswith("/lib/movies")


def test_forced_manual_placement_ignores_library_dir():
    from tests.test_video_importer import FakeFS, _movie_dl
    from core.video.importer import plan_import
    fs = FakeFS()
    plan = plan_import(_movie_dl("The.Matrix.1999.1080p.BluRay.x264"),
                       "/dl/m.mkv", list_dir=fs.list_dir, force=True,
                       override={"scope": "movie", "title": "The Matrix", "year": 1999,
                                 "target_dir": "/chosen"},
                       library_dir="/mnt/media/The Matrix (1999)")
    assert plan["dest"]["dir"].startswith("/chosen")


# ── monitor: the owned-library-dir lookup end to end ────────────────────────
def test_owned_library_dir_resolves_real_folder(db, tmp_path, monkeypatch):
    from core.video.download_monitor import _owned_library_dir
    real = tmp_path / "media" / "The Matrix (1999)"
    real.mkdir(parents=True)
    (real / "matrix.mkv").write_bytes(b"x")
    db.set_setting("movies_path", str(tmp_path / "media"))
    db.upsert_movie("plex", {"server_id": "m1", "tmdb_id": 603, "title": "The Matrix",
                             "file": {"relative_path": "/data/The Matrix (1999)/matrix.mkv",
                                      "size_bytes": 5}})
    dl = {"id": 1, "kind": "movie", "media_source": "tmdb", "media_id": "603",
          "search_ctx": json.dumps({"scope": "movie"})}
    assert _owned_library_dir(db, dl) == str(real)
    # Unowned target → None (template destination applies).
    dl2 = dict(dl, media_id="999")
    assert _owned_library_dir(db, dl2) is None
