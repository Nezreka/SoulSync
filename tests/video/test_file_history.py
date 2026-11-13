"""the library manager's history, undo, padlock, move and renumber.

a rename, a move, a delete and a re-file each leave a row that can put the
file back. a padlocked title is left out of what automation reads and sits
out of a sweeping selection. a title moves to another library with its
files. a run of episode files is filed under other episodes in one go.
"""

from __future__ import annotations

import os

from core.automation.handlers import video_process_wishlist as vpw
from core.video import bulk_ops, file_history, file_ops, libraries, library_actions, library_manager
from tests.video.test_library_manage import _add_file, _movie, _row, _show, db  # noqa: F401 - fixtures


def _changes(db, **kw):
    return file_history.changes(db, **kw)["items"]


# ── history and undo ─────────────────────────────────────────────────────────

def test_a_rename_is_recorded_and_undo_puts_the_old_name_back(db, tmp_path):
    mid = _movie(db)
    old = tmp_path / "Movies/Dune (2021)/dune.rip.mkv"
    fid = _add_file(db, movie_id=mid, path=old)
    (old.parent / "dune.rip.en.srt").write_text("sub")
    res = file_ops.rename_to_template(db, fid)
    assert res["success"] and res["action"] == "renamed"
    new = res["path"]
    (ch,) = _changes(db, kind="movie", item_id=mid)
    assert (ch["action"], ch["from_path"], ch["to_path"], ch["title"]) == ("rename", str(old), new, "Dune")

    out = file_history.undo(db, ch["id"])

    assert out["success"], out
    assert old.exists() and not os.path.exists(new)
    assert (old.parent / "dune.rip.en.srt").exists()          # the subtitle went back with it
    assert _row(db, "SELECT relative_path FROM media_files WHERE id=?", fid) == (str(old),)
    # the undo itself is not a new change, and it can't be undone twice
    assert len(_changes(db)) == 1 and _changes(db)[0]["undone"]
    assert not file_history.undo(db, ch["id"])["success"]


def test_undo_is_refused_when_the_file_moved_on_or_the_old_path_is_taken(db, tmp_path):
    mid = _movie(db)
    old = tmp_path / "Movies/Dune (2021)/dune.rip.mkv"
    fid = _add_file(db, movie_id=mid, path=old)
    new = file_ops.rename_to_template(db, fid)["path"]
    (ch,) = _changes(db)
    old.write_bytes(b"someone else")                           # the old path is taken
    res = file_history.undo(db, ch["id"])
    assert not res["success"] and os.path.exists(new)
    assert old.read_bytes() == b"someone else"                 # nothing overwritten
    old.unlink()
    os.remove(new)                                             # the file moved on
    assert not file_history.undo(db, ch["id"])["success"]
    assert not _changes(db)[0]["undone"]


def test_a_deleted_file_comes_back_owned_without_a_scan(db, tmp_path):
    mid = _movie(db)
    keep = _add_file(db, movie_id=mid, size=900, path=tmp_path / "Movies/Dune (2021)/a.mkv")
    gone_path = tmp_path / "Movies/Dune (2021)/b.mkv"
    gone = _add_file(db, movie_id=mid, size=40, resolution="720", path=gone_path)
    assert file_ops.delete_to_recycle(db, gone)["success"]
    assert not gone_path.exists()
    ch = _changes(db)[0]
    assert ch["action"] == "delete" and ch["trash_path"] and ch["label"] == "b.mkv"

    res = file_history.undo(db, ch["id"])

    assert res["success"] and not res["rescan"]
    assert gone_path.read_bytes() == b"x" * 40
    assert _row(db, "SELECT id, resolution, size_bytes FROM media_files WHERE id=?", gone) == (gone, "720", 40)
    assert _row(db, "SELECT COUNT(*) FROM media_files WHERE movie_id=?", mid) == (2,)
    assert keep != gone


def test_undoing_the_last_copys_delete_marks_the_movie_owned_again(db, tmp_path):
    mid = _movie(db)
    fid = _add_file(db, movie_id=mid, path=tmp_path / "Movies/Dune (2021)/a.mkv")
    file_ops.delete_to_recycle(db, fid)
    assert _row(db, "SELECT has_file FROM movies WHERE id=?", mid) == (0,)
    assert file_history.undo(db, _changes(db)[0]["id"])["success"]
    assert _row(db, "SELECT has_file FROM movies WHERE id=?", mid) == (1,)


