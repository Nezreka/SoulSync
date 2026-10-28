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


def test_deepest_match_wins_over_bare_basename():
    # The resolver's callers REPLACE the file they resolve to — a generic
    # 'movie.mkv' at the library root must never shadow the real folder match.
    have = {"/mnt/media/movie.mkv",                          # a DIFFERENT film, flat
            "/mnt/media/The Matrix (1999)/movie.mkv"}        # the real one
    got = resolve_video_file_path("/data/movies/The Matrix (1999)/movie.mkv",
                                  ["/mnt/media"], exists=lambda p: p in have)
    assert got == "/mnt/media/The Matrix (1999)/movie.mkv"


def test_size_proves_identity_for_rerooted_candidates():
    have = {"/mnt/media/movie.mkv"}
    sizes = {"/mnt/media/movie.mkv": 111}
    # Wrong size → not our file → no match (better no upgrade than a wrong delete).
    assert resolve_video_file_path("/data/movies/X/movie.mkv", ["/mnt/media"],
                                   size_bytes=999, exists=lambda p: p in have,
                                   getsize=lambda p: sizes[p]) is None
    # Matching size → identity proven.
    assert resolve_video_file_path("/data/movies/X/movie.mkv", ["/mnt/media"],
                                   size_bytes=111, exists=lambda p: p in have,
                                   getsize=lambda p: sizes[p]) == "/mnt/media/movie.mkv"
    # Unknown stored size → size can't gate (classic probe).
    assert resolve_video_file_path("/data/movies/X/movie.mkv", ["/mnt/media"],
                                   size_bytes=None, exists=lambda p: p in have,
                                   getsize=lambda p: sizes[p]) == "/mnt/media/movie.mkv"


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
    assert db.video_stored_file_path("movie", tmdb_id=603) \
        == {"path": "/data/movies/mx/matrix.mkv", "size_bytes": 5}
    assert db.video_stored_file_path("episode", tmdb_id=9, season=1, episode=1) \
        == {"path": "/data/tv/show/s01e01.mkv", "size_bytes": 5}
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
    (real / "matrix.mkv").write_bytes(b"xxxxx")   # 5 bytes — must match the stored size
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


def test_extra_drive_resolution_and_recycling_stay_on_that_drive(db, tmp_path):
    from core.video.recycle import trash_dir_for
    from core.video.library_roots import containing_root, library_roots
    root = tmp_path / "movies2"
    file = root / "Film" / "Film.mkv"
    file.parent.mkdir(parents=True)
    file.write_bytes(b"movie")
    db.set_setting("movies_path", str(tmp_path / "primary"))
    db.set_setting("movies_additional_paths", json.dumps([str(root)]))
    resolved = resolve_video_file_path("/plex/movies/Film/Film.mkv", video_base_dirs(db), size_bytes=5)
    assert resolved == str(file)
    assert containing_root(resolved, library_roots(db, "movie")) == str(root)
    assert trash_dir_for(resolved, {}, db) == str(root / "ss_recycle")
    assert db.get_setting("movies_path") == str(tmp_path / "primary")


def test_specific_folder_on_extra_drive_beats_primary_basename():
    files = {"/primary/movie.mkv", "/extra/Film/movie.mkv"}
    assert resolve_video_file_path("/server/Film/movie.mkv", ["/primary", "/extra"],
                                   exists=lambda path: path in files) == "/extra/Film/movie.mkv"


# ── a NEW episode of an owned show lands in the show's existing folder ──────
def _show_with_episodes(db, eps, tmdb_id=9):
    """eps: [(season, episode, stored_path, size)]"""
    seasons = {}
    for sn, en, path, size in eps:
        seasons.setdefault(sn, []).append(
            {"server_id": "e%s-%s" % (sn, en), "episode_number": en, "title": "E%s" % en,
             "file": {"relative_path": path, "size_bytes": size}})
    db.upsert_show_tree("plex", {"server_id": "s%s" % tmdb_id, "tmdb_id": tmdb_id,
                                 "title": "Firefly", "seasons": [
                                     {"season_number": sn, "episodes": e}
                                     for sn, e in sorted(seasons.items())]})


def _episode_dl(season, episode, tmdb_id=9):
    return {"id": 7, "kind": "episode", "media_source": "tmdb", "media_id": str(tmdb_id),
            "search_ctx": json.dumps({"scope": "episode", "title": "Firefly", "year": 2002,
                                      "season": season, "episode": episode})}


def test_show_episode_file_path_prefers_the_same_season(db):
    _show_with_episodes(db, [(1, 1, "/data/tv/F/Season 01/a.mkv", 5),
                             (1, 2, "/data/tv/F/Season 01/b.mkv", 5),
                             (2, 1, "/data/tv/F/Season 02/c.mkv", 5)])
    assert db.video_show_episode_file_path(9, season=1) \
        == {"path": "/data/tv/F/Season 01/b.mkv", "size_bytes": 5, "season_number": 1}
    # a season with nothing owned yet: the latest season's latest episode
    assert db.video_show_episode_file_path(9, season=3)["path"] == "/data/tv/F/Season 02/c.mkv"
    assert db.video_show_episode_file_path(999, season=1) is None
    assert db.video_show_episode_file_path(None, season=1) is None


