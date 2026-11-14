"""managing a title from its own page: seasons, episodes and single files.

the page previews a rename or a delete of the files it picked, hunts or
stops hunting a picked set of episodes, sets the padlock, and every file
action answers with the handle that puts it back.
"""

from __future__ import annotations

from core.video import bulk_ops, file_history, libraries
from tests.video.test_library_manage import _add_file, _movie, _row, _show, db  # noqa: F401 - fixtures
from tests.video.test_library_manager import _client


def _season_one(db, tmp_path):
    sid, eps = _show(db, tmp_path)
    a = _add_file(db, episode_id=eps[(1, 1)], size=700,
                  path=tmp_path / "TV/Breaking Bad/Season 01/bb.101.mkv")
    b = _add_file(db, episode_id=eps[(1, 2)], size=300,
                  path=tmp_path / "TV/Breaking Bad/Season 01/bb.102.mkv")
    return sid, eps, a, b


# ── previews ─────────────────────────────────────────────────────────────────

def test_the_rename_preview_names_each_picked_file_and_changes_nothing(db, tmp_path):
    sid, eps, a, b = _season_one(db, tmp_path)
    client = _client(db)
    out = client.get("/api/video/files/rename-preview?file_ids=%d,%d" % (a, b)).get_json()
    assert out["success"] and (out["files"], out["to_rename"], out["already_named"]) == (2, 2, 0)
    assert [c["file_id"] for c in out["changes"]] == [a, b]
    assert out["changes"][0]["current"].endswith("bb.101.mkv")
    assert "S01E01" in out["changes"][0]["expected"]
    assert (tmp_path / "TV/Breaking Bad/Season 01/bb.101.mkv").exists()
    assert client.get("/api/video/files/rename-preview").status_code == 400


def test_the_delete_preview_adds_up_the_files_and_counts_what_goes_missing(db, tmp_path):
    sid, eps, a, b = _season_one(db, tmp_path)
    second = _add_file(db, episode_id=eps[(1, 2)], size=50,
                       path=tmp_path / "TV/Breaking Bad/Season 01/bb.102.copy.mkv")
    client = _client(db)
    # episode 1 loses its only file; episode 2 keeps a copy
    out = client.get("/api/video/files/delete-preview?file_ids=%d,%d" % (a, second)).get_json()
    assert out["success"] and (out["files"], out["size_bytes"], out["emptied"]) == (2, 750, 1)
    assert {i["label"] for i in out["items"]} == {"S01E01", "S01E02"}
    assert out["recycle"] is True
    both = client.get("/api/video/files/delete-preview?file_ids=%d,%d,%d" % (a, b, second)).get_json()
    assert both["emptied"] == 2


def test_a_file_out_of_reach_stays_and_never_counts_as_the_last_copy_going(db, tmp_path):
    import os
    sid, eps, a, b = _season_one(db, tmp_path)
    os.remove(tmp_path / "TV/Breaking Bad/Season 01/bb.101.mkv")      # soulsync can't reach it
    client = _client(db)
    out = client.get("/api/video/files/delete-preview?file_ids=%d,%d" % (a, b)).get_json()
    assert (out["files"], out["unreachable"], out["emptied"]) == (2, 1, 1)
    assert {i["file_id"]: i["reachable"] for i in out["items"]} == {a: False, b: True}
    assert out["size_bytes"] == 300


def test_a_movies_delete_preview_knows_its_last_copy(db, tmp_path):
    mid = _movie(db)
    a = _add_file(db, movie_id=mid, size=900, path=tmp_path / "Movies/Dune (2021)/a.mkv")
    b = _add_file(db, movie_id=mid, size=40, path=tmp_path / "Movies/Dune (2021)/b.mkv")
    client = _client(db)
    assert client.get("/api/video/files/delete-preview?file_ids=%d" % b).get_json()["emptied"] == 0
    assert client.get("/api/video/files/delete-preview?file_ids=%d,%d" % (a, b)).get_json()["emptied"] == 1


# ── a preview of many files reads each folder once ───────────────────────────

