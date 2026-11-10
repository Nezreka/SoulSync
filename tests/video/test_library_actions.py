"""the Your library tab can fix what it reports.

it used to say "two copies", "split across 5 folders" and "9 files below your
target", and offer nothing for any of them. these pin the fixes: keep the best
copy, act on a chosen set, hand a file to the right episode, and bring a
show's files into one folder without ever leaving a half-written file.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.video import file_ops, library_actions, library_manage
from tests.video.test_library_manage import _add_file, _movie, _row, _show, db  # noqa: F401 - fixtures


def _files(db, sql="SELECT id FROM media_files ORDER BY id"):
    conn = db._get_connection()
    try:
        return [r[0] for r in conn.execute(sql)]
    finally:
        conn.close()


# ── the read model ───────────────────────────────────────────────────────────

def test_the_tab_counts_extra_copies_and_marks_what_is_below_target(db, tmp_path):
    mid = _movie(db)
    _add_file(db, movie_id=mid, size=900, resolution="1080", path=tmp_path / "Movies/Dune/a.mkv")
    _add_file(db, movie_id=mid, size=400, resolution="720", path=tmp_path / "Movies/Dune/b.mkv")
    tab = library_manage.movie_library(db, mid)
    assert tab["extra_copies"] == {"titles": 1, "files": 1, "size_bytes": 400}
    assert [f["best"] for f in tab["files"]] == [True, False]
    assert all("below_cutoff" in f for f in tab["files"])


def test_a_file_says_which_download_it_came_from(db, tmp_path):
    mid = _movie(db)
    path = tmp_path / "Movies/Dune/Dune.2021.1080p.mkv"
    _add_file(db, movie_id=mid, path=path)
    _add_file(db, movie_id=mid, size=10, resolution="720", path=tmp_path / "Movies/Dune/ripped-by-hand.mkv")
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO video_download_history (kind, media_type, title, release_title, source, username, "
        "dest_path, media_id, media_source, outcome, completed_at) VALUES "
        "('movie','movie','Dune','Dune.2021.1080p.WEB-DL-GRP','torrent','1337x',?,?,'tmdb','completed','2026-11-05')",
        (str(path), "438631"))
    conn.commit()
    conn.close()
    by_name = {f["filename"]: f for f in library_manage.movie_library(db, mid)["files"]}
    assert by_name["Dune.2021.1080p.mkv"]["source"] == {
        "release": "Dune.2021.1080p.WEB-DL-GRP", "via": "torrent", "indexer": "1337x", "at": "2026-11-05"}
    # a file SoulSync never downloaded claims no source
    assert "source" not in by_name["ripped-by-hand.mkv"]


def test_show_folders_carry_what_a_folder_is_picked_by(db, tmp_path):
    sid, eps = _show(db, tmp_path)
    _add_file(db, episode_id=eps[(1, 1)], size=50, path=tmp_path / "TV/Breaking Bad/Season 01/e1.mkv")
    _add_file(db, episode_id=eps[(1, 2)], size=70, path=tmp_path / "TV/Breaking Bad/Season 01/e2.mkv")
    _add_file(db, episode_id=eps[(2, 1)], size=30, path=tmp_path / "Other/BB.S02E01.720p-GRP/e3.mkv")
    tab = library_manage.show_library(db, sid, today="2026-10-25")
    main, loose = tab["summary"]["folders"]
    assert (main["name"], main["files"], main["size_bytes"], main["main"]) == ("Breaking Bad", 2, 120, True)
    assert (loose["name"], loose["files"], loose["main"]) == ("BB.S02E01.720p-GRP", 1, False)
    season = tab["seasons"][0]
    assert season["id"] and "has_poster" in season and "has_still" in season["episodes"][0]


# ── extra copies ─────────────────────────────────────────────────────────────

def test_keep_the_best_recycles_the_rest_and_never_the_best(db, tmp_path):
    mid = _movie(db)
    best = _add_file(db, movie_id=mid, size=900, resolution="1080", path=tmp_path / "Movies/Dune/a.mkv")
    small = _add_file(db, movie_id=mid, size=400, resolution="1080", path=tmp_path / "Movies/Dune/b.mkv")
    low = _add_file(db, movie_id=mid, size=999, resolution="720", path=tmp_path / "Movies/Dune/c.mkv")
    plan = library_actions.extra_copies_plan(db, "movie", mid)
    assert plan["groups"][0]["keep"]["file_id"] == best
    assert {f["file_id"] for f in plan["groups"][0]["remove"]} == {small, low}
    assert (plan["files"], plan["size_bytes"]) == (2, 1399)

    res = library_actions.remove_extra_copies(db, "movie", mid)

    assert (res["removed"], res["failed"], res["size_bytes"]) == (2, 0, 1399)
    assert _files(db) == [best]
    assert (tmp_path / "Movies/Dune/a.mkv").exists()
    assert not (tmp_path / "Movies/Dune/b.mkv").exists()
    assert _row(db, "SELECT has_file FROM movies WHERE id=?", mid) == (1,)


def test_a_copy_soulsync_cannot_reach_is_left_and_counted(db, tmp_path):
    mid = _movie(db)
    best = _add_file(db, movie_id=mid, size=900, path=tmp_path / "Movies/Dune/a.mkv")
    far = _add_file(db, movie_id=mid, size=400, path="/mnt/elsewhere/Dune/b.mkv", write=False)
    res = library_actions.remove_extra_copies(db, "movie", mid)
    assert (res["removed"], res["unreachable"]) == (0, 1)
    assert _files(db) == [best, far]


def test_one_copy_of_everything_plans_nothing(db, tmp_path):
    sid, eps = _show(db, tmp_path)
    _add_file(db, episode_id=eps[(1, 1)], path=tmp_path / "TV/Breaking Bad/Season 01/e1.mkv")
    _add_file(db, episode_id=eps[(1, 2)], path=tmp_path / "TV/Breaking Bad/Season 01/e2.mkv")
    assert library_actions.extra_copies_plan(db, "show", sid)["groups"] == []
    assert library_actions.remove_extra_copies(db, "show", sid)["removed"] == 0
    assert len(_files(db)) == 2


def test_extra_copies_are_judged_per_episode(db, tmp_path):
    sid, eps = _show(db, tmp_path)
    keep = _add_file(db, episode_id=eps[(1, 1)], size=90, resolution="1080",
                     path=tmp_path / "TV/Breaking Bad/Season 01/e1-1080.mkv")
    drop = _add_file(db, episode_id=eps[(1, 1)], size=40, resolution="720",
                     path=tmp_path / "TV/Breaking Bad/Season 01/e1-720.mkv")
    only = _add_file(db, episode_id=eps[(1, 2)], size=10, resolution="480",
                     path=tmp_path / "TV/Breaking Bad/Season 01/e2.mkv")
    plan = library_actions.extra_copies_plan(db, "show", sid)
    assert [g["label"] for g in plan["groups"]] == ["S01E01"]
    library_actions.remove_extra_copies(db, "show", sid)
    assert _files(db) == [keep, only] and drop not in _files(db)


# ── bulk ─────────────────────────────────────────────────────────────────────

def test_bulk_delete_counts_each_file_and_carries_on_past_a_failure(db, tmp_path):
    mid = _movie(db)
    a = _add_file(db, movie_id=mid, path=tmp_path / "Movies/Dune/a.mkv")
    far = _add_file(db, movie_id=mid, path="/mnt/elsewhere/Dune/b.mkv", write=False)
    c = _add_file(db, movie_id=mid, path=tmp_path / "Movies/Dune/c.mkv")
    res = library_actions.bulk(db, "delete", [a, far, c])
    assert (res["done"], res["failed"]) == (2, 1)
    assert res["errors"][0]["file_id"] == far
    assert _files(db) == [far]


def test_bulk_refuses_an_action_it_does_not_know(db, tmp_path):
    mid = _movie(db)
    a = _add_file(db, movie_id=mid, path=tmp_path / "Movies/Dune/a.mkv")
    assert library_actions.bulk(db, "shred", [a])["success"] is False
    assert _files(db) == [a] and (tmp_path / "Movies/Dune/a.mkv").exists()


# ── reassign ─────────────────────────────────────────────────────────────────

def test_reassign_moves_the_file_and_both_episodes_follow(db, tmp_path):
    sid, eps = _show(db, tmp_path)
    fid = _add_file(db, episode_id=eps[(1, 1)], path=tmp_path / "TV/Breaking Bad/Season 01/wrong.mkv")
    res = library_actions.reassign_file(db, fid, eps[(1, 2)])
    assert res["success"] and res["action"] == "reassigned"
    assert _row(db, "SELECT episode_id FROM media_files WHERE id=?", fid) == (eps[(1, 2)],)
    assert _row(db, "SELECT has_file FROM episodes WHERE id=?", eps[(1, 1)]) == (0,)
    assert _row(db, "SELECT has_file FROM episodes WHERE id=?", eps[(1, 2)]) == (1,)


def test_reassign_keeps_the_old_episode_owned_while_it_has_another_copy(db, tmp_path):
    sid, eps = _show(db, tmp_path)
    fid = _add_file(db, episode_id=eps[(1, 1)], path=tmp_path / "TV/Breaking Bad/Season 01/a.mkv")
    _add_file(db, episode_id=eps[(1, 1)], path=tmp_path / "TV/Breaking Bad/Season 01/b.mkv")
    library_actions.reassign_file(db, fid, eps[(1, 2)])
    assert _row(db, "SELECT has_file FROM episodes WHERE id=?", eps[(1, 1)]) == (1,)


def test_reassign_never_crosses_shows_or_touches_a_movie(db, tmp_path):
    sid, eps = _show(db, tmp_path)
    other = db.upsert_show_tree("plex", {"server_id": "x", "title": "Other", "year": 2020, "seasons": [
        {"server_id": "xs", "season_number": 1, "episodes": [
            {"server_id": "xe", "episode_number": 1, "title": "One"}]}]})
    other_ep = _row(db, "SELECT id FROM episodes WHERE show_id=?", other)[0]
    fid = _add_file(db, episode_id=eps[(1, 1)], path=tmp_path / "TV/Breaking Bad/Season 01/a.mkv")
    assert library_actions.reassign_file(db, fid, other_ep)["success"] is False
    assert _row(db, "SELECT episode_id FROM media_files WHERE id=?", fid) == (eps[(1, 1)],)
    mid = _movie(db)
    mfid = _add_file(db, movie_id=mid, path=tmp_path / "Movies/Dune/a.mkv")
    assert library_actions.reassign_file(db, mfid, eps[(1, 2)])["success"] is False


# ── moving a file safely ─────────────────────────────────────────────────────

def _no_rename(*_a):
    raise OSError("cross-device link")


def test_a_move_to_another_drive_lands_whole_and_removes_the_source(tmp_path):
    src, dst = tmp_path / "a/file.mkv", tmp_path / "b/file.mkv"
    src.parent.mkdir()
    dst.parent.mkdir()
    src.write_bytes(b"x" * 5000)
    seen = []
    file_ops.safe_move(str(src), str(dst), progress=seen.append, rename=_no_rename)
    assert dst.read_bytes() == b"x" * 5000 and not src.exists()
    assert seen and seen[-1] == 5000
    assert os.listdir(dst.parent) == ["file.mkv"]


def test_a_copy_that_dies_leaves_the_source_and_no_broken_file(tmp_path):
    src, dst = tmp_path / "a/file.mkv", tmp_path / "b/file.mkv"
    src.parent.mkdir()
    dst.parent.mkdir()
    src.write_bytes(b"x" * 5000)

    def die(_copied):
        raise OSError("the share went away")

    with pytest.raises(OSError):
        file_ops.safe_move(str(src), str(dst), progress=die, rename=_no_rename)
    assert src.read_bytes() == b"x" * 5000
    # neither the real name nor a part file is left behind
    assert os.listdir(dst.parent) == []


# ── consolidate ──────────────────────────────────────────────────────────────

def _split_show(db, tmp_path):
    sid, eps = _show(db, tmp_path)
    home = [_add_file(db, episode_id=eps[(1, 1)], size=50,
                      path=tmp_path / "TV/Breaking Bad/Season 1/BB - S01E01.mkv"),
            _add_file(db, episode_id=eps[(2, 1)], size=50,
                      path=tmp_path / "TV/Breaking Bad/Season 2/BB - S02E01.mkv")]
    loose = _add_file(db, episode_id=eps[(1, 2)], size=70,
                      path=tmp_path / "TV2/BB.S01E02.720p-GRP/bb.s01e02.mkv")
    (tmp_path / "TV2/BB.S01E02.720p-GRP/bb.s01e02.en.srt").write_text("subs")
    return sid, eps, home, loose


def test_consolidate_plans_into_the_fullest_folder_using_its_season_names(db, tmp_path):
    sid, eps, home, loose = _split_show(db, tmp_path)
    plan = library_actions.consolidate_plan(db, sid, free_bytes=lambda _p: 10**9)
    assert plan["target"].endswith("TV/Breaking Bad")
    (move,) = plan["moves"]
    assert move["file_id"] == loose
    # "Season 1", the way that folder already names it, not "Season 01"
    assert Path(move["to"]) == tmp_path / "TV/Breaking Bad/Season 1/bb.s01e02.mkv"
    assert (plan["files"], plan["size_bytes"], plan["enough_room"]) == (1, 70, True)


def test_consolidate_moves_the_file_its_subtitles_and_the_stored_path(db, tmp_path):
    sid, eps, home, loose = _split_show(db, tmp_path)
    done = []
    res = library_actions.consolidate_start(db, sid, background=False,
                                            on_done=lambda: done.append(1),
                                            free_bytes=lambda _p: 10**9)
    assert res["success"] and res["files"] == 1
    dest = tmp_path / "TV/Breaking Bad/Season 1"
    assert (dest / "bb.s01e02.mkv").exists() and (dest / "bb.s01e02.en.srt").exists()
    assert not (tmp_path / "TV2/BB.S01E02.720p-GRP").exists()
    stored = _row(db, "SELECT relative_path FROM media_files WHERE id=?", loose)[0]
    assert Path(stored) == dest / "bb.s01e02.mkv"
    state = library_actions.consolidate_state(sid)
    assert (state["running"], state["moved"], state["failed"], state["done"]) == (False, 1, 0, 1)
    assert done == [1]
    # one folder now: nothing left to bring together
    assert "one folder" in library_actions.consolidate_plan(db, sid)["error"]


def test_consolidate_never_overwrites_and_skips_what_it_cannot_reach(db, tmp_path):
    sid, eps, home, loose = _split_show(db, tmp_path)
    (tmp_path / "TV/Breaking Bad/Season 1/bb.s01e02.mkv").write_bytes(b"already here")
    far = _add_file(db, episode_id=eps[(2, 2)], path="/mnt/elsewhere/BB/Season 2/x.mkv", write=False)
    plan = library_actions.consolidate_plan(db, sid, free_bytes=lambda _p: 10**9)
    assert plan["moves"] == []
    assert {s["file_id"]: s["reason"] for s in plan["skipped"]} == {
        loose: "a file with that name is already there", far: "only your media server can see it"}
    assert library_actions.consolidate_start(db, sid, background=False,
                                             free_bytes=lambda _p: 10**9)["success"] is False
    assert (tmp_path / "TV2/BB.S01E02.720p-GRP/bb.s01e02.mkv").exists()
    assert (tmp_path / "TV/Breaking Bad/Season 1/bb.s01e02.mkv").read_bytes() == b"already here"


def test_consolidate_refuses_a_folder_the_show_is_not_in_and_a_full_drive(db, tmp_path, monkeypatch):
    sid, eps, home, loose = _split_show(db, tmp_path)
    assert "already in" in library_actions.consolidate_plan(db, sid, "/somewhere/else")["error"]
    # a copy to another drive needs the room for it
    monkeypatch.setattr(library_actions, "_same_drive", lambda _a, _b: False)
    res = library_actions.consolidate_start(db, sid, background=False, free_bytes=lambda _p: 10)
    assert res["success"] is False and "free space" in res["error"]
    assert (tmp_path / "TV2/BB.S01E02.720p-GRP/bb.s01e02.mkv").exists()


def test_a_stop_is_honoured_between_files(db, tmp_path):
    sid, eps, home, loose = _split_show(db, tmp_path)
    second = _add_file(db, episode_id=eps[(2, 2)], size=20,
                       path=tmp_path / "TV3/BB.S02E02/bb.s02e02.mkv")
    plan = library_actions.consolidate_plan(db, sid, free_bytes=lambda _p: 10**9)
    assert len(plan["moves"]) == 2
    import threading
    job = {"running": True, "done": 0, "moved": 0, "failed": 0, "bytes_done": 0, "current": None,
           "errors": [], "stopped": False, "error": None, "stop": threading.Event()}
    job["stop"].set()
    library_actions.run_consolidate(db, sid, plan, job)
    assert (job["moved"], job["stopped"], job["running"]) == (0, True, False)
    assert (tmp_path / "TV3/BB.S02E02/bb.s02e02.mkv").exists() and second in _files(db)


def test_a_release_folder_inside_a_season_folder_is_still_the_show(db, tmp_path):
    """Severance showed as five folders; one was 'Season 2/www.UIndex.org -
    Severance.S02E10.../file.mkv', which is the show's own folder."""
    out = library_manage._show_folders([
        "/m/TV/Severance/Season 2/a.mkv",
        "/m/TV/Severance/Season 2/Severance.S02E10.1080p-GRP/b.mkv",
        "/m/TV/Severance.S01E02.720p-GRP/c.mkv",
    ])
    assert out == [{"path": "/m/TV/Severance", "files": 2},
                   {"path": "/m/TV/Severance.S01E02.720p-GRP", "files": 1}]


# ── the routes ──────────────────────────────────────────────────────────────

def test_every_new_action_is_admin_only_previews_included(tmp_path):
    """the previews list file names and folders, so they are gated too."""
    from tests.video.test_library_manage import _client
    c, db = _client(tmp_path, is_admin=False)
    mid = _movie(db)
    a = _add_file(db, movie_id=mid, path=tmp_path / "Movies/Dune/a.mkv")
    _add_file(db, movie_id=mid, size=5, path=tmp_path / "Movies/Dune/b.mkv")
    assert c.get(f"/api/video/files/extra-copies/movie/{mid}").status_code == 403
    assert c.post(f"/api/video/files/extra-copies/movie/{mid}", json={}).status_code == 403
    assert c.post("/api/video/files/bulk", json={"action": "delete", "file_ids": [a]}).status_code == 403
    assert c.post(f"/api/video/files/{a}/reassign", json={"episode_id": 1}).status_code == 403
    assert c.get("/api/video/files/consolidate/1").status_code == 403
    assert c.post("/api/video/files/consolidate/1", json={}).status_code == 403
    assert c.get("/api/video/files/consolidate/1/status").status_code == 403
    assert c.post("/api/video/files/consolidate/1/stop", json={}).status_code == 403
    assert len(_files(db)) == 2 and (tmp_path / "Movies/Dune/b.mkv").exists()


def test_the_routes_plan_then_act(tmp_path):
    from tests.video.test_library_manage import _client
    c, db = _client(tmp_path)
    db.set_setting("movies_path", str(tmp_path / "Movies"))
    mid = _movie(db)
    best = _add_file(db, movie_id=mid, size=900, path=tmp_path / "Movies/Dune/a.mkv")
    _add_file(db, movie_id=mid, size=400, path=tmp_path / "Movies/Dune/b.mkv")
    plan = c.get(f"/api/video/files/extra-copies/movie/{mid}").get_json()
    assert plan["success"] and plan["files"] == 1
    assert len(_files(db)) == 2            # the preview removed nothing
    done = c.post(f"/api/video/files/extra-copies/movie/{mid}", json={}).get_json()
    assert done["removed"] == 1 and _files(db) == [best]
    bad = c.post("/api/video/files/bulk", json={"action": "delete", "file_ids": "all"})
    assert bad.status_code == 400 and _files(db) == [best]
    assert c.post("/api/video/files/bulk", json={"action": "shred", "file_ids": [best]}).status_code == 400
    assert c.get("/api/video/files/consolidate/999").status_code == 409
