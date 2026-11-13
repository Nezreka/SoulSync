"""#2052: two shows with one title never share a folder.

The Office (2005) was in the library folder "The Office". The Office (2001)
rendered to the same folder, its S01E01 was read as a better copy of the other
show's S01E01, and it replaced it."""

from __future__ import annotations

import json

import pytest

from core.video import importer
from core.video.show_folder import series_folder_name
from database.video_database import VideoDatabase

US, UK = 2316, 2996
NFO = '<tvshow><uniqueid type="tmdb" default="true">%d</uniqueid></tvshow>'


class _Db:
    def __init__(self, files=(), placements=()):
        self.files, self.placements = list(files), list(placements)

    def same_titled_show_files(self, title, *, exclude_tmdb_id=None):
        return [f for f in self.files if f["tmdb_id"] != exclude_tmdb_id]

    def show_download_placements(self, title):
        return self.placements


def _name(db, tmdb_id=UK, year=2001, nfo=None, settings=None):
    def read_text(path):
        if nfo and path in nfo:
            return nfo[path]
        raise OSError(path)
    return series_folder_name(db, settings or {}, tmdb_id=tmdb_id, title="The Office", year=year,
                              root="/lib/tv", tmdb_of=lambda row: row["tmdb"], read_text=read_text)


def test_the_first_show_keeps_the_plain_folder():
    assert _name(_Db()) is None


def test_a_show_the_library_owns_under_that_name_sends_the_next_one_to_its_year():
    db = _Db(files=[{"tmdb_id": US, "path": "/media/TV/The Office/Season 01/The Office - S01E01.mkv"}])
    assert _name(db) == "The Office (2001)"
    # letter case and a windows path are the same folder
    db = _Db(files=[{"tmdb_id": US, "path": "D:\\TV\\the office\\Season 01\\x.mkv"}])
    assert _name(db) == "The Office (2001)"


def test_a_same_titled_show_filed_elsewhere_does_not_take_the_plain_folder():
    db = _Db(files=[{"tmdb_id": US, "path": "/media/TV/The Office (US)/Season 01/x.mkv"}])
    assert _name(db) is None


def test_a_download_placed_before_any_scan_counts(tmp_path):
    """the reporter's order: US downloaded, then UK, with no scan in between."""
    db = _Db(placements=[{"tmdb": US, "dest_path": "/lib/tv/The Office/Season 01/The Office - S01E01 - Pilot.mkv"}])
    assert _name(db) == "The Office (2001)"


def test_a_show_follows_its_own_earlier_episodes():
    db = _Db(placements=[
        {"tmdb": US, "dest_path": "/lib/tv/The Office/Season 01/a.mkv"},
        {"tmdb": UK, "dest_path": "/lib/tv/The Office (2001)/Season 01/b.mkv"},
    ])
    assert _name(db, UK, 2001) == "The Office (2001)"
    # and the one that was there first is not pushed out of its folder by the other
    assert _name(db, US, 2005) is None


def test_the_folders_own_nfo_names_another_show():
    """a library brought over from sonarr, never scanned: nothing in the
    database or the download history, but the folder says whose it is."""
    nfo = {"/lib/tv/The Office/tvshow.nfo": NFO % US}
    assert _name(_Db(), nfo=nfo) == "The Office (2001)"
    # its own nfo is its own folder
    assert _name(_Db(), US, 2005, nfo=nfo) is None
    # kodi's older <tmdbid> tag reads too
    assert _name(_Db(), nfo={"/lib/tv/The Office/tvshow.nfo": "<tvshow><tmdbid>%d</tmdbid></tvshow>" % US}) \
        == "The Office (2001)"


def test_the_same_year_falls_back_to_the_id():
    db = _Db(placements=[
        {"tmdb": US, "dest_path": "/lib/tv/The Office/Season 01/a.mkv"},
        {"tmdb": 77, "dest_path": "/lib/tv/The Office (2001)/Season 01/b.mkv"},
    ])
    assert _name(db) == "The Office [tmdb-%d]" % UK
    # no year at all goes straight to the id
    db = _Db(placements=[{"tmdb": US, "dest_path": "/lib/tv/The Office/Season 01/a.mkv"}])
    assert _name(db, year=None) == "The Office [tmdb-%d]" % UK


def test_a_template_that_already_names_the_year_needs_nothing():
    settings = {"episode_template": "$series ($year)/Season $season/$series - S$seasonE$episode"}
    db = _Db(files=[{"tmdb_id": US, "path": "/media/TV/The Office (2005)/Season 01/x.mkv"}])
    assert _name(db, settings=settings) is None


def test_a_template_with_no_series_folder_is_left_alone():
    db = _Db(files=[{"tmdb_id": US, "path": "/media/TV/The Office/Season 01/x.mkv"}])
    assert _name(db, settings={"episode_template": "Season $season/$series - S$seasonE$episode"}) is None


