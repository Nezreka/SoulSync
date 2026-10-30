from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from core.automation.handlers.sync_playlist import _sync_single_mirrored_playlist
from core.downloads.wishlist_failed import add_failed_tracks_to_wishlist
from core.playlists.curation import (
    CurationPolicyStale,
    curated_tracks_for_operation,
    verify_curation_snapshot,
    wishlist_track_allowed,
)
from core.playlists.organize_download import run_playlist_organize_download
from core.wishlist.processing import _run_wishlist_cycle
from database.music_database import MusicDatabase
from services.sync_service import PlaylistSyncService


def _mirror(db, *, source_playlist_id="mix"):
    return db.mirror_playlist(
        source="spotify",
        source_playlist_id=source_playlist_id,
        name="Mix",
        tracks=[
            {"track_name": "One", "artist_name": "Artist", "source_track_id": "one"},
            {"track_name": "Two", "artist_name": "Artist", "source_track_id": "two"},
        ],
        profile_id=1,
    )


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _enable(db, playlist_id, updates=()):
    if updates:
        db.update_mirrored_playlist_decisions(playlist_id, list(updates), profile_id=1)
    assert db.update_mirrored_playlist_preferences(
        playlist_id, profile_id=1, curation_enabled=True
    ) == "ok"


def _sync_deps(db, calls):
    return SimpleNamespace(
        get_database=lambda: db,
        logger=SimpleNamespace(debug=lambda *a, **k: None, error=lambda *a, **k: None),
        update_progress=lambda *a, **k: None,
        config_manager=SimpleNamespace(
            get=lambda key, default=None: default,
            get_active_media_server=lambda: None,
        ),
        load_sync_status_file=lambda: {},
        server_playlist_exists=None,
        run_sync_task=lambda *a, **k: calls.append((a, k)),
        sync_states={},
    )


@pytest.mark.parametrize("user_initiated", [False, True])
def test_scheduled_and_manual_zero_keep_are_curation_pending(db, user_initiated):
    playlist_id = _mirror(db)
    _enable(db, playlist_id)
    calls = []

    result = _sync_single_mirrored_playlist(
        {"playlist_id": playlist_id, "user_initiated": user_initiated},
        _sync_deps(db, calls),
    )

    assert result["status"] == "curation_pending"
    assert calls == []


def test_only_keep_tracks_reach_sync_and_snapshot_is_forwarded(db):
    playlist_id = _mirror(db)
    _enable(db, playlist_id, [
        {"source_track_id": "one", "decision": "keep"},
        {"source_track_id": "two", "decision": "pending"},
    ])
    calls = []

    result = _sync_single_mirrored_playlist(
        {"playlist_id": playlist_id}, _sync_deps(db, calls)
    )
    for _ in range(100):
        if calls:
            break
        time.sleep(0.01)

    assert result["status"] == "started"
    assert len(calls) == 1
    assert [track["source_track_id"] for track in calls[0][0][2]] == ["one"]
    assert calls[0][1]["curation_snapshot"]["source_track_ids"] == ["one"]


def test_enabled_curation_policy_read_failure_blocks_sync(db, monkeypatch):
    playlist_id = _mirror(db)
    _enable(db, playlist_id, [{"source_track_id": "one", "decision": "keep"}])
    monkeypatch.setattr(
        db,
        "get_mirrored_playlist_curation",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )
    calls = []

    result = _sync_single_mirrored_playlist(
        {"playlist_id": playlist_id}, _sync_deps(db, calls)
    )

    assert result["status"] == "error"
    assert "could not be verified" in result["reason"]
    assert calls == []


def test_policy_revision_change_invalidates_in_flight_snapshot(db):
    playlist_id = _mirror(db)
    _enable(db, playlist_id, [{"source_track_id": "one", "decision": "keep"}])
    tracks, snapshot = curated_tracks_for_operation(
        db, playlist_id, db.get_mirrored_playlist_tracks(playlist_id), profile_id=1
    )
    assert [track["source_track_id"] for track in tracks] == ["one"]

    db.update_mirrored_playlist_decisions(
        playlist_id,
        [{"source_track_id": "one", "decision": "reject"}],
        profile_id=1,
    )

    with pytest.raises(CurationPolicyStale):
        verify_curation_snapshot(db, snapshot)


def test_organize_download_zero_keep_does_not_create_batch(db):
    playlist_id = _mirror(db)
    _enable(db, playlist_id)
    batches = {}
    deps = SimpleNamespace(
        get_database=lambda: db,
        get_download_batches=lambda: batches,
        tasks_lock=threading.Lock(),
        missing_download_executor=SimpleNamespace(submit=lambda *a, **k: pytest.fail("submitted")),
    )

    result = run_playlist_organize_download(
        deps,
        mirrored_playlist_id=playlist_id,
        get_batch_max_concurrent=lambda _album: 1,
        run_full_missing_tracks_process=lambda *a, **k: None,
    )

    assert result["status"] == "curation_pending"
    assert batches == {}


