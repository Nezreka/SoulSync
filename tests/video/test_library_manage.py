"""The "Your library" tab on a title's page, and the fix-match that backs it.

Boulder, Oct 25 2026: "there is no true library management at all ... the
relinking functionality doesnt seem to work that well". Two root causes on
the relink side, both pinned here:

- a TMDB re-point (Manage panel → Matches → Fix) was never locked, so the
  next server scan wrote the server's wrong tmdb id straight back. Repro:
  Dune (1984) matched by Plex as Dune (2021), re-pointed to 841, rescanned,
  back to 438631.
- the re-point kept the OLD title's imdb id, so ratings came from the wrong
  movie forever.

And the tab itself: per-file facts, reachability, subtitles beside the file,
template conformance, rename and delete-to-recycle that never overwrite and
never silently delete for good.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.video import file_ops, identity, library_manage
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    d.set_setting("movies_path", str(tmp_path / "Movies"))
    d.set_setting("tv_path", str(tmp_path / "TV"))
    return d


def _movie(db, *, title="Dune", year=2021, tmdb=438631, imdb="tt1160419", sid="100"):
    return db.upsert_movie("plex", {"server_id": sid, "title": title, "year": year,
                                    "tmdb_id": tmdb, "imdb_id": imdb})


def _add_file(db, *, movie_id=None, episode_id=None, path, size=32, resolution="1080",
              quality=None, write=True, video_codec="h264", audio_codec="aac"):
    if write:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
    conn = db._get_connection()
    cur = conn.execute(
        "INSERT INTO media_files (movie_id, episode_id, relative_path, size_bytes, resolution, "
        "quality, video_codec, audio_codec) VALUES (?,?,?,?,?,?,?,?)",
        (movie_id, episode_id, str(path), size, resolution, quality, video_codec, audio_codec))
    fid = cur.lastrowid
    if movie_id:
        conn.execute("UPDATE movies SET has_file=1 WHERE id=?", (movie_id,))
    if episode_id:
        conn.execute("UPDATE episodes SET has_file=1 WHERE id=?", (episode_id,))
    conn.commit()
    conn.close()
    return fid


def _row(db, sql, *args):
    conn = db._get_connection()
    try:
        r = conn.execute(sql, args).fetchone()
        return tuple(r) if r else None
    finally:
        conn.close()


_DUNE_1984 = {"title": "Dune", "year": "1984", "imdb_id": "tt0087182"}


# ── fix match ────────────────────────────────────────────────────────────────

def test_a_tmdb_rematch_survives_the_next_server_scan(db):
    """the Manage panel's re-point used to be undone by the very next scan."""
    mid = _movie(db)
    assert db.rematch_item("movie", mid, "tmdb", 841)
    _movie(db)   # the next plex scan reports its (wrong) guids again
    assert _row(db, "SELECT tmdb_id FROM movies WHERE id=?", mid) == (841,)


def test_a_tmdb_rematch_drops_the_old_titles_imdb_id(db):
    mid = _movie(db)
    db.rematch_item("movie", mid, "tmdb", 841)
    assert _row(db, "SELECT imdb_id FROM movies WHERE id=?", mid) == (None,)


def test_clearing_a_match_releases_its_lock(db):
    mid = _movie(db)
    db.rematch_item("movie", mid, "tmdb", 841)
    db.rematch_item("movie", mid, "tmdb", None)
    _movie(db)
    assert _row(db, "SELECT tmdb_id FROM movies WHERE id=?", mid) == (438631,)


def test_identify_fills_the_new_ids_and_name_and_locks_them(db):
    mid = _movie(db)
    res = identity.identify(db, "movie", mid, 841, lookup=lambda k, t: dict(_DUNE_1984))
    assert res["success"] and res["year"] == 1984
    _movie(db)   # rescan
    tmdb, imdb, year, locked = _row(db, "SELECT tmdb_id, imdb_id, year, locked_fields FROM movies WHERE id=?", mid)
    assert (tmdb, imdb, year) == (841, "tt0087182", 1984)
    assert {"tmdb_id", "imdb_id", "year"} <= set(json.loads(locked))


def test_identify_changes_nothing_when_tmdb_does_not_answer(db):
    mid = _movie(db)
    res = identity.identify(db, "movie", mid, 841, lookup=lambda k, t: None)
    assert not res["success"]
    assert _row(db, "SELECT tmdb_id, imdb_id, locked_fields FROM movies WHERE id=?", mid) == \
        (438631, "tt1160419", None)


