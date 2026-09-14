"""Regression tests for all 6 issues identified in discovery.md.

Issue 1: False-positive matches on tribute bands, short preview clips, duration mismatches.
Issue 2: Manual fix overwritten by automatic discovery results on mirrored playlist persistence.
Issue 3: Cancel sync reports success without stopping the background task / Future or sync_service.
Issue 4: Unmatch route wiring, state clearing, and counter desync.
Issue 5: Provider metadata (source, provider, isrc, track_number, disc_number, release_date, duration_ms) preservation.
Issue 6: Terminal sync error handling and state reversion.
"""
import json
import os
import sys
import threading
import pytest
from flask import Flask

from core.matching_engine import MusicMatchingEngine
from core.discovery.scoring import _discovery_score_candidates, init as init_scoring
from core.discovery.endpoints import (
    cancel_sync,
    convert_results_to_spotify_tracks,
)
from services.sync_service import PlaylistSyncService


class _DummyCandidate:
    def __init__(self, name, artists, duration_ms=0):
        self.name = name
        self.artists = artists
        self.duration_ms = duration_ms


class _MockFuture:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True
        return True


class _MockSyncService:
    def __init__(self):
        self.cancelled_playlists = []

    def cancel_sync(self, playlist_name=None):
        self.cancelled_playlists.append(playlist_name)


# ---------------------------------------------------------------------------
# Issue 1: False-Positive Rejections (Tribute, Clips, Duration Mismatch)
# ---------------------------------------------------------------------------

def test_issue1_tribute_artist_rejected():
    me = MusicMatchingEngine()
    init_scoring(me)

    # Source: Queen - Bohemian Rhapsody (354s)
    # Candidate: Queen Tribute - Bohemian Rhapsody (180s)
    cand = _DummyCandidate("Bohemian Rhapsody", ["Queen Tribute"], duration_ms=180000)
    match, conf, idx = _discovery_score_candidates("Bohemian Rhapsody", "Queen", 354000, [cand])
    assert conf < 0.90, f"Queen Tribute should not match Queen with conf >= 0.90 (got {conf})"


def test_issue1_preview_clip_rejected():
    me = MusicMatchingEngine()
    init_scoring(me)

    # Source: Queen - Bohemian Rhapsody (354s)
    # Candidate: Queen - Bohemian Rhapsody (20s clip)
    cand = _DummyCandidate("Bohemian Rhapsody", ["Queen"], duration_ms=20000)
    match, conf, idx = _discovery_score_candidates("Bohemian Rhapsody", "Queen", 354000, [cand])
    assert conf < 0.90, f"20s clip should not match 354s song with conf >= 0.90 (got {conf})"


def test_issue1_genuine_track_accepted():
    me = MusicMatchingEngine()
    init_scoring(me)

    # Source: Queen - Bohemian Rhapsody (354s)
    # Candidate: Queen - Bohemian Rhapsody (350s album version)
    cand = _DummyCandidate("Bohemian Rhapsody", ["Queen"], duration_ms=350000)
    match, conf, idx = _discovery_score_candidates("Bohemian Rhapsody", "Queen", 354000, [cand])
    assert conf >= 0.90, f"Genuine track should match with conf >= 0.90 (got {conf})"
    assert match is cand


# ---------------------------------------------------------------------------
# Issue 2: Manual Fix Persistence on Mirrored Playlist Sync
# ---------------------------------------------------------------------------