def test_a_preview_asks_the_disk_once_per_folder_not_once_per_file(db, tmp_path, monkeypatch):
    import os
    from core.video import file_ops
    sid, eps = _show(db, tmp_path)
    here = _add_file(db, episode_id=eps[(1, 1)], path=tmp_path / "TV/Breaking Bad/Season 01/bb.101.mkv")
    gone = [_add_file(db, episode_id=eps[(1, 2)], size=5,
                      path=tmp_path / ("Elsewhere/Breaking Bad/Season 01/bb.102.v%d.mkv" % i))
            for i in range(12)]
    for i in range(12):                              # only the media server can see these
        os.remove(tmp_path / ("Elsewhere/Breaking Bad/Season 01/bb.102.v%d.mkv" % i))
    os.rmdir(tmp_path / "Elsewhere/Breaking Bad/Season 01")
    stats, lists = [], []
    real_exists, real_scandir = os.path.exists, os.scandir
    monkeypatch.setattr(os.path, "exists", lambda p: stats.append(p) or real_exists(p))
    monkeypatch.setattr(os, "scandir", lambda p: lists.append(str(p)) or real_scandir(p))

    finder = file_ops.Locator(db)
    rows = db.library_file_rows([here, *gone])
    found = {fid: finder.locate(rows[fid]) for fid in [here, *gone]}

    assert found[here] and all(found[g] is None for g in gone)
    # thirteen files, and no folder was listed twice
    assert len(lists) == len(set(lists))
    # nothing under the library was asked about file by file
    assert [p for p in stats if str(p).startswith(str(tmp_path))] == []


def test_a_rename_preview_reads_the_folders_and_the_settings_once(db, tmp_path, monkeypatch):
    from core.video import library_roots, organization, path_resolver
    sid, eps = _show(db, tmp_path)
    ids = [_add_file(db, episode_id=eps[(1, 1)], size=5 + i,
                     path=tmp_path / ("TV/Breaking Bad/Season 01/bb.101.v%d.mkv" % i)) for i in range(6)]
    calls = {"bases": 0, "roots": 0, "settings": 0}

    def counted(name, fn):
        def run(*a, **k):
            calls[name] += 1
            return fn(*a, **k)
        return run
    monkeypatch.setattr(path_resolver, "video_base_dirs", counted("bases", path_resolver.video_base_dirs))
    monkeypatch.setattr(organization, "load", counted("settings", organization.load))
    real_roots = library_roots.library_roots
    monkeypatch.setattr(library_roots, "library_roots",
                        lambda db_, scope=None: calls.__setitem__("roots", calls["roots"] + (scope is not None))
                        or real_roots(db_, scope) if scope is not None else real_roots(db_))
    out = _client(db).get("/api/video/files/rename-preview?file_ids=%s"
                          % ",".join(map(str, ids))).get_json()
    assert out["success"] and out["files"] == 6 and out["unreachable"] == 0
    assert calls == {"bases": 1, "roots": 1, "settings": 1}, calls


def test_the_folder_listing_agrees_with_the_disk(db, tmp_path):
    import os
    from core.video import file_ops
    folder = tmp_path / "TV/Show/Season 01"
    folder.mkdir(parents=True)
    (folder / "a.mkv").write_bytes(b"x")
    finder = file_ops.Locator(db)
    assert finder.exists(str(folder / "a.mkv"))
    assert not finder.exists(str(folder / "b.mkv"))
    assert not finder.exists(str(tmp_path / "Nowhere/Season 01/a.mkv"))
    # another case is the disk's call: this one tells names apart by case
    assert finder.exists(str(folder / "A.MKV")) == os.path.exists(str(folder / "A.MKV"))


# ── a file action can be put back from the page ──────────────────────────────

def test_rename_delete_and_refile_each_answer_with_what_undoes_them(db, tmp_path):
    sid, eps, a, b = _season_one(db, tmp_path)
    client = _client(db)
    renamed = client.post("/api/video/files/%d/rename" % a, json={}).get_json()
    assert renamed["success"] and renamed["batch"]
    undone = client.post("/api/video/manager/history/undo-batch", json={"batch": renamed["batch"]}).get_json()
    assert undone["undone"] == 1
    assert (tmp_path / "TV/Breaking Bad/Season 01/bb.101.mkv").exists()

    moved = client.post("/api/video/files/%d/reassign" % a, json={"episode_id": eps[(2, 1)]}).get_json()
    assert moved["success"] and moved["batch"]
    client.post("/api/video/manager/history/undo-batch", json={"batch": moved["batch"]})
    assert _row(db, "SELECT episode_id FROM media_files WHERE id=?", a) == (eps[(1, 1)],)

    gone = client.post("/api/video/files/%d/delete" % b, json={}).get_json()
    assert gone["success"] and gone["recycled"] and gone["batch"]
    back = client.post("/api/video/manager/history/undo-batch", json={"batch": gone["batch"]}).get_json()
    assert back["undone"] == 1
    assert (tmp_path / "TV/Breaking Bad/Season 01/bb.102.mkv").exists()