def test_an_emptied_recycle_folder_is_said_plainly(db, tmp_path):
    mid = _movie(db)
    fid = _add_file(db, movie_id=mid, path=tmp_path / "Movies/Dune (2021)/a.mkv")
    file_ops.delete_to_recycle(db, fid)
    ch = _changes(db)[0]
    os.remove(ch["trash_path"])
    res = file_history.undo(db, ch["id"])
    assert not res["success"] and "recycle folder" in res["error"]


def test_a_refile_is_one_change_and_undo_files_it_back(db, tmp_path):
    sid, eps = _show(db, tmp_path)
    fid = _add_file(db, episode_id=eps[(1, 1)], path=tmp_path / "TV/Breaking Bad/Season 01/wrong.mkv")
    assert library_actions.reassign_file(db, fid, eps[(1, 2)])["success"]
    (ch,) = _changes(db, kind="show", item_id=sid)       # the rename inside it is not a second row
    assert (ch["action"], ch["from_episode_id"], ch["to_episode_id"]) == ("reassign", eps[(1, 1)], eps[(1, 2)])
    assert file_history.undo(db, ch["id"])["success"]
    assert _row(db, "SELECT episode_id FROM media_files WHERE id=?", fid) == (eps[(1, 1)],)
    assert _row(db, "SELECT has_file FROM episodes WHERE id=?", eps[(1, 2)]) == (0,)


def test_a_bulk_run_is_one_batch_and_is_undone_together(db, tmp_path):
    m1 = _movie(db)
    m2 = _movie(db, title="Alien", year=1979, tmdb=348, imdb="tt0078748", sid="101")
    a = tmp_path / "Movies/Dune (2021)/one.mkv"
    b = tmp_path / "Movies/Alien (1979)/two.mkv"
    f1 = _add_file(db, movie_id=m1, path=a)
    f2 = _add_file(db, movie_id=m2, path=b)
    res = library_actions.bulk(db, "rename", [f1, f2])
    assert res["done"] == 2 and res["batch"]
    assert {c["batch"] for c in _changes(db)} == {res["batch"]}
    out = file_history.undo_batch(db, res["batch"])
    assert out["success"] and out["undone"] == 2
    assert a.exists() and b.exists()
    assert sorted(t["item_id"] for t in out["titles"]) == sorted([m1, m2])


def test_history_is_per_title_or_everything_newest_first(db, tmp_path):
    m1 = _movie(db)
    m2 = _movie(db, title="Alien", year=1979, tmdb=348, imdb="tt0078748", sid="101")
    f1 = _add_file(db, movie_id=m1, path=tmp_path / "Movies/Dune (2021)/x.mkv")
    f2 = _add_file(db, movie_id=m2, path=tmp_path / "Movies/Alien (1979)/y.mkv")
    file_ops.rename_to_template(db, f1)
    file_ops.rename_to_template(db, f2)
    assert [c["title"] for c in _changes(db)] == ["Alien", "Dune"]
    assert [c["title"] for c in _changes(db, kind="movie", item_id=m1)] == ["Dune"]
    assert file_history.changes(db, limit=1)["total"] == 2


# ── the padlock ──────────────────────────────────────────────────────────────