def test_identify_a_show_takes_tmdbs_tvdb_id(db):
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "Lucky", "year": 2022,
                                       "tmdb_id": 1, "tvdb_id": 9, "seasons": []})
    res = identity.identify(db, "show", sid, 2,
                            lookup=lambda k, t: {"title": "Lucky", "year": "2026", "tvdb_id": 77})
    assert res["success"]
    assert _row(db, "SELECT tmdb_id, tvdb_id, tvdb_match_status FROM shows WHERE id=?", sid) == \
        (2, 77, "matched")


def test_release_hands_identity_back_to_the_server(db):
    mid = _movie(db)
    identity.identify(db, "movie", mid, 841, lookup=lambda k, t: dict(_DUNE_1984))
    assert identity.release(db, "movie", mid)["success"]
    _movie(db)
    assert _row(db, "SELECT tmdb_id, imdb_id, year FROM movies WHERE id=?", mid) == \
        (438631, "tt1160419", 2021)


def test_a_worker_match_never_overrides_a_locked_id(db):
    mid = _movie(db)
    db.rematch_item("movie", mid, "tmdb", 841)
    db.enrichment_apply("tmdb", "movie", mid, True, external_id=438631, metadata={})
    assert _row(db, "SELECT tmdb_id FROM movies WHERE id=?", mid) == (841,)


def test_identify_clears_stale_i_have_this_links(db):
    mid = _movie(db)
    db.set_manual_match("movie", 555, mid)
    identity.identify(db, "movie", mid, 841, lookup=lambda k, t: dict(_DUNE_1984))
    assert db.manual_match_for_library("movie", mid) is None


# ── the tab ──────────────────────────────────────────────────────────────────

def test_movie_tab_reports_each_file_with_disk_facts(db, tmp_path):
    mid = _movie(db, title="Heat", year=1995, tmdb=949, imdb="tt0113277")
    good = tmp_path / "Movies/Heat (1995)/Heat (1995) Bluray-1080p.mkv"
    fid = _add_file(db, movie_id=mid, path=good, size=64, quality="Bluray-1080p")
    Path(str(good.with_suffix("")) + ".en.srt").write_text("1")
    Path(str(good.with_suffix("")) + ".es.forced.srt").write_text("1")
    far = "/nowhere/server/Heat.720p.mkv"
    _add_file(db, movie_id=mid, path=far, size=16, resolution="720", write=False)

    tab = library_manage.movie_library(db, mid)
    assert tab["summary"]["files"] == 2 and tab["summary"]["size_bytes"] == 80
    by_id = {f["id"]: f for f in tab["files"]}
    here = by_id[fid]
    assert here["reachable"] and here["best"]
    assert {(s["language"], s["forced"]) for s in here["subtitles"]} == {("en", False), ("es", True)}
    assert here["naming"]["conforms"] is True
    away = next(f for f in tab["files"] if f["id"] != fid)
    assert away["reachable"] is False and away["subtitles"] is None and away["naming"] is None
    assert tab["copies"]["spans_drives"] is True


def test_movie_tab_shows_the_template_name_a_messy_file_would_get(db, tmp_path):
    mid = _movie(db, title="Ronin", year=1998, tmdb=8195, imdb="tt0122690")
    _add_file(db, movie_id=mid, path=tmp_path / "Movies/ronin.1998.mkv")
    f = library_manage.movie_library(db, mid)["files"][0]
    assert f["naming"]["conforms"] is False
    assert f["naming"]["expected_name"].startswith("Ronin (1998)")


def _show(db, tmp_path):
    sid = db.upsert_show_tree("plex", {
        "server_id": "bb", "title": "Breaking Bad", "year": 2008, "tmdb_id": 1396,
        "seasons": [{"server_id": "s1", "season_number": 1, "episodes": [
            {"server_id": "e1", "episode_number": 1, "title": "Pilot", "air_date": "2008-01-20"},
            {"server_id": "e2", "episode_number": 2, "title": "Cat's in the Bag", "air_date": "2008-01-27"}]},
                    {"server_id": "s2", "season_number": 2, "episodes": [
            {"server_id": "e3", "episode_number": 1, "title": "Seven Thirty-Seven", "air_date": "2009-03-08"},
            {"server_id": "e4", "episode_number": 2, "title": "Future", "air_date": "2999-01-01"}]}]})
    eps = {(s, e): i for i, s, e in (db._get_connection().execute(
        "SELECT id, season_number, episode_number FROM episodes WHERE show_id=?", (sid,)).fetchall())}
    return sid, eps


