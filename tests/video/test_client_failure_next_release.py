"""A release SABnzbd (or the torrent client) failed is replaced by the next one.

SeadogsBooty, Discord Nov 4 2026: a MeGusta 1080p grab failed in SABnzbd and
the episode just sat failed, though more MeGusta releases were posted. client
grabs had no retry: the row failed, went back on the wishlist, and waited for
the wishlist's next pass (hours, with backoff). now the failed release is
blocklisted and the item searched again at once, so the profile's next pick
is grabbed; nothing is announced unless nothing else could be taken, and a
client that fails everything stops after a few releases.
"""

from __future__ import annotations

import json

import pytest

from core.video import download_monitor as monitor
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path, monkeypatch):
    vdb = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    published = []
    import core.video.download_events as events
    # the failure announcements only; re-adding to the wishlist has its own event
    monkeypatch.setattr(events, "publish", lambda name, payload: published.append((name, payload))
                        if name == "video_download_failed" else None)
    spawned = []
    monkeypatch.setattr(monitor, "_spawn_next_release",
                        lambda db_, dl, err: spawned.append((dl["id"], err)))
    removed = []
    import core.video.client_grab as cg
    monkeypatch.setattr(cg, "remove", lambda source, ref, delete_files=True:
                        removed.append((source, ref, delete_files)) or True)
    vdb.removed = removed
    vdb.published = published
    vdb.spawned = spawned
    return vdb


def _row(db, *, source="usenet", release="Show.S01E02.1080p.HEVC-MeGusta", episode=2):
    ctx = {"scope": "episode", "title": "Show", "season": 1, "episode": episode}
    dl_id = db.add_video_download({
        "kind": "episode", "title": "Show", "status": "downloading", "source": source,
        "username": "NZBgeek", "filename": release, "release_title": release,
        "media_id": "1399", "media_source": "tmdb", "search_ctx": json.dumps(ctx),
        "client_ref": "SABnzbd_nzo_x", "candidates": "[]", "tried_files": "[]",
        "tried_queries": "[]"})
    return db.get_video_download(dl_id)


def test_a_failed_usenet_release_is_blocked_and_the_next_one_searched(db):
    dl = _row(db)
    monitor._fail_or_retry(db, dl, "Aborted, cannot be completed", client_failure=True)

    row = db.get_video_download(dl["id"])
    assert row["status"] == "failed"
    assert row["error"] == "Aborted, cannot be completed · trying the next release"
    assert ("NZBgeek", "Show.S01E02.1080p.HEVC-MeGusta") in db.video_blocklist_pairs()
    assert db.spawned == [(dl["id"], row["error"])]
    assert db.published == [], "a replaced release is not something to come and fix"


def test_the_setting_off_keeps_the_old_failure(db):
    from core.video.download_config import save
    save(db, {"redownload_failed": False})
    dl = _row(db)
    monitor._fail_or_retry(db, dl, "Aborted, cannot be completed", client_failure=True)

    assert db.get_video_download(dl["id"])["error"] == "Aborted, cannot be completed"
    assert db.video_blocklist_pairs() == set()
    assert db.spawned == []
    assert [name for name, _ in db.published] == ["video_download_failed"]


def test_a_missing_job_is_not_a_failed_release(db):
    # the client forgetting the job (or a connection problem) says nothing
    # about the release, so it is neither blocked nor replaced
    dl = _row(db)
    monitor._fail_or_retry(db, dl, "Download client could not report this transfer")
    assert db.video_blocklist_pairs() == set()
    assert db.spawned == []


def test_after_five_failed_releases_it_stops_and_says_so(db):
    for n in range(monitor.REDOWNLOAD_LIMIT - 1):
        monitor._fail_or_retry(db, _row(db, release="Show.S01E02.1080p-R%d" % n),
                               "Aborted", client_failure=True)
    assert len(db.spawned) == monitor.REDOWNLOAD_LIMIT - 1

    last = _row(db, release="Show.S01E02.720p-LAST")
    monitor._fail_or_retry(db, last, "Aborted", client_failure=True)

    assert len(db.spawned) == monitor.REDOWNLOAD_LIMIT - 1, "the fifth failure doesn't retry"
    error = db.get_video_download(last["id"])["error"]
    assert "5 releases failed in a row" in error
    assert [name for name, _ in db.published] == ["video_download_failed"]