def _payload():
    return {
        "id": "recording-one",
        "name": "One",
        "artists": [{"name": "Artist"}],
        "album": {"name": "Release", "id": "release-one"},
    }


def _origin(playlist_id):
    return {"mirrored_playlist_id": playlist_id, "source_track_id": "one"}


def test_wishlist_keeps_release_aware_independent_playlist_origins(db):
    first = _mirror(db, source_playlist_id="first")
    second = _mirror(db, source_playlist_id="second")
    _enable(db, first, [{"source_track_id": "one", "decision": "keep"}])
    _enable(db, second, [{"source_track_id": "one", "decision": "keep"}])

    for playlist_id in (first, second):
        outcome = db.add_to_wishlist_detailed(
            spotify_track_data=_payload(),
            source_type="playlist",
            source_info=_origin(playlist_id),
            profile_id=1,
        )
        assert outcome["applied"] is True

    row = db.get_wishlist_tracks(profile_id=1)[0]
    assert row["spotify_track_id"] == "recording-one::release-one"
    assert row["source_info"]["playlist_origins"] == [_origin(first), _origin(second)]

    db.update_mirrored_playlist_decisions(
        first, [{"source_track_id": "one", "decision": "reject"}], profile_id=1
    )
    assert wishlist_track_allowed(db, row) is True
    db.update_mirrored_playlist_decisions(
        second, [{"source_track_id": "one", "decision": "reject"}], profile_id=1
    )
    assert wishlist_track_allowed(db, row) is False


def test_wishlist_keeps_distinct_album_releases_separate(db):
    playlist_id = _mirror(db)
    _enable(db, playlist_id, [{"source_track_id": "one", "decision": "keep"}])
    first = _payload()
    second = _payload()
    second["album"] = {"name": "Other Release", "id": "release-two"}

    for payload in (first, second):
        outcome = db.add_to_wishlist_detailed(
            spotify_track_data=payload,
            source_type="playlist",
            source_info=_origin(playlist_id),
            profile_id=1,
        )
        assert outcome["applied"] is True

    rows = db.get_wishlist_tracks(profile_id=1)
    assert {row["spotify_track_id"] for row in rows} == {
        "recording-one::release-one",
        "recording-one::release-two",
    }
    assert all(
        row["source_info"]["playlist_origins"] == [_origin(playlist_id)]
        for row in rows
    )


def test_deleted_origin_does_not_authorize_but_other_keep_still_does(db):
    deleted = _mirror(db, source_playlist_id="deleted")
    approved = _mirror(db, source_playlist_id="approved")
    _enable(db, deleted, [{"source_track_id": "one", "decision": "keep"}])
    _enable(db, approved, [{"source_track_id": "one", "decision": "keep"}])

    track = {
        "source_type": "playlist",
        "source_info": {"playlist_origins": [_origin(deleted), _origin(approved)]},
    }
    assert db.delete_mirrored_playlist(deleted, profile_id=1) is True
    assert wishlist_track_allowed(db, track) is True

    assert db.delete_mirrored_playlist(approved, profile_id=1) is True
    assert wishlist_track_allowed(db, track) is False


def test_manual_request_remains_eligible_when_shared_playlist_origin_is_rejected(db):
    playlist_id = _mirror(db)
    _enable(db, playlist_id, [{"source_track_id": "one", "decision": "keep"}])
    assert db.add_to_wishlist_detailed(
        spotify_track_data=_payload(),
        source_type="playlist",
        source_info=_origin(playlist_id),
        profile_id=1,
    )["applied"] is True
    assert db.add_to_wishlist_detailed(
        spotify_track_data=_payload(), source_type="manual", profile_id=1
    )["applied"] is True

    db.update_mirrored_playlist_decisions(
        playlist_id,
        [{"source_track_id": "one", "decision": "reject"}],
        profile_id=1,
    )
    row = db.get_wishlist_tracks(profile_id=1)[0]
    assert row["source_type"] == "manual"
    assert row["source_info"]["playlist_origins"] == [_origin(playlist_id)]
    assert wishlist_track_allowed(db, row) is True


def test_rejected_origin_cannot_queue_but_manual_row_remains_eligible(db):
    playlist_id = _mirror(db)
    _enable(db, playlist_id, [{"source_track_id": "one", "decision": "reject"}])

    rejected = db.add_to_wishlist_detailed(
        spotify_track_data=_payload(),
        source_type="playlist",
        source_info=_origin(playlist_id),
        profile_id=1,
    )
    assert rejected["reason"] == "curation_not_keep"
    assert db.get_wishlist_tracks(profile_id=1) == []

    manual = db.add_to_wishlist_detailed(
        spotify_track_data=_payload(), source_type="manual", profile_id=1
    )
    assert manual["applied"] is True
    row = db.get_wishlist_tracks(profile_id=1)[0]
    assert row["source_type"] == "manual"
    assert wishlist_track_allowed(db, row) is True


