"""Release matching and one-pass album imports without external audio tools."""

from copy import deepcopy
import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace
import wave

import pytest

import core.acoustid_verification as acoustid
from core.downloads import release_import as releases
from core.downloads.post_processing import _copy_release_audio_to_transfer
from core.imports import pipeline
from core.imports.file_integrity import check_audio_integrity
from core.library_scope import invalidate_library_scope_cache
from core.quality.selection import load_profile_by_id
from core.runtime_state import download_batches, download_tasks, matched_downloads_context
from core.settings import config_manager
from core.tag_writer import read_file_tags, write_tags_to_file
import core.wishlist.service as wishlist_service
import database.music_database as database


def _wav(path, seconds=3, bits=16):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(bits // 8)
        audio.setframerate(44100)
        sample_pair = b"\x01\x10\xff\xef" if bits == 16 else b"\x70\x90"
        audio.writeframes(sample_pair * int(seconds * 44100 / 2))


def _flac(path, bits=24):
    # soundfile is an application dependency; real FLAC encoding here needs
    # neither an ffmpeg subprocess nor a machine-wide codec installation.
    soundfile = pytest.importorskip("soundfile")
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    samples = np.random.default_rng(42).uniform(-0.8, 0.8, 3 * 44100)
    soundfile.write(str(path), samples, 44100, subtype=f"PCM_{bits}")


@pytest.mark.parametrize("title", [
    "Money (2011 Remastered Version)", "Money - Mono Version", "Money (Deluxe Edition)",
    "Money [Deluxe Edition]", "Money - Stereo Version", "Money (Album Version)",
    "Money (Explicit)",
])
@pytest.mark.parametrize("reverse", [False, True])
def test_release_selection_accepts_catalogue_metadata_annotations(title, reverse):
    wanted, actual = ("Money", title) if reverse else (title, "Money")
    item = releases.ReleaseFile("track.flac", actual, "Pink Floyd", "Album", 1, 1, 180000, True)
    track = {"name": wanted, "artists": ["Pink Floyd"], "duration_ms": 180000,
             "track_number": 1, "disc_number": 1}
    assert releases.select_requested_file([item], track) == item
    assert releases.match_album_tracks([item], [track], "Album") == [(track, item)]


@pytest.mark.parametrize("title", [
    "Money Trees (feat. Jay Rock)", "Money Trees [ft. Jay Rock]",
    "Money Trees featuring Jay Rock", "Money Trees feat. Jay Rock", "Money Trees (with Jay Rock)",
])
@pytest.mark.parametrize("reverse", [False, True])
def test_credit_variants_select_the_same_recording(title, reverse):
    wanted, actual = ("Money Trees", title) if reverse else (title, "Money Trees")
    item = releases.ReleaseFile("track.flac", actual, "Kendrick Lamar", duration_ms=180000)
    track = {"name": wanted, "artists": ["Kendrick Lamar"], "duration_ms": 180000}
    assert releases.select_requested_file([item], track) == item


@pytest.mark.parametrize("version", ["Live", "Remix", "Acoustic", "Instrumental"])
def test_stripping_credits_keeps_version_gate(version):
    item = releases.ReleaseFile("track.flac", f"Money Trees (feat. Jay Rock) ({version})", "Kendrick Lamar")
    assert releases.select_requested_file([item], {"name": "Money Trees", "artists": ["Kendrick Lamar"]}) is None


@pytest.mark.parametrize("seconds,tolerance,accepted", [
    (128, 0, True), (136, 0, False), (112, 0, False),
    (112, 10, True), (128, 10, True), (128, 5, False), (110, 10, True), (109, 10, False),
])
def test_duration_is_checked_by_import_guard_after_selection(tmp_path, monkeypatch, seconds, tolerance, accepted):
    path = tmp_path / "Song.wav"
    _wav(path, seconds)
    monkeypatch.setattr(config_manager, "get", lambda key, default=None: tolerance if key == "post_processing.duration_tolerance_seconds" else default)
    item = releases.read_release_file(str(path))
    track = {"name": "Song", "duration_ms": 120000}
    assert releases.select_requested_file([item], track) == item
    assert check_audio_integrity(str(path), 120000, length_tolerance_s=tolerance or None).ok is accepted


@pytest.fixture
def album(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "music.db"))
    monkeypatch.setenv("SOULSYNC_CONFIG_PATH", str(tmp_path / "config.json"))
    db = database.MusicDatabase(str(tmp_path / "music.db"))
    monkeypatch.setattr(database, "_database_instances", {threading.get_ident(): db})
    monkeypatch.setattr(wishlist_service, "_wishlist_service", None)
    monkeypatch.setattr(releases, "_recent_imports", type(releases._recent_imports)())
    monkeypatch.setattr(releases, "_active_albums", set())
    profile = db.create_profile("Release requester")
    quality_profile = db.create_quality_profile("Release quality", {
        "release_import_mode": "album_tracks", "fallback_enabled": False,
        "ranked_targets": [{"label": "FLAC 24", "format": "flac", "bit_depth": 24}],
        "acoustid_required": True, "deep_audio_verify": True,
    })
    settings = {
        "soulseek.transfer_path": str(tmp_path / "Library"),
        "soulseek.download_path": str(tmp_path / "Downloads"),
        "active_media_server": "soulsync", "album_downloads.atomic_publish": False,
        "file_organization.enabled": True, "file_organization.auto_disambiguation": False,
        "file_organization.templates": {"album_path": "$albumartist/$album/$track - $title"},
        "post_processing.replaygain_enabled": False,
    }
    monkeypatch.setattr(config_manager, "get", lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr(config_manager, "get_active_media_server", lambda: settings["active_media_server"])
    monkeypatch.setattr(pipeline.time, "sleep", lambda _: None)
    # These tests exercise import guards, tags and ownership. Background DSP
    # would outlive the fixture and read the next test's temporary database.
    monkeypatch.setattr("core.sample.worker.enqueue_analysis", lambda track_id: None)
    monkeypatch.setattr(pipeline, "detect_broken_audio", lambda _: None)
    # Any actual retry or new wishlist entry is an error; ordinary success may
    # still settle a wishlist entry explicitly created before these spies.
    monkeypatch.setattr("core.downloads.monitor.requeue_quarantined_task_for_retry", lambda *a, **k: pytest.fail("album extra retried"))
    monkeypatch.setattr(db, "add_to_wishlist", lambda *a, **k: pytest.fail("album extra added to wishlist"))
    outcomes = {"Second": acoustid.VerificationResult.PASS, "Third": acoustid.VerificationResult.PASS}

    class Verifier:
        def quick_check_available(self):
            return True, "fixture"

        def verify_audio_file(self, path, title, artist, context):
            return outcomes[title], "fixture fingerprint result"

    monkeypatch.setattr(acoustid, "AcoustIDVerification", Verifier)

    def enrich(path, context, artist, album_info, runtime=None):
        track = context["track_info"]
        assert write_tags_to_file(path, {
            "title": track["name"], "artist_name": "Artist", "album_title": "Album",
            "track_number": track["track_number"], "disc_number": 1,
        }, embed_cover=False)["success"]
        return True

    monkeypatch.setattr(pipeline, "enhance_file_metadata", enrich)
    events, calls = [], []
    engine = SimpleNamespace(emit=lambda name, payload: events.append((name, payload)), is_event_action_enabled=lambda *a: False)
    runtime = pipeline.build_import_pipeline_runtime(automation_engine=engine,
        on_download_completed=lambda *a, **k: pytest.fail("album extra completed initiating task"))
    tracks = [{"id": str(number), "name": title, "artists": [{"name": "Artist"}],
               "track_number": number, "disc_number": 1, "duration_ms": 3000}
              for number, title in enumerate(["First", "Second", "Third"], 1)]
    album_info = {"id": "album", "name": "Album", "artists": [{"name": "Artist"}], "total_tracks": 3,
                  "album_type": "album", "images": [], "release_date": "2024-01-01"}
    paths = []
    for track in tracks:
        path = tmp_path / "Client" / f"{track['track_number']:02d}.flac"
        _flac(path)
        enrich(path, {"track_info": track}, {}, {})
        paths.append(path)
    context = {"profile_id": profile, "source": "deezer", "artist": {"name": "Artist"}, "album": album_info,
               "track_info": dict(tracks[0], quality_profile_id=quality_profile),
               "task_id": "requested-task", "batch_id": "requested-batch",
               "original_search_result": {"username": "usenet", "filename": "release.nzb"}}
    download_tasks["requested-task"] = {"status": "completed"}

    def process(key, ctx, path):
        pipeline.post_process_matched_download(key, ctx, path, runtime)
        calls.append(deepcopy(ctx))

    def expand(root=None):
        files = [releases.read_release_file(str(path)) for path in paths]
        return releases.import_album_tracks("request", context, files, files[0], root or settings["soulseek.transfer_path"],
            process, _copy_release_audio_to_transfer, lookup_album=lambda *a, **k: {
                "success": True, "source": "deezer", "album": deepcopy(album_info), "tracks": deepcopy(tracks)},
            is_owned=lambda *a: False)

    yield SimpleNamespace(db=db, profile=profile, qp=quality_profile, settings=settings, paths=paths,
                          context=context, tracks=tracks, outcomes=outcomes, calls=calls, events=events, expand=expand, tmp=tmp_path)
    invalidate_library_scope_cache()


def test_full_pipeline_inherits_profile_quality_and_persists_real_files(album):
    original = deepcopy(album.context)
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in album.paths]
    assert album.expand() == 2
    assert [ctx["track_info"]["name"] for ctx in album.calls] == ["Second", "Third"]
    for ctx in album.calls:
        path = Path(ctx["_final_processed_path"])
        assert path.is_file() and path.is_relative_to(Path(album.settings["soulseek.transfer_path"]))
        assert read_file_tags(str(path))["title"] == ctx["track_info"]["name"]
        assert ctx["profile_id"] == album.profile and ctx["track_info"]["quality_profile_id"] == album.qp
        assert "task_id" not in ctx and "batch_id" not in ctx
    with album.db._get_connection() as conn:
        rows = conn.execute("SELECT file_path, owner_profile_id, quality_profile_id FROM tracks").fetchall()
    assert len(rows) == 2
    assert all(Path(row["file_path"]).is_file() and row["owner_profile_id"] == album.profile
               and row["quality_profile_id"] == album.qp for row in rows)
    assert {key: value for key, value in album.context.items() if key != "_quality_profile"} == original
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in album.paths] == hashes
    assert download_tasks["requested-task"] == {"status": "completed"}
    assert not matched_downloads_context