def test_show_tab_counts_seasons_and_only_probes_the_asked_season(db, tmp_path):
    sid, eps = _show(db, tmp_path)
    _add_file(db, episode_id=eps[(1, 1)], resolution="1080",
              path=tmp_path / "TV/Breaking Bad/Season 01/Breaking Bad - S01E01 - Pilot.mkv")
    _add_file(db, episode_id=eps[(2, 1)], resolution="480",
              path=tmp_path / "TV/Breaking Bad/Season 02/Breaking Bad - S02E01.mkv")
    tab = library_manage.show_library(db, sid, season=1, today="2026-10-25")
    s1, s2 = tab["seasons"][0], tab["seasons"][1]
    assert (s1["owned"], s1["missing"], s1["upcoming"]) == (1, 1, 0)
    assert (s2["owned"], s2["missing"], s2["upcoming"]) == (1, 0, 1)
    f1 = s1["episodes"][0]["files"][0]
    f2 = s2["episodes"][0]["files"][0]
    assert f1["reachable"] is True          # the asked season gets the disk checks
    assert "reachable" not in f2            # the rest stay database reads
    assert tab["summary"]["folders"] == [{"path": str(tmp_path / "TV/Breaking Bad").replace("\\", "/"), "files": 2}]
    assert tab["quality"]["below_cutoff"] == 1 and s2["below_cutoff"] == 1


# ── file actions ─────────────────────────────────────────────────────────────

def test_rename_moves_the_file_and_its_subtitles_and_the_stored_path(db, tmp_path):
    mid = _movie(db, title="Ronin", year=1998, tmdb=8195, imdb="tt0122690")
    old = tmp_path / "Movies/ronin.1998.mkv"
    fid = _add_file(db, movie_id=mid, path=old)
    Path(str(old.with_suffix("")) + ".en.srt").write_text("1")
    res = file_ops.rename_to_template(db, fid)
    assert res["success"] and res["action"] == "renamed" and res["sidecars"] == 1
    new = Path(res["path"])
    assert new.exists() and not old.exists()
    assert Path(str(new.with_suffix("")) + ".en.srt").exists()
    assert db.media_file_stored_path(fid) == str(new).replace("\\", "/") or \
        db.media_file_stored_path(fid) == str(new)


def test_rename_never_overwrites(db, tmp_path):
    mid = _movie(db, title="Ronin", year=1998, tmdb=8195, imdb="tt0122690")
    old = tmp_path / "Movies/ronin.1998.mkv"
    fid = _add_file(db, movie_id=mid, path=old)
    want = Path(file_ops.rename_plan(db, fid)["expected"])
    want.parent.mkdir(parents=True, exist_ok=True)
    want.write_bytes(b"theirs")
    res = file_ops.rename_to_template(db, fid)
    assert not res["success"] and old.exists() and want.read_bytes() == b"theirs"


def test_rename_refuses_a_file_it_cannot_reach(db):
    mid = _movie(db)
    fid = _add_file(db, movie_id=mid, path="/nowhere/Dune.mkv", write=False)
    res = file_ops.rename_to_template(db, fid)
    assert not res["success"] and "can't reach" in res["error"]


def test_delete_recycles_and_flips_the_movie_to_missing(db, tmp_path):
    mid = _movie(db, title="Heat", year=1995, tmdb=949, imdb="tt0113277")
    p = tmp_path / "Movies/Heat (1995)/Heat (1995).mkv"
    fid = _add_file(db, movie_id=mid, path=p)
    res = file_ops.delete_to_recycle(db, fid)
    assert res["success"] and res["recycled"] and res["remaining"] == 0
    assert not p.exists() and Path(res["trash_path"]).exists()
    assert "ss_recycle" in res["trash_path"]
    assert _row(db, "SELECT has_file FROM movies WHERE id=?", mid) == (0,)
    assert db.library_file_row(fid) is None


