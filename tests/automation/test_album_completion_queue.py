from threading import Lock
from types import SimpleNamespace

from core.automation.album_completion_queue import queue_album_completion


def test_duplicate_active_batch_is_not_queued():
    submissions = []
    deps = SimpleNamespace(claim_completion_attempt=lambda *args: "test-token", release_completion_attempt=lambda *args: None,
        download_batches={"existing": {
            "phase": "downloading", "profile_id": 2,
            "album_completion_only": True,
            "canonical_source": "spotify",
            "canonical_album_id": "release-123",
        }},
        profile_id=2,
        verify_album_scope=lambda album_id, profile_id: True, verify_missing_tracks=lambda album_id, tracks, profile_id, related_album_ids: tracks,
        tasks_lock=Lock(),
        submit=lambda *args: submissions.append(args),
    )
    result = queue_album_completion(
        canonical_source="spotify", canonical_album_id="release-123",
        album_id=42, album_title="Test Album", artist="Test Artist",
        canonical_album={"id": "release-123", "source": "spotify", "name": "Test Album", "artists": [{"name": "Test Artist"}]},
        missing_tracks=[{"id": "track-1", "name": "Missing Song"}],
        deps=deps,
    )
    assert result["status"] != "queued"
    assert submissions == []

def test_other_profile_does_not_block_queue():
    submissions = []
    deps = SimpleNamespace(claim_completion_attempt=lambda *args: "test-token", release_completion_attempt=lambda *args: None,
        download_batches={"existing": {
            "phase": "downloading", "profile_id": 2,
            "album_completion_only": True,
            "canonical_source": "spotify",
            "canonical_album_id": "release-123",
        }},
        profile_id=3,
        verify_album_scope=lambda album_id, profile_id: True, verify_missing_tracks=lambda album_id, tracks, profile_id, related_album_ids: tracks,
        tasks_lock=Lock(),
        submit=lambda *args: submissions.append(args),
    )
    result = queue_album_completion(
        canonical_source="spotify", canonical_album_id="release-123",
        album_id=42, album_title="Test Album", artist="Test Artist",
        canonical_album={"id": "release-123", "source": "spotify", "name": "Test Album", "artists": [{"name": "Test Artist"}]},
        missing_tracks=[{"id": "track-1", "name": "Missing Song"}],
        deps=deps,
    )
    assert result["status"] == "queued"
    assert len(submissions) == 1


def test_completion_batch_preserves_existing_music():
    batches = {}
    submissions = []
    deps = SimpleNamespace(claim_completion_attempt=lambda *args: "test-token", release_completion_attempt=lambda *args: None,
        download_batches=batches,
        profile_id=2,
        verify_album_scope=lambda album_id, profile_id: True, verify_missing_tracks=lambda album_id, tracks, profile_id, related_album_ids: tracks,
        tasks_lock=Lock(),
        submit=lambda *args: submissions.append(args),
    )
    result = queue_album_completion(
        canonical_source="spotify", canonical_album_id="release-123",
        album_id=42, album_title="Test Album", artist="Test Artist",
        canonical_album={"id": "release-123", "source": "spotify", "name": "Test Album", "artists": [{"name": "Test Artist"}]},
        missing_tracks=[{"id": "track-1", "name": "Missing Song"}],
        deps=deps,
    )
    assert result["status"] == "queued"
    assert len(submissions) == 1
    assert len(batches) == 1
    batch = next(iter(batches.values()))
    assert batch["album_completion_only"] is True
    assert batch["is_album_download"] is True
    assert batch["force_download_all"] is False
    assert batch["force_replace"] is False
    assert batch["ignore_manual_matches"] is False
    assert batch["ignore_blocklist"] is False
    assert batch["profile_id"] == 2
    assert batch["album_context"]["id"] == "release-123"
    assert batch["album_context"]["source"] == "spotify"
    assert batch["artist_context"]["name"] == "Test Artist"
    assert submissions[0][1] == "album_completion:42"
    submitted_tracks = submissions[0][2]
    assert len(submitted_tracks) == 1
    assert submitted_tracks[0]["id"] == "track-1"
    assert submitted_tracks[0]["album"]["id"] == "release-123"
    assert submitted_tracks[0]["_is_explicit_album_download"] is True


