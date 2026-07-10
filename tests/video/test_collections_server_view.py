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


# ── POST /collections/server/delete ──────────────────────────────────────────
def test_bulk_delete_clears_ledger_for_managed(tmp_path, monkeypatch):
    client, vdb = _make_client(tmp_path)
    cid = _ledgered_definition(vdb, "Action", "col1")
    fake = _FakeSource([])
    monkeypatch.setattr("core.video.collections.sync.get_collection_source", lambda: fake)

    d = client.post("/api/video/collections/server/delete",
                    json={"ids": ["col1", "k9"]}).get_json()
    assert d["ok"] and d["deleted"] == 2 and d["failed"] == []
    assert sorted(fake.deleted) == ["col1", "k9"]
    # Managed one: ledger row gone (no ghost); definition itself untouched.
    assert vdb.get_collection_sync(cid) is None
    assert vdb.get_collection_definition(cid) is not None


def test_bulk_delete_reports_failures_and_keeps_going(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path)
    fake = _FakeSource([])
    fake.fail_ids = {"bad"}
    monkeypatch.setattr("core.video.collections.sync.get_collection_source", lambda: fake)

    d = client.post("/api/video/collections/server/delete",
                    json={"ids": ["bad", "ok1"]}).get_json()
    assert d["ok"] and d["deleted"] == 1
    assert d["failed"] == [{"server_id": "bad", "error": "boom"}]
    assert fake.deleted == ["ok1"]


def test_bulk_delete_validates_input(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path)
    monkeypatch.setattr("core.video.collections.sync.get_collection_source",
                        lambda: _FakeSource([]))
    assert client.post("/api/video/collections/server/delete", json={}).status_code == 400
    assert client.post("/api/video/collections/server/delete", json={"ids": []}).status_code == 400
    # /collections/server must not shadow /collections/<int:cid>.
    assert client.get("/api/video/collections/12345").status_code == 404
