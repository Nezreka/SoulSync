"""Failed-release blocklist — never re-grab a proven-bad release (Sonarr-style).

Identity = (username, filename): the exact remote file. Auto-added ONLY when the
importer proves the FILE ITSELF is junk (sample / corrupt / fake / not-a-video —
the ``bad_release`` reject tag); peer flakes and context rejects (pack, wrong
episode, not-an-upgrade) never auto-block. Filtered at every pick path: the
ranker (`_evaluate_hits` un-accepts + annotates, so manual search still SHOWS
them greyed), stored-candidate retries (`plan_retry`) and requery merges
(`merge_candidates`). Manual grab of a blocked release stays possible — that's
a deliberate user override.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent.parent
_DLPAGE_JS = (_ROOT / "webui" / "static" / "video" / "video-downloads-page.js").read_text(encoding="utf-8")
_HISTORY_JS = (_ROOT / "webui" / "static" / "video" / "video-download-history.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


@pytest.fixture()
def client(tmp_path):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client(), videoapi._video_db
    finally:
        videoapi._video_db = None


def _block(db, user="peer1", fn="@@x\\Movies\\Heat.1995.mkv", **kw):
    row = {"username": user, "filename": fn, "kind": "movie", "title": "Heat",
           "release_title": "Heat.1995.1080p", "reason": "test", **kw}
    return db.add_video_blocklist(row)


# ── DB spine ─────────────────────────────────────────────────────────────────
def test_add_is_idempotent_on_the_release_identity(db):
    a = _block(db)
    b = _block(db, reason="second time")
    assert a == b and len(db.list_video_blocklist()) == 1
    assert db.video_blocklist_pairs() == {("peer1", "@@x\\Movies\\Heat.1995.mkv")}
    assert _block(db, user="peer2") != a          # other peer's copy = other identity


def test_remove_and_clear(db):
    rid = _block(db)
    _block(db, user="peer2")
    assert db.remove_video_blocklist(rid) is True
    assert db.clear_video_blocklist() == 1
    assert db.video_blocklist_pairs() == set()


def test_bad_input_never_writes(db):
    assert db.add_video_blocklist({"username": "", "filename": "x"}) == 0
    assert db.add_video_blocklist({}) == 0
    assert db.list_video_blocklist() == []


# ── the ranker: blocked stays visible but never accepted ─────────────────────
def _hit(user, fn, title="Heat.1995.1080p.BluRay.x264"):
    return {"title": title, "username": user, "filename": fn,
            "size_bytes": 2 * 1024 ** 3, "availability": 5}


def test_ranker_unaccepts_blocked_releases(db):
    from api.video.downloads import _evaluate_hits
    from core.video.quality_profile import load as load_profile
    profile = load_profile(db)
    blocked = {("peer1", "bad.mkv")}
    out = _evaluate_hits([_hit("peer1", "bad.mkv"), _hit("peer2", "good.mkv")],
                         profile, "movie", None, None, blocked=blocked, blocked_users=set())
    by_fn = {r["filename"]: r for r in out}
    assert by_fn["bad.mkv"]["blocked"] is True
    assert by_fn["bad.mkv"]["accepted"] is False
    assert "Blocklisted release" in by_fn["bad.mkv"]["rejected"]
    assert by_fn["good.mkv"]["blocked"] is False and by_fn["good.mkv"]["accepted"] is True
    # pick_best (first accepted) therefore skips the blocked one
    from core.automation.handlers.video_process_wishlist import pick_best
    assert pick_best(out)["filename"] == "good.mkv"


# ── source (uploader) blocklist — block a whole peer, skip all their releases ──
def test_block_source_roundtrip_and_excluded_from_release_pairs(db):
    rid = db.block_video_source("baduser", reason="always fake")
    assert rid > 0
    assert db.blocked_usernames() == {"baduser"}
    assert db.block_video_source("baduser") == rid          # idempotent on the username
    assert db.block_video_source("") == 0                   # empty never writes
    # a source-wide block is NOT a per-release pair (the '' sentinel is excluded)
    _block(db, user="peer1")
    assert db.video_blocklist_pairs() == {("peer1", "@@x\\Movies\\Heat.1995.mkv")}
    assert db.blocked_usernames() == {"baduser"}


def test_ranker_unaccepts_every_release_from_a_blocked_uploader(db):
    from api.video.downloads import _evaluate_hits
    from core.video.quality_profile import load as load_profile
    profile = load_profile(db)
    out = _evaluate_hits([_hit("baduser", "a.mkv"), _hit("goodpeer", "b.mkv")],
                         profile, "movie", None, None, blocked=set(), blocked_users={"baduser"})
    by_user = {r["username"]: r for r in out}
    assert by_user["baduser"]["accepted"] is False and by_user["baduser"]["blocked"] is True
    assert "Uploader blocklisted" in by_user["baduser"]["rejected"]
    assert by_user["goodpeer"]["accepted"] is True          # a different peer still passes


def test_block_source_api_via_scope(client):
    c, db = client
    r = c.post("/api/video/downloads/blocklist", json={"username": "baduser", "scope": "source"})
    assert r.status_code == 200 and r.get_json()["success"] is True
    assert db.blocked_usernames() == {"baduser"}


# ── retry paths ──────────────────────────────────────────────────────────────
def test_plan_retry_skips_blocked_stored_candidates():
    from core.video.retry import plan_retry
    row = {"attempts": 1,
           "candidates": json.dumps([{"username": "p1", "filename": "bad.mkv"},
                                     {"username": "p2", "filename": "good.mkv"}]),
           "tried_files": "[]", "search_ctx": "{}", "tried_queries": "[]"}
    plan = plan_retry(row, blocked={("p1", "bad.mkv")})
    assert plan["action"] == "candidate" and plan["candidate"]["filename"] == "good.mkv"
    assert plan["rest"] == []


def test_merge_candidates_drops_blocked_requery_hits():
    from core.video.retry import merge_candidates
    hits = [{"username": "p1", "filename": "bad.mkv", "title": "x"},
            {"username": "p2", "filename": "good.mkv", "title": "y"}]
    out = merge_candidates(hits, [], blocked={("p1", "bad.mkv")})
    assert [c["filename"] for c in out] == ["good.mkv"]


def test_stored_candidate_from_a_blocked_uploader_is_dropped_on_retry():
    # a candidate stored BEFORE the uploader was blocked must still be skipped
    from core.video.retry import plan_retry, merge_candidates
    row = {"attempts": 1,
           "candidates": json.dumps([{"username": "baduser", "filename": "a.mkv"},
                                     {"username": "goodpeer", "filename": "b.mkv"}]),
           "tried_files": "[]", "search_ctx": "{}", "tried_queries": "[]"}
    plan = plan_retry(row, blocked=set(), blocked_users={"baduser"})
    assert plan["action"] == "candidate" and plan["candidate"]["filename"] == "b.mkv"
    # and merging fresh requery hits drops the blocked uploader too
    hits = [{"username": "baduser", "filename": "a.mkv", "title": "x"},
            {"username": "goodpeer", "filename": "b.mkv", "title": "y"}]
    assert [c["filename"] for c in merge_candidates(hits, [], blocked=set(),
                                                    blocked_users={"baduser"})] == ["b.mkv"]


# ── importer tagging: file-is-junk vs fine-release-wrong-context ─────────────
def test_importer_tags_only_proven_bad_files():
    from core.video.importer import plan_import
    dl = {"kind": "movie", "title": "Heat", "size_bytes": 50 * 1024 * 1024,
          "release_title": "Heat.1995.1080p.sample", "search_ctx": json.dumps({"scope": "movie"})}
    plan = plan_import(dl, "/dl/Heat.sample.mkv", list_dir=lambda d: [])
    assert plan["action"] == "reject" and plan["bad_release"] is True   # sample

    ep = {"kind": "show", "title": "Severance",
          "release_title": "Severance.S01E05.1080p",
          "search_ctx": json.dumps({"scope": "episode", "season": 1, "episode": 3})}
    plan2 = plan_import(ep, "/dl/Severance.S01E05.mkv", list_dir=lambda d: [])
    assert plan2["action"] == "reject" and "S01E05" in plan2["reason"]
    assert plan2["bad_release"] is False    # right release, wrong context — NOT blocked


def test_run_import_reject_patch_carries_the_transient_tag():
    from core.video.importer import run_import

    class _FS:
        def list_dir(self, d):
            return []
    dl = {"kind": "movie", "title": "Heat", "size_bytes": 50 * 1024 * 1024,
          "release_title": "Heat.1995.sample", "search_ctx": json.dumps({"scope": "movie"})}
    patch = run_import(dl, "/dl/Heat.sample.mkv", fs=_FS())
    assert patch["status"] == "import_failed" and patch["_bad_release"] is True


# ── the monitor auto-adds on a bad-release import reject ─────────────────────
def test_tick_blocklists_a_proven_bad_release(db, monkeypatch):
    import core.video.download_monitor as mon
    conn = db._get_connection()
    conn.execute("INSERT INTO video_downloads (kind, title, status, source, username, filename, "
                 "release_title, media_id, media_source, search_ctx) "
                 "VALUES ('movie','Heat','downloading','slskd','peer1','@@x\\Heat.mkv',"
                 "'Heat.1995.sample','603','tmdb','{\"season\": null}')")
    conn.commit(); conn.close()
    monkeypatch.setattr(mon, "list_downloads", lambda: [])
    monkeypatch.setattr(mon, "process_download", lambda dl, *a, **k: {
        "status": "import_failed", "progress": 100.0,
        "error": "Looks like a sample, not the feature", "_bad_release": True})
    mon._tick(db)
    rows = db.list_video_blocklist()
    assert len(rows) == 1
    r = rows[0]
    assert (r["username"], r["filename"]) == ("peer1", "@@x\\Heat.mkv")
    assert "sample" in r["reason"] and r["title"] == "Heat"


def test_tick_never_blocklists_context_rejects(db, monkeypatch):
    import core.video.download_monitor as mon
    conn = db._get_connection()
    conn.execute("INSERT INTO video_downloads (kind, title, status, source, username, filename) "
                 "VALUES ('movie','Heat','downloading','slskd','peer1','@@x\\Heat.mkv')")
    conn.commit(); conn.close()
    monkeypatch.setattr(mon, "list_downloads", lambda: [])
    monkeypatch.setattr(mon, "process_download", lambda dl, *a, **k: {
        "status": "import_failed", "progress": 100.0,
        "error": "Not an upgrade over the copy already in the library", "_bad_release": False})
    mon._tick(db)
    assert db.list_video_blocklist() == []


# ── API ──────────────────────────────────────────────────────────────────────
def test_blocklist_api_lifecycle(client):
    c, db = client
    conn = db._get_connection()
    cur = conn.execute("INSERT INTO video_downloads (kind, title, status, source, username, filename, "
                       "release_title, error) VALUES ('movie','Heat','failed','slskd','peer1',"
                       "'@@x\\Heat.mkv','Heat.1995','Transfer died')")
    dl_id = cur.lastrowid
    conn.commit(); conn.close()

    r = c.post("/api/video/downloads/blocklist", json={"download_id": dl_id})
    assert r.get_json()["success"] is True
    items = c.get("/api/video/downloads/blocklist").get_json()["items"]
    assert len(items) == 1 and items[0]["reason"] == "Transfer died"

    r2 = c.post("/api/video/downloads/blocklist",
                json={"username": "peer2", "filename": "other.mkv", "title": "Heat"})
    assert r2.get_json()["success"] is True
    rid = r2.get_json()["id"]
    assert c.delete(f"/api/video/downloads/blocklist/{rid}").get_json()["success"] is True
    assert c.post("/api/video/downloads/blocklist/clear", json={}).get_json()["removed"] == 1
    # a row with no release identity can't be blocked
    assert c.post("/api/video/downloads/blocklist", json={}).status_code == 400


def test_blocklist_api_from_history_row(client):
    c, db = client
    hid = db.record_download_history({
        "id": 991, "kind": "movie", "source": "slskd", "status": "failed",
        "title": "Heat", "username": "peer1", "filename": "@@x\\Heat.mkv",
        "release_title": "Heat.1995", "error": "Soulseek transfer errored",
        "media_id": "603", "media_source": "tmdb"})
    assert hid
    r = c.post("/api/video/downloads/blocklist", json={"history_id": hid})
    assert r.get_json()["success"] is True
    items = c.get("/api/video/downloads/blocklist").get_json()["items"]
    assert items[0]["username"] == "peer1" and items[0]["title"] == "Heat"


# ── frontend contracts ───────────────────────────────────────────────────────
def test_downloads_page_has_block_button_and_modal():
    assert "data-vdpg-block" in _DLPAGE_JS
    assert "'/api/video/downloads/blocklist'" in _DLPAGE_JS
    # failed rows block-and-retry; import_failed only blocks
    assert "data-was" in _DLPAGE_JS and "wasFailed" in _DLPAGE_JS
    # YT rows never show it (no slskd release identity)
    btn = _DLPAGE_JS.split("var blockBtn =")[1].split("var actHTML")[0]
    assert "dlType(d.kind) !== 'youtube'" in btn and "d.username && d.filename" in btn
    for fn in ("function blkOpen", "function blkLoad", "data-vblk-remove", "data-vblk-clear"):
        assert fn in _DLPAGE_JS, fn
    assert "showConfirmDialog" in _DLPAGE_JS      # clear-all is guarded


def test_history_modal_and_header_wiring():
    assert "data-vdh-block" in _HISTORY_JS
    assert "/api/video/downloads/blocklist" in _HISTORY_JS
    assert "data-vblk-open" in _INDEX             # header button opens the modal