def test_failed_submission_removes_batch():
    batches = {}
    def reject(*args):
        raise RuntimeError("Executor unavailable")
    deps = SimpleNamespace(claim_completion_attempt=lambda *args: "test-token", release_completion_attempt=lambda *args: None,
        download_batches=batches,
        profile_id=2,
        verify_album_scope=lambda album_id, profile_id: True, verify_missing_tracks=lambda album_id, tracks, profile_id, related_album_ids: tracks,
        tasks_lock=Lock(),
        submit=reject,
    )
    result = queue_album_completion(
        canonical_source="spotify", canonical_album_id="release-123",
        album_id=42, album_title="Test Album", artist="Test Artist",
        canonical_album={"id": "release-123", "source": "spotify", "name": "Test Album", "artists": [{"name": "Test Artist"}]},
        missing_tracks=[{"id": "track-1", "name": "Missing Song"}],
        deps=deps,
    )
    assert result["status"] != "queued"
    assert batches == {}


def test_analysis_limit_prevents_submission():
    batches = {str(i): {"phase": "analysis"} for i in range(3)}
    submissions = []
    deps = SimpleNamespace(claim_completion_attempt=lambda *args: "test-token", release_completion_attempt=lambda *args: None,
        download_batches=batches,
        profile_id=2,
        verify_album_scope=lambda album_id, profile_id: True, verify_missing_tracks=lambda album_id, tracks, profile_id, related_album_ids: tracks,
        tasks_lock=Lock(),
        submit=lambda *args: submissions.append(args),
    )
    result = queue_album_completion(
        canonical_source="spotify", canonical_album_id="release-123",
        album_id=42, album_title="Test Album", artist="Test Artist",
        canonical_album={"id": "release-123", "source": "spotify", "name": "Test Album", "artists": [{"name": "Test Artist"}]},
        missing_tracks=[{"id": "track-1", "name": "Missing Song"}],
        deps=deps,
    )
    assert result["status"] == "skipped"
    assert len(batches) == 3
    assert submissions == []


def test_prepared_tracks_keep_canonical_album():
    from core.automation.album_completion_queue import prepare_completion_tracks
    album = {"id": "release-123", "source": "spotify", "name": "Test Album", "artists": [{"name": "Test Artist"}]}
    missing = [{"id": "track-1", "name": "Missing Song", "track_number": 4, "disc_number": 1}]
    context, tracks = prepare_completion_tracks(album, missing, "spotify", "Test Artist")
    assert context["id"] == "release-123"
    assert len(tracks) == 1
    assert tracks[0]["id"] == "track-1"
    assert tracks[0]["album"]["id"] == "release-123"
    assert tracks[0]["_is_explicit_album_download"] is True


def test_wrong_canonical_release_is_rejected():
    import pytest
    from core.automation.album_completion_queue import prepare_completion_tracks
    album = {"id": "wrong-release", "source": "spotify", "name": "Test Album"}
    missing = [{"id": "track-1", "name": "Missing Song"}]
    with pytest.raises(ValueError):
        prepare_completion_tracks(album, missing, "spotify", "Test Artist", expected_album_id="release-123")


def test_invalid_canonical_metadata_never_queues():
    for album in (None,
                  {"id": "wrong-release", "source": "spotify", "name": "Test Album"},
                  {"id": "release-123", "source": "deezer", "name": "Test Album"}):
        batches = {}
        submissions = []
        deps = SimpleNamespace(claim_completion_attempt=lambda *args: "test-token", release_completion_attempt=lambda *args: None,
            download_batches=batches, profile_id=2, verify_album_scope=lambda album_id, profile_id: True, verify_missing_tracks=lambda album_id, tracks, profile_id, related_album_ids: tracks, tasks_lock=Lock(),
            submit=lambda *args: submissions.append(args),
        )
        result = queue_album_completion(
            canonical_source="spotify", canonical_album_id="release-123",
            album_id=42, album_title="Test Album", artist="Test Artist",
            missing_tracks=[{"id": "track-1", "name": "Missing Song"}],
            canonical_album=album, deps=deps,
        )
        assert result["status"] != "queued"
        assert batches == {}
        assert submissions == []


def test_album_already_complete_never_queues():
    batches = {}
    submissions = []
    deps = SimpleNamespace(claim_completion_attempt=lambda *args: "test-token", release_completion_attempt=lambda *args: None, download_batches=batches, profile_id=2, tasks_lock=Lock(), verify_album_scope=lambda album_id, profile_id: True, verify_missing_tracks=lambda album_id, tracks, profile_id, related_album_ids: [], submit=lambda *args: submissions.append(args))
    result = queue_album_completion(canonical_source="spotify", canonical_album_id="release-123", album_id=42, album_title="Test Album", artist="Test Artist", canonical_album={"id": "release-123", "source": "spotify", "name": "Test Album", "artists": [{"name": "Test Artist"}]}, missing_tracks=[{"id": "track-1", "name": "Already Owned"}], deps=deps)
    assert result["status"] == "skipped"
    assert batches == {}
    assert submissions == []


