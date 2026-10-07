"""Album gating keeps release originals and publishes only after every check."""

from copy import deepcopy
from pathlib import Path
import shutil
import pytest
from core.downloads.release_import import ReleaseFile, try_complete_album_import


@pytest.fixture
def release(tmp_path):
    source = tmp_path / "client"
    source.mkdir()
    files = []
    for i, name in enumerate(("First", "Second"), 1):
        path = source / f"{i}.flac"
        path.write_bytes(f"original-{i}".encode())
        files.append(ReleaseFile(str(path), name, "Artist", "Album", i, 1, 2000, True))
    root = tmp_path / "library"
    root.mkdir()
    tracks = [
        {"id": f"track-{i}", "name": name, "artists": [{"name": "Artist"}], "track_number": i, "disc_number": 1, "duration_ms": 2000}
        for i, name in enumerate(("First", "Second"), 1)
    ]
    context = {
        "artist": {"name": "Artist"},
        "album": {"id": "album-1", "name": "Album"},
        "source": "test",
        "track_info": dict(tracks[0], quality_profile_id=7),
        "_quality_profile": {"id": 7, "release_import_mode": "complete_album", "acoustid_required": False},
        "task_id": "task",
        "batch_id": "batch",
        "has_clean_metadata": True,
    }
    payload = {"success": True, "album": {"id": "album-1", "name": "Album"}, "tracks": tracks, "source": "test"}
    return root, files, context, payload


def execute(release, process, **kwargs):
    root, files, context, payload = release
    return try_complete_album_import("requested", context, files, files[0], str(root), process, lookup_album=lambda *a, **kw: payload, **kwargs)


def processor(root, calls, fail_title=None):
    def process(key, ctx, path):
        calls.append((ctx["track_info"]["name"], bool(ctx.get("_release_preflight_only")), deepcopy(ctx)))
        if ctx["track_info"]["name"] == fail_title:
            ctx["_quality_filtered"] = True
            return
        if ctx.get("_release_preflight_only"):
            ctx["_release_checks_passed"] = True
            return
        stage = Path(ctx["_release_staging_root"]) / "Artist" / "Album" / (ctx["track_info"]["name"] + ".flac")
        stage.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(path, stage)
        ctx["_final_processed_path"] = str(stage)
        ctx["_pipeline_import_succeeded"] = True

    return process


def test_default_profile_only_imports_requested_track(release):
    release[2]["_quality_profile"]["release_import_mode"] = "requested_tracks"
    calls = []
    assert execute(release, processor(release[0], calls)) is None
    assert calls == []


def test_one_rejected_track_prevents_all_bonus_publication(release):
    calls = []
    assert execute(release, processor(release[0], calls, fail_title="Second")) is None
    assert all(preflight for _, preflight, _ in calls)
    assert not list(release[0].rglob("*.flac"))
    assert [Path(f.path).read_bytes() for f in release[1]] == [b"original-1", b"original-2"]


def test_complete_album_uses_same_profile_own_metadata_then_publishes(release):
    calls = []
    original = deepcopy(release[2])
    publications = []

    def publish(stage, root, move, update):
        assert len(calls) == 4 and [c[1] for c in calls] == [True, True, False, False]
        from core.downloads.atomic_album_publish import publish_album_batch

        result = publish_album_batch(stage, root, move)
        publications.append(result)
        return result

    result = execute(release, processor(release[0], calls), publish=publish)
    assert result["status"] == "imported" and result["count"] == 2
    assert Path(result["path"]).read_bytes() == b"original-1"
    assert publications[0]["success"]
    assert all(ctx["_quality_profile"] == original["_quality_profile"] for _, _, ctx in calls)
    assert all(ctx["track_info"]["quality_profile_id"] == 7 for _, _, ctx in calls)
    assert all("task_id" not in ctx and "batch_id" not in ctx for _, _, ctx in calls)
    assert release[2]["track_info"] == original["track_info"]
    assert Path(release[1][1].path).read_bytes() == b"original-2"


def test_missing_file_or_incomplete_edition_falls_back_without_import(release):
    release[3]["tracks"].append({"id": "absent", "name": "Absent"})
    calls = []
    assert execute(release, processor(release[0], calls)) is None
    assert not calls


def test_failed_staged_import_never_publishes_part_of_album(release):
    calls = []
    normal = processor(release[0], calls)

    def process(key, ctx, path):
        if not ctx.get("_release_preflight_only") and ctx["track_info"]["name"] == "Second":
            return
        normal(key, ctx, path)

    assert execute(release, process) is None
    assert not list(release[0].rglob("*.flac"))


def test_publish_failure_is_recoverable_without_single_track_fallback(release):
    calls = []
    result = execute(release, processor(release[0], calls), publish=lambda *a: {"success": False, "failed": [("path", "disk error")]})
    assert result["status"] == "pending"
    assert not (release[0] / "Artist" / "Album").exists()
    assert len(list(release[0].rglob("*.flac"))) == 2


def test_cancelled_job_cannot_publish_release(release):
    calls = []
    result = execute(release, processor(release[0], calls), is_cancelled=lambda: True)
    assert result["status"] == "cancelled"
    assert not calls


def test_cancellation_outcome_cannot_be_republished_after_restart(release):
    calls = []
    cancelled = []

    def publish(*a):
        cancelled.append(True)
        return {"success": False, "failed": [("file", "cancelled")]}

    result = execute(release, processor(release[0], calls), publish=publish, is_cancelled=lambda: bool(cancelled))
    assert result["status"] == "cancelled"
    from core.downloads.atomic_manifest import read_manifest
    from core.downloads.atomic_recovery import recover_orphan_staging

    assert read_manifest(result["staging_root"])["release_state"] == "cancelled"
    recovered = recover_orphan_staging([str(release[0])])
    assert recovered["published"] == 0 and recovered["reported"] == 1
    assert not (release[0] / "Artist" / "Album").exists()
