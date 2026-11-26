"""$absolute in the episode template: anime's absolute number beside SxxEyy.

The number is the one searches already use (episode_absolute_number): the
episode's place in the show's aired order, specials left out. The importer,
mass rename and the naming check all fill it, and a file whose number isn't
known yet is left alone rather than renamed without it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.video import importer, organization
from core.video.download_monitor import absolute_lookup
from core.video.file_ops import expected_path, name_check, naming_fields
from database.video_database import VideoDatabase

_ANIME_TPL = "$series/Season $season/$series - S$seasonE$episode - $absolute"


# ── the token ────────────────────────────────────────────────────────────────

def _render(fields, tpl=_ANIME_TPL):
    return organization.render_path("episode", "/a", fields, {"episode_template": tpl}, ".mkv")["path"]


def test_absolute_renders_at_three_digits():
    p = _render({"series": "Black Butler", "season": 2, "episode": 8, "absolute": 32})
    assert os.path.normpath(p) == os.path.normpath("/a/Black Butler/Season 02/Black Butler - S02E08 - 032.mkv")


def test_absolute_grows_past_999():
    p = _render({"series": "One Piece", "season": 21, "episode": 80, "absolute": 1071})
    assert p.endswith("One Piece - S21E80 - 1071.mkv")


def test_multi_episode_file_carries_both_spans():
    p = _render({"series": "Mob", "season": 1, "episode": 1, "episode_end": 2,
                 "absolute": 1, "absolute_end": 2})
    assert p.endswith("Mob - S01E01-E02 - 001-002.mkv")


def test_unknown_absolute_drops_its_separator():
    p = _render({"series": "Mob", "season": 1, "episode": 3, "absolute": None})
    assert p.endswith("Mob - S01E03.mkv")


def test_absolute_unknown_only_when_the_template_names_it():
    s = {"episode_template": _ANIME_TPL}
    ep = {"season": 2, "episode": 8}
    assert organization.absolute_unknown("episode", dict(ep, absolute=None), s) is True
    assert organization.absolute_unknown("episode", dict(ep, absolute=4), s) is False
    assert organization.absolute_unknown("movie", {}, s) is False
    plain = {"episode_template": "$series - S$seasonE$episode"}
    assert organization.absolute_unknown("episode", dict(ep, absolute=None), plain) is False
    braced = {"episode_template": "$series - ${absolute}"}
    assert organization.absolute_unknown("episode", ep, braced) is True


# ── the library rows ─────────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    # the rows carry the number only while a saved template names it
    organization.save(d, {"episode_template": _ANIME_TPL})
    return d


def _seed_show(db, tmp_path, *, tmdb_id=500, seasons=((0, 2), (1, 24), (2, 12)), files=()):
    """a show with every episode listed (season 0 = specials) and a file on
    each (season, episode) in ``files``"""
    conn = db._get_connection()
    conn.execute("INSERT INTO shows (server_source, server_id, title, year, tmdb_id) "
                 "VALUES ('plex', 'sv1', 'Black Butler', 2008, ?)", (tmdb_id,))
    show_id = conn.execute("SELECT id FROM shows WHERE server_id='sv1'").fetchone()[0]
    for season, count in seasons:
        season_id = conn.execute("INSERT INTO seasons (show_id, season_number) VALUES (?,?)",
                                 (show_id, season)).lastrowid
        for ep in range(1, count + 1):
            conn.execute("INSERT INTO episodes (show_id, season_id, season_number, episode_number, "
                         "title, has_file) VALUES (?,?,?,?,?,?)",
                         (show_id, season_id, season, ep, "E%d" % ep, 1 if (season, ep) in files else 0))
    reals = {}
    for season, ep in files:
        eid = conn.execute("SELECT id FROM episodes WHERE show_id=? AND season_number=? "
                           "AND episode_number=?", (show_id, season, ep)).fetchone()[0]
        real = tmp_path / "TV" / "Black Butler" / ("s%02de%02d.mkv" % (season, ep))
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_bytes(b"x")
        conn.execute("INSERT INTO media_files (episode_id, relative_path, size_bytes) VALUES (?,?,?)",
                     (eid, str(real), 1))
        reals[(season, ep)] = real
    conn.commit()
    conn.close()
    return show_id, reals


def test_rows_carry_the_same_number_searches_use(db, tmp_path):
    show_id, _ = _seed_show(db, tmp_path, files=((1, 1), (2, 8), (0, 1)))
    want = {(s, e): db.episode_absolute_number(500, s, e) for s, e in ((1, 1), (2, 8))}
    assert want == {(1, 1): 1, (2, 8): 32}

    eps = [r for r in db.repair_library_files() if r["scope"] == "episode"]
    got = {(r["season"], r["episode"]): r["absolute_number"] for r in eps}
    assert got == {(1, 1): 1, (2, 8): 32, (0, 1): None}   # a special has none

    got = {(r["season"], r["episode"]): r["absolute_number"]
           for r in db.library_title_files("show", show_id)}
    assert got == {(1, 1): 1, (2, 8): 32, (0, 1): None}

    got = {(r["season_number"], r["episode_number"]): r["absolute_number"]
           for r in db.rename_owned_episode_files()}
    assert got == {(1, 1): 1, (2, 8): 32, (0, 1): None}

    assert all(r["absolute_number"] is None for r in db.repair_library_files()
               if r["scope"] == "movie")


def test_rows_skip_the_number_when_no_template_names_it(db, tmp_path):
    # most libraries never name by it: their scans don't pay for counting it
    show_id, _ = _seed_show(db, tmp_path, files=((1, 1), (2, 8)))
    organization.save(db, {"episode_template": "$series - S$seasonE$episode"})
    assert {r["absolute_number"] for r in db.repair_library_files()} == {None}
    assert {r["absolute_number"] for r in db.library_title_files("show", show_id)} == {None}
    assert {r["absolute_number"] for r in db.rename_owned_episode_files()} == {None}
    # the anime template naming it is enough
    organization.save(db, {"anime_episode_template": _ANIME_TPL})
    assert {r["absolute_number"] for r in db.rename_owned_episode_files()} == {1, 32}


def test_mass_rename_and_import_count_on_the_files_own_show_row(db, tmp_path):
    # a show on two servers whose episode lists differ: the number a file
    # gets at import is the one mass rename and the naming check give it
    show_id, _ = _seed_show(db, tmp_path, files=((2, 8),))
    conn = db._get_connection()
    other = conn.execute("INSERT INTO shows (server_source, server_id, title, year, tmdb_id) "
                         "VALUES ('jellyfin', 'jf1', 'Black Butler', 2008, 500)").lastrowid
    # jellyfin knows the first season only, so its count runs short
    season_id = conn.execute("INSERT INTO seasons (show_id, season_number) VALUES (?, 1)",
                             (other,)).lastrowid
    for ep in range(1, 13):
        conn.execute("INSERT INTO episodes (show_id, season_id, season_number, episode_number, has_file) "
                     "VALUES (?,?,1,?,0)", (other, season_id, ep))
    conn.commit()
    conn.close()
    # the plex row owns S02E08 and counts 24 + 8
    assert db.episode_naming_show_id(500, 2, 8) == show_id
    rows = {r["episode_id"]: r for r in db.rename_owned_episode_files()}
    (row,) = rows.values()
    look = absolute_lookup(db, {"tmdb_id": 500}, {"episode_template": _ANIME_TPL})
    assert look(2, 8) == row["absolute_number"] == 32
    # S01E05 has no file anywhere: the row that lists it names it
    assert db.episode_naming_show_id(500, 1, 5) == show_id   # both list it: the first
    # a library re-grab counts on its own row
    regrab = absolute_lookup(db, {"tmdb_id": 500}, {"episode_template": _ANIME_TPL},
                             {"kind": "show", "media_source": "library", "media_id": other})
    assert regrab(1, 5) == db.episode_absolute_number(500, 1, 5, show_id=other) == 5
    assert regrab(2, 8) is None   # jellyfin's row doesn't list it


def test_naming_check_renders_the_number_and_names_a_special_without_one(db, tmp_path):
    _show_id, reals = _seed_show(db, tmp_path, files=((2, 8), (0, 1)))
    settings = {"episode_template": "$series/Season $season/$series - S$seasonE$episode - $absolute"}
    rows = {(r["season"], r["episode"]): r for r in db.repair_library_files() if r["scope"] == "episode"}
    root = str(tmp_path / "TV")
    want = expected_path(rows[(2, 8)], str(reals[(2, 8)]), [root], settings)
    assert want == str(Path(root) / "Black Butler" / "Season 02" / "Black Butler - S02E08 - 032.mkv")
    assert naming_fields(rows[(0, 1)])["absolute"] is None
    # season 0 never has an absolute number: named without one, not held
    assert expected_path(rows[(0, 1)], str(reals[(0, 1)]), [root], settings) == \
        str(Path(root) / "Black Butler" / "Season 00" / "Black Butler - S00E01.mkv")


# ── the importer ─────────────────────────────────────────────────────────────

def _episode_dl(season=2, episode=8):
    return {"kind": "show", "title": "Black Butler", "source": "soulseek",
            "release_title": "Black.Butler.S%02dE%02d.1080p.WEB.mkv" % (season, episode),
            "size_bytes": 1_000_000_000, "target_dir": "/lib/anime",
            "search_ctx": json.dumps({"scope": "episode", "title": "Black Butler",
                                      "season": season, "episode": episode})}


def test_importer_names_a_new_episode_by_its_absolute_number():
    settings = organization.normalize({"episode_template": _ANIME_TPL})
    p = importer.plan_import(_episode_dl(), "/dl/x/Black.Butler.S02E08.1080p.WEB.mkv",
                             list_dir=lambda d: [], settings=settings,
                             absolute_of=lambda s, e: 24 + e if s == 2 else e)
    assert p["action"] == "import"
    assert p["dest"]["filename"] == "Black Butler - S02E08 - 032.mkv"


def test_importer_names_a_multi_episode_file_by_both_numbers():
    settings = organization.normalize({"episode_template": _ANIME_TPL})
    dl = _episode_dl(season=1, episode=1)
    dl["release_title"] = "Black.Butler.S01E01-E02.1080p.WEB.mkv"
    p = importer.plan_import(dl, "/dl/x/Black.Butler.S01E01-E02.1080p.WEB.mkv",
                             list_dir=lambda d: [], settings=settings,
                             absolute_of=lambda s, e: e)
    assert p["dest"]["filename"] == "Black Butler - S01E01-E02 - 001-002.mkv"


def test_importer_without_a_number_still_imports():
    settings = organization.normalize({"episode_template": _ANIME_TPL})

    def boom(_s, _e):
        raise RuntimeError("db gone")
    p = importer.plan_import(_episode_dl(), "/dl/x/Black.Butler.S02E08.1080p.WEB.mkv",
                             list_dir=lambda d: [], settings=settings, absolute_of=boom)
    assert p["action"] == "import"
    assert p["dest"]["filename"] == "Black Butler - S02E08.mkv"


def test_absolute_lookup_only_asks_when_the_template_names_it(db, tmp_path):
    _seed_show(db, tmp_path)
    assert absolute_lookup(db, {"tmdb_id": 500}, {"episode_template": "$series - S$seasonE$episode"}) is None
    assert absolute_lookup(db, {"tmdb_id": None}, {"episode_template": _ANIME_TPL}) is None
    look = absolute_lookup(db, {"tmdb_id": 500}, {"episode_template": _ANIME_TPL})
    assert look(2, 8) == 32
    assert look(0, 1) is None


# ── held like a missing id ───────────────────────────────────────────────────

def _anime_library(db, tmp_path):
    db.set_setting("tv_path", str(tmp_path / "TV"))
    organization.save(db, {"episode_template": _ANIME_TPL})


def test_naming_check_says_an_episode_waits_on_its_number(db, tmp_path):
    _show_id, reals = _seed_show(db, tmp_path, files=((2, 8),))
    rows = {(r["season"], r["episode"]): r for r in db.repair_library_files() if r["scope"] == "episode"}
    # a numbered episode whose number isn't known yet
    row = dict(rows[(2, 8)], absolute_number=None)
    check = name_check(row, str(reals[(2, 8)]), [str(tmp_path / "TV")],
                       {"episode_template": _ANIME_TPL})
    assert check == {"path": None, "why": "absolute", "missing": []}


def test_a_special_never_waits_on_a_number():
    s = {"episode_template": _ANIME_TPL}
    assert organization.absolute_unknown("episode", {"season": 0, "episode": 1, "absolute": None}, s) is False
    assert organization.absolute_unknown("episode", {"season": 1, "episode": 0, "absolute": None}, s) is False
    assert organization.absolute_unknown("episode", {"season": None, "episode": None}, s) is False
    assert organization.absolute_unknown("episode", {"season": 2, "episode": 8, "absolute": None}, s) is True


def test_an_episode_already_named_without_its_number_is_not_held(db, tmp_path):
    # nothing to lose: the name it would get without a number is the one it has
    _show_id, reals = _seed_show(db, tmp_path, files=((2, 8),))
    bare = tmp_path / "TV" / "Black Butler" / "Season 02" / "Black Butler - S02E08.mkv"
    bare.parent.mkdir(parents=True)
    os.replace(reals[(2, 8)], bare)
    conn = db._get_connection()
    conn.execute("UPDATE media_files SET relative_path=?", (str(bare),))
    conn.commit()
    conn.close()
    row = next(r for r in db.repair_library_files() if r["scope"] == "episode")
    row = dict(row, absolute_number=None)
    check = name_check(row, str(bare), [str(tmp_path / "TV")], {"episode_template": _ANIME_TPL})
    assert check == {"path": str(bare), "why": None, "missing": []}


def test_mass_rename_names_a_special_without_a_number(db, tmp_path, monkeypatch):
    from api import video as videoapi
    from core.video import mass_rename
    _seed_show(db, tmp_path, files=((2, 8), (0, 1)))
    _anime_library(db, tmp_path)
    monkeypatch.setattr(videoapi, "_video_db", db)
    out = mass_rename.preview()
    assert [os.path.basename(e["proposed"]) for e in out["entries"]] == \
        ["Black Butler - S00E01.mkv", "Black Butler - S02E08 - 032.mkv"]
    assert out["held"] == 0   # a special never has a number to wait on


def test_mass_rename_holds_an_episode_waiting_on_its_number(db, tmp_path, monkeypatch):
    from api import video as videoapi
    from core.video import mass_rename
    _seed_show(db, tmp_path, files=((2, 8),))
    _anime_library(db, tmp_path)
    monkeypatch.setattr(videoapi, "_video_db", db)
    real_rows = db.rename_owned_episode_files
    monkeypatch.setattr(db, "rename_owned_episode_files",
                        lambda: [dict(r, absolute_number=None) for r in real_rows()])
    out = mass_rename.preview()
    assert out["entries"] == [] and out["held"] == 1


def test_the_library_tab_says_an_episode_waits_on_its_number(db, tmp_path):
    from core.video import file_ops
    _seed_show(db, tmp_path, files=((2, 8),))
    _anime_library(db, tmp_path)
    fid = db._get_connection().execute("SELECT id FROM media_files").fetchone()[0]
    row = dict(db.library_file_row(fid), absolute_number=None)
    plan = file_ops.rename_plan(db, fid, row=row)
    assert "absolute number" in plan["error"] and "outside your library folders" not in plan["error"]


def test_the_library_tab_names_a_special_without_a_number(db, tmp_path):
    from core.video import file_ops
    _seed_show(db, tmp_path, files=((0, 1),))
    _anime_library(db, tmp_path)
    fid = db._get_connection().execute("SELECT id FROM media_files").fetchone()[0]
    plan = file_ops.rename_plan(db, fid)
    assert os.path.basename(plan["expected"]) == "Black Butler - S00E01.mkv"


def test_a_move_takes_an_episode_waiting_on_its_number_along(db, tmp_path, monkeypatch):
    # moving the show to another library must not leave such a file behind:
    # it moves under its own name, like a file waiting on an id
    from core.video import bulk_ops, libraries
    show_id, reals = _seed_show(db, tmp_path, files=((2, 8), (0, 1)))
    _anime_library(db, tmp_path)
    lib = libraries.create_library(db, {"kind": "show", "name": "Anime", "path": str(tmp_path / "Anime")})
    real_rows = db.library_title_files
    # S02E08's number isn't known yet; the special never has one
    monkeypatch.setattr(db, "library_title_files", lambda kind, item: [
        dict(r, absolute_number=None) if (r["season"], r["episode"]) == (2, 8) else r
        for r in real_rows(kind, item)])
    plan = bulk_ops.move_plan(db, "show", show_id, lib["id"])
    assert plan["skipped"] == [] and plan["files"] == 2
    to = {os.path.basename(m["from"]): m["to"] for m in plan["moves"]}
    assert to["s02e08.mkv"] == str(tmp_path / "Anime" / "Black Butler" / "s02e08.mkv")   # its own name
    assert to["s00e01.mkv"] == str(tmp_path / "Anime" / "Black Butler" / "Season 00" / "Black Butler - S00E01.mkv")


def test_a_move_names_episodes_by_their_numbers(db, tmp_path):
    from core.video import bulk_ops, libraries
    show_id, reals = _seed_show(db, tmp_path, files=((2, 8), (0, 1)))
    _anime_library(db, tmp_path)
    lib = libraries.create_library(db, {"kind": "show", "name": "Anime", "path": str(tmp_path / "Anime")})
    plan = bulk_ops.move_plan(db, "show", show_id, lib["id"])
    assert plan["skipped"] == [] and plan["files"] == 2
    to = {os.path.basename(m["from"]): m["to"] for m in plan["moves"]}
    assert to["s02e08.mkv"] == str(tmp_path / "Anime" / "Black Butler" / "Season 02" / "Black Butler - S02E08 - 032.mkv")
    assert to["s00e01.mkv"] == str(tmp_path / "Anime" / "Black Butler" / "Season 00" / "Black Butler - S00E01.mkv")