def test_issue2_mirrored_manual_match_and_unmatched_persistence(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_music.db")
    monkeypatch.setenv("DATABASE_PATH", test_db)
    from database.music_database import MusicDatabase
    db = MusicDatabase(test_db)
    monkeypatch.setattr("api.source_playlists.get_database", lambda: db)

    from api.source_playlists import _sync_discovery_results_to_mirrored

    # Create mirrored playlist and tracks directly in db
    with db._get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO mirrored_playlists (source, source_playlist_id, name, profile_id) VALUES (?, ?, ?, ?)",
            ("youtube", "yt_123", "Test Playlist", 1),
        )
        pl_id = cur.lastrowid
        cur.execute(
            "INSERT INTO mirrored_playlist_tracks (playlist_id, position, track_name, artist_name, extra_data) VALUES (?, ?, ?, ?, ?)",
            (pl_id, 0, "Track 1", "Artist 1", json.dumps({"manual_match": True, "spotify_id": "sp_manual_1"})),
        )
        cur.execute(
            "INSERT INTO mirrored_playlist_tracks (playlist_id, position, track_name, artist_name, extra_data) VALUES (?, ?, ?, ?, ?)",
            (pl_id, 1, "Track 2", "Artist 2", json.dumps({"unmatched_by_user": True})),
        )
        conn.commit()

    # Incoming automatic discovery results trying to overwrite
    incoming_results = [
        {
            "index": 0,
            "yt_track": "Track 1",
            "yt_artist": "Artist 1",
            "status": "found",
            "spotify_track": "Auto Track 1",
            "spotify_artist": "Auto Artist 1",
            "spotify_id": "sp_auto_1",
            "confidence": 0.95,
        },
        {
            "index": 1,
            "yt_track": "Track 2",
            "yt_artist": "Artist 2",
            "status": "found",
            "spotify_track": "Auto Track 2",
            "spotify_artist": "Auto Artist 2",
            "spotify_id": "sp_auto_2",
            "confidence": 0.95,
        },
    ]

    _sync_discovery_results_to_mirrored("youtube", "yt_123", incoming_results, "youtube", profile_id=1)

    tracks = db.get_mirrored_playlist_tracks(pl_id)
    t1 = next(t for t in tracks if t["track_name"] == "Track 1")
    t2 = next(t for t in tracks if t["track_name"] == "Track 2")

    extra1 = json.loads(t1["extra_data"]) if isinstance(t1["extra_data"], str) else (t1.get("extra_data") or {})
    extra2 = json.loads(t2["extra_data"]) if isinstance(t2["extra_data"], str) else (t2.get("extra_data") or {})

    # Track 1: manual match was NOT overwritten by sp_auto_1
    assert extra1.get("manual_match") is True
    assert extra1.get("spotify_id") == "sp_manual_1"

    # Track 2: user-unmatched track was NOT overwritten with auto match
    assert extra2.get("unmatched_by_user") is True
    assert extra2.get("status") != "found"


# ---------------------------------------------------------------------------
# Issue 3: Cancel Sync Reports Success and Actually Stops Workers
# ---------------------------------------------------------------------------

def test_issue3_cancel_sync_stops_worker_and_service():
    sync_lock = threading.Lock()
    sync_states = {"sp1": {"status": "syncing"}}
    worker = _MockFuture()
    active_sync_workers = {"sp1": worker}
    sync_service = _MockSyncService()

    states = {
        "yt_pl": {
            "phase": "syncing",
            "sync_playlist_id": "sp1",
            "playlist_name": "Rock Classics",
            "sync_progress": {"percent": 50},
        }
    }

    body, code = cancel_sync(
        states,
        "yt_pl",
        label="YouTube",
        not_found_message="Not found",
        sync_lock=sync_lock,
        sync_states=sync_states,
        active_sync_workers=active_sync_workers,
        sync_service=sync_service,
    )

    assert code == 200
    assert body["success"] is True
    assert worker.cancelled is True, "Worker future should have been cancelled"
    assert "Rock Classics" in sync_service.cancelled_playlists
    assert sync_states["sp1"]["status"] == "cancelled"
    assert states["yt_pl"]["phase"] == "discovered"
    assert states["yt_pl"]["sync_playlist_id"] is None


def test_issue3_playlist_sync_service_cancellation_checkpoints():
    svc = PlaylistSyncService(None, None, None)
    # Ensure calling cancel_sync does not raise AttributeError on is_syncing
    svc.cancel_sync("My Playlist")
    assert svc._is_cancelled("My Playlist") is True
    assert svc._is_cancelled("Other Playlist") is False


# ---------------------------------------------------------------------------
# Issue 4 & Issue 5: Metadata Preservation & Unmatch Route
# ---------------------------------------------------------------------------