def test_episode_folders_below_series():
    from core.video.organization import episode_folders_below_series
    f = {"series": "Firefly", "year": 2002, "season": 3}
    assert episode_folders_below_series({}, f) == ["Season 03"]
    assert episode_folders_below_series(
        {"episode_template": "$series ($year)/S$season - $series/x"}, f) == ["S03 - Firefly"]
    # the last segment is the file name, never a folder
    assert episode_folders_below_series(
        {"episode_template": "$series ($year)/S$season - $series"}, f) == []
    assert episode_folders_below_series(
        {"episode_template": "${series}/$series - S${season}E$episode"}, f) == []
    assert episode_folders_below_series(
        {"episode_template": "TV/$series/Specials/Season $season/x"}, f) == ["Specials", "Season 03"]
    # no series folder at all -> nothing to place under
    assert episode_folders_below_series({"episode_template": "Season $season/$series"}, f) is None
    assert episode_folders_below_series({"episode_template": "$series - S$season"}, f) is None


def _sonarr_tree(tmp_path, db):
    show = tmp_path / "tv" / "Firefly (2002) {imdb-tt0303461}"
    (show / "Season 01").mkdir(parents=True)
    (show / "Season 01" / "Firefly (2002) - S01E01 [Bluray-1080p].mkv").write_bytes(b"xxxxx")
    db.set_setting("tv_path", str(tmp_path / "tv"))
    _show_with_episodes(db, [(1, 1, "/data/Firefly (2002) {imdb-tt0303461}/Season 01/"
                                    "Firefly (2002) - S01E01 [Bluray-1080p].mkv", 5)])
    return show


def test_new_episode_same_season_goes_beside_its_siblings(db, tmp_path):
    from core.video.download_monitor import _owned_library_dir
    show = _sonarr_tree(tmp_path, db)
    assert _owned_library_dir(db, _episode_dl(1, 2), {}) == str(show / "Season 01")


def test_new_season_goes_under_the_existing_show_folder(db, tmp_path):
    from core.video.download_monitor import _owned_library_dir
    show = _sonarr_tree(tmp_path, db)
    assert _owned_library_dir(db, _episode_dl(2, 1), {}) == str(show / "Season 02")
    # the template's own season folder shape is what's created
    s = {"episode_template": "$series/Series $seasonraw/$series - S$seasonE$episode"}
    assert _owned_library_dir(db, _episode_dl(2, 1), s) == str(show / "Series 2")


def test_new_season_with_a_flat_template_uses_the_show_folder(db, tmp_path):
    from core.video.download_monitor import _owned_library_dir
    show = tmp_path / "tv" / "Firefly"
    show.mkdir(parents=True)
    (show / "e1.mkv").write_bytes(b"xxxxx")
    db.set_setting("tv_path", str(tmp_path / "tv"))
    _show_with_episodes(db, [(1, 1, "/data/Firefly/e1.mkv", 5)])
    s = {"episode_template": "$series/$series - S$seasonE$episode"}
    assert _owned_library_dir(db, _episode_dl(2, 1), s) == str(show)


def test_new_season_never_climbs_out_of_the_library(db, tmp_path):
    """An owned episode sitting directly in the library root (no show folder) must
    not make the library root itself the 'show folder' -> template path instead."""
    from core.video.download_monitor import _owned_library_dir
    root = tmp_path / "tv"
    (root / "Season 01").mkdir(parents=True)
    (root / "Season 01" / "e1.mkv").write_bytes(b"xxxxx")
    db.set_setting("tv_path", str(root))
    _show_with_episodes(db, [(1, 1, "/data/Season 01/e1.mkv", 5)])
    assert _owned_library_dir(db, _episode_dl(2, 1), {}) is None


def test_new_episode_of_an_unowned_or_unresolvable_show_uses_the_template(db, tmp_path):
    from core.video.download_monitor import _owned_library_dir
    _sonarr_tree(tmp_path, db)
    assert _owned_library_dir(db, _episode_dl(1, 2, tmdb_id=999), {}) is None
    db.set_setting("tv_path", str(tmp_path / "elsewhere"))   # file can't be found here
    assert _owned_library_dir(db, _episode_dl(1, 2), {}) is None


def test_an_owned_episode_still_upgrades_in_place(db, tmp_path):
    from core.video.download_monitor import _owned_library_dir
    show = _sonarr_tree(tmp_path, db)
    assert _owned_library_dir(db, _episode_dl(1, 1), {}) == str(show / "Season 01")


def test_plan_import_files_a_new_episode_in_the_show_folder(db, tmp_path):
    from core.video.download_monitor import _owned_library_dir
    from core.video.importer import plan_import
    from tests.test_video_importer import FakeFS
    show = _sonarr_tree(tmp_path, db)
    dl = dict(_episode_dl(1, 2), title="Firefly", target_dir="/lib/tv",
              release_title="Firefly.S01E02.1080p.BluRay.x264-GRP")
    lib = _owned_library_dir(db, dl, {})
    plan = plan_import(dl, "/dl/Firefly.S01E02.1080p.BluRay.x264-GRP.mkv",
                       list_dir=FakeFS().list_dir, library_dir=lib)
    assert plan["action"] == "import"
    assert plan["dest"]["dir"] == str(show / "Season 01")
    assert plan["dest"]["filename"].startswith("Firefly - S01E02")