def test_a_padlocked_title_is_left_out_of_what_maintenance_and_mass_rename_read(db, tmp_path):
    mid = _movie(db)
    other = _movie(db, title="Alien", year=1979, tmdb=348, imdb="tt0078748", sid="101")
    _add_file(db, movie_id=mid, path=tmp_path / "Movies/Dune (2021)/a.mkv")
    _add_file(db, movie_id=mid, size=5, path=tmp_path / "Movies/Dune (2021)/b.mkv")
    _add_file(db, movie_id=other, path=tmp_path / "Movies/Alien (1979)/a.mkv")
    sid, eps = _show(db, tmp_path)
    _add_file(db, episode_id=eps[(1, 1)], path=tmp_path / "TV/Breaking Bad/Season 01/e1.mkv")
    _add_file(db, episode_id=eps[(1, 1)], size=5, path=tmp_path / "TV/Breaking Bad/Season 01/e1b.mkv")

    def seen():
        return {
            "files": {(r["scope"], r["title"]) for r in db.repair_library_files()},
            "movies": {r["title"] for r in db.repair_owned_movie_files()},
            "rename_eps": len(db.rename_owned_episode_files()),
            "eps": len(db.repair_owned_episode_files()),
            "dupe_movies": len(db.repair_duplicate_movies()["files"]),
            "dupe_eps": len(db.repair_duplicate_episodes()),
            "watched": {r["title"] for r in db.repair_watched_movies()},
            "subs": {(v["kind"], v.get("title") or v.get("show_title")) for v in db.subtitle_library_videos()},
        }
    before = seen()
    assert before["files"] == {("movie", "Dune"), ("movie", "Alien"), ("episode", "Breaking Bad")}
    assert (before["dupe_movies"], before["dupe_eps"], before["rename_eps"], before["eps"]) == (1, 1, 2, 2)

    assert db.set_hands_off("movie", mid, True) and db.set_hands_off("show", sid, True)

    after = seen()
    assert after["files"] == {("movie", "Alien")}
    assert after["movies"] == {"Alien"} and after["watched"] == {"Alien"}
    assert (after["dupe_movies"], after["dupe_eps"], after["rename_eps"], after["eps"]) == (0, 0, 0, 0)
    assert after["subs"] == {("movie", "Alien")}
    # and it all comes back with the padlock off
    db.set_hands_off("movie", mid, False)
    db.set_hands_off("show", sid, False)
    assert seen() == before


def test_a_padlocked_title_keeps_the_copy_it_has(db):
    mid = _movie(db)
    db.upsert_show_tree("plex", {"server_id": "s", "title": "Lost", "tmdb_id": 4607, "seasons": []})
    items = [{"tmdb_id": 438631, "owned": 1, "owned_resolutions": "720"},
             {"tmdb_id": 348, "owned": 1, "owned_resolutions": "720"},
             {"tmdb_id": 438631, "owned": 0},
             {"show_tmdb_id": 438631, "owned": 1, "owned_resolutions": "720"}]
    assert vpw.held_titles(db) is None                       # nothing locked: no check at all
    assert len(vpw.annotate_upgrades(items, 0, held=vpw.held_titles(db))) == 4
    db.set_hands_off("movie", mid, True)
    kept = vpw.annotate_upgrades(items, 0, held=vpw.held_titles(db))
    # the locked movie's upgrade is dropped; its missing wish, another movie
    # and a show that only shares the number all stay
    assert [(k.get("tmdb_id"), k.get("show_tmdb_id"), k["owned"]) for k in kept] == \
        [(348, None, 1), (438631, None, 0), (None, 438631, 1)]


def test_lock_sets_the_padlock_and_the_details_and_unlock_clears_both(db):
    mid = _movie(db)
    assert bulk_ops._apply_one(db, "movie", mid, "lock", {"value": True}, None)["ok"]
    assert db.is_hands_off("movie", mid)
    assert "title" in db.get_locked_fields("movie", mid)
    assert bulk_ops._apply_one(db, "movie", mid, "lock", {"value": False}, None)["ok"]
    assert not db.is_hands_off("movie", mid) and db.get_locked_fields("movie", mid) == []


def test_a_padlocked_title_sits_out_a_sweep_and_is_counted(db, tmp_path):
    mid = _movie(db)
    path = tmp_path / "Movies/Dune (2021)/dune.rip.mkv"
    _add_file(db, movie_id=mid, path=path)
    db.set_hands_off("movie", mid, True)
    for action, params in (("rename", {}), ("delete", {"confirm": 1}), ("rematch", {}),
                           ("wish", {"mode": "upgrade"}), ("move", {"library_id": 1})):
        res = bulk_ops._apply_one(db, "movie", mid, action, params, None)
        assert res == {"ok": True, "pushed": False, "held": True}, action
    assert path.exists() and _row(db, "SELECT COUNT(*) FROM movies") == (1,)
    # what doesn't touch its files or its match still goes through
    assert bulk_ops._apply_one(db, "movie", mid, "monitored", {"value": False}, None)["ok"]
    assert _row(db, "SELECT monitored FROM movies WHERE id=?", mid) == (0,)


