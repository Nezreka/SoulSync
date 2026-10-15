"""a video scan that can't read anything says why instead of finishing green
with 0 items (discord: "deep scan starts and immediately finishes, detects
nothing" on jellyfin). and a jellyfin page that fails is an error, not the
end of the library."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.video.scanner import VideoLibraryScanner
from core.video.sources import JellyfinVideoSource, PlexVideoSource
from database.video_database import VideoDatabase

VIEWS = {"Items": [{"Id": "mv", "Name": "Movies", "CollectionType": "movies"},
                   {"Id": "tv", "Name": "Shows", "CollectionType": "tvshows"}]}


class FakeJellyfin:
    """answers _make_request from a route table; a route mapped to None fails
    the way the real client does (returns None, sets last_error)."""

    def __init__(self, routes):
        self.routes = routes
        self.user_id = "u1"
        self.base_url = "http://jf"
        self.api_key = "k"
        self.last_error = None
        self.calls = []

    def _make_request(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        for key, value in self.routes.items():
            if path.endswith(key):
                value = value(params or {}) if callable(value) else value
                if value is None:
                    self.last_error = "ReadTimeout: timed out"
                return value
        return None


def _movies_page(ids):
    return {"Items": [{"Id": i, "Name": "Movie " + i, "Type": "Movie"} for i in ids],
            "TotalRecordCount": len(ids)}


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _scan(db, source, mode="deep", media_type="all"):
    return VideoLibraryScanner(db).scan_sync(lambda: source, mode=mode, media_type=media_type)


# ── nothing picked / picked library gone ──

def test_no_library_picked_is_an_error_not_an_empty_success(db):
    jf = FakeJellyfin({"/Views": VIEWS, "/Items": _movies_page(["a"])})
    st = _scan(db, JellyfinVideoSource(jf))
    assert st["state"] == "error"
    assert "No Movies or TV library is picked" in st["error"]
    assert db.dashboard_stats()["library"]["movies"] == 0


def test_movie_scan_with_only_tv_picked_names_the_movies_library(db):
    jf = FakeJellyfin({"/Views": VIEWS})
    st = _scan(db, JellyfinVideoSource(jf, tv_lib="Shows"), media_type="movie")
    assert st["state"] == "error"
    assert "No Movies library is picked" in st["error"]


def test_one_kind_picked_is_enough_for_an_all_scan(db):
    """movies-only users leave TV on none. that's a choice, not a problem."""
    jf = FakeJellyfin({"/Views": VIEWS, "/Items": _movies_page(["a", "b"])})
    st = _scan(db, JellyfinVideoSource(jf, movies_lib="Movies"), mode="full")
    assert st["state"] == "done"
    assert st["movies"] == 2


def test_a_renamed_or_hidden_library_is_named_in_the_error(db):
    jf = FakeJellyfin({"/Views": VIEWS})
    st = _scan(db, JellyfinVideoSource(jf, movies_lib="Films", tv_lib="Shows"))
    assert st["state"] == "error"
    assert "Movies library 'Films'" in st["error"]


def test_views_that_fail_to_load_report_the_server_error(db):
    jf = FakeJellyfin({"/Views": None})
    st = _scan(db, JellyfinVideoSource(jf, movies_lib="Movies"))
    assert st["state"] == "error"
    assert "Couldn't list Jellyfin libraries" in st["error"]
    assert "timed out" in st["error"]


def test_plex_reports_the_same_problems(db):
    sections = [SimpleNamespace(type="movie", title="Movies"), SimpleNamespace(type="show", title="TV")]
    server = SimpleNamespace(library=SimpleNamespace(sections=lambda: sections))
    assert "No Movies or TV library" in PlexVideoSource(server).scan_problem("all")
    assert "Movies library 'Films'" in PlexVideoSource(server, movies_lib="Films").scan_problem("all")
    assert PlexVideoSource(server, movies_lib="Movies").scan_problem("all") is None


# ── a failed page ──