# ── a sibling renamed in place since the last scan still names the show folder ──
def test_resolve_video_dir_reroots_the_folder_deepest_first():
    from core.video.path_resolver import resolve_video_dir
    dirs = {"/lib/tv/Show (2022)/Season 01", "/lib/tv/Season 01", "/lib/tv/Show (2022)"}
    isdir = dirs.__contains__
    stored = "\\\\?\\Y:\\media\\tv\\Show (2022)\\Season 01\\Show - S01E01 [long].mkv"
    assert resolve_video_dir(stored, ["/lib/tv"], isdir=isdir) == "/lib/tv/Show (2022)/Season 01"
    # the raw stored folder wins when it exists here
    assert resolve_video_dir("/srv/x/a.mkv", ["/lib/tv"], isdir={"/srv/x"}.__contains__) == "/srv/x"


def test_resolve_video_dir_never_trusts_a_bare_season_folder():
    from core.video.path_resolver import resolve_video_dir
    # the show folder is gone; only a stray root-level 'Season 01' exists
    isdir = {"/lib/tv/Season 01"}.__contains__
    stored = "/srv/Show (2022)/Season 01/e1.mkv"
    assert resolve_video_dir(stored, ["/lib/tv"], min_segments=2, isdir=isdir) is None
    assert resolve_video_dir(stored, ["/lib/tv"], min_segments=1, isdir=isdir) == "/lib/tv/Season 01"
    assert resolve_video_dir("", ["/lib/tv"]) is None
    assert resolve_video_dir("e1.mkv", ["/lib/tv"]) is None


def _renamed_tree(tmp_path, db):
    """The server still reports Sonarr's long name; the file on disk was renamed
    to the short template name since (a rename pass), same bytes."""
    show = tmp_path / "anime" / "Cyberpunk Edgerunners (2022) {imdb-tt12590266}"
    (show / "Season 01").mkdir(parents=True)
    (show / "Season 01" / "Cyberpunk Edgerunners (2022) - S01E01.mkv").write_bytes(b"xxxxx")
    db.set_setting("tv_path", str(tmp_path / "tv"))
    db.set_setting("anime_path", str(tmp_path / "anime"))
    _show_with_episodes(db, [(1, 1, "\\\\?\\Y:\\media\\screens\\anime\\Cyberpunk Edgerunners (2022) "
                                    "{imdb-tt12590266}\\Season 01\\Cyberpunk Edgerunners (2022) - "
                                    "S01E01 - 001 [NF WEBDL-1080p][EAC3 5.1]-NanDesuKa.mkv", 5)])
    return show


def test_new_episode_follows_a_renamed_sibling_into_its_folder(db, tmp_path):
    from core.video.download_monitor import _owned_library_dir
    show = _renamed_tree(tmp_path, db)
    assert _owned_library_dir(db, _episode_dl(1, 2), {}) == str(show / "Season 01")


def test_new_season_climbs_from_a_renamed_sibling(db, tmp_path):
    from core.video.download_monitor import _owned_library_dir
    show = _renamed_tree(tmp_path, db)
    assert _owned_library_dir(db, _episode_dl(2, 10), {}) == str(show / "Season 02")


def test_an_owned_episode_renamed_in_place_still_upgrades_in_its_folder(db, tmp_path):
    from core.video.download_monitor import _owned_library_dir
    show = _renamed_tree(tmp_path, db)
    assert _owned_library_dir(db, _episode_dl(1, 1), {}) == str(show / "Season 01")


def test_a_renamed_sibling_whose_show_folder_is_gone_uses_the_template(db, tmp_path):
    from core.video.download_monitor import _owned_library_dir
    show = _renamed_tree(tmp_path, db)
    import shutil
    shutil.rmtree(show)
    (tmp_path / "anime" / "Season 01").mkdir()   # a stray bare season folder
    assert _owned_library_dir(db, _episode_dl(1, 2), {}) is None
    assert _owned_library_dir(db, _episode_dl(1, 1), {}) is None


def test_plan_import_files_beside_a_renamed_sibling(db, tmp_path):
    from core.video.download_monitor import _owned_library_dir
    from core.video.importer import plan_import
    from tests.test_video_importer import FakeFS
    show = _renamed_tree(tmp_path, db)
    dl = dict(_episode_dl(1, 2), title="Firefly", target_dir="/lib/anime",
              release_title="Firefly.S01E02.1080p.BluRay.x264-GRP")
    plan = plan_import(dl, "/dl/Firefly.S01E02.1080p.BluRay.x264-GRP.mkv",
                       list_dir=FakeFS().list_dir, library_dir=_owned_library_dir(db, dl, {}))
    assert plan["action"] == "import"
    assert plan["dest"]["dir"] == str(show / "Season 01")