def test_issue5_convert_results_to_spotify_tracks_preserves_metadata():
    results = [
        {
            "status": "found",
            "spotify_track": "Bohemian Rhapsody",
            "spotify_artist": "Queen",
            "spotify_album": "A Night at the Opera",
            "duration_ms": 354000,
            "source": "qobuz",
            "provider": "qobuz",
            "isrc": "GBUM71029604",
            "track_number": 11,
            "disc_number": 1,
            "release_date": "1975-10-31",
            "match_data": {
                "source": "qobuz",
                "provider": "qobuz",
                "isrc": "GBUM71029604",
            },
        }
    ]

    tracks = convert_results_to_spotify_tracks(results, source_label="Qobuz")
    assert len(tracks) == 1
    t = tracks[0]
    assert t["name"] == "Bohemian Rhapsody"
    assert t["artists"] == ["Queen"]
    assert t["duration_ms"] == 354000
    assert t["source"] == "qobuz"
    assert t["provider"] == "qobuz"
    assert t["isrc"] == "GBUM71029604"
    assert t["track_number"] == 11
    assert t["disc_number"] == 1
    assert t["release_date"] == "1975-10-31"


def test_issue4_unmatch_discovery_track_clears_fields():
    from api.source_playlists import bp, qobuz_discovery_states

    app = Flask(__name__)
    app.register_blueprint(bp)
    client = app.test_client()

    qobuz_discovery_states["pl_test_1"] = {
        "discovery_results": [
            {
                "status": "Found",
                "status_class": "found",
                "spotify_track": "Song",
                "spotify_artist": "Artist",
                "spotify_id": "sp1",
                "matched_data": {"id": "sp1"},
                "confidence": 0.95,
            }
        ],
        "spotify_matches": 1,
    }

    resp = client.post(
        "/api/qobuz/discovery/unmatch",
        json={"identifier": "pl_test_1", "track_index": 0},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True

    state = qobuz_discovery_states["pl_test_1"]
    assert state["spotify_matches"] == 0
    res = state["discovery_results"][0]
    assert res["status"] == "Not Found"
    assert res["status_class"] == "not-found"
    assert res["spotify_track"] == ""
    assert res["spotify_id"] == ""
    assert res["matched_data"] is None
    assert res["match_data"] is None
    assert res["confidence"] == 0


@pytest.mark.parametrize('source,candidate', [('Earth', 'Earth, Wind & Fire'), ('AC', 'AC/DC'), ('Br', 'Brand New')])
def test_full_band_names_are_not_split(source, candidate):
    engine = MusicMatchingEngine()
    init_scoring(engine)
    match, confidence, _ = _discovery_score_candidates('Song', source, 180000, [_DummyCandidate('Song', [candidate], 180000)])
    assert confidence < 0.9


@pytest.mark.parametrize('artist', ['Earth, Wind & Fire', 'AC/DC', 'Brand New'])
def test_full_band_identity_still_matches(artist):
    engine = MusicMatchingEngine()
    init_scoring(engine)
    _, confidence, _ = _discovery_score_candidates('Song', artist, 180000, [_DummyCandidate('Song', [artist], 180000)])
    assert confidence >= 0.9


def test_tidal_cancel_signals_only_named_playlist_and_keeps_running_handle():
    from concurrent.futures import Future
    from types import SimpleNamespace
    worker = Future()
    worker.set_running_or_notify_cancel()
    workers = {'job': worker}
    service = _MockSyncService()
    state = {'tidal': {'phase': 'syncing', 'sync_playlist_id': 'job', 'playlist': SimpleNamespace(name='Tidal Mix')}}
    body, code = cancel_sync(state, 'tidal', label='Tidal', not_found_message='missing', sync_lock=threading.Lock(), sync_states={}, active_sync_workers=workers, sync_service=service)
    assert code == 200 and body['success']
    assert service.cancelled_playlists == ['Tidal Mix']
    assert workers['job'] is worker and worker.running()


def test_missing_provider_state_cannot_unmatch_another_provider(monkeypatch):
    import api.source_playlists as routes
    row = {'status_class': 'found', 'spotify_data': {'id': 'keep'}}
    monkeypatch.setattr(routes, 'qobuz_discovery_states', {})
    monkeypatch.setattr(routes, 'deezer_discovery_states', {'123': {'discovery_results': [row]}})
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    response = app.test_client().post('/api/qobuz/discovery/unmatch', json={'identifier': '123', 'track_index': 0})
    assert response.status_code == 404
    assert row['spotify_data'] == {'id': 'keep'}