def test_another_episode_has_its_own_count(db):
    for n in range(monitor.REDOWNLOAD_LIMIT - 1):
        monitor._fail_or_retry(db, _row(db, release="Show.S01E02-R%d" % n),
                               "Aborted", client_failure=True)
    monitor._fail_or_retry(db, _row(db, release="Show.S01E03-A", episode=3),
                           "Aborted", client_failure=True)
    assert len(db.spawned) == monitor.REDOWNLOAD_LIMIT


def test_nothing_left_to_grab_is_announced(db, monkeypatch):
    dl = _row(db)
    monkeypatch.setattr(monitor, "_search_again_now", lambda db_, dl_, wait=False: False)
    assert monitor._next_release_now(db, dl, "Aborted · trying the next release") is False

    error = db.get_video_download(dl["id"])["error"]
    assert error.startswith("Aborted, and no other release your profile accepts was found")
    assert [name for name, _ in db.published] == ["video_download_failed"]


def test_a_grabbed_replacement_is_quiet(db, monkeypatch):
    dl = _row(db)
    monkeypatch.setattr(monitor, "_search_again_now", lambda db_, dl_, wait=False: True)
    assert monitor._next_release_now(db, dl, "Aborted · trying the next release") is True
    assert db.published == []


def test_the_wait_search_reports_a_grab(monkeypatch, db):
    import core.video.wishlist_search as ws
    calls = []

    def fake(scope, tmdb_id, season_number=None, episode_number=None, profile_id=1, wait=False):
        calls.append((scope, tmdb_id, season_number, episode_number, profile_id, wait))
        return {"queued": 1, "skipped": 0, "total": 1, "grabbed": 1}

    monkeypatch.setattr(ws, "manual_search", fake)
    db.add_episodes_to_wishlist(1399, "Show", [{"season_number": 1, "episode_number": 2}],
                                profile_id=3)
    assert monitor._search_again_now(db, _row(db), wait=True) is True
    assert calls == [("episode", 1399, 1, 2, 3, True)]


def test_the_wait_search_reports_nothing_taken(monkeypatch, db):
    import core.video.wishlist_search as ws
    monkeypatch.setattr(ws, "manual_search",
                        lambda *a, **k: {"queued": 1, "skipped": 0, "total": 1, "grabbed": 0})
    assert monitor._search_again_now(db, _row(db), wait=True) is False


def test_a_stalled_torrent_is_a_failed_release_too(db, monkeypatch):
    # the tick hands both the client's "failed" and a stall to the same path
    import inspect
    src = inspect.getsource(monitor._tick)
    assert src.count('client_failure=dl.get("source") in ("torrent", "usenet")') == 2


def test_a_replaced_release_leaves_the_client_with_its_files(db):
    # a stalled torrent otherwise kept going next to its replacement, and a
    # failed sab job kept its partial files
    monitor._fail_or_retry(db, _row(db, source="torrent"), "Stalled", client_failure=True)
    assert db.removed == [("torrent", "SABnzbd_nzo_x", True)]


def test_a_release_it_gave_up_on_stays_in_the_client(db):
    from core.video.download_config import save
    save(db, {"redownload_failed": False})
    monitor._fail_or_retry(db, _row(db), "Aborted", client_failure=True)
    assert db.removed == []


def _cancel_client(db, monkeypatch):
    import api.video as videoapi
    from flask import Flask
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _admin():
        from flask import g
        g.profile_id = 1
        g.is_admin = True
        g.can_download = True

    import core.video.slskd_download as slskd
    monkeypatch.setattr(slskd, "cancel_download", lambda *a: db.removed.append(("slskd",) + a))
    return app.test_client()


