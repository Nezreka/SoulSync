"""The anime episode template: an anime show's episodes take their own template.

A show is anime by the rule that already files it in the anime folder (#1835):
set by hand, else it sits in a library marked anime. Never the genre. Empty
means anime is named like every other show, so nothing changes until it's set.
"""

from __future__ import annotations

import json
import os

import pytest

from core.video import importer, organization
from core.video.download_monitor import naming_series_type
from core.video.file_ops import expected_path
from database.video_database import VideoDatabase

_TV = "$series/Season $season/$series - S$seasonE$episode"
_ANIME = "$series/Season $season/$series - S$seasonE$episode - $absolute"
_BOTH = {"episode_template": _TV, "anime_episode_template": _ANIME}


# ── the setting ──────────────────────────────────────────────────────────────

def test_blank_by_default_and_blank_stays_blank():
    assert organization.default_settings()["anime_episode_template"] == ""
    assert organization.normalize({"anime_episode_template": "  "})["anime_episode_template"] == ""
    assert organization.normalize({"anime_episode_template": " $series "})["anime_episode_template"] == "$series"
    # the episode template still falls back to its default when blank
    assert organization.normalize({"episode_template": ""})["episode_template"] == \
        organization.DEFAULTS["episode_template"]


def test_only_an_anime_show_takes_it():
    assert organization.for_series_type(_BOTH, "anime")["episode_template"] == _ANIME
    assert organization.for_series_type(_BOTH, "standard") is _BOTH
    assert organization.for_series_type(_BOTH, None) is _BOTH
    assert organization.for_series_type(_BOTH, "daily") is _BOTH
    # no anime template of its own: anime is named like any other show
    tv_only = {"episode_template": _TV, "anime_episode_template": ""}
    assert organization.for_series_type(tv_only, "anime") is tv_only


# ── the library rows ─────────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    d.set_setting("plex.anime_libraries", json.dumps(["Anime"]))
    # the rows carry the series type only while an anime template is saved
    organization.save(d, _BOTH)
    return d


def _seed(db, tmp_path, server_id, title, *, tmdb_id, library, series_type=None):
    conn = db._get_connection()
    sid = conn.execute("INSERT INTO shows (server_source, server_id, title, year, tmdb_id, "
                       "library_name, series_type) VALUES ('plex', ?, ?, 2020, ?, ?, ?)",
                       (server_id, title, tmdb_id, library, series_type)).lastrowid
    season = conn.execute("INSERT INTO seasons (show_id, season_number) VALUES (?, 1)", (sid,)).lastrowid
    eid = conn.execute("INSERT INTO episodes (show_id, season_id, season_number, episode_number, "
                       "has_file) VALUES (?, ?, 1, 1, 1)", (sid, season)).lastrowid
    real = tmp_path / "lib" / title / "x.mkv"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_bytes(b"x")
    conn.execute("INSERT INTO media_files (episode_id, relative_path, size_bytes) VALUES (?,?,1)",
                 (eid, str(real)))
    conn.commit()
    conn.close()
    return sid, real


def test_rows_carry_the_routing_type(db, tmp_path):
    _seed(db, tmp_path, "a", "Frieren", tmdb_id=1, library="Anime")
    _seed(db, tmp_path, "b", "Pluto", tmdb_id=2, library="TV Shows", series_type="anime")
    _seed(db, tmp_path, "c", "Severance", tmdb_id=3, library="TV Shows")
    _seed(db, tmp_path, "d", "Mushishi", tmdb_id=None, library="Anime")
    # filed under TV by hand, though its library says anime
    _seed(db, tmp_path, "e", "Arcane", tmdb_id=5, library="Anime", series_type="standard")
    want = {"Frieren": "anime", "Pluto": "anime", "Severance": None, "Mushishi": "anime",
            "Arcane": "standard"}
    rows = [r for r in db.repair_library_files() if r["scope"] == "episode"]
    assert {r["title"]: r["series_type"] for r in rows} == want
    assert {r["show_title"]: r["series_type"] for r in db.rename_owned_episode_files()} == want
    for title, st in want.items():
        sid = db._get_connection().execute("SELECT id FROM shows WHERE title=?", (title,)).fetchone()[0]
        assert [r["series_type"] for r in db.library_title_files("show", sid)] == [st]
    # and it is the answer routing gives
    assert db.routing_series_type(tmdb_id=1) == "anime"
    assert db.routing_series_type(tmdb_id=5) == "standard"


def test_the_naming_check_names_anime_by_its_template(db, tmp_path):
    _seed(db, tmp_path, "a", "Frieren", tmdb_id=1, library="Anime")
    _seed(db, tmp_path, "c", "Severance", tmdb_id=3, library="TV Shows")
    root = str(tmp_path / "lib")
    rows = {r["title"]: r for r in db.repair_library_files() if r["scope"] == "episode"}
    reals = {t: str(tmp_path / "lib" / t / "x.mkv") for t in rows}
    assert os.path.normpath(expected_path(rows["Frieren"], reals["Frieren"], [root], _BOTH)).endswith(
        os.path.normpath("Frieren/Season 01/Frieren - S01E01 - 001.mkv"))
    assert os.path.normpath(expected_path(rows["Severance"], reals["Severance"], [root], _BOTH)).endswith(
        os.path.normpath("Severance/Season 01/Severance - S01E01.mkv"))


def test_rows_skip_the_type_without_an_anime_template(db, tmp_path):
    # no anime template: nothing reads the type, so the scan doesn't work it out
    _seed(db, tmp_path, "a", "Frieren", tmdb_id=1, library="Anime")
    organization.save(db, {"episode_template": _TV})
    assert [r["series_type"] for r in db.rename_owned_episode_files()] == [None]


# ── the importer ─────────────────────────────────────────────────────────────

def _dl():
    return {"kind": "show", "title": "Frieren", "source": "soulseek",
            "release_title": "Frieren.S01E05.1080p.WEB.mkv", "size_bytes": 1_000_000_000,
            "target_dir": "/lib/anime", "media_source": "tmdb", "media_id": 1,
            "search_ctx": json.dumps({"scope": "episode", "title": "Frieren",
                                      "season": 1, "episode": 5})}


def test_importer_names_an_anime_episode_by_the_anime_template():
    p = importer.plan_import(_dl(), "/dl/x/Frieren.S01E05.1080p.WEB.mkv", list_dir=lambda d: [],
                             settings=_BOTH, series_type="anime", absolute_of=lambda s, e: e)
    assert p["dest"]["filename"] == "Frieren - S01E05 - 005.mkv"
    p = importer.plan_import(_dl(), "/dl/x/Frieren.S01E05.1080p.WEB.mkv", list_dir=lambda d: [],
                             settings=_BOTH, series_type="standard", absolute_of=lambda s, e: e)
    assert p["dest"]["filename"] == "Frieren - S01E05.mkv"


def test_the_monitor_only_asks_the_type_while_an_anime_template_is_set(db, tmp_path):
    _seed(db, tmp_path, "a", "Frieren", tmdb_id=1, library="Anime")
    assert naming_series_type(db, _dl(), {"episode_template": _TV, "anime_episode_template": ""}) is None
    assert naming_series_type(db, _dl(), _BOTH) == "anime"
    assert naming_series_type(db, dict(_dl(), kind="movie"), _BOTH) is None
    sid = db._get_connection().execute("SELECT id FROM shows WHERE title='Frieren'").fetchone()[0]
    regrab = dict(_dl(), media_source="library", media_id=sid)
    assert naming_series_type(db, regrab, _BOTH) == "anime"
