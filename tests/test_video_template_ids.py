"""The $tmdbid / $imdbid / $tvdbid naming tokens carry the title's real ids --
an episode's are its show's -- at import AND in both re-namers, and a brace
group an unknown id leaves empty ('{imdb-}') is dropped like an empty '()'.

Before: $imdbid was never filled, an episode template had no $imdbid or
$tmdbid token at all (it rendered as the literal text), and $tmdbid/$tvdbid
were the download's media_id -- SoulSync's own library row id for a re-grab,
and never a TVDB id."""

from __future__ import annotations

import pytest

from core.video import organization
from core.video.importer import plan_import
from database.video_database import VideoDatabase
from tests.test_video_importer import FakeFS, _episode_dl, _movie_dl

SONARR = "$series ($year) {imdb-$imdbid}/Season $season/$series ($year) - S$seasonE$episode [$quality]"
RADARR = "$title ($year)/$title ($year) {imdb-$imdbid} [$quality]"


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _ep(**kw):
    f = {"series": "Firefly", "year": 2002, "season": 1, "episode": 2, "quality": "Bluray-1080p"}
    f.update(kw)
    return f


# ── the renderer ─────────────────────────────────────────────────────────────
def test_episode_tokens_render_the_shows_ids():
    out = organization.render_path("episode", "/tv", _ep(imdbid="tt0303461", tmdbid=1437,
                                                         tvdbid=78874),
                                   {"episode_template": "$series {imdb-$imdbid} {tmdb-$tmdbid} "
                                                        "{tvdb-$tvdbid}/x"}, ".mkv")
    assert out["dir"] == "/tv/Firefly {imdb-tt0303461} {tmdb-1437} {tvdb-78874}"


def test_a_bare_imdb_number_gains_its_tt():
    out = organization.render_path("episode", "/tv", _ep(imdbid="0303461"),
                                   {"episode_template": SONARR}, ".mkv")
    assert out["dir"] == "/tv/Firefly (2002) {imdb-tt0303461}/Season 01"


def test_sonarr_shaped_template_reproduces_the_sonarr_folder():
    out = organization.render_path("episode", "/tv", _ep(imdbid="tt0303461"),
                                   {"episode_template": SONARR}, ".mkv")
    assert out["path"] == ("/tv/Firefly (2002) {imdb-tt0303461}/Season 01/"
                           "Firefly (2002) - S01E02 [Bluray-1080p].mkv")


@pytest.mark.parametrize("template, expect", [
    ("$series ($year) {imdb-$imdbid}/f", "Firefly (2002)"),
    ("$series {$imdbid}/f", "Firefly"),
    ("$series - {tvdb-$tvdbid} - extra/f", "Firefly - extra"),
    ("{imdb-$imdbid} $series/f", "Firefly"),
    ("$series ({tmdb-$tmdbid})/f", "Firefly"),
])
def test_an_unknown_id_leaves_no_empty_brace_group(template, expect):
    out = organization.render_path("episode", "/tv", _ep(), {"episode_template": template}, ".mkv")
    assert out["dir"] == "/tv/" + expect


def test_movie_edition_and_imdb_groups_drop_when_empty_and_stay_when_filled():
    t = {"movie_template": "$title ($year) {edition-$edition}/$title ($year) {imdb-$imdbid}"}
    bare = organization.render_path("movie", "/m", {"title": "Alien", "year": 1979}, t, ".mkv")
    assert bare["path"] == "/m/Alien (1979)/Alien (1979).mkv"
    full = organization.render_path("movie", "/m", {"title": "Alien", "year": 1979,
                                                    "edition": "Directors Cut",
                                                    "imdbid": "tt0078748"}, t, ".mkv")
    assert full["path"] == "/m/Alien (1979) {edition-Directors Cut}/Alien (1979) {imdb-tt0078748}.mkv"


def test_braces_that_hold_real_text_are_kept():
    out = organization.render_path("episode", "/tv", _ep(series="Show {Uncut}"),
                                   {"episode_template": "$series/f"}, ".mkv")
    assert out["dir"] == "/tv/Show {Uncut}"