def test_batch_only_profile_and_resolved_default_quality_are_inherited(album):
    album.context.pop("profile_id")
    album.context["track_info"].pop("quality_profile_id")
    album.context["_quality_profile"] = load_profile_by_id(album.qp)
    download_batches["requested-batch"] = {"profile_id": album.profile}
    assert album.expand() == 2
    assert all(ctx["profile_id"] == album.profile and ctx["track_info"]["quality_profile_id"] == album.qp for ctx in album.calls)


def test_cache_does_not_cross_own_library_roots(album):
    album.settings["active_media_server"] = "plex"
    profile_b = album.db.create_profile("Another requester")
    for profile in [album.profile, profile_b]:
        root = str(album.tmp / f"Own-{profile}")
        assert album.db.set_profile_library(profile, "own", root)
        invalidate_library_scope_cache()
        album.context["profile_id"] = profile
        before = len(album.calls)
        assert album.expand(root) == 2
        assert all(Path(ctx["_final_processed_path"]).is_relative_to(Path(root))
                   and ctx["profile_id"] == profile and ctx["track_info"]["quality_profile_id"] == album.qp
                   for ctx in album.calls[before:])
    assert len(album.calls) == 4


def test_cache_checks_the_published_file_before_skipping(album):
    assert album.expand() == 2
    assert album.expand() == 0  # simulates a media server that has not scanned yet
    removed = Path(album.calls[0]["_final_processed_path"])
    removed.unlink()
    assert album.expand() == 1
    assert removed.is_file()