def test_deleting_one_of_two_copies_keeps_the_movie_owned(db, tmp_path):
    mid = _movie(db, title="Heat", year=1995, tmdb=949, imdb="tt0113277")
    a = _add_file(db, movie_id=mid, path=tmp_path / "Movies/Heat (1995)/Heat 1080p.mkv")
    _add_file(db, movie_id=mid, path=tmp_path / "Movies/Heat (1995)/Heat 720p.mkv")
    res = file_ops.delete_to_recycle(db, a)
    assert res["success"] and res["remaining"] == 1
    assert _row(db, "SELECT has_file FROM movies WHERE id=?", mid) == (1,)


def test_delete_refuses_a_file_outside_every_library_folder(db, tmp_path):
    """the recycle helper falls back to a PERMANENT delete for such a file;
    a button in the library tab must never do that silently."""
    mid = _movie(db)
    p = tmp_path / "elsewhere/Dune.mkv"
    fid = _add_file(db, movie_id=mid, path=p)
    res = file_ops.delete_to_recycle(db, fid)
    assert not res["success"] and p.exists() and db.library_file_row(fid)


def test_delete_refuses_a_file_it_cannot_reach(db):
    mid = _movie(db)
    fid = _add_file(db, movie_id=mid, path="/nowhere/Dune.mkv", write=False)
    res = file_ops.delete_to_recycle(db, fid)
    assert not res["success"] and db.library_file_row(fid)


def test_deleting_the_last_episode_file_marks_the_episode_missing(db, tmp_path):
    sid, eps = _show(db, tmp_path)
    p = tmp_path / "TV/Breaking Bad/Season 01/Breaking Bad - S01E01.mkv"
    fid = _add_file(db, episode_id=eps[(1, 1)], path=p)
    assert file_ops.delete_to_recycle(db, fid)["success"]
    assert _row(db, "SELECT has_file FROM episodes WHERE id=?", eps[(1, 1)]) == (0,)


def test_show_folders_fold_plex_style_season_folders_into_the_show():
    """'Breaking Bad/Breaking Bad Season 5' is a season folder too. Read as
    its own folder, every season of a tidy show looked like a split."""
    out = library_manage._show_folders([
        "/m/TV/Breaking Bad/Breaking Bad Season 5/a.mkv",
        "/m/TV/Breaking Bad/Season 01/b.mkv",
        "/x/Breaking.Bad/c.mkv",
    ])
    assert out == [{"path": "/m/TV/Breaking Bad", "files": 2},
                   {"path": "/x/Breaking.Bad", "files": 1}]


# ── the routes ──────────────────────────────────────────────────────────────

def _client(tmp_path, *, is_admin=True):
    from flask import Flask
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "api.db"))
    app = Flask(__name__)

    @app.before_request
    def _stamp_g():
        from flask import g
        g.is_admin = is_admin
        g.can_download = True

    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client(), videoapi._video_db


def test_library_route_hides_paths_from_a_profile_that_is_not_an_admin(tmp_path):
    c, db = _client(tmp_path, is_admin=False)
    mid = _movie(db)
    _add_file(db, movie_id=mid, path="/srv/Movies/Dune (2021)/Dune.mkv", write=False)
    body = c.get(f"/api/video/detail/movie/{mid}/library").get_json()
    assert body["can_manage"] is False
    assert body["files"][0]["path"] is None and body["files"][0]["filename"] == "Dune.mkv"
    (tmp_path / "admin").mkdir()
    c2, db2 = _client(tmp_path / "admin")
    mid2 = _movie(db2)
    _add_file(db2, movie_id=mid2, path="/srv/Movies/Dune (2021)/Dune.mkv", write=False)
    assert c2.get(f"/api/video/detail/movie/{mid2}/library").get_json()["files"][0]["path"].endswith("Dune.mkv")


def test_file_and_identity_writes_are_admin_only(tmp_path):
    c, db = _client(tmp_path, is_admin=False)
    mid = _movie(db)
    fid = _add_file(db, movie_id=mid, path="/srv/Dune.mkv", write=False)
    assert c.post(f"/api/video/files/{fid}/delete", json={}).status_code == 403
    assert c.post(f"/api/video/files/{fid}/rename", json={}).status_code == 403
    assert c.get(f"/api/video/files/{fid}/rename-plan").status_code == 403
    assert c.post(f"/api/video/detail/movie/{mid}/identify", json={"tmdb_id": 841}).status_code == 403
    assert c.post(f"/api/video/detail/movie/{mid}/identify/release", json={}).status_code == 403
    assert db.library_file_row(fid) is not None