def test_a_rename_that_changed_nothing_has_nothing_to_undo(db, tmp_path):
    sid, eps, a, b = _season_one(db, tmp_path)
    client = _client(db)
    client.post("/api/video/files/%d/rename" % a, json={})
    again = client.post("/api/video/files/%d/rename" % a, json={}).get_json()
    assert again["action"] == "unchanged" and not again.get("batch")


# ── delete and stop hunting ──────────────────────────────────────────────────

def test_deleting_the_last_copy_can_stop_the_hunt_for_that_episode(db, tmp_path):
    sid, eps, a, b = _season_one(db, tmp_path)
    client = _client(db)
    out = client.post("/api/video/files/%d/delete" % a, json={"stop_hunting": True}).get_json()
    assert out["success"] and out["unmonitored"] == 1
    assert _row(db, "SELECT monitored FROM episodes WHERE id=?", eps[(1, 1)]) == (0,)
    assert _row(db, "SELECT monitored FROM episodes WHERE id=?", eps[(1, 2)]) == (1,)


def test_an_episode_that_keeps_a_copy_is_still_hunted(db, tmp_path):
    sid, eps, a, b = _season_one(db, tmp_path)
    second = _add_file(db, episode_id=eps[(1, 1)], size=50,
                       path=tmp_path / "TV/Breaking Bad/Season 01/bb.101.copy.mkv")
    client = _client(db)
    out = client.post("/api/video/files/bulk", json={
        "action": "delete", "file_ids": [second, b], "kind": "show", "stop_hunting": True}).get_json()
    assert out["success"] and out["done"] == 2 and out["unmonitored"] == 1
    assert _row(db, "SELECT monitored FROM episodes WHERE id=?", eps[(1, 1)]) == (1,)   # a copy is left
    assert _row(db, "SELECT monitored FROM episodes WHERE id=?", eps[(1, 2)]) == (0,)


def test_a_plain_delete_leaves_the_hunt_as_it_was(db, tmp_path):
    sid, eps, a, b = _season_one(db, tmp_path)
    client = _client(db)
    out = client.post("/api/video/files/bulk", json={
        "action": "delete", "file_ids": [a, b], "kind": "show"}).get_json()
    assert out["done"] == 2 and "unmonitored" not in out
    assert _row(db, "SELECT COUNT(*) FROM episodes WHERE show_id=? AND monitored=1", sid) == (4,)


# ── hunting a picked set of episodes ─────────────────────────────────────────

def test_picked_episodes_stop_being_hunted_and_start_again(db, tmp_path):
    sid, eps = _show(db, tmp_path)
    client = _client(db)
    picked = [eps[(1, 1)], eps[(2, 1)]]
    out = client.post("/api/video/detail/show/%d/episodes/monitor" % sid,
                      json={"episode_ids": picked, "monitored": False}).get_json()
    assert out["success"] and out["episodes"] == 2
    assert _row(db, "SELECT COUNT(*) FROM episodes WHERE show_id=? AND monitored=0", sid) == (2,)
    assert _row(db, "SELECT monitored FROM episodes WHERE id=?", eps[(1, 2)]) == (1,)
    client.post("/api/video/detail/show/%d/episodes/monitor" % sid,
                json={"episode_ids": picked, "monitored": True})
    assert _row(db, "SELECT COUNT(*) FROM episodes WHERE show_id=? AND monitored=0", sid) == (0,)


