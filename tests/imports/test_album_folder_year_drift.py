"""SeadogsBooty, Nov 5: one album, two folders.

six tracks of "Different Route" were post-processed within 19 seconds. five
had no date in their album context and landed in "EP/Different Route", one
had a year and landed in "EP/[2021] Different Route". none of them was in the
library yet, so the existing-folder lookup had nothing to steer them by.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest

import core.imports.paths as paths

_REAL_LIBRARY_YEAR = paths._library_album_year
from core.imports.paths import build_final_path_for_track
from core.library_reorganize import _build_album_info, _build_post_process_context

TEMPLATE = "$albumartist/[$year] $album/$track - $title"


@pytest.fixture(autouse=True)
def root(tmp_path, monkeypatch):
    root = tmp_path / "Music"
    root.mkdir()

    class _Cfg:
        def get(self, key, default=None):
            if key == "soulseek.transfer_path":
                return str(root)
            if key == "file_organization.templates":
                return {"album_path": TEMPLATE}
            return default

        def get_active_media_server(self):
            return None

    monkeypatch.setattr(paths, "_get_config_manager", lambda: _Cfg())
    monkeypatch.setattr("core.library_reorganize._preserve_casing_enabled", lambda: True)
    monkeypatch.setattr("core.library_reorganize._feat_in_title_enabled", lambda: False)
    monkeypatch.setattr(paths, "_library_album_year", lambda *a: "")
    # no provider calls for the disc count: one disc
    monkeypatch.setattr(paths, "_get_album_tracks_for_source", lambda *a, **k: None)
    paths._RECENT_ALBUM_FOLDERS.clear()
    yield root
    paths._RECENT_ALBUM_FOLDERS.clear()


def _build(n, release_date):
    album = {"id": "AL1", "name": "Different Route", "total_tracks": 6,
             "images": [{"url": ""}]}
    if release_date:
        album["release_date"] = release_date
    title = f"Song {n}"
    ctx = _build_post_process_context(
        album, {"name": title, "track_number": n, "disc_number": 1,
                "artists": [{"name": "Priscilla Block"}]},
        "Priscilla Block", "Different Route", 1, local_title=title)
    # the helper is reorganize's, which opts out of folder reuse; a download doesn't
    ctx.pop("_no_album_folder_reuse", None)
    return build_final_path_for_track(
        ctx, ctx["spotify_artist"], _build_album_info(ctx), ".flac", create_dirs=True)[0]


def test_tracks_of_one_album_share_a_folder_when_their_years_disagree(root):
    first = _build(1, "")
    second = _build(2, "2021-03-05")
    assert os.path.dirname(first) == os.path.dirname(second)
    assert len(list((root / "Priscilla Block").iterdir())) == 1


def test_the_race_still_ends_in_one_folder(root):
    dates = ["", "", "2021-03-05", "", "", ""]
    with ThreadPoolExecutor(max_workers=6) as pool:
        built = list(pool.map(lambda a: _build(*a), [(i + 1, d) for i, d in enumerate(dates)]))
    assert len({os.path.dirname(p) for p in built}) == 1
    assert len(list((root / "Priscilla Block").iterdir())) == 1


def test_a_missing_year_comes_from_the_library(root, monkeypatch):
    monkeypatch.setattr(paths, "_library_album_year", lambda artist, album: "2017")
    built = _build(1, "")
    assert os.path.basename(os.path.dirname(built)) == "[2017] Different Route"


def test_another_album_is_not_pulled_in(root):
    one = _build(1, "2017-08-25")
    album = {"id": "AL2", "name": "Welcome to the Opry", "total_tracks": 1,
             "release_date": "2023-01-01", "images": [{"url": ""}]}
    ctx = _build_post_process_context(
        album, {"name": "X", "track_number": 1, "disc_number": 1,
                "artists": [{"name": "Priscilla Block"}]},
        "Priscilla Block", "Welcome to the Opry", 1, local_title="X")
    ctx.pop("_no_album_folder_reuse", None)
    other = build_final_path_for_track(
        ctx, ctx["spotify_artist"], _build_album_info(ctx), ".flac", create_dirs=True)[0]
    assert os.path.dirname(one) != os.path.dirname(other)


def test_the_library_year_lookup(tmp_path, monkeypatch):
    from tests import lib2_seed
    from database.music_database import MusicDatabase

    db = MusicDatabase(str(tmp_path / "lib.db"))
    monkeypatch.setattr("database.music_database.get_database", lambda: db)
    conn = lib2_seed.row_conn(str(tmp_path / "lib.db"))
    lib2_seed.album(conn, "Priscilla Block", "Different Route", year=2017)
    conn.commit()
    conn.close()
    real = _REAL_LIBRARY_YEAR
    assert real("priscilla block", "different route") == "2017"
    assert real("Priscilla Block", "Nope") == ""
    assert real("", "Different Route") == ""
