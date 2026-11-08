"""Delete Show / Delete Movie: a title deleted from its page stays deleted.

Deleting a show's files alone stops nothing: an airing library show is
followed by default, so the airing automation wishes for its next episode;
any profile's wishlist rows get drained; a running download that fails puts
itself back on the wishlist; a pending request can be approved; a pending
repair finding grabs it again when approved. core.video.delete_title stops
all of that for EVERY profile first, then recycles the title's own folder as
one bin entry, asks the server to rescan, and forgets the rows.

Torrents the client is still seeding are left alone on purpose; the preview
counts them so the dialog can say so.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.video import delete_title, recycle
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    d.set_setting("movies_path", str(tmp_path / "Movies"))
    d.set_setting("tv_path", str(tmp_path / "TV"))
    return d


def _file(db, path: Path, *, movie_id=None, episode_id=None, size=16):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    conn = db._get_connection()
    cur = conn.execute("INSERT INTO media_files (movie_id, episode_id, relative_path, size_bytes) "
                       "VALUES (?,?,?,?)", (movie_id, episode_id, str(path), size))
    if movie_id:
        conn.execute("UPDATE movies SET has_file=1 WHERE id=?", (movie_id,))
    if episode_id:
        conn.execute("UPDATE episodes SET has_file=1 WHERE id=?", (episode_id,))
    conn.commit()
    conn.close()
    return cur.lastrowid


def _movie(db, tmp_path, *, title="Heat", year=1995, tmdb=949, sid="m1", folder=True):
    mid = db.upsert_movie("plex", {"server_id": sid, "title": title, "year": year, "tmdb_id": tmdb})
    where = tmp_path / "Movies" / f"{title} ({year})" if folder else tmp_path / "Movies"
    _file(db, where / f"{title} ({year}).mkv", movie_id=mid)
    if folder:
        (where / "poster.jpg").write_bytes(b"img")
    return mid


def _show(db, tmp_path, *, title="Tulsa King", tmdb=153312, sid="s1"):
    sid_ = db.upsert_show_tree("plex", {
        "server_id": sid, "title": title, "year": 2022, "tmdb_id": tmdb,
        "seasons": [{"server_id": sid + "a", "season_number": 1, "episodes": [
            {"server_id": sid + "e1", "episode_number": 1, "title": "Go West", "air_date": "2022-11-13"},
            {"server_id": sid + "e2", "episode_number": 2, "title": "Center", "air_date": "2022-11-13"}]}]})
    eps = [r["id"] for r in db._get_connection().execute(
        "SELECT id FROM episodes WHERE show_id=? ORDER BY episode_number", (sid_,))]
    folder = tmp_path / "TV" / f"{title} (2022)"
    for n, eid in enumerate(eps, 1):
        _file(db, folder / "Season 01" / f"{title} - S01E0{n}.mkv", episode_id=eid)
    (folder / "tvshow.nfo").write_text("nfo")
    return sid_, eps, folder


def _no_cancel(db, dl):
    db.update_video_download(dl["id"], status="cancelled", error="Cancelled: title deleted")


def _run(db, kind, item_id, title, **kw):
    kw.setdefault("profile_ids", [1, 2, 3])
    kw.setdefault("cancel", _no_cancel)
    kw.setdefault("refresh", lambda kind: {"ok": True})
    return delete_title.run(db, kind, item_id, typed_title=title, resolved_by=1, **kw)


def _one(db, sql, *args):
    conn = db._get_connection()
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


# ── the whole delete, a show ────────────────────────────────────────────────

def test_deleting_a_show_stops_every_regrab_path_for_every_profile(db, tmp_path):
    show_id, eps, folder = _show(db, tmp_path)
    db.add_to_watchlist("show", 153312, "Tulsa King", profile_id=2)          # Z follows it
    db.add_episodes_to_wishlist(153312, "Tulsa King",
                                [{"season_number": 1, "episode_number": 3}], profile_id=1)
    db.add_episodes_to_wishlist(153312, "Tulsa King",
                                [{"season_number": 2, "episode_number": 1}], profile_id=2)
    db.add_video_request(profile_id=3, requester_name="Kim", kind="show", tmdb_id=153312,
                         title="Tulsa King")
    db.add_video_request(profile_id=2, requester_name="Z", kind="episode", tmdb_id=153312,
                         title="Tulsa King", season_number=2, episode_number=2)
    dl = db.add_video_download({"kind": "show", "title": "Tulsa King", "status": "downloading",
                                "media_id": str(show_id), "media_source": "library",
                                "source": "usenet", "client_ref": "SABnzbd_nzo_1"})
    conn = db._get_connection()
    conn.execute("INSERT INTO video_repair_findings (job_id, finding_type, entity_type, entity_id, "
                 "title, details_json) VALUES ('missing_episodes','missing_episodes','show',?, "
                 "'Tulsa King — 9 missing episodes', ?)",
                 (f"{show_id}:abc123", json.dumps({"show_id": show_id, "tmdb_id": 153312})))
    conn.commit()
    conn.close()
    notes = []

    res = _run(db, "show", show_id, "Tulsa King",
               notify=lambda pid, msg, lvl: notes.append((pid, msg)))

    assert res["success"], res
    # follow muted for EVERY profile -- an airing library show is followed by default
    states = {r["profile_id"]: r["state"] for r in db._get_connection().execute(
        "SELECT profile_id, state FROM video_watchlist WHERE kind='show' AND tmdb_id=153312")}
    assert states == {1: "mute", 2: "mute", 3: "mute"}
    assert db.title_wishlist_count("show", 153312) == 0
    assert db.get_video_download(dl)["status"] == "cancelled"
    statuses = [r["status"] for r in db._get_connection().execute(
        "SELECT status FROM video_requests WHERE tmdb_id=153312")]
    assert statuses == ["denied", "denied"]
    assert sorted(pid for pid, _m in notes) == [2, 3]
    assert all("Removed from the library" in m for _p, m in notes)
    assert _one(db, "SELECT status, user_action FROM video_repair_findings")[0] == "resolved"
    assert ("show:153312") in db.ignored_keys()
    # the files: the show folder went into the bin as ONE entry, nfo included
    assert not folder.exists()
    entries = recycle.list_entries(recycle_settings(db), db)
    assert len(entries) == 1 and entries[0]["is_folder"]
    assert entries[0]["original_path"] == str(folder)
    # the rows
    assert _one(db, "SELECT COUNT(*) FROM shows")[0] == 0
    assert _one(db, "SELECT COUNT(*) FROM media_files")[0] == 0
    assert res["folders_recycled"] == 1 and res["files_recycled"] == 0


def recycle_settings(db):
    from core.video import organization
    return organization.load(db)


def test_steps_run_in_order_acquisition_first_then_files_then_server_then_rows(db, tmp_path):
    show_id, _eps, folder = _show(db, tmp_path)
    db.add_video_download({"kind": "show", "title": "Tulsa King", "status": "queued",
                           "media_id": "153312", "media_source": "tmdb", "source": "torrent"})
    seen = []

    def cancel(db_, dl):
        seen.append(("cancel", folder.exists(), db_.title_delete_rows("show", show_id) != []))
        _no_cancel(db_, dl)

    def refresh(kind):
        seen.append(("refresh", folder.exists(), db.title_delete_rows("show", show_id) != []))
        return {"ok": True}

    assert _run(db, "show", show_id, "Tulsa King", cancel=cancel, refresh=refresh)["success"]
    # cancel: files and rows still there; refresh: files gone, rows still there
    assert seen == [("cancel", True, True), ("refresh", False, True)]
    assert db.title_delete_rows("show", show_id) == []


# ── the two confirmations ───────────────────────────────────────────────────

def test_nothing_happens_unless_the_title_is_typed_exactly(db, tmp_path):
    show_id, _eps, folder = _show(db, tmp_path)
    db.add_episodes_to_wishlist(153312, "Tulsa King", [{"season_number": 1, "episode_number": 3}])
    for typed in ("", "tulsa king", "Tulsa", "Tulsa King 2"):
        res = _run(db, "show", show_id, typed)
        assert not res["success"] and "doesn't match" in res["error"]
    assert folder.exists() and db.title_wishlist_count("show", 153312) == 1
    assert db.title_delete_rows("show", show_id) == [show_id]
    assert _run(db, "show", show_id, "  Tulsa King ")["success"]      # surrounding spaces ok


def test_preview_counts_what_the_dialog_must_say_and_changes_nothing(db, tmp_path):
    mid = _movie(db, tmp_path)
    db.add_movie_to_wishlist(949, "Heat", profile_id=1)
    db.add_movie_to_wishlist(949, "Heat", profile_id=2)
    db.add_video_request(profile_id=2, requester_name="Z", kind="movie", tmdb_id=949, title="Heat")
    seed = db.add_video_download({"kind": "movie", "title": "Heat", "status": "completed",
                                  "media_id": str(mid), "media_source": "library",
                                  "source": "torrent", "client_ref": "abc"})
    p = delete_title.plan(db, "movie", mid)
    assert p["title"] == "Heat" and p["files"] == 1 and p["files_missing"] == 0
    assert len(p["folders"]) == 1 and p["folders"][0]["path"].endswith("Heat (1995)")
    assert p["wishlist"] == 2 and p["requests"] == 1 and p["seeding"] == 1
    assert p["recycling"] and p["blocked"] is None
    assert db.get_video_download(seed)["status"] == "completed"
    assert db.title_wishlist_count("movie", 949) == 2


def test_a_seeding_torrent_is_left_alone(db, tmp_path):
    mid = _movie(db, tmp_path)
    seed = db.add_video_download({"kind": "movie", "title": "Heat", "status": "completed",
                                  "media_id": str(mid), "media_source": "library",
                                  "source": "torrent", "client_ref": "abc"})
    cancelled = []
    res = _run(db, "movie", mid, "Heat", cancel=lambda d, dl: cancelled.append(dl["id"]))
    assert res["success"] and res["seeding_left"] == 1
    assert cancelled == [] and db.get_video_download(seed)["status"] == "completed"


# ── the folder guards ───────────────────────────────────────────────────────

def test_a_movie_loose_in_the_library_root_goes_file_by_file(db, tmp_path):
    mid = _movie(db, tmp_path, folder=False)
    neighbour = tmp_path / "Movies" / "Ronin (1998).mkv"
    neighbour.write_bytes(b"y")
    p = delete_title.plan(db, "movie", mid)
    assert p["folders"] == [] and len(p["loose_files"]) == 1
    assert _run(db, "movie", mid, "Heat")["success"]
    assert neighbour.exists() and (tmp_path / "Movies").is_dir()
    assert not (tmp_path / "Movies" / "Heat (1995).mkv").exists()


def test_a_folder_holding_another_titles_video_is_not_taken_whole(db, tmp_path):
    mid = _movie(db, tmp_path)
    other = tmp_path / "Movies" / "Heat (1995)" / "Heat (1986).mkv"      # not ours
    other.write_bytes(b"z")
    p = delete_title.plan(db, "movie", mid)
    assert p["folders"] == [] and len(p["loose_files"]) == 1
    assert _run(db, "movie", mid, "Heat")["success"]
    assert other.exists()


def test_files_outside_every_library_folder_block_the_delete(db, tmp_path):
    mid = db.upsert_movie("plex", {"server_id": "x", "title": "Heat", "year": 1995, "tmdb_id": 949})
    _file(db, tmp_path / "elsewhere" / "Heat (1995)" / "Heat.mkv", movie_id=mid)
    db.add_movie_to_wishlist(949, "Heat")
    p = delete_title.plan(db, "movie", mid)
    assert p["blocked"]
    res = _run(db, "movie", mid, "Heat")
    assert not res["success"] and "outside your library folders" in res["error"]
    assert (tmp_path / "elsewhere" / "Heat (1995)" / "Heat.mkv").exists()
    assert db.title_wishlist_count("movie", 949) == 1            # nothing touched at all


def test_a_stale_twin_row_goes_with_the_title(db, tmp_path):
    show_id, _eps, _folder = _show(db, tmp_path)
    twin = db.upsert_show_tree("plex", {"server_id": "old-key", "title": "Tulsa King", "year": 2022,
                                        "tmdb_id": 153312, "seasons": []})
    assert sorted(db.title_delete_rows("show", show_id)) == sorted([show_id, twin])
    assert _run(db, "show", show_id, "Tulsa King")["success"]
    assert _one(db, "SELECT COUNT(*) FROM shows")[0] == 0


def test_a_movie_and_a_show_sharing_a_tmdb_id_stay_apart(db, tmp_path):
    show_id, _eps, _folder = _show(db, tmp_path, tmdb=949)
    mid = _movie(db, tmp_path)                                   # Heat, tmdb 949 as a movie
    db.add_movie_to_wishlist(949, "Heat")
    db.add_video_request(profile_id=2, requester_name="Z", kind="movie", tmdb_id=949, title="Heat")
    assert _run(db, "show", show_id, "Tulsa King")["success"]
    assert db.title_wishlist_count("movie", 949) == 1
    assert _one(db, "SELECT status FROM video_requests")[0] == "pending"
    assert db.title_delete_rows("movie", mid) == [mid]


# ── the recycle bin learns folder entries ───────────────────────────────────

def test_a_recycled_title_folder_restores_in_one_step(db, tmp_path):
    show_id, _eps, folder = _show(db, tmp_path)
    assert _run(db, "show", show_id, "Tulsa King")["success"]
    e = recycle.list_entries(recycle_settings(db), db)[0]
    assert e["size"] > 0
    res = recycle.restore_entry(e["trash_dir"], e["name"], recycle_settings(db), db)
    assert res["success"] and (folder / "Season 01" / "Tulsa King - S01E01.mkv").exists()


def test_an_expired_title_folder_is_purged_and_a_young_one_kept(db, tmp_path):
    trash = tmp_path / "TV" / "ss_recycle"
    old = trash / "20000101_000000_Old Show (2001)"
    (old / "Season 01").mkdir(parents=True)
    (old / "Season 01" / "a.mkv").write_bytes(b"x" * 10)
    show_id, _eps, _folder = _show(db, tmp_path)
    assert _run(db, "show", show_id, "Tulsa King")["success"]  # discard runs the purge pass
    names = sorted(p.name for p in trash.iterdir() if not p.name.startswith("."))
    assert len(names) == 1 and names[0].endswith("_Tulsa King (2022)")


def test_purging_one_folder_entry_by_hand_removes_it_whole(db, tmp_path):
    show_id, _eps, _folder = _show(db, tmp_path)
    assert _run(db, "show", show_id, "Tulsa King")["success"]
    e = recycle.list_entries(recycle_settings(db), db)[0]
    assert recycle.purge_entry(e["trash_dir"], e["name"], recycle_settings(db), db)["success"]
    assert recycle.list_entries(recycle_settings(db), db) == []


def test_discard_folder_refuses_a_library_root_and_a_folder_outside_every_root(db, tmp_path):
    from core.video import organization
    s = organization.load(db)
    (tmp_path / "TV").mkdir()
    assert not recycle.discard_folder(str(tmp_path / "TV"), s, db)["ok"]
    stray = tmp_path / "elsewhere" / "Show"
    stray.mkdir(parents=True)
    assert not recycle.discard_folder(str(stray), s, db)["ok"]
    assert (tmp_path / "TV").is_dir() and stray.is_dir()


# ── the routes ──────────────────────────────────────────────────────────────

def _client(tmp_path, *, is_admin=True):
    from flask import Flask
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "api.db"))
    videoapi._video_db.set_setting("movies_path", str(tmp_path / "Movies"))
    app = Flask(__name__)

    @app.before_request
    def _stamp_g():
        from flask import g
        g.is_admin = is_admin
        g.profile_id = 1 if is_admin else 2
        g.can_download = True

    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client(), videoapi._video_db


def test_the_preview_and_the_delete_are_admin_only(tmp_path):
    c, db = _client(tmp_path, is_admin=False)
    mid = _movie(db, tmp_path)
    assert c.get(f"/api/video/title-delete/movie/{mid}").status_code == 403
    assert c.post(f"/api/video/title-delete/movie/{mid}",
                  json={"confirm_title": "Heat"}).status_code == 403
    assert db.title_delete_rows("movie", mid) == [mid]


def test_the_route_checks_the_typed_title_itself(tmp_path, monkeypatch):
    monkeypatch.setattr(delete_title, "_default_refresh", lambda kind: {"ok": True})
    monkeypatch.setattr(delete_title, "_all_profile_ids", lambda: [1])
    c, db = _client(tmp_path)
    mid = _movie(db, tmp_path)
    assert c.get(f"/api/video/title-delete/movie/{mid}").get_json()["title"] == "Heat"
    r = c.post(f"/api/video/title-delete/movie/{mid}", json={})
    assert r.status_code == 409 and db.title_delete_rows("movie", mid) == [mid]
    r = c.post(f"/api/video/title-delete/movie/{mid}", json={"confirm_title": "Heat"})
    assert r.status_code == 200 and r.get_json()["success"]
    assert db.title_delete_rows("movie", mid) == []


# ── the page ────────────────────────────────────────────────────────────────

def test_the_more_menu_item_is_admin_only_and_both_steps_say_torrents_keep_seeding():
    root = Path(__file__).resolve().parent.parent.parent
    js = (root / "webui" / "static" / "video" / "video-detail.js").read_text(encoding="utf-8")
    item = js[js.index("var delId ="):js.index("var links = externalLinks(d);")]
    assert "_canManualMatch()" in item and "'delete-title'" in item
    assert "'Delete Movie' : 'Delete Show'" in item
    assert "which === 'delete-title'" in js
    one = js[js.index("function _deleteStepOne"):js.index("function _deleteStepTwo")]
    two = js[js.index("function _deleteStepTwo"):js.index("function unlinkManualMatch")]
    assert "fate.seed" in one and "fate.seed" in two
    assert "Torrents are left seeding." in js
    assert "confirm_title: input.value" in two                 # the server checks it again
    assert "input.value.trim() !== want" in two                # the button only arms on a match


def test_an_unstamped_folder_in_the_bin_is_never_treated_as_an_entry(db, tmp_path):
    from core.video import organization
    override = tmp_path / "Trash"
    (override / "my stuff").mkdir(parents=True)
    (override / "my stuff" / "keep.txt").write_text("mine")
    db.set_setting("organization", json.dumps({"recycle_path": str(override)}))
    s = {**organization.load(db), "recycle_path": str(override)}
    assert recycle.list_entries(s, db) == []
    assert not recycle.purge_entry(str(override), "my stuff", s, db)["success"]
    recycle.purge_old(s, db)
    assert (override / "my stuff" / "keep.txt").exists()