def test_another_shows_episodes_are_not_touched(db, tmp_path):
    sid, eps = _show(db, tmp_path)
    client = _client(db)
    res = client.post("/api/video/detail/show/%d/episodes/monitor" % (sid + 50),
                      json={"episode_ids": [eps[(1, 1)]], "monitored": False})
    assert res.status_code == 404
    assert _row(db, "SELECT monitored FROM episodes WHERE id=?", eps[(1, 1)]) == (1,)
    bad = client.post("/api/video/detail/show/%d/episodes/monitor" % sid,
                      json={"episode_ids": [eps[(1, 1)]], "monitored": "no"})
    assert bad.status_code == 400


# ── the padlock, from the title's page ───────────────────────────────────────

def test_the_page_sets_the_padlock_and_the_library_payload_says_so(db, tmp_path, monkeypatch):
    monkeypatch.setattr("core.video.sources.get_active_video_source", lambda: None)
    mid = _movie(db)
    _add_file(db, movie_id=mid, path=tmp_path / "Movies/Dune (2021)/a.mkv")
    client = _client(db)
    assert client.get("/api/video/detail/movie/%d/library" % mid).get_json()["locked"] is False
    out = client.post("/api/video/detail/movie/%d/padlock" % mid, json={"locked": True}).get_json()
    assert out["success"] and out["locked"] is True
    assert db.is_hands_off("movie", mid)
    assert client.get("/api/video/detail/movie/%d/library" % mid).get_json()["locked"] is True
    client.post("/api/video/detail/movie/%d/padlock" % mid, json={"locked": False})
    assert not db.is_hands_off("movie", mid)
    assert client.post("/api/video/detail/movie/%d/padlock" % mid, json={}).status_code == 400
    assert client.post("/api/video/detail/movie/99999/padlock", json={"locked": True}).status_code == 409


def test_a_locked_title_still_moves_when_its_own_page_asks(db, tmp_path):
    mid = _movie(db)
    _add_file(db, movie_id=mid, path=tmp_path / "Movies/Dune (2021)/a.mkv")
    lid = libraries.create_library(db, {"kind": "movie", "name": "Kids", "path": str(tmp_path / "Kids")})["id"]
    db.set_hands_off("movie", mid, True)
    held = bulk_ops._apply_one(db, "movie", mid, "move", {"library_id": lid}, None)
    assert held.get("held")
    with file_history.batch("move"):
        res = bulk_ops._apply_one(db, "movie", mid, "move", {"library_id": lid, "by_hand": True}, None)
    assert res.get("ok") and not res.get("held")
    client = _client(db)
    plan = client.get("/api/video/manager/move-preview?kind=movie&ids=%d&library_id=%d&by_hand=1"
                      % (mid, lid)).get_json()
    assert plan["held"] == 0


# ── a library on many drives ─────────────────────────────────────────────────

def _main(db):
    return next(x for x in libraries.list_libraries(db, "movie") if x["builtin"] == "main")


