"""Regression: a Soulseek album folder that yields ZERO usable files must fall
back to the per-track flow rather than hard-failing the whole batch.

The Slipknot case — the preflight-selected peer aborts/stalls every transfer (all
tracks reported "Completed, Aborted" at 0 bytes) — makes
``_poll_album_bundle_downloads`` return ``[]``. ``download_album_to_staging`` used
to return that empty result with ``fallback=False``, so the dispatcher
(``core/downloads/album_bundle_dispatch.try_dispatch``) hit its ``mark_failed``
branch and the batch died with nothing tried elsewhere.

The fix flips that branch to ``fallback=True`` so the existing, proven per-track
flow takes over and re-searches every missing track across ALL sources/peers —
reusing that robustness instead of looping candidate folders inside the bundle.
The downstream "fallback=True -> per-track" routing is covered by
``tests/test_album_bundle_dispatch.py``
(``test_dispatch_fallback_failure_returns_false_for_per_track_flow``); these tests
prove the empty-folder branch actually sets the flag, and that a healthy folder
does NOT fall back.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from core.soulseek_client import SoulseekClient


class _Stub:
    """Stand-in exposing only what download_album_to_staging touches on the
    preflight-reuse path (preferred_source + preferred_tracks skip search/browse)."""

    def __init__(self, poll_result):
        self._poll_result = poll_result

    def is_configured(self):
        return True

    def filter_results_by_quality_preference(self, tracks):
        return tracks

    def download(self, username, filename, size):
        return f"dl-{filename}"          # truthy id; run_async patched to identity

    def _poll_album_bundle_downloads(self, transfer_keys, emit):
        return self._poll_result


def _track(name):
    return SimpleNamespace(username="deadpeer", filename=name, size=100)


def _run(poll_result):
    stub = _Stub(poll_result)
    tracks = [_track("01.flac"), _track("02.flac")]
    with patch("core.soulseek_client.run_async", lambda x: x):
        result = SoulseekClient.download_album_to_staging(
            stub,
            album_name="All Hope Is Gone",
            artist_name="Slipknot",
            staging_dir="/tmp/staging-does-not-matter",
            preferred_source={
                "username": "deadpeer",
                "folder_path": "music/Slipknot/All Hope Is Gone",
            },
            preferred_tracks=tracks,
        )
    return result, tracks


def test_empty_folder_falls_back_to_per_track():
    # Poll returns [] — every transfer aborted / stalled (dead peer).
    result, _ = _run([])
    assert result['success'] is False
    assert result['fallback'] is True            # <-- the fix: hand off to per-track
    assert result['files'] == []
    assert 'per-track' in (result['error'] or '')


def test_healthy_folder_does_not_fall_back():
    # Positive control: the poll yields staged files and the copy succeeds, so we
    # must NOT flip fallback — this proves the empty-branch isn't blanket-returning
    # True. Patch the atomic copy to echo the completed paths through.
    from pathlib import Path
    completed = [Path("/staged/01.flac"), Path("/staged/02.flac")]
    with patch("core.soulseek_client.copy_audio_files_atomically", lambda files, dest, **kw: list(files)):
        stub = _Stub(completed)
        with patch("core.soulseek_client.run_async", lambda x: x):
            result = SoulseekClient.download_album_to_staging(
                stub,
                album_name="All Hope Is Gone",
                artist_name="Slipknot",
                staging_dir="/tmp/staging-does-not-matter",
                preferred_source={"username": "goodpeer", "folder_path": "music/Slipknot/AHIG"},
                preferred_tracks=[_track("01.flac"), _track("02.flac")],
            )
    assert result['success'] is True
    assert result['fallback'] is False
    assert result['partial'] is False
    assert result['files'] == completed
