"""An unwritable staging dir must not fail the download.

Reported with a traceback that says it plainly:

    PermissionError: [Errno 13] Permission denied: '/app/.soulsync_atomic_staging'
      File "core/imports/pipeline.py", line 1227, in post_process_matched_download
        safe_move_file(file_path, final_path)

The setup is the ordinary Docker one — ``D:/Music:/app/Transfer`` — which made
the old staging location (a SIBLING of the transfer dir) resolve to ``/app``,
the container's own layer: different filesystem, not writable, discarded when
the container is recreated. mkdir raised, the move never happened, the file
stayed in ``/app/downloads``, and post-processing then reported

    File verification failed: expected file at 08 - Alabama.flac
    but it was not found after processing

which reads like a missing file when it is a permissions problem three frames
down.

Two separate faults, both covered here:

1. WHERE staging goes. Now inside the transfer dir — the only place that is
   same-filesystem and writable by construction, since a library you cannot
   write to has nothing to publish into anyway.
2. What happens when it STILL cannot be created. Staging is an optimisation;
   one that cannot run must degrade to publishing directly, never take the
   download down with it.

The tests that matter are the behavioural ones at the bottom: they drive the
real ``_maybe_stage_album_track`` against a genuinely unwritable directory.
"""

from __future__ import annotations

import os

import pytest

import core.imports.pipeline as pl
from core.downloads.atomic_album_publish import (
    is_staged_path,
    staging_root_for_batch,
    to_staging_path,
)


class _Cfg:
    def __init__(self, vals):
        self.vals = vals

    def get(self, key, default=None):
        return self.vals.get(key, default)


def _wire(monkeypatch, transfer, *, batch, flag=True):
    """Same harness the existing wiring tests use."""
    monkeypatch.setattr(pl, "config_manager", _Cfg({
        "album_downloads.atomic_publish": flag,
        "soulseek.transfer_path": str(transfer),
    }))
    monkeypatch.setattr(pl, "docker_resolve_path", lambda p: p)
    monkeypatch.setattr(pl, "download_batches", {"B": batch})


def _make_unwritable(path) -> bool:
    """chmod the dir read-only and confirm it actually denies writes.

    Returns False when it does not — running as root (common in CI containers)
    ignores the mode bits, and a test that silently proves nothing is worse
    than one that skips."""
    os.chmod(path, 0o500)
    probe = os.path.join(str(path), ".write-probe")
    try:
        with open(probe, "w"):
            pass
    except OSError:
        return True
    os.remove(probe)
    return False


# ── where staging goes ────────────────────────────────────────────────────

def test_staging_lives_inside_the_transfer_dir(tmp_path):
    transfer = tmp_path / "music"
    root = staging_root_for_batch(str(transfer), "b1")
    assert os.path.normpath(root).startswith(os.path.normpath(str(transfer)) + os.sep)


def test_staging_is_writable_whenever_the_library_is(tmp_path):
    """The whole point of the move: if you can write the library, you can
    write the staging dir. No second filesystem, no second permission set."""
    transfer = tmp_path / "music"
    transfer.mkdir()
    root = staging_root_for_batch(str(transfer), "b1")
    os.makedirs(root, exist_ok=True)
    assert os.path.isdir(root) and os.access(root, os.W_OK)


def test_the_old_sibling_location_is_no_longer_used(tmp_path):
    """Pins the bug itself: with the library bind-mounted at /app/Transfer the
    sibling is /app, which is not the library's filesystem at all."""
    transfer = tmp_path / "app" / "Transfer"
    transfer.mkdir(parents=True)
    sibling = os.path.join(os.path.dirname(str(transfer)), ".soulsync_atomic_staging")
    root = staging_root_for_batch(str(transfer), "b1")
    assert not os.path.normpath(root).startswith(os.path.normpath(sibling) + os.sep)


def test_mapping_still_preserves_the_album_structure(tmp_path):
    transfer = str(tmp_path / "music")
    root = staging_root_for_batch(transfer, "b1")
    staged = to_staging_path(
        os.path.join(transfer, "Neil Young", "Harvest", "08 - Alabama.flac"), transfer, root)
    assert staged == os.path.join(root, "Neil Young", "Harvest", "08 - Alabama.flac")


