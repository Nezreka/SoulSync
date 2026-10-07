"""Restart tests simulate death between file moves and preserve checked bytes."""

from pathlib import Path
from types import SimpleNamespace
import pytest
from core.downloads.atomic_manifest import (
    begin_batch,
    record_staged_track,
    set_release_state,
    record_release_request,
    read_manifest,
    record_release_publication,
)
from core.downloads.atomic_album_publish import staging_root_for_batch
from core.downloads.release_import import publish_verified_release, move_without_replace
from core.downloads.atomic_recovery import recover_orphan_staging


@pytest.fixture
def ready(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    stage = Path(staging_root_for_batch(str(root), "release-test"))
    begin_batch(str(stage), batch_id="release-test", transfer_dir=str(root), album_name="Album")
    outputs = []
    for n in (1, 2):
        path = stage / "Artist" / "Album" / f"{n}.flac"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"checked-{n}".encode())
        final = root / path.relative_to(stage)
        record_staged_track(str(stage), staged_path=str(path), final_path=str(final), track_name=f"Song{n}")
        outputs.append((path, final))
    record_release_request(
        str(stage),
        {
            "staged_path": str(outputs[0][0]),
            "final_path": str(outputs[0][1]),
            "source": "spotify",
            "source_ids": {"track_id": "original-request"},
            "track_name": "Song1",
        },
    )
    assert set_release_state(str(stage), state="ready", expected_count=2)
    # Only the DB/publication boundary is replaced; manifest and moves are real.
    import core.downloads.atomic_recovery as recovery
    import database.music_database as database

    monkeypatch.setattr(database, "MusicDatabase", lambda: SimpleNamespace())
    monkeypatch.setattr(recovery, "make_db_path_updater", lambda db, **kw: lambda *a: None)
    return root, stage, outputs


@pytest.mark.parametrize("moved", [0, 1, 2])
def test_restart_resumes_each_possible_interrupted_publication(ready, moved):
    root, stage, outputs = ready
    for src, dst in outputs[:moved]:
        assert record_release_publication(str(stage), str(src))
        move_without_replace(str(src), str(dst))
    pending = []
    result = recover_orphan_staging([str(root)], remove_from_wishlist=lambda entries, mapping, **kw: pending.extend(entries) or len(entries))
    assert result["published"] == 1 and result["failed"] == 0
    assert all(dst.read_bytes() == f"checked-{i}".encode() for i, (_, dst) in enumerate(outputs, 1))
    assert any(e["context"]["track_info"].get("id") == "original-request" for e in pending)
    assert not stage.exists()


def test_crash_between_hardlink_and_source_unlink_is_recoverable(ready):
    root, stage, outputs = ready
    src, dst = outputs[0]
    dst.parent.mkdir(parents=True)
    import os

    assert record_release_publication(str(stage), str(src))
    os.link(src, dst)
    result = publish_verified_release(str(stage), str(root), move_without_replace)
    assert result["success"] and len(result["published"]) == 2


def test_changed_staged_bytes_prevent_publication(ready):
    root, stage, outputs = ready
    outputs[1][0].write_bytes(b"corrupted-after-check")
    result = publish_verified_release(str(stage), str(root), move_without_replace)
    assert not result["success"]
    assert not any(dst.exists() for _, dst in outputs)


def test_foreign_existing_file_survives_and_interrupted_own_file_rolls_back(ready):
    root, stage, outputs = ready
    assert record_release_publication(str(stage), str(outputs[0][0]))
    move_without_replace(*map(str, outputs[0]))
    outputs[1][1].write_bytes(b"already-owned-better")
    result = publish_verified_release(str(stage), str(root), move_without_replace)
    assert not result["success"]
    assert outputs[1][1].read_bytes() == b"already-owned-better"
    assert outputs[0][0].exists() and not outputs[0][1].exists()


def test_unchecked_partial_album_stays_private_after_restart(ready):
    root, stage, outputs = ready
    assert set_release_state(str(stage), state="checking", expected_count=2)
    result = recover_orphan_staging([str(root)])
    assert result["reported"] == 1 and result["published"] == 0
    assert all(src.exists() and not dst.exists() for src, dst in outputs)


def test_profile_companion_audio_is_inventory_not_an_extra_album_track(ready):
    root, stage, outputs = ready
    for src, _ in outputs:
        src.with_suffix(".mp3").write_bytes(b"profile companion")
    assert set_release_state(str(stage), state="ready", expected_count=2)
    manifest = read_manifest(str(stage))
    assert len(manifest["tracks"]) == 2 and len(manifest["release_inventory"]) == 4
    result = publish_verified_release(str(stage), str(root), move_without_replace)
    assert result["success"] and len(result["published"]) == 4