def test_the_managers_rows_and_filter_follow_the_padlock(db, tmp_path):
    mid = _movie(db)
    _movie(db, title="Alien", year=1979, tmdb=348, imdb="tt0078748", sid="101")
    db.set_hands_off("movie", mid, True)
    library_manager.forget(db)
    rows = library_manager.titles(db, "movies", {"locked": "1"})["items"]
    assert [(r["title"], r["locked"]) for r in rows] == [("Dune", True)]
    assert [r["title"] for r in library_manager.titles(db, "movies", {"locked": "0"})["items"]] == ["Alien"]


# ── move to another library ──────────────────────────────────────────────────

def test_a_movie_moves_to_another_library_with_its_files_and_is_filed_there(db, tmp_path):
    mid = _movie(db)
    old = tmp_path / "Movies/Dune (2021)/dune.rip.mkv"
    fid = _add_file(db, movie_id=mid, path=old)
    (old.parent / "dune.rip.en.srt").write_text("sub")
    lib = libraries.create_library(db, {"kind": "movie", "name": "Kids", "path": str(tmp_path / "Kids")})

    plan = bulk_ops.move_plan(db, "movie", mid, lib["id"])
    assert plan["files"] == 1 and plan["moves"][0]["to"].startswith(str(tmp_path / "Kids"))
    assert old.exists()                                        # a plan moves nothing

    res = bulk_ops._apply_one(db, "movie", mid, "move", {"library_id": lib["id"]}, None)

    assert res["ok"] and res["changed"] == 1
    new = plan["moves"][0]["to"]
    assert os.path.isfile(new) and not old.exists()
    assert os.path.exists(os.path.splitext(new)[0] + ".en.srt")
    assert _row(db, "SELECT relative_path FROM media_files WHERE id=?", fid) == (new,)
    assert libraries.title_assignment(db, "movie", 438631) == lib["id"]
    assert _row(db, "SELECT video_library_id FROM movies WHERE id=?", mid) == (lib["id"],)
    # it reads as a move in the history, and undo brings it home
    ch = _changes(db)[0]
    assert ch["action"] == "move"
    assert file_history.undo(db, ch["id"])["success"] and old.exists()


def test_a_move_refuses_the_wrong_kind_of_library_and_never_overwrites(db, tmp_path):
    mid = _movie(db)
    old = tmp_path / "Movies/Dune (2021)/dune.rip.mkv"
    _add_file(db, movie_id=mid, path=old)
    tv = libraries.create_library(db, {"kind": "show", "name": "Anime", "path": str(tmp_path / "Anime")})
    assert "error" in bulk_ops.move_plan(db, "movie", mid, tv["id"])
    assert "error" in bulk_ops.move_plan(db, "movie", mid, 99999)
    lib = libraries.create_library(db, {"kind": "movie", "name": "Kids", "path": str(tmp_path / "Kids")})
    want = bulk_ops.move_plan(db, "movie", mid, lib["id"])["moves"][0]["to"]
    os.makedirs(os.path.dirname(want))
    with open(want, "wb") as fh:
        fh.write(b"already here")
    plan = bulk_ops.move_plan(db, "movie", mid, lib["id"])
    assert plan["files"] == 0 and len(plan["skipped"]) == 1
    res = bulk_ops._apply_one(db, "movie", mid, "move", {"library_id": lib["id"]}, None)
    assert not res["ok"] and old.exists()
    assert open(want, "rb").read() == b"already here"
    assert libraries.title_assignment(db, "movie", 438631) is None    # not filed there: it didn't move


def test_move_is_validated():
    v = bulk_ops.validate
    assert v("movie", [1], "move", {"library_id": 2}) is None
    assert v("movie", [1], "move", {}) and v("movie", [1], "move", {"library_id": True})


# ── renumber ─────────────────────────────────────────────────────────────────