# ── the importer ─────────────────────────────────────────────────────────────
def test_import_renders_the_ids_it_is_given():
    dl = _episode_dl("Breaking.Bad.S01E01.1080p.BluRay.x264-GRP")
    plan = plan_import(dl, "/dl/x.mkv", list_dir=FakeFS().list_dir,
                       settings={"episode_template": "$series {imdb-$imdbid} {tvdb-$tvdbid}/f"},
                       ids={"tmdb_id": 1396, "imdb_id": "tt0903747", "tvdb_id": 81189})
    assert plan["dest"]["dir"] == "/lib/tv/Breaking Bad {imdb-tt0903747} {tvdb-81189}"


def test_a_library_row_id_never_reaches_an_id_token():
    """A re-grab of an owned title carries SoulSync's own row id as media_id."""
    t = {"movie_template": "$title {tmdb-$tmdbid}/f", "episode_template": "$series {tvdb-$tvdbid}/f"}
    mv = dict(_movie_dl("The.Matrix.1999.1080p.BluRay.x264"), media_source="library", media_id=42)
    assert plan_import(mv, "/dl/m.mkv", list_dir=FakeFS().list_dir,
                       settings=t)["dest"]["dir"] == "/lib/movies/The Matrix"
    ep = dict(_episode_dl("Breaking.Bad.S01E01.1080p.WEB.x264"), media_source="tmdb", media_id=1396)
    # a TMDB id is not a TVDB id
    assert plan_import(ep, "/dl/e.mkv", list_dir=FakeFS().list_dir,
                       settings=t)["dest"]["dir"] == "/lib/tv/Breaking Bad"


def test_a_tmdb_sourced_grab_keeps_its_tmdb_id_without_resolved_ids():
    mv = dict(_movie_dl("The.Matrix.1999.1080p.BluRay.x264"), media_source="tmdb", media_id=603)
    plan = plan_import(mv, "/dl/m.mkv", list_dir=FakeFS().list_dir,
                       settings={"movie_template": "$title {tmdb-$tmdbid}/f"})
    assert plan["dest"]["dir"] == "/lib/movies/The Matrix {tmdb-603}"


# ── resolving the ids for a download ─────────────────────────────────────────
def _owned_show(db):
    db.upsert_show_tree("plex", {"server_id": "s1", "tmdb_id": 1437, "tvdb_id": 78874,
                                 "imdb_id": "tt0303461", "title": "Firefly", "year": 2002,
                                 "seasons": []})


def _owned_movie(db):
    db.upsert_movie("plex", {"server_id": "m1", "tmdb_id": 603, "imdb_id": "tt0133093",
                             "title": "The Matrix"})


def test_external_ids_by_row_or_tmdb_id(db):
    _owned_show(db)
    _owned_movie(db)
    want = {"tmdb_id": 1437, "imdb_id": "tt0303461", "tvdb_id": 78874}
    assert db.video_external_ids("show", tmdb_id=1437) == want
    show_row = db.library_id_for_tmdb("show", 1437)
    assert db.video_external_ids("show", media_id=show_row) == want
    assert db.video_external_ids("movie", tmdb_id=603) == \
        {"tmdb_id": 603, "imdb_id": "tt0133093", "tvdb_id": None}
    assert db.video_external_ids("show", tmdb_id=999) == \
        {"tmdb_id": None, "imdb_id": None, "tvdb_id": None}


def _never(*_a):
    raise AssertionError("TMDB must not be asked")


def test_template_ids_from_the_library_without_asking_tmdb(db):
    from core.video.download_monitor import template_ids
    _owned_show(db)
    row = db.library_id_for_tmdb("show", 1437)
    s = {"episode_template": SONARR}
    for dl in ({"kind": "show", "media_source": "tmdb", "media_id": "1437"},
               {"kind": "show", "media_source": "library", "media_id": row}):
        assert template_ids(db, dl, s, tmdb_lookup=_never) == \
            {"tmdb_id": 1437, "imdb_id": "tt0303461", "tvdb_id": 78874, "year": 2002}


def test_template_ids_ask_tmdb_only_for_a_used_missing_id(db):
    from core.video.download_monitor import template_ids
    calls = []

    def lookup(kind, tmdb_id):
        calls.append((kind, tmdb_id))
        return {"imdb_id": "tt9999999", "tvdb_id": 12345}

    dl = {"kind": "show", "media_source": "tmdb", "media_id": "777"}
    # template uses no id -> no call, and the tmdb id is still known
    assert template_ids(db, dl, {"episode_template": "$series/f"}, tmdb_lookup=_never)["tmdb_id"] == 777
    # template uses $imdbid of a show not in the library -> one call
    out = template_ids(db, dl, {"episode_template": SONARR}, tmdb_lookup=lookup)
    assert out["imdb_id"] == "tt9999999" and out["tvdb_id"] is None   # tvdb not used here
    assert calls == [("show", 777)]