def test_nothing_to_go_on_changes_nothing():
    assert series_folder_name(_Db(), {}, tmdb_id=None, title="The Office") is None
    assert series_folder_name(_Db(), {}, tmdb_id=UK, title="") is None


# ── the importer files it there, and never over the other show ───────────────

def _uk_dl():
    return {"kind": "show", "title": "The Office", "source": "torrent", "target_dir": "/lib/tv",
            "release_title": "The.Office.UK.S01E01.1080p.BluRay.x264", "size_bytes": 2_000_000_000,
            "search_ctx": json.dumps({"scope": "episode", "title": "The Office", "year": 2001,
                                      "season": 1, "episode": 1, "episode_title": "Downsize"})}


def test_the_second_show_is_imported_beside_the_first_not_over_it():
    us_file = "The Office - S01E01 - Pilot WEBDL-720p.mkv"
    listing = {"/lib/tv/The Office/Season 01": [us_file]}
    plan = importer.plan_import(_uk_dl(), "/dl/office.uk.s01e01.mkv",
                                list_dir=lambda d: listing.get(d, []),
                                series_name="The Office (2001)")
    assert plan["action"] == "import"
    assert plan["dest"]["dir"] == "/lib/tv/The Office (2001)/Season 01"
    assert plan["dest"]["filename"].startswith("The Office (2001) - S01E01 - Downsize")
    assert "replace_path" not in plan


def test_without_the_name_it_was_an_upgrade_over_the_other_show():
    """what happened before: the guard this file is about."""
    us_file = "The Office - S01E01 - Pilot WEBDL-720p.mkv"
    listing = {"/lib/tv/The Office/Season 01": [us_file]}
    plan = importer.plan_import(_uk_dl(), "/dl/office.uk.s01e01.mkv",
                                list_dir=lambda d: listing.get(d, []))
    assert plan["action"] == "upgrade" and plan["replace_path"].endswith(us_file)


def test_an_owned_show_keeps_its_own_folder_and_name():
    plan = importer.plan_import(_uk_dl(), "/dl/office.uk.s01e01.mkv", list_dir=lambda d: [],
                                library_dir="/lib/tv/The Office UK/Season 1",
                                series_name="The Office (2001)")
    assert plan["dest"]["dir"] == "/lib/tv/The Office UK/Season 1"
    assert plan["dest"]["filename"].startswith("The Office - S01E01")


# ── the two reads ────────────────────────────────────────────────────────────

@pytest.fixture
def vdb(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def test_finished_downloads_of_a_title_are_listed_with_where_they_went(vdb):
    def add(status="completed", dest="/lib/tv/The Office/Season 01/a.mkv", **over):
        row = {"kind": "show", "title": "The Office", "media_id": str(US), "media_source": "tmdb"}
        row.update(over)
        dl_id = vdb.add_video_download(row)
        vdb.update_video_download(dl_id, status=status, dest_path=dest)

    add()
    add(title="the office", media_id=str(UK), dest="/lib/tv/The Office (2001)/Season 01/b.mkv")
    add(status="downloading", dest="")
    add(kind="movie")
    add(title="Parks and Recreation")
    rows = vdb.show_download_placements("The Office")
    assert sorted(r["media_id"] for r in rows) == [str(US), str(UK)]
    assert vdb.show_download_placements("") == []


def test_another_owned_show_of_the_same_title_gives_up_where_it_lives(vdb):
    def show(tmdb_id, sid, path, title="The Office"):
        vdb.upsert_show_tree("plex", {
            "server_id": sid, "title": title, "tmdb_id": tmdb_id,
            "seasons": [{"season_number": 1, "episodes": [
                {"episode_number": 1, "title": "E1",
                 "file": {"relative_path": path, "size_bytes": 10}}]}]})

    show(US, "s-us", "/media/TV/The Office/Season 01/us.mkv")
    show(UK, "s-uk", "/media/TV/The Office (2001)/Season 01/uk.mkv")
    show(5, "s-other", "/media/TV/Parks/Season 01/p.mkv", title="Parks and Recreation")
    rows = vdb.same_titled_show_files("the office", exclude_tmdb_id=UK)
    assert rows == [{"tmdb_id": US, "path": "/media/TV/The Office/Season 01/us.mkv"}]
    # and through the whole question: the US show keeps the folder it is in
    assert series_folder_name(vdb, {}, tmdb_id=US, title="The Office", year=2005, root="/lib/tv",
                              read_text=lambda p: (_ for _ in ()).throw(OSError())) is None
    assert series_folder_name(vdb, {}, tmdb_id=99, title="The Office", year=1999, root="/lib/tv",
                              read_text=lambda p: (_ for _ in ()).throw(OSError())) == "The Office (1999)"