def test_cancel_during_publish_rolls_every_output_back(ready):
    root, stage, outputs = ready
    moved = []

    def move(src, dst):
        move_without_replace(src, dst)
        if not str(dst).startswith(str(stage)):
            moved.append(dst)

    result = publish_verified_release(str(stage), str(root), move, is_cancelled=lambda: bool(moved))
    assert not result["success"]
    assert all(src.exists() and not dst.exists() for src, dst in outputs)


def test_database_failure_after_interrupted_move_rolls_it_back(ready):
    root, stage, outputs = ready
    assert record_release_publication(str(stage), str(outputs[0][0]))
    move_without_replace(*map(str, outputs[0]))

    def update(src, dst):
        if not str(dst).startswith(str(stage)):
            raise OSError("database unavailable")

    result = publish_verified_release(str(stage), str(root), move_without_replace, update)
    assert not result["success"]
    assert all(src.exists() and not dst.exists() for src, dst in outputs)


@pytest.mark.parametrize("manifest_content", [None, "{corrupt json"])
def test_missing_or_corrupt_release_manifest_cannot_publish_partial_album(ready, manifest_content):
    root, stage, outputs = ready
    from core.downloads.atomic_manifest import manifest_path

    path = Path(manifest_path(str(stage)))
    if manifest_content is None:
        path.unlink()
    else:
        path.write_text(manifest_content)
    result = recover_orphan_staging([str(root)])
    assert result["published"] == 0 and result["reported"] == 1
    assert all(src.exists() and not dst.exists() for src, dst in outputs)


def test_identical_independent_existing_file_is_never_claimed_or_deleted(ready):
    root, stage, outputs = ready
    outputs[0][1].parent.mkdir(parents=True)
    outputs[0][1].write_bytes(outputs[0][0].read_bytes())
    outputs[1][1].write_bytes(b"already-owned-better")
    result = publish_verified_release(str(stage), str(root), move_without_replace)
    assert not result["success"]
    assert outputs[0][1].read_bytes() == b"checked-1"
    assert outputs[1][1].read_bytes() == b"already-owned-better"


def test_existing_optional_artist_sidecar_does_not_block_album(ready):
    root, stage, outputs = ready
    sidecar = stage / "Artist" / "artist.nfo"
    sidecar.write_bytes(b"generated")
    existing = root / "Artist" / "artist.nfo"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"manually enriched")
    assert set_release_state(str(stage), state="ready", expected_count=2)
    result = publish_verified_release(str(stage), str(root), move_without_replace)
    assert result["success"] and all(dst.exists() for _, dst in outputs)
    assert existing.read_bytes() == b"manually enriched"


def test_restart_rescues_journal_owned_incomplete_smb_copy_and_retries(ready):
    root, stage, outputs = ready
    src, dst = outputs[0]
    dst.parent.mkdir(parents=True)
    assert record_release_publication(str(stage), str(src))
    dst.write_bytes(b"partial")
    assert record_release_publication(str(stage), str(src), created_path=str(dst))
    result = publish_verified_release(str(stage), str(root), move_without_replace)
    assert result["success"]
    assert dst.read_bytes() == b"checked-1"
    rescued = list((root / ".soulsync_release_recovery").rglob("*"))
    assert any(p.is_file() and p.read_bytes() == b"partial" for p in rescued)


def test_cancellation_during_final_move_also_rolls_back_current_output(ready):
    root, stage, outputs = ready
    published = []

    def move(src, dst):
        move_without_replace(src, dst)
        if not str(dst).startswith(str(stage)):
            published.append(dst)

    result = publish_verified_release(str(stage), str(root), move, is_cancelled=lambda: len(published) >= 2)
    assert not result["success"]
    assert all(src.exists() and not dst.exists() for src, dst in outputs)


def test_source_unlink_failure_removes_only_its_exclusive_destination(ready, monkeypatch):
    import os

    root, stage, outputs = ready
    real_unlink = os.unlink
    failed = []

    def unlink(path, *a, **kw):
        if str(path) == str(outputs[0][0]) and not failed:
            failed.append(path)
            raise PermissionError("SMB lease prevents unlink")
        return real_unlink(path, *a, **kw)

    monkeypatch.setattr(os, "unlink", unlink)
    result = publish_verified_release(str(stage), str(root), move_without_replace)
    assert not result["success"]
    assert all(src.exists() and not dst.exists() for src, dst in outputs)
