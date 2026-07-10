"""Server-cleanup view: list every collection ON the media server (SoulSync-
managed marked via the sync ledger, foreign — e.g. old Kometa — unmarked) and
bulk-delete by server id."""

from __future__ import annotations

import pytest
from flask import Flask

from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


class _FakeSource:
    server_name = "plex"

    def __init__(self, cols):
        self.cols = cols
        self.deleted = []
        self.fail_ids = set()

    def list_collections(self):
        return [dict(c) for c in self.cols]

    def delete_collection(self, sid):
        if str(sid) in self.fail_ids:
            return {"ok": False, "error": "boom"}
        self.deleted.append(str(sid))
        return {"ok": True}


def _make_client(tmp_path):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client(), videoapi._video_db


def _ledgered_definition(db, name, server_id, member_count=3):
    cid = db.create_collection_definition(name, media_type="movie")
    db.record_collection_sync(cid, server_source="plex", server_id=server_id,
                              members_sig="sig", member_count=member_count)
    return cid


# ── DB: ledger listing ───────────────────────────────────────────────────────
def test_list_collection_syncs_joins_definition_names(db):
    cid = _ledgered_definition(db, "Action", "col1")
    rows = db.list_collection_syncs()
    assert len(rows) == 1
    r = rows[0]
    assert r["definition_id"] == cid and r["server_id"] == "col1"
    assert r["definition_name"] == "Action" and r["server_source"] == "plex"


# ── GET /collections/server ──────────────────────────────────────────────────
def test_server_list_marks_managed_and_sorts_foreign_first(tmp_path, monkeypatch):
    client, vdb = _make_client(tmp_path)
    _ledgered_definition(vdb, "Action", "col1")
    fake = _FakeSource([
        {"server_id": "col1", "name": "Action", "count": 3, "media_type": "movie", "section": "Movies"},
        {"server_id": "k9", "name": "IMDb Top 250", "count": 250, "media_type": "movie", "section": "Movies"},
        {"server_id": "k8", "name": "Christmas Movies", "count": 12, "media_type": "movie", "section": "Movies"},
    ])
    monkeypatch.setattr("core.video.collections.sync.get_collection_source", lambda: fake)

    d = client.get("/api/video/collections/server").get_json()
    assert d["ok"] and d["server"] == "plex"
    names = [c["name"] for c in d["collections"]]
    # Foreign first (alphabetical), managed after — cleanup targets on top.
    assert names == ["Christmas Movies", "IMDb Top 250", "Action"]
    by_name = {c["name"]: c for c in d["collections"]}
    assert by_name["Action"]["managed"] is True
    assert by_name["Action"]["definition_name"] == "Action"
    assert by_name["IMDb Top 250"]["managed"] is False
    assert by_name["IMDb Top 250"]["definition_id"] is None


def test_server_list_detects_kometa_labels(tmp_path, monkeypatch):
    client, vdb = _make_client(tmp_path)
    _ledgered_definition(vdb, "Action", "col1")
    fake = _FakeSource([
        {"server_id": "col1", "name": "Action", "count": 3, "media_type": "movie",
         "section": "Movies", "labels": ["Kometa"], "smart": False},   # ours wins over the label
        {"server_id": "k1", "name": "IMDb Top 250", "count": 250, "media_type": "movie",
         "section": "Movies", "labels": ["Kometa"], "smart": False},
        {"server_id": "k2", "name": "Oscars", "count": 30, "media_type": "movie",
         "section": "Movies", "labels": ["PMM"], "smart": True},       # legacy label
        {"server_id": "h1", "name": "Hand-made", "count": 4, "media_type": "movie",
         "section": "Movies", "labels": [], "smart": False},
    ])
    monkeypatch.setattr("core.video.collections.sync.get_collection_source", lambda: fake)

    d = client.get("/api/video/collections/server").get_json()
    by_name = {c["name"]: c for c in d["collections"]}
    assert by_name["IMDb Top 250"]["kometa"] is True
    assert by_name["Oscars"]["kometa"] is True and by_name["Oscars"]["smart"] is True
    assert by_name["Hand-made"]["kometa"] is False
    assert by_name["Action"]["kometa"] is False        # managed by us — never flagged Kometa
    # Sort: Kometa targets first, then other foreign, managed last.
    assert [c["name"] for c in d["collections"]] == ["IMDb Top 250", "Oscars", "Hand-made", "Action"]