def test_an_already_staged_path_is_never_staged_again(tmp_path):
    """Newly reachable now that staging sits under the transfer dir: without
    the guard a staged file maps into a staging mirror of itself, deeper on
    every pass."""
    transfer = str(tmp_path / "music")
    root = staging_root_for_batch(transfer, "b1")
    staged = os.path.join(root, "Artist", "Album", "01.flac")
    assert is_staged_path(staged, transfer)
    assert to_staging_path(staged, transfer, root) is None


# ── the behaviour that fixes the report ───────────────────────────────────

def test_a_writable_library_still_stages(monkeypatch, tmp_path):
    """Positive control. Without this, a fix that simply disabled staging
    everywhere would pass every test below."""
    transfer = tmp_path / "music"
    (transfer / "Neil Young" / "Harvest").mkdir(parents=True)
    batch = {"is_album_download": True}
    _wire(monkeypatch, transfer, batch=batch)

    final = str(transfer / "Neil Young" / "Harvest" / "08 - Alabama.flac")
    got = pl._maybe_stage_album_track({"batch_id": "B"}, final)

    assert got != final, "a writable library should still stage"
    assert batch["_atomic_active"] is True
    assert is_staged_path(got, str(transfer))


def test_an_unwritable_library_publishes_directly_instead_of_failing(monkeypatch, tmp_path):
    """THE regression. Previously this raised PermissionError inside
    safe_move_file, the track never left downloads, and the user got
    "File verification failed ... not found after processing"."""
    transfer = tmp_path / "music"
    (transfer / "Neil Young" / "Harvest").mkdir(parents=True)
    final = str(transfer / "Neil Young" / "Harvest" / "08 - Alabama.flac")
    if not _make_unwritable(transfer):
        pytest.skip("cannot deny writes here (running as root?)")
    try:
        batch = {"is_album_download": True}
        _wire(monkeypatch, transfer, batch=batch)

        got = pl._maybe_stage_album_track({"batch_id": "B"}, final)

        assert got == final, "must fall back to the direct library path"
        assert batch.get("_atomic_active") is False, "must not claim the batch is staged"
    finally:
        os.chmod(transfer, 0o700)


def test_the_fallback_decision_is_made_once_for_the_batch(monkeypatch, tmp_path):
    """The probe must not re-run per track — 30 tracks would mean 30 failed
    mkdirs and 30 warnings for one album."""
    transfer = tmp_path / "music"
    (transfer / "A" / "B").mkdir(parents=True)
    finals = [str(transfer / "A" / "B" / f"0{i}.flac") for i in (1, 2, 3)]
    if not _make_unwritable(transfer):
        pytest.skip("cannot deny writes here (running as root?)")
    try:
        batch = {"is_album_download": True}
        _wire(monkeypatch, transfer, batch=batch)
        for f in finals:
            assert pl._maybe_stage_album_track({"batch_id": "B"}, f) == f
        assert batch["_atomic_decided"] is True
    finally:
        os.chmod(transfer, 0o700)


def test_every_track_of_a_failed_batch_lands_in_the_same_album_folder(monkeypatch, tmp_path):
    """The user's actual outcome: a complete album in the library, published
    per-track, rather than one failed track and nine oddly-placed ones."""
    transfer = tmp_path / "music"
    (transfer / "Neil Young" / "Harvest").mkdir(parents=True)
    finals = [str(transfer / "Neil Young" / "Harvest" / f"{i:02d} - t.flac")
              for i in range(1, 11)]
    if not _make_unwritable(transfer):
        pytest.skip("cannot deny writes here (running as root?)")
    try:
        _wire(monkeypatch, transfer, batch={"is_album_download": True})
        got = [pl._maybe_stage_album_track({"batch_id": "B"}, f) for f in finals]
        assert got == finals
        assert len({os.path.dirname(p) for p in got}) == 1
    finally:
        os.chmod(transfer, 0o700)


def test_the_flag_being_off_is_still_a_pure_pass_through(monkeypatch, tmp_path):
    """Default behaviour must remain untouched by any of this."""
    transfer = tmp_path / "music"
    (transfer / "A" / "B").mkdir(parents=True)
    batch = {"is_album_download": True}
    _wire(monkeypatch, transfer, batch=batch, flag=False)
    final = str(transfer / "A" / "B" / "01.flac")
    assert pl._maybe_stage_album_track({"batch_id": "B"}, final) == final
    assert batch == {"is_album_download": True}, "batch state must not be touched at all"
