"""A new import adopts a folder that already exists under another letter case.

Linux filesystems are case-sensitive, so a grab titled 'YAGA' used to start a
second show folder beside an existing 'Yaga'. The planner now walks each folder
below the library root and uses the one existing folder that matches ignoring
case. The filesystem is injected, so nothing touches disk.
"""

from __future__ import annotations

import json
import os

from core.video import importer


def _lister(dirs):
    return lambda path: list(dirs.get(str(path), []))


def _episode(title="YAGA", root="/lib/tv", season=1, episode=2):
    return {
        "kind": "show", "title": title, "source": "soulseek",
        "release_title": "%s S%02dE%02d 1080p WEB-DL" % (title, season, episode),
        "size_bytes": 1_000_000_000, "target_dir": root,
        "search_ctx": json.dumps({"scope": "episode", "title": title,
                                  "season": season, "episode": episode}),
    }


def _movie(title="SE7EN", year=1995, root="/lib/movies"):
    return {
        "kind": "movie", "title": title, "year": year, "source": "soulseek",
        "release_title": "%s %d 1080p BluRay" % (title, year),
        "size_bytes": 2_000_000_000, "target_dir": root,
        "search_ctx": json.dumps({"scope": "movie", "title": title, "year": year}),
    }


def _plan(dl, dirs, **kw):
    return importer.plan_import(dl, "/dl/x/file.mkv", list_dir=_lister(dirs), **kw)


def test_show_folder_differing_only_by_case_is_adopted():
    p = _plan(_episode(), {"/lib/tv": ["Yaga"], "/lib/tv/Yaga": ["Season 01"]})
    assert p["action"] == "import"
    assert p["dest"]["dir"] == os.path.join("/lib/tv", "Yaga", "Season 01")
    assert p["dest"]["path"] == os.path.join(p["dest"]["dir"], p["dest"]["filename"])


def test_season_folder_differing_only_by_case_is_adopted():
    dirs = {"/lib/tv": ["YAGA"], "/lib/tv/YAGA": ["season 01"],
            "/lib/tv/YAGA/season 01": ["extra.nfo"]}
    p = _plan(_episode(), dirs)
    assert p["dest"]["dir"] == os.path.join("/lib/tv", "YAGA", "season 01")


def test_movie_folder_differing_only_by_case_is_adopted():
    dirs = {"/lib/movies": ["Se7en (1995)"], "/lib/movies/Se7en (1995)": ["poster.jpg"]}
    p = _plan(_movie(), dirs)
    assert p["action"] == "import"
    assert p["dest"]["dir"] == os.path.join("/lib/movies", "Se7en (1995)")


def test_exact_name_wins_over_a_case_variant():
    dirs = {"/lib/tv": ["YAGA", "Yaga"], "/lib/tv/Yaga": ["Season 01"]}
    p = _plan(_episode(), dirs)
    assert p["dest"]["dir"] == os.path.join("/lib/tv", "YAGA", "Season 01")


def test_two_case_variants_adopt_neither():
    dirs = {"/lib/tv": ["Yaga", "yaga"], "/lib/tv/Yaga": ["Season 01"],
            "/lib/tv/yaga": ["Season 01"]}
    p = _plan(_episode(), dirs)
    assert p["dest"]["dir"] == os.path.join("/lib/tv", "YAGA", "Season 01")


def test_a_variant_that_lists_nothing_is_not_adopted():
    # a file, or an empty folder: nothing to join, keep the rendered name
    p = _plan(_episode(), {"/lib/tv": ["Yaga"]})
    assert p["dest"]["dir"] == os.path.join("/lib/tv", "YAGA", "Season 01")


def test_missing_root_keeps_the_rendered_path():
    p = _plan(_episode(), {})
    assert p["dest"]["dir"] == os.path.join("/lib/tv", "YAGA", "Season 01")


def test_owned_library_dir_is_not_touched():
    owned = os.path.join("/lib/tv", "Somewhere Else", "Season 01")
    dirs = {"/lib/tv": ["Yaga"], "/lib/tv/Yaga": ["Season 01"]}
    p = _plan(_episode(), dirs, library_dir=owned)
    assert p["dest"]["dir"] == owned


def test_forced_placement_goes_where_the_user_pointed():
    target = "/lib/tv"
    dirs = {target: ["Yaga"], "/lib/tv/Yaga": ["Season 01"]}
    override = {"scope": "episode", "title": "YAGA", "season": 1, "episode": 2,
                "target_dir": target}
    p = _plan(_episode(), dirs, force=True, override=override)
    assert p["dest"]["dir"] == os.path.join(target, "YAGA", "Season 01")