def test_success_settles_an_existing_wishlist_request_without_adding_one(album):
    wanted = {"id": "2", "name": "Second", "artists": [{"name": "Artist"}],
              "source": "deezer", "album": deepcopy(album.context["album"])}
    # Seed an existing independent request, bypassing only this test's spy.
    assert database.MusicDatabase.add_to_wishlist(album.db, track_data=wanted, profile_id=album.profile,
                                                quality_profile_id=album.qp, user_initiated=True)
    assert album.db.get_wishlist_track("2", profile_id=album.profile)
    assert album.expand() == 2
    assert album.db.get_wishlist_track("2", profile_id=album.profile) is None


@pytest.mark.parametrize("failure", ["quality", "integrity", "duration", "acoustid", "silence", "exception"])
def test_failed_extra_is_one_pass_without_new_downloads_or_wishlist(album, monkeypatch, failure):
    if failure == "quality":
        _flac(album.paths[1], bits=16)
        assert write_tags_to_file(str(album.paths[1]), {
            "title": "Second", "artist_name": "Artist", "album_title": "Album",
            "track_number": 2, "disc_number": 1,
        }, embed_cover=False)["success"]
    elif failure == "integrity":
        album.paths[1].write_bytes(b"broken")
        # Keep valid original release tags for pairing; bytes fail the real size guard.
        real = releases.read_release_file
        monkeypatch.setattr(releases, "read_release_file", lambda path: releases.ReleaseFile(path, "Second", "Artist", "Album", 2, 1, 3000, True)
                            if Path(path) == album.paths[1] else real(path))
    elif failure == "duration":
        album.tracks[1]["duration_ms"] = 18000
    elif failure == "acoustid":
        album.outcomes["Second"] = acoustid.VerificationResult.FAIL
    elif failure == "silence":
        monkeypatch.setattr(pipeline, "detect_broken_audio", lambda path: "silent audio" if Path(path).name == "02.flac" else None)
    else:
        real = pipeline.safe_move_file

        def move(source, destination):
            if Path(source).name == "02.flac":
                raise OSError("publish failed")
            return real(source, destination)

        monkeypatch.setattr(pipeline, "safe_move_file", move)
    assert album.expand() == 1
    assert album.calls[-1]["track_info"]["name"] == "Third"
    assert not any(key for key in matched_downloads_context if "album-track" in key)
    assert download_tasks == {"requested-task": {"status": "completed"}}
    with album.db._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM wishlist_tracks").fetchone()[0] == 0
    assert not (Path(album.settings["soulseek.transfer_path"]) / "02.flac").exists()
    sidecars = list((Path(album.settings["soulseek.download_path"]) / "ss_quarantine").glob("*.json"))
    if failure != "exception":
        assert len(sidecars) == 1
        ctx = json.loads(sidecars[0].read_text())["context"]
        assert ctx["profile_id"] == album.profile and ctx["track_info"]["quality_profile_id"] == album.qp
        assert "task_id" not in ctx and "batch_id" not in ctx
    assert [payload["title"] for event, payload in album.events if event == "track_downloaded"] == ["Third"]