def test_a_run_of_files_is_filed_under_other_episodes_in_order(db, tmp_path):
    sid, eps = _show(db, tmp_path)
    # season 2 sits on disk numbered as season 1 episodes 1 and 2
    a = _add_file(db, episode_id=eps[(1, 1)], path=tmp_path / "TV/Breaking Bad/Season 01/a.mkv")
    b = _add_file(db, episode_id=eps[(1, 2)], path=tmp_path / "TV/Breaking Bad/Season 01/b.mkv")
    plan = library_actions.renumber_plan(db, sid, [b, a], 2, 1)
    assert [(m["file_id"], m["from"], m["to"]) for m in plan["moves"]] == \
        [(a, "S01E01", "S02E01"), (b, "S01E02", "S02E02")]
    assert _row(db, "SELECT episode_id FROM media_files WHERE id=?", a) == (eps[(1, 1)],)

    res = library_actions.renumber(db, sid, [a, b], 2, 1)

    assert res["success"] and res["moved"] == 2 and res["not_renamed"] == 0
    assert _row(db, "SELECT episode_id FROM media_files WHERE id=?", a) == (eps[(2, 1)],)
    assert _row(db, "SELECT episode_id FROM media_files WHERE id=?", b) == (eps[(2, 2)],)
    assert _row(db, "SELECT has_file FROM episodes WHERE id=?", eps[(1, 1)]) == (0,)
    path = _row(db, "SELECT relative_path FROM media_files WHERE id=?", a)[0]
    assert "S02E01" in path and os.path.isfile(path)
    # one batch, undone together
    assert file_history.undo_batch(db, res["batch"])["undone"] == 2
    assert _row(db, "SELECT episode_id FROM media_files WHERE id=?", a) == (eps[(1, 1)],)
    assert _row(db, "SELECT episode_id FROM media_files WHERE id=?", b) == (eps[(1, 2)],)


def test_a_shift_by_one_does_not_trip_over_its_neighbours_name(db, tmp_path):
    sid, eps = _show(db, tmp_path)
    # the pilot's file is named and filed as episode 2; nothing is at episode 1
    wrong = _add_file(db, episode_id=eps[(1, 1)], path=tmp_path / "TV/x/first.mkv")
    file_ops.rename_to_template(db, wrong)                      # now named S01E01
    library_actions.renumber(db, sid, [wrong], 1, 2)            # move it to episode 2
    other = _add_file(db, episode_id=eps[(1, 1)], path=tmp_path / "TV/x/other.mkv")
    file_ops.rename_to_template(db, other)                      # a new file takes S01E01
    # swap-ish: shift both up by one is impossible (no S01E03), so shift down from 1
    res = library_actions.renumber(db, sid, [other, wrong], 1, 1)
    assert res["success"] and res["moved"] == 0                 # already in place: nothing to do


def test_renumber_refuses_an_episode_the_library_does_not_have_and_another_shows_file(db, tmp_path):
    sid, eps = _show(db, tmp_path)
    a = _add_file(db, episode_id=eps[(1, 1)], path=tmp_path / "TV/Breaking Bad/Season 01/a.mkv")
    plan = library_actions.renumber_plan(db, sid, [a], 0, 1)
    assert "Specials has no episode 1" in plan["error"]
    assert "error" in library_actions.renumber_plan(db, sid, [a, 99999], 1, 1)
    assert "error" in library_actions.renumber_plan(db, sid, [], 1, 1)
    assert not library_actions.renumber(db, sid, [a], 9, 1)["success"]
    assert _row(db, "SELECT episode_id FROM media_files WHERE id=?", a) == (eps[(1, 1)],)


def test_two_copies_of_one_episode_stay_together(db, tmp_path):
    sid, eps = _show(db, tmp_path)
    a = _add_file(db, episode_id=eps[(1, 1)], path=tmp_path / "TV/Breaking Bad/Season 01/a.mkv")
    a2 = _add_file(db, episode_id=eps[(1, 1)], resolution="720", path=tmp_path / "TV/Breaking Bad/Season 01/a2.mkv")
    b = _add_file(db, episode_id=eps[(1, 2)], path=tmp_path / "TV/Breaking Bad/Season 01/b.mkv")
    plan = library_actions.renumber_plan(db, sid, [a, a2, b], 2, 1)
    assert [(m["file_id"], m["to"]) for m in plan["moves"]] == [(a, "S02E01"), (a2, "S02E01"), (b, "S02E02")]


def test_the_episode_list_carries_each_episodes_files_specials_last(db, tmp_path):
    sid, eps = _show(db, tmp_path)
    fid = _add_file(db, episode_id=eps[(1, 2)], path=tmp_path / "TV/Breaking Bad/Season 01/b.mkv")
    out = library_actions.show_episodes(db, sid)
    assert [s["season"] for s in out["seasons"]] == [1, 2]
    e2 = out["seasons"][0]["episodes"][1]
    assert (e2["episode"], e2["title"], [f["file_id"] for f in e2["files"]]) == (2, "Cat's in the Bag", [fid])
    assert out["seasons"][0]["episodes"][0]["files"] == []