def test_server_list_no_source_is_400(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path)
    monkeypatch.setattr("core.video.collections.sync.get_collection_source", lambda: None)
    r = client.get("/api/video/collections/server")
    assert r.status_code == 400 and r.get_json()["ok"] is False


# ── cleanup job (background bulk delete + live progress) ─────────────────────
@pytest.fixture(autouse=True)
def _reset_job():
    from core.video.collections import server_cleanup as sc
    sc._JOB.update(running=False, phase="idle", done=0, total=0,
                   deleted=0, failed=0, name=None, error=None)
    sc.set_cleanup_progress_emitter(None)
    yield
    sc._JOB["running"] = False
    sc.set_cleanup_progress_emitter(None)


def test_run_delete_clears_ledger_and_keeps_going_on_failures(db):
    from core.video.collections.server_cleanup import run_delete, status
    cid = _ledgered_definition(db, "Action", "col1")
    fake = _FakeSource([])
    fake.fail_ids = {"bad"}

    r = run_delete(db, ["col1", "bad", "k9"], fake)
    assert r == {"ok": True, "deleted": 2, "failed": 1}
    assert sorted(fake.deleted) == ["col1", "k9"]
    # Managed one: ledger row gone (no ghost); definition itself untouched.
    assert db.get_collection_sync(cid) is None
    assert db.get_collection_definition(cid) is not None
    s = status()
    assert s["done"] == 3 and s["deleted"] == 2 and s["failed"] == 1 and s["phase"] == "done"


def test_start_delete_runs_in_background_and_emits_progress(db):
    import time
    from core.video.collections import server_cleanup as sc
    _ledgered_definition(db, "Action", "col1")
    fake = _FakeSource([])
    events = []
    sc.set_cleanup_progress_emitter(lambda name, payload: events.append((name, payload)))

    r = sc.start_delete(db, ["col1", "k9"], source=fake)
    assert r == {"ok": True, "total": 2}
    for _ in range(100):                       # the fake makes this near-instant
        if not sc.status()["running"] and sc.status()["phase"] in ("done", "error"):
            break
        time.sleep(0.05)
    s = sc.status()
    assert s["phase"] == "done" and s["deleted"] == 2
    # Start + finish always emit (throttling only limits the middle).
    assert events and events[0][0] == "collections:cleanup"
    assert events[0][1]["phase"] == "starting" and events[-1][1]["phase"] == "done"


def test_start_delete_refuses_overlap_and_validates(db):
    from core.video.collections import server_cleanup as sc
    fake = _FakeSource([])
    assert sc.start_delete(db, [], source=fake)["ok"] is False
    sc._JOB["running"] = True                  # simulate a purge mid-flight
    r = sc.start_delete(db, ["x"], source=fake)
    assert r["ok"] is False and "already running" in r["error"]


# ── API seam: start + status ─────────────────────────────────────────────────
def test_delete_api_starts_job_and_reports_status(tmp_path, monkeypatch):
    import time
    from core.video.collections import server_cleanup as sc
    client, vdb = _make_client(tmp_path)
    cid = _ledgered_definition(vdb, "Action", "col1")
    fake = _FakeSource([])
    monkeypatch.setattr("core.video.collections.sync.get_collection_source", lambda: fake)

    r = client.post("/api/video/collections/server/delete", json={"ids": ["col1", "k9"]})
    assert r.status_code == 200 and r.get_json() == {"ok": True, "total": 2}
    for _ in range(100):
        s = client.get("/api/video/collections/server/delete/status").get_json()
        if not s["running"] and s["phase"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert s["phase"] == "done" and s["deleted"] == 2 and s["done"] == 2
    assert sorted(fake.deleted) == ["col1", "k9"]
    assert vdb.get_collection_sync(cid) is None

    # Overlap → 409 (the UI attaches to the running job's progress instead).
    sc._JOB["running"] = True
    r = client.post("/api/video/collections/server/delete", json={"ids": ["x"]})
    assert r.status_code == 409


def test_bulk_delete_validates_input(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path)
    monkeypatch.setattr("core.video.collections.sync.get_collection_source",
                        lambda: _FakeSource([]))
    assert client.post("/api/video/collections/server/delete", json={}).status_code == 400
    assert client.post("/api/video/collections/server/delete", json={"ids": []}).status_code == 400
    # /collections/server must not shadow /collections/<int:cid>.
    assert client.get("/api/video/collections/12345").status_code == 404