def test_deferred_wishlist_dispatch_blocks_when_all_origins_rejected(db):
    playlist_id = _mirror(db)
    _enable(db, playlist_id, [{"source_track_id": "one", "decision": "reject"}])
    submitted = []
    runtime = SimpleNamespace(
        logger=SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None),
        get_music_database=lambda: db,
        tasks_lock=threading.Lock(),
        download_batches={},
        missing_download_executor=SimpleNamespace(submit=lambda *a, **k: submitted.append(a)),
        album_bundle_executor=None,
        profile_id=1,
    )

    result = _run_wishlist_cycle(
        runtime,
        playlist_id="wishlist",
        cycle="singles",
        tracks=[{"source_type": "playlist", "source_info": _origin(playlist_id)}],
        run_id="run",
        auto_initiated=False,
    )

    assert result == {"submitted": [], "album_batches": 0, "residual_count": 0}
    assert submitted == []


def test_failed_download_requeue_preserves_track_curation_origin(monkeypatch):
    import core.downloads.wishlist_failed as failed

    monkeypatch.setattr(failed, "_record_failed_attempt", lambda *a, **k: None)
    monkeypatch.setattr(failed, "_resolve_wishlist_source_type_for_batch", lambda batch: "playlist")
    monkeypatch.setattr(failed, "_build_wishlist_source_context", lambda batch: {"playlist_id": "7"})
    seen = []
    service = SimpleNamespace(
        add_failed_track_from_modal=lambda **kwargs: seen.append(kwargs) or True
    )
    origin = {"mirrored_playlist_id": 7, "source_track_id": "one"}

    add_failed_tracks_to_wishlist(
        {"profile_id": 1},
        [{
            "track_name": "One",
            "track_data": {
                "id": "recording-one",
                "name": "One",
                "artists": [{"name": "Artist"}],
                "_curation_origin": origin,
            },
        }],
        service,
    )

    assert seen[0]["source_context"] == {"playlist_id": "7", **origin}


class _MediaClient:
    def __init__(self):
        self.writes = []

    def is_connected(self):
        return True

    def update_playlist(self, *args, **kwargs):
        self.writes.append((args, kwargs))
        return True


def _service_track():
    return SimpleNamespace(
        id="recording-one",
        source_track_id="one",
        name="One",
        artists=[{"name": "Artist"}],
        album="Release",
        duration_ms=1000,
        image_url=None,
    )


def _service_playlist():
    tracks = [_service_track()]
    return SimpleNamespace(id="auto_mirror_7", name="Mix", tracks=tracks, total_tracks=1)


def test_stale_policy_blocks_direct_missing_track_download(db, monkeypatch):
    playlist_id = _mirror(db)
    _enable(db, playlist_id, [{"source_track_id": "one", "decision": "keep"}])
    _tracks, snapshot = curated_tracks_for_operation(
        db, playlist_id, db.get_mirrored_playlist_tracks(playlist_id), profile_id=1
    )
    db.update_mirrored_playlist_decisions(
        playlist_id, [{"source_track_id": "one", "decision": "reject"}], profile_id=1
    )
    monkeypatch.setattr("database.music_database.get_database", lambda: db)
    client = _MediaClient()
    service = PlaylistSyncService(None, None)
    service._get_active_media_client = lambda *a, **k: (client, "navidrome")
    downloaded = []

    async def _missing(*args, **kwargs):
        return None, 0.0

    async def _download(rows):
        downloaded.extend(rows)
        return len(rows)

    service._find_track_in_media_server = _missing
    service._download_missing_tracks = _download
    result = asyncio.run(service.sync_playlist(
        _service_playlist(), download_missing=True, profile_id=1,
        curation_snapshot=snapshot,
    ))

    assert downloaded == []
    assert client.writes == []
    assert any("policy changed" in error.lower() for error in result.errors)


def test_stale_policy_blocks_media_server_write(db, monkeypatch):
    playlist_id = _mirror(db)
    _enable(db, playlist_id, [{"source_track_id": "one", "decision": "keep"}])
    _tracks, snapshot = curated_tracks_for_operation(
        db, playlist_id, db.get_mirrored_playlist_tracks(playlist_id), profile_id=1
    )
    db.update_mirrored_playlist_decisions(
        playlist_id, [{"source_track_id": "one", "decision": "pending"}], profile_id=1
    )
    monkeypatch.setattr("database.music_database.get_database", lambda: db)
    client = _MediaClient()
    service = PlaylistSyncService(None, None)
    service._get_active_media_client = lambda *a, **k: (client, "navidrome")

    async def _matched(*args, **kwargs):
        return SimpleNamespace(
            ratingKey="server-one", title="One", artist="Artist", album="Release"
        ), 1.0

    service._find_track_in_media_server = _matched
    result = asyncio.run(service.sync_playlist(
        _service_playlist(), profile_id=1, curation_snapshot=snapshot,
    ))

    assert client.writes == []
    assert any("policy changed" in error.lower() for error in result.errors)