def test_template_ids_for_a_movie_never_carry_a_tvdb_id(db):
    from core.video.download_monitor import template_ids
    dl = {"kind": "movie", "media_source": "tmdb", "media_id": "603"}
    out = template_ids(db, dl, {"movie_template": RADARR + " {tvdb-$tvdbid}"},
                       tmdb_lookup=lambda k, i: {"imdb_id": "tt0133093", "tvdb_id": 5})
    assert out == {"tmdb_id": 603, "imdb_id": "tt0133093", "tvdb_id": None, "year": None}


def test_template_ids_never_raise(db):
    from core.video.download_monitor import template_ids

    def boom(*_a):
        raise RuntimeError("tmdb down")

    dl = {"kind": "show", "media_source": "tmdb", "media_id": "777"}
    assert template_ids(db, dl, {"episode_template": SONARR}, tmdb_lookup=boom)["imdb_id"] is None
    assert template_ids(db, {"kind": "youtube", "media_id": "x"}, {}) == \
        {"tmdb_id": None, "imdb_id": None, "tvdb_id": None, "year": None}


# ── the re-namers render what the importer rendered ──────────────────────────
def _owned_episode_file(db, tmp_path):
    db.upsert_show_tree("plex", {"server_id": "s1", "tmdb_id": 1437, "tvdb_id": 78874,
                                 "imdb_id": "tt0303461", "title": "Firefly", "year": 2002,
                                 "seasons": [{"season_number": 1, "episodes": [
                                     {"server_id": "e1", "episode_number": 2, "title": "Train Job",
                                      "file": {"relative_path": "/x/f.mkv", "size_bytes": 5,
                                               "quality": "Bluray-1080p"}}]}]})


def test_mass_rename_passes_the_shows_ids(db, tmp_path):
    from core.video.mass_rename import _episode_fields
    _owned_episode_file(db, tmp_path)
    row = db.rename_owned_episode_files()[0]
    out = organization.render_path("episode", "/tv", _episode_fields(row),
                                   {"episode_template": SONARR}, ".mkv")
    assert out["dir"] == "/tv/Firefly (2002) {imdb-tt0303461}/Season 01"


def test_mass_rename_passes_a_movies_imdb_id(db):
    from core.video.mass_rename import _movie_fields
    db.upsert_movie("plex", {"server_id": "m1", "tmdb_id": 603, "imdb_id": "tt0133093",
                             "title": "The Matrix", "year": 1999,
                             "file": {"relative_path": "/x/m.mkv", "size_bytes": 5}})
    row = db.repair_owned_movie_files()[0]
    out = organization.render_path("movie", "/m", _movie_fields(row),
                                   {"movie_template": RADARR}, ".mkv")
    assert out["filename"].startswith("The Matrix (1999) {imdb-tt0133093}")


def test_naming_conformance_renders_the_shows_year_and_ids(db, tmp_path):
    from core.video.repair.naming_conformance import _fields_of
    _owned_episode_file(db, tmp_path)
    row = [r for r in db.repair_library_files() if r["scope"] == "episode"][0]
    out = organization.render_path("episode", "/tv", _fields_of(row),
                                   {"episode_template": SONARR}, ".mkv")
    # before: year was NULL for every episode, and no imdb id was passed
    assert out["dir"] == "/tv/Firefly (2002) {imdb-tt0303461}/Season 01"


def test_naming_conformance_passes_a_movies_imdb_id(db):
    from core.video.repair.naming_conformance import _fields_of
    db.upsert_movie("plex", {"server_id": "m1", "tmdb_id": 603, "imdb_id": "tt0133093",
                             "title": "The Matrix", "year": 1999,
                             "file": {"relative_path": "/x/m.mkv", "size_bytes": 5}})
    row = [r for r in db.repair_library_files() if r["scope"] == "movie"][0]
    out = organization.render_path("movie", "/m", _fields_of(row), {"movie_template": RADARR}, ".mkv")
    assert out["filename"].startswith("The Matrix (1999) {imdb-tt0133093}")
    assert row["tvdb_id"] is None