def test_cancel_takes_a_client_job_out_of_the_client(db, monkeypatch):
    import api.video as videoapi
    try:
        http = _cancel_client(db, monkeypatch)
        dl = _row(db, source="usenet")
        assert http.post("/api/video/downloads/cancel", json={"id": dl["id"]}).get_json()["ok"]
        assert db.get_video_download(dl["id"])["status"] == "cancelled"
        assert db.removed == [("usenet", "SABnzbd_nzo_x", True)]
    finally:
        videoapi._video_db = None


def test_cancel_of_a_finished_download_touches_nothing(db, monkeypatch):
    import api.video as videoapi
    try:
        http = _cancel_client(db, monkeypatch)
        dl = _row(db, source="torrent")
        db.update_video_download(dl["id"], status="completed")
        assert http.post("/api/video/downloads/cancel", json={"id": dl["id"]}).get_json()["already"]
        assert db.removed == []
    finally:
        videoapi._video_db = None


# -- the retry keeps the profile the failed grab was ranked by (SeadogsBooty) ---

def test_the_retry_ranks_under_the_failed_grabs_profile(monkeypatch, db):
    # Dexter: Resurrection, not in the library, profile 3 picked in the search
    # modal: the replacement was ranked under Default and took a 1.4 GB
    # non-MeGusta release
    import core.video.wishlist_search as ws
    seen = []
    monkeypatch.setattr(ws, "manual_search", lambda *a, **k: seen.append(k.get("quality_profile_id"))
                        or {"queued": 1, "skipped": 0, "total": 1, "grabbed": 1})
    dl = _row(db)
    db.update_video_download(dl["id"], quality_profile_id=3)
    assert monitor._search_again_now(db, db.get_video_download(dl["id"]), wait=True) is True
    assert seen == [3]


def test_a_retry_without_a_profile_passes_none(monkeypatch, db):
    import core.video.wishlist_search as ws
    seen = []
    monkeypatch.setattr(ws, "manual_search", lambda *a, **k: seen.append("quality_profile_id" in k)
                        or {"grabbed": 1})
    monitor._search_again_now(db, _row(db), wait=True)
    assert seen == [False]


def test_the_search_ranks_items_under_the_given_profile(monkeypatch, db):
    import core.video.wishlist_search as ws
    import api.video as api_video
    monkeypatch.setattr(api_video, "get_video_db", lambda: db)
    monkeypatch.setattr(ws.vpw, "_default_target_dir", lambda media_type: "/tv")
    monkeypatch.setattr(ws.vpw, "_default_active_keys", lambda media_type: set())
    import core.video.numbering as numbering
    monkeypatch.setattr(numbering, "annotate", lambda items: items)
    ran = []
    monkeypatch.setattr(ws, "_run_batch", lambda todo, media_type, **k: ran.extend(todo) or 1)
    db.add_episodes_to_wishlist(1399, "Show", [{"season_number": 1, "episode_number": 2}])
    ws.manual_search("episode", 1399, season_number=1, episode_number=2, wait=True,
                     quality_profile_id=3)
    assert [it["quality_profile_id"] for it in ran] == [3]


def test_a_profile_picked_in_the_search_modal_reaches_the_wishlist_search(db):
    # a show not in the library keeps the modal's pick in video_title_profiles;
    # the wishlist search only read the wish row and the library row. the pick
    # stamps rows already wished; an episode wished after it carried none
    assert db.set_title_profile_choice("show", 1399, 3)
    db.add_episodes_to_wishlist(1399, "Show", [{"season_number": 1, "episode_number": 2}])
    items = db.wishlist_manual_search_items("episode", 1399, season_number=1, episode_number=2)
    assert [it["quality_profile_id"] for it in items] == [3]
    due = db.episode_wishlist_to_download(due_only=False)
    assert [it["quality_profile_id"] for it in due] == [3]