def _three_drives(db, tmp_path):
    """the main movie library on its own folder, an extra folder and a mapped server drive"""
    import json
    from core.video import drive_map
    for name in ("Movies", "Drive2/Movies", "Drive3/Movies"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    db.set_setting("movies_additional_paths", json.dumps([str(tmp_path / "Drive2/Movies")]))
    drive_map.save(db, [{"server": "/mnt/easystore3/Movies", "local": str(tmp_path / "Drive3/Movies"),
                         "kind": "movie"},
                        {"server": "/mnt/easystore3/TV", "local": str(tmp_path / "Drive3/TV"),
                         "kind": "episode"}])


def test_the_main_library_is_every_drive_it_sits_on_its_own_folder_first(db, tmp_path):
    _three_drives(db, tmp_path)
    kids = libraries.create_library(db, {"kind": "movie", "name": "Kids", "path": str(tmp_path / "Kids")})
    assert libraries.library_folders(db, _main(db)) == [
        str(tmp_path / "Movies"), str(tmp_path / "Drive2/Movies"), str(tmp_path / "Drive3/Movies")]
    # a tv drive is not a movie library's, and another library keeps its one folder
    assert libraries.library_folders(db, libraries.get_library(db, kids["id"])) == [str(tmp_path / "Kids")]


def test_a_folder_that_is_another_librarys_is_not_also_the_main_ones(db, tmp_path):
    import json
    (tmp_path / "Kids/Shorts").mkdir(parents=True)
    libraries.create_library(db, {"kind": "movie", "name": "Kids", "path": str(tmp_path / "Kids")})
    db.set_setting("movies_additional_paths",
                   json.dumps([str(tmp_path / "Kids"), str(tmp_path / "Kids/Shorts")]))
    assert libraries.library_folders(db, _main(db)) == [str(tmp_path / "Movies")]


def test_a_title_moves_to_another_drive_of_the_library_it_is_in(db, tmp_path):
    import os
    _three_drives(db, tmp_path)
    mid = _movie(db)
    old = tmp_path / "Movies/Dune (2021)/dune.rip.mkv"
    fid = _add_file(db, movie_id=mid, path=old)
    main, drive3 = _main(db), str(tmp_path / "Drive3/Movies")

    plan = bulk_ops.move_plan(db, "movie", mid, main["id"], drive3)
    assert plan["files"] == 1 and plan["moves"][0]["to"].startswith(drive3) and old.exists()
    # no drive named: the library's own folder, which is where it already sits
    assert bulk_ops.move_plan(db, "movie", mid, main["id"])["moves"][0]["to"].startswith(
        str(tmp_path / "Movies"))

    with file_history.batch("move") as batch:
        res = bulk_ops._apply_one(db, "movie", mid, "move",
                                  {"library_id": main["id"], "folder": drive3}, None)
    assert res["ok"] and res["changed"] == 1 and not old.exists()
    assert os.path.isfile(plan["moves"][0]["to"])
    assert _row(db, "SELECT relative_path FROM media_files WHERE id=?", fid)[0].startswith(
        "/mnt/easystore3/Movies")                     # stored the way the media server sees that drive
    assert file_history.undo_batch(db, batch)["undone"] == 1 and old.exists()


def test_a_move_only_takes_a_folder_the_library_really_has(db, tmp_path):
    _three_drives(db, tmp_path)
    mid = _movie(db)
    _add_file(db, movie_id=mid, path=tmp_path / "Movies/Dune (2021)/dune.rip.mkv")
    main = _main(db)
    for folder in (str(tmp_path / "Elsewhere"), "/etc", str(tmp_path / "Drive3/TV")):
        assert "not one of this library" in bulk_ops.move_plan(db, "movie", mid, main["id"], folder)["error"]
    assert bulk_ops.validate("movie", [mid], "move", {"library_id": main["id"], "folder": 5})
    assert bulk_ops.validate("movie", [mid], "move",
                             {"library_id": main["id"], "folder": str(tmp_path / "Drive2/Movies")}) is None


def test_the_move_dialog_is_handed_each_library_with_its_drives_and_their_room(db, tmp_path):
    _three_drives(db, tmp_path)
    mid = _movie(db)
    _add_file(db, movie_id=mid, size=700, path=tmp_path / "Movies/Dune (2021)/dune.rip.mkv")
    client = _client(db)
    libs = client.get("/api/video/manager/libraries?kind=movie").get_json()["libraries"]
    main = next(x for x in libs if x["folders"] and len(x["folders"]) == 3)
    assert [f["primary"] for f in main["folders"]] == [True, False, False]
    assert all(isinstance(f["free_bytes"], int) for f in main["folders"])
    drive2 = str(tmp_path / "Drive2/Movies")
    plan = client.get("/api/video/manager/move-preview", query_string={
        "kind": "movie", "ids": str(mid), "library_id": main["id"], "folder": drive2}).get_json()
    assert plan["success"] and plan["files"] == 1 and plan["moves"][0]["to"].startswith(drive2)
    bad = client.get("/api/video/manager/move-preview", query_string={
        "kind": "movie", "ids": str(mid), "library_id": main["id"], "folder": "/etc"})
    assert bad.status_code == 409


# ── the gate ─────────────────────────────────────────────────────────────────

def test_only_an_admin_manages_files_from_the_page(db, tmp_path):
    sid, eps, a, b = _season_one(db, tmp_path)
    client = _client(db, is_admin=False)
    for method, path in (("get", "files/rename-preview?file_ids=%d" % a),
                         ("get", "files/delete-preview?file_ids=%d" % a),
                         ("post", "detail/show/%d/episodes/monitor" % sid),
                         ("post", "detail/show/%d/padlock" % sid)):
        res = getattr(client, method)("/api/video/" + path, json={})
        assert res.status_code == 403, (method, path, res.status_code)