def test_episode_dl_without_ids_is_unchanged(tmp_path):
    """No id token in the template: same path as before this change."""
    dl = _episode_dl("Breaking.Bad.S01E01.1080p.WEB.x264")
    plan = plan_import(dl, "/dl/e.mkv", list_dir=FakeFS().list_dir)
    assert plan["dest"]["dir"] == "/lib/tv/Breaking Bad/Season 01"


# ── the year a grab carried none of ───────────────────────────────────────────
# A show not in the library yet, and a repair (quality upgrade) grab, reach the
# importer with no year in their search context; the template's $year used to
# render empty ('YAGA {imdb-...}', 'Show - S01E02').
def test_title_year_by_row_or_tmdb_id(db):
    _owned_show(db)
    db.upsert_show_tree("plex", {"server_id": "s2", "tmdb_id": 500, "title": "Aired Only",
                                 "first_air_date": "2019-03-01", "seasons": []})
    _owned_movie(db)
    assert db.video_title_year("show", tmdb_id=1437) == 2002
    assert db.video_title_year("show", media_id=db.library_id_for_tmdb("show", 1437)) == 2002
    assert db.video_title_year("show", tmdb_id=500) == 2019      # first-air year
    assert db.video_title_year("movie", tmdb_id=603) is None      # no year stored
    assert db.video_title_year("show", tmdb_id=999) is None
    assert db.video_title_year("show") is None


def test_template_ids_give_an_owned_shows_year_without_tmdb(db):
    """A repair grab of an owned show: tmdb-sourced, no year in its context."""
    from core.video.download_monitor import template_ids
    _owned_show(db)
    dl = {"kind": "episode", "media_source": "tmdb", "media_id": "1437"}
    assert template_ids(db, dl, {"episode_template": SONARR}, tmdb_lookup=_never)["year"] == 2002


def test_template_ids_ask_tmdb_once_for_a_new_shows_ids_and_year(db):
    from core.video.download_monitor import template_ids
    calls = []

    def lookup(kind, tmdb_id):
        calls.append(tmdb_id)
        return {"imdb_id": "tt39980704", "year": "2026"}

    dl = {"kind": "episode", "media_source": "tmdb", "media_id": "314360"}
    out = template_ids(db, dl, {"episode_template": SONARR}, tmdb_lookup=lookup)
    assert (out["imdb_id"], out["year"], calls) == ("tt39980704", "2026", [314360])
    # a template without $year or ids never asks
    assert template_ids(db, dl, {"episode_template": "$series/f"}, tmdb_lookup=_never)["year"] is None


def test_import_names_by_the_resolved_year_when_the_grab_has_none():
    import json
    dl = _episode_dl("YAGA.S01E01.2160p.WEB.H265-GLHF")
    ctx = json.loads(dl["search_ctx"])
    ctx.update({"title": "YAGA", "year": None, "season": 1, "episode": 1})
    dl = dict(dl, search_ctx=json.dumps(ctx), year=None)
    plan = plan_import(dl, "/dl/YAGA.S01E01.2160p.WEB.H265-GLHF.mkv", list_dir=FakeFS().list_dir,
                       settings={"episode_template": SONARR},
                       ids={"tmdb_id": 314360, "imdb_id": "tt39980704", "year": "2026"})
    assert plan["dest"]["path"] == ("/lib/tv/YAGA (2026) {imdb-tt39980704}/Season 01/"
                                    "YAGA (2026) - S01E01 [WEBDL-2160p].mkv")


def test_the_grabs_own_year_wins_over_the_resolved_one():
    import json
    dl = _episode_dl("Firefly.S01E02.1080p.BluRay.x264-GRP")
    ctx = json.loads(dl["search_ctx"])
    ctx.update({"title": "Firefly", "year": 2002, "season": 1, "episode": 2})
    dl = dict(dl, search_ctx=json.dumps(ctx))
    plan = plan_import(dl, "/dl/Firefly.S01E02.1080p.BluRay.x264-GRP.mkv", list_dir=FakeFS().list_dir,
                       settings={"episode_template": "$series ($year)/f"}, ids={"year": 1999})
    assert plan["dest"]["dir"] == "/lib/tv/Firefly (2002)"