def test_library_verification_failure_never_queues():
    batches = {}
    submissions = []
    def fail(*args):
        raise RuntimeError("Database unavailable")
    deps = SimpleNamespace(claim_completion_attempt=lambda *args: "test-token", release_completion_attempt=lambda *args: None, download_batches=batches, profile_id=2, tasks_lock=Lock(), verify_album_scope=lambda album_id, profile_id: True, verify_missing_tracks=fail, submit=lambda *args: submissions.append(args))
    result = queue_album_completion(canonical_source="spotify", canonical_album_id="release-123", album_id=42, album_title="Test Album", artist="Test Artist", canonical_album={"id": "release-123", "source": "spotify", "name": "Test Album", "artists": [{"name": "Test Artist"}]}, missing_tracks=[{"id": "track-1", "name": "Missing Song"}], deps=deps)
    assert result["status"] == "skipped"
    assert batches == {}
    assert submissions == []


def test_queue_rejects_unsafe_album_scope():
    """Unsafe ownership must never create a batch or submit downloads."""
    import pytest

    def broken_verifier(album_id, profile_id):
        raise RuntimeError("Ownership database unavailable")

    for verifier in (None, lambda album_id, profile_id: False,
                     broken_verifier):
        batches = {}
        submissions = []
        deps = SimpleNamespace(claim_completion_attempt=lambda *args: "test-token", release_completion_attempt=lambda *args: None,
            download_batches=batches,
            profile_id=2,
            tasks_lock=Lock(),
            verify_missing_tracks=lambda *args: pytest.fail(
                "Missing-track verification must not run"
            ),
            submit=lambda *args: submissions.append(args),
        )
        if verifier is not None:
            deps.verify_album_scope = verifier

        result = queue_album_completion(
            canonical_source="spotify",
            canonical_album_id="release-123",
            album_id=42,
            album_title="Test Album",
            artist="Test Artist",
            canonical_album={
                "id": "release-123",
                "source": "spotify",
                "name": "Test Album",
            },
            missing_tracks=[{"id": "track-1", "name": "Missing Song"}],
            deps=deps,
        )

        assert result["status"] == "skipped"
        assert batches == {}
        assert submissions == []


def test_failed_submission_releases_completion_cooldown():
    claims = []
    releases = []

    def reject(*args):
        raise RuntimeError("Executor unavailable")

    deps = SimpleNamespace(
        profile_id=2,
        download_batches={},
        tasks_lock=Lock(),
        verify_album_scope=lambda album_id, profile_id: True,
        verify_missing_tracks=lambda album_id, tracks, profile_id, related_album_ids: tracks,
        claim_completion_attempt=lambda *args: claims.append(args) or "token-1",
        release_completion_attempt=lambda *args: releases.append(args),
        submit=reject,
    )

    result = queue_album_completion(
        canonical_source="spotify",
        canonical_album_id="release-123",
        album_id=42,
        album_title="Test Album",
        artist="Test Artist",
        missing_tracks=[{"id": "track-1", "name": "Missing Song"}],
        canonical_album={
            "id": "release-123",
            "source": "spotify",
            "name": "Test Album",
            "artists": [{"name": "Test Artist"}],
        },
        deps=deps,
    )

    assert result["status"] == "failed"
    assert claims == [(2, "spotify", "release-123")]
    assert releases == [(2, "spotify", "release-123", "token-1")]
    assert deps.download_batches == {}


def test_successful_submission_keeps_completion_cooldown():
    claims = []
    releases = []
    submissions = []

    deps = SimpleNamespace(
        profile_id=2,
        download_batches={},
        tasks_lock=Lock(),
        verify_album_scope=lambda album_id, profile_id: True,
        verify_missing_tracks=lambda album_id, tracks, profile_id, related_album_ids: tracks,
        claim_completion_attempt=lambda *args: claims.append(args) or "token-1",
        release_completion_attempt=lambda *args: releases.append(args),
        submit=lambda *args: submissions.append(args),
    )

    result = queue_album_completion(
        canonical_source="spotify",
        canonical_album_id="release-123",
        album_id=42,
        album_title="Test Album",
        artist="Test Artist",
        missing_tracks=[{"id": "track-1", "name": "Missing Song"}],
        canonical_album={
            "id": "release-123",
            "source": "spotify",
            "name": "Test Album",
            "artists": [{"name": "Test Artist"}],
        },
        deps=deps,
    )

    assert result["status"] == "queued"
    assert len(claims) == 1
    assert releases == []
    assert len(submissions) == 1