def test_a_failed_page_raises_instead_of_ending_the_library():
    def items(params):
        page = dict(_movies_page(["a", "b"]), TotalRecordCount=4)
        return page if params.get("StartIndex") == "0" else None
    jf = FakeJellyfin({"/Items": items})
    pages = JellyfinVideoSource(jf)._paged("/Users/u1/Items", {}, page_size=2)
    assert [it["Id"] for it in (next(pages), next(pages))] == ["a", "b"]
    with pytest.raises(RuntimeError, match="stopped answering at item 2"):
        next(pages)


def test_deep_scan_on_a_timed_out_server_errors_and_prunes_nothing(db):
    seed = FakeJellyfin({"/Views": VIEWS, "/Items": _movies_page(["a", "b", "c"])})
    assert _scan(db, JellyfinVideoSource(seed, movies_lib="Movies"), mode="full")["movies"] == 3

    down = FakeJellyfin({"/Views": VIEWS, "/Items": None})
    st = _scan(db, JellyfinVideoSource(down, movies_lib="Movies"), mode="deep")
    assert st["state"] == "error"
    assert "stopped answering" in st["error"]
    assert db.dashboard_stats()["library"]["movies"] == 3


# ── a fresh install picks the only library ──

import core.video.sources as vs  # noqa: E402


def test_only_library_of_a_kind_is_picked_and_saved(db):
    libs = {"movies": [{"title": "Movies"}], "tv": [{"title": "Shows"}]}
    sel = vs._auto_pick_single_libraries("jellyfin", libs, {"movies": None, "tv": None}, db=db)
    assert sel == {"movies": "Movies", "tv": "Shows"}
    assert db.get_library_selection("jellyfin") == {"movies": "Movies", "tv": "Shows"}


def test_several_libraries_stay_unpicked(db):
    libs = {"movies": [{"title": "Movies"}, {"title": "4K Movies"}], "tv": []}
    sel = vs._auto_pick_single_libraries("jellyfin", libs, {"movies": None, "tv": None}, db=db)
    assert sel == {"movies": None, "tv": None}
    assert db.get_library_selection("jellyfin") == {"movies": None, "tv": None}


def test_none_picked_on_purpose_is_left_alone(db):
    """'' is the user choosing "— None —" (a movies-only setup), not unset."""
    db.set_library_selection("jellyfin", "Movies", "")
    libs = {"movies": [{"title": "Movies"}], "tv": [{"title": "Shows"}]}
    sel = vs._auto_pick_single_libraries("jellyfin", libs, db.get_library_selection("jellyfin"), db=db)
    assert sel == {"movies": "Movies", "tv": ""}
    assert db.get_setting("jellyfin.tv_library") == ""


def test_a_fresh_install_scan_reads_the_only_libraries(db, monkeypatch):
    """the c5pie case end to end: nothing ever picked, one movies and one tv
    library on jellyfin, deep scan -> it scans them instead of reading nothing."""
    shows = {"Items": [{"Id": "s1", "Name": "Show", "Type": "Series"}], "TotalRecordCount": 1}

    def items(params):
        return shows if params.get("IncludeItemTypes") == "Series" else _movies_page(["a", "b"])
    jf = FakeJellyfin({"/Views": VIEWS, "/Items": items, "/Episodes": {"Items": []}, "/Seasons": {"Items": []}})
    monkeypatch.setattr(vs, "_vdb", lambda d=None: db)
    monkeypatch.setattr(vs, "_load_selection", lambda: db.get_library_selection("jellyfin"))
    monkeypatch.setattr(vs, "_build_source",
                        lambda movies_lib=None, tv_lib=None, **kw: JellyfinVideoSource(jf, movies_lib, tv_lib))
    st = VideoLibraryScanner(db).scan_sync(vs.scan_video_source, mode="deep")
    assert st["state"] == "done", st
    assert (st["movies"], st["shows"]) == (2, 1)
    assert db.get_library_selection("jellyfin") == {"movies": "Movies", "tv": "Shows"}
