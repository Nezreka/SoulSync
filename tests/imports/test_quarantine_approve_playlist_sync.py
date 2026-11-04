"""An approved quarantine track gets what a finished batch gives its tracks.

#1976: approval can come long after the batch finished, so no batch completion
ran for it. The media server only heard of the file from the manager tab, and
the playlist the track was downloaded for waited for its next full sync."""

from __future__ import annotations

import api.quarantine as q


class _Scan:
    def __init__(self):
        self.reasons = []

    def request_scan(self, reason):
        self.reasons.append(reason)


def _wire(monkeypatch):
    scan = _Scan()
    registered = []
    monkeypatch.setattr(q, "_automation_engine", lambda: None)
    monkeypatch.setattr(q, "_web_scan_manager", lambda: scan)
    monkeypatch.setattr(
        "core.sync.post_download_sync.register_completed_playlist_batch",
        lambda batch, tasks_snapshot=None, database=None: registered.append((batch, tasks_snapshot)) or [1])
    return scan, registered


def test_an_approved_playlist_track_queues_its_playlist_and_a_scan(tmp_path, monkeypatch):
    scan, registered = _wire(monkeypatch)
    song = tmp_path / "Silver Bells.flac"
    song.write_bytes(b"audio")
    info = {"source_type": "playlist",
            "source_info": {"playlist_id": "pl-1", "playlist_name": "Christmas"}}
    batch = {"is_playlist_batch": True, "playlist_id": "pl-1", "playlist_name": "Christmas"}

    q._after_approved_import({"_final_processed_path": str(song), "track_info": info}, batch)

    assert scan.reasons == ["Quarantine approval completed"]
    (got_batch, snapshot), = registered
    assert got_batch == batch
    assert snapshot == {"approved": {"status": "completed", "track_info": info}}


def test_a_restart_dropped_batch_still_finds_the_playlist_on_the_track(tmp_path, monkeypatch):
    scan, registered = _wire(monkeypatch)
    song = tmp_path / "a.flac"
    song.write_bytes(b"audio")
    info = {"source_info": {"playlist_name": "Christmas"}}

    q._after_approved_import({"_final_path": str(song), "track_info": info}, {})

    assert registered == [({}, {"approved": {"status": "completed", "track_info": info}})]
    assert scan.reasons == ["Quarantine approval completed"]


def test_an_import_that_produced_no_file_queues_nothing(tmp_path, monkeypatch):
    scan, registered = _wire(monkeypatch)

    q._after_approved_import({"_final_processed_path": str(tmp_path / "gone.flac"),
                              "track_info": {"source_type": "playlist"}}, {"is_playlist_batch": True})
    q._after_approved_import({}, {})

    assert registered == [] and scan.reasons == []


def test_the_playlist_registration_resolves_the_approved_track(monkeypatch):
    """the real registration reads the approved track from the snapshot"""
    from core.sync import post_download_sync as pds

    seen = []

    class _Db:
        def resolve_mirrored_playlist_assignment(self, playlist_ref, playlist_name, profile_id):
            seen.append((playlist_ref, playlist_name, profile_id))
            return {"id": 42, "name": playlist_name}

    pds.clear_pending_playlist_syncs()
    ids = pds.register_completed_playlist_batch(
        {}, tasks_snapshot={"approved": {"status": "completed", "track_info": {
            "source_type": "playlist", "profile_id": 2,
            "source_info": {"playlist_id": "pl-1", "playlist_name": "Christmas"}}}},
        database=_Db())
    pending = pds.drain_pending_playlist_syncs()

    assert ids == [42] and seen == [("pl-1", "Christmas", 2)]
    assert [(p["mirrored_playlist_id"], p["profile_id"]) for p in pending] == [(42, 2)]


def test_the_approve_url_still_runs_the_approve_view():
    """the helper once landed between the route decorator and the view, so
    the approve url called it with an entry id and every approve failed"""
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(q.bp)
    endpoint, args = app.url_map.bind('localhost').match('/api/quarantine/abc/approve', method='POST')

    assert app.view_functions[endpoint] is q.approve_quarantine_item
    assert args == {'entry_id': 'abc'}