def test_active_duplicate_does_not_claim_cooldown():
    claims = []
    submissions = []

    deps = SimpleNamespace(
        profile_id=2,
        download_batches={
            "existing": {
                "phase": "downloading",
                "profile_id": 2,
                "album_completion_only": True,
                "canonical_source": "spotify",
                "canonical_album_id": "release-123",
            }
        },
        tasks_lock=Lock(),
        verify_album_scope=lambda album_id, profile_id: True,
        verify_missing_tracks=lambda album_id, tracks, profile_id, related_album_ids: tracks,
        claim_completion_attempt=lambda *args: claims.append(args) or "token-1",
        release_completion_attempt=lambda *args: None,
        submit=lambda *args: submissions.append(args),
    )

    result = queue_album_completion(
        canonical_source="spotify",
        canonical_album_id="release-123",
        album_id=42,
        album_title="Test Album",
        artist="Test Artist",
        missing_tracks=[{"id": "track-1", "name": "Missing Song"}],
        canonical_album={
            "id": "release-123",
            "source": "spotify",
            "name": "Test Album",
            "artists": [{"name": "Test Artist"}],
        },
        deps=deps,
    )

    assert result["status"] == "skipped"
    assert claims == []
    assert submissions == []


def test_unavailable_cooldown_never_submits():
    submissions = []

    deps = SimpleNamespace(
        profile_id=2,
        download_batches={},
        tasks_lock=Lock(),
        verify_album_scope=lambda album_id, profile_id: True,
        verify_missing_tracks=lambda album_id, tracks, profile_id, related_album_ids: tracks,
        claim_completion_attempt=lambda *args: None,
        release_completion_attempt=lambda *args: None,
        submit=lambda *args: submissions.append(args),
    )

    result = queue_album_completion(
        canonical_source="spotify",
        canonical_album_id="release-123",
        album_id=42,
        album_title="Test Album",
        artist="Test Artist",
        missing_tracks=[{"id": "track-1", "name": "Missing Song"}],
        canonical_album={
            "id": "release-123",
            "source": "spotify",
            "name": "Test Album",
            "artists": [{"name": "Test Artist"}],
        },
        deps=deps,
    )

    assert result["status"] == "skipped"
    assert submissions == []
    assert deps.download_batches == {}


def _queue_kwargs(deps):
    return dict(
        canonical_source="spotify", canonical_album_id="release-123",
        album_id=42, album_title="Test Album", artist="Test Artist",
        missing_tracks=[{"id": "track-1", "name": "Missing Song"}],
        canonical_album={"id": "release-123", "source": "spotify", "name": "Test Album",
                         "artists": [{"name": "Test Artist"}]},
        deps=deps,
    )


def test_cooldown_claim_runs_outside_tasks_lock():
    # the claim is a sqlite write, holding tasks_lock across it stalls every download
    lock = Lock()
    held = []
    submissions = []
    deps = SimpleNamespace(
        profile_id=2, download_batches={}, tasks_lock=lock,
        verify_album_scope=lambda album_id, profile_id: True,
        verify_missing_tracks=lambda album_id, tracks, profile_id, related_album_ids: tracks,
        claim_completion_attempt=lambda *args: held.append(lock.locked()) or "token-1",
        release_completion_attempt=lambda *args: None,
        submit=lambda *args: submissions.append(args),
    )

    result = queue_album_completion(**_queue_kwargs(deps))

    assert result["status"] == "queued"
    assert held == [False]
    assert len(submissions) == 1


def test_duplicate_landing_during_claim_releases_the_cooldown():
    batches = {}
    releases = []
    submissions = []

    def claim(*args):
        # another run queues the same release while we're claiming
        batches["racer"] = {
            "phase": "analysis", "profile_id": 2, "album_completion_only": True,
            "canonical_source": "spotify", "canonical_album_id": "release-123",
        }
        return "token-1"

    deps = SimpleNamespace(
        profile_id=2, download_batches=batches, tasks_lock=Lock(),
        verify_album_scope=lambda album_id, profile_id: True,
        verify_missing_tracks=lambda album_id, tracks, profile_id, related_album_ids: tracks,
        claim_completion_attempt=claim,
        release_completion_attempt=lambda *args: releases.append(args),
        submit=lambda *args: submissions.append(args),
    )

    result = queue_album_completion(**_queue_kwargs(deps))

    assert result == {"status": "skipped", "reason": "Release already queued"}
    assert list(batches) == ["racer"]
    assert releases == [(2, "spotify", "release-123", "token-1")]
    assert submissions == []
