"""Complete album expansion through real audio, SQLite and ordinary imports.

Only catalogue/fingerprint/enrichment services and external event receivers are
faked. Guard decisions, tags, output transforms, staging, publishing, database
writes and wishlist settlement use their production implementations.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import shutil
import subprocess
import threading
from types import SimpleNamespace

from mutagen.flac import FLAC
import pytest

import core.acoustid_verification as acoustid
from core.downloads.atomic_album_publish import publish_album_batch
from core.downloads.release_import import read_release_file, try_complete_album_import
import core.imports.pipeline as pipeline
from core.quality.selection import load_profile_by_id
from core.settings import config_manager
from core.tag_writer import read_file_tags, write_tags_to_file
import core.wishlist.service as wishlist_service
import database.music_database as database


ARTIST = "Integration Artist"
ALBUM = "Integration Album"


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _make_flac(path, title, number, *, hires=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-ar", "96000" if hires else "44100", "-sample_fmt", "s32" if hires else "s16",
        "-c:a", "flac", "-compression_level", "0", "-y", str(path),
    ], check=True, capture_output=True)
    result = write_tags_to_file(str(path), {
        "title": title, "artist_name": ARTIST, "album_title": ALBUM,
        "track_number": number, "disc_number": 1, "total_tracks": 2, "year": "1999",
    }, embed_cover=False)
    assert result["success"], result
    audio = FLAC(str(path))
    audio["DEEZER_TRACK_ID"] = [f"foreign-{number}"]
    audio.save()
    assert path.stat().st_size > 10240
    return path


@pytest.fixture
def album_environment(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg") or not shutil.which("flac"):
        pytest.skip("real album pipeline integration requires ffmpeg and flac")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "music.db"))
    monkeypatch.setenv("SOULSYNC_CONFIG_PATH", str(tmp_path / "config.json"))
    db = database.MusicDatabase(str(tmp_path / "music.db"))
    monkeypatch.setattr(database, "_database_instances", {threading.get_ident(): db})
    monkeypatch.setattr(wishlist_service, "_wishlist_service", None)
    profile_id = db.create_profile("Album requester")
    assert profile_id and profile_id != 1
    library = tmp_path / "Library"
    downloads = tmp_path / "Downloads"
    settings = {
        "soulseek.transfer_path": str(library), "soulseek.download_path": str(downloads),
        "active_media_server": "soulsync", "album_downloads.atomic_publish": False,
        "post_processing.verify_flac_decode": True,
        "post_processing.replaygain_enabled": False,
        "file_organization.enabled": True,
        "file_organization.auto_disambiguation": False,
        "file_organization.templates": {"album_path": "$albumartist/$album/$track - $title"},
        "import.replace_lower_quality": False,
        "lossy_copy.enabled": False,
    }
    monkeypatch.setattr(config_manager, "get", lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr(config_manager, "get_active_media_server", lambda: "soulsync")
    monkeypatch.setattr(pipeline.time, "sleep", lambda seconds: None)
    timeline, contexts, events, completions = [], [], [], []
    outcomes = {"First": acoustid.VerificationResult.PASS, "Second": acoustid.VerificationResult.PASS}

    def visible_outputs():
        return sorted(str(path.relative_to(library)) for path in library.rglob("*")
                      if path.is_file() and not any(part.startswith('.') for part in path.relative_to(library).parts))

    class Verifier:
        def quick_check_available(self):
            return True, "test fingerprint backend"

        def verify_audio_file(self, path, title, artist, context):
            timeline.append(("verify", title, bool(context.get("_release_preflight_only"))))
            assert artist == ARTIST
            return outcomes[title], f"fingerprint diagnostic for {title}"

    monkeypatch.setattr(acoustid, "AcoustIDVerification", Verifier)

    def enrich(path, context, artist, album_info, runtime=None):
        # Replaces the REMOTE metadata lookup while keeping the actual writer.
        track = context["track_info"]
        timeline.append(("tag", track["name"], track["id"]))
        result = write_tags_to_file(path, {
            "title": track["name"], "artist_name": artist["name"],
            "album_title": context["album"]["name"],
            "track_number": track["track_number"], "disc_number": track["disc_number"],
            "total_tracks": 2, "year": "2024-06-01",
        }, embed_cover=False)
        assert result["success"], result
        audio = FLAC(path)
        audio["DEEZER_TRACK_ID"] = [str(track["id"])]
        audio["DEEZER_ALBUM_ID"] = [str(context["album"]["id"])]
        audio.save()
        context["_embedded_id_tags"] = {"DEEZER_TRACK_ID": str(track["id"])}
        return True

    monkeypatch.setattr(pipeline, "enhance_file_metadata", enrich)

    def emit(name, payload):
        timeline.append(("event", name, payload["title"]))
        events.append((name, deepcopy(payload), visible_outputs()))

    engine = SimpleNamespace(emit=emit, is_event_action_enabled=lambda *args: False)
    runtime = pipeline.build_import_pipeline_runtime(
        automation_engine=engine,
        on_download_completed=lambda *args, **kwargs: completions.append((args, kwargs)),
    )

    def process(key, context, path):
        phase = "preflight" if context.get("_release_preflight_only") else "import"
        timeline.append(("start", phase, context["track_info"]["name"]))
        pipeline.post_process_matched_download(key, context, path, runtime)
        contexts.append(deepcopy(context))
        timeline.append(("done", phase, context["track_info"]["name"]))

    def publish(stage, root, move, update):
        timeline.append(("publish_start", visible_outputs()))
        result = publish_album_batch(stage, root, move, update)
        timeline.append(("publish_done", result["success"], visible_outputs()))
        return result

    tracks = [{
        "id": str(1000 + number), "name": title,
        "artists": [{"id": "11", "name": ARTIST}],
        "track_number": number, "disc_number": 1, "duration_ms": 3000,
        "isrc": f"USAAA240000{number}",
    } for number, title in enumerate(("First", "Second"), 1)]
    album = {
        "id": "9001", "name": ALBUM, "artists": [{"id": "11", "name": ARTIST}],
        "album_type": "album", "release_date": "2024-06-01", "total_tracks": 2,
        "total_discs": 1, "total_discs_declared": True, "duration_ms": 6000, "images": [],
    }
    files = [_make_flac(tmp_path / "Client" / f"{number:02d}.flac", title, number)
             for number, title in enumerate(("First", "Second"), 1)]
    env = SimpleNamespace(
        db=db, profile_id=profile_id, library=library, downloads=downloads,
        settings=settings, timeline=timeline, contexts=contexts, events=events,
        completions=completions, outcomes=outcomes, tracks=tracks, album=album,
        originals=files, process=process, publish=publish, engine=engine,
    )
    return env


def _request(env, *, required=False, lossy=False):
    qp_id = env.db.create_quality_profile("Album import profile", {
        "release_import_mode": "complete_album",
        "ranked_targets": [{"label": "FLAC", "format": "flac", "bit_depth": 16}],
        "fallback_enabled": False, "acoustid_required": required,
        "deep_audio_verify": True, "downsample_enabled": False,
        "lossy_copy_enabled": lossy, "lossy_copy_codec": "mp3",
        "lossy_copy_bitrate": "128", "lossy_copy_delete_original": False,
    })
    assert qp_id
    profile = load_profile_by_id(qp_id)
    assert profile["release_import_mode"] == "complete_album"
    assert profile["acoustid_required"] is required
    # The legacy setting deliberately disagrees, to prove the item profile wins.
    env.settings["acoustid.require_verified"] = not required
    context = {
        "source": "spotify", "profile_id": env.profile_id,
        "artist": {"id": "spotify-artist", "name": ARTIST},
        "album": dict(env.album, id="spotify-album"),
        "track_info": dict(deepcopy(env.tracks[0]), id="spotify-request", quality_profile_id=qp_id,
                           album=dict(env.album, id="spotify-album"),
                           _dl_origin="watchlist", _dl_origin_context=ARTIST),
        "original_search_result": {
            "id": "spotify-request", "title": "First", "artist": ARTIST,
            "album": ALBUM, "username": "usenet", "filename": "release.nzb",
        },
        "_download_username": "usenet", "has_clean_metadata": True,
        "task_id": "initiating-task", "batch_id": "initiating-batch",
    }
    env.qp_id = qp_id
    env.context = context
    return context


def _expand(env, *, requested=None, files=None):
    release_files = [read_release_file(str(path)) for path in (files if files is not None else env.originals)]
    requested = requested or release_files[0]
    result = try_complete_album_import(
        "requested", env.context, release_files, requested, str(env.library), env.process,
        lookup_album=lambda *args, **kwargs: {
            "success": True, "source": "deezer", "album": deepcopy(env.album), "tracks": deepcopy(env.tracks),
        }, publish=env.publish, automation_engine=env.engine,
    )
    return result


def _rows(env, table):
    assert table in {"tracks", "library_history", "track_downloads", "wishlist_tracks"}
    with env.db._get_connection() as connection:
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table}").fetchall()]


def _expected_paths(env, extension="flac"):
    return [env.library / ARTIST / ALBUM / f"{number:02d} - {title}.{extension}"
            for number, title in enumerate(("First", "Second"), 1)]


def test_complete_album_pipeline_writes_each_tracks_own_metadata_and_initiating_profile(album_environment):
    env = album_environment
    _request(env, required=True)
    original_hashes = [_digest(path) for path in env.originals]
    requested_track = deepcopy(env.context["track_info"])
    result = _expand(env)

    assert result and result["status"] == "imported", env.context.get("_release_import_note")
    assert result["count"] == 2
    assert result["path"] == str(_expected_paths(env)[0])
    for number, path in enumerate(_expected_paths(env), 1):
        tags = read_file_tags(str(path))
        assert tags["title"] == ("First" if number == 1 else "Second")
        assert tags["artist"] == ARTIST and tags["album"] == ALBUM
        assert tags["track_number"] == number and tags["disc_number"] == 1
        assert tags["year"].startswith("2024")
        assert FLAC(str(path))["DEEZER_TRACK_ID"] == [str(1000 + number)]
        assert tags["verification_status"] == "verified"
    assert all(ctx["track_info"]["quality_profile_id"] == env.qp_id for ctx in env.contexts)
    assert all(ctx["profile_id"] == env.profile_id for ctx in env.contexts)
    assert all(ctx["source"] == "deezer" for ctx in env.contexts)
    assert env.context["track_info"] == requested_track
    assert [_digest(path) for path in env.originals] == original_hashes
    assert env.completions == []


@pytest.mark.parametrize("required, mismatch_track", [(False, "First"), (False, "Second"), (True, "First"), (True, "Second")])
def test_complete_album_pipeline_uses_per_track_acoustid_policy(album_environment, required, mismatch_track):
    env = album_environment
    _request(env, required=required)
    env.outcomes[mismatch_track] = acoustid.VerificationResult.FAIL
    hashes = [_digest(path) for path in env.originals]
    result = _expand(env)
    assert [_digest(path) for path in env.originals] == hashes
    if required:
        assert result is None
        assert not any(path.exists() for path in _expected_paths(env))
        assert not _rows(env, "tracks")
        assert not any(event[0] == "track_downloaded" for event in env.events)
        assert "AcoustID" in env.context["_release_import_note"]
    else:
        assert result and result["status"] == "imported", env.context.get("_release_import_note")
        imported = _expected_paths(env)[0 if mismatch_track == "First" else 1]
        assert read_file_tags(str(imported))["verification_status"] == "unverified"
        history = {row["title"]: row for row in _rows(env, "library_history")}
        assert history[mismatch_track]["acoustid_result"] == "fail"
        assert history[mismatch_track]["verification_status"] == "unverified"


@pytest.mark.parametrize("failure", ["corrupt", "incomplete"])
def test_bad_or_incomplete_release_never_publishes_bonus_or_changes_originals(album_environment, failure):
    env = album_environment
    _request(env)
    if failure == "corrupt":
        # Keep STREAMINFO/tags readable while truncating the actual audio frames.
        path = env.originals[1]
        path.write_bytes(path.read_bytes()[:path.stat().st_size // 2])
        assert read_release_file(str(path)).tagged
    hashes = [_digest(path) for path in env.originals]
    result = _expand(env, files=env.originals[:1] if failure == "incomplete" else None)
    assert result is None
    assert [_digest(path) for path in env.originals] == hashes
    assert not any(path.exists() for path in _expected_paths(env))
    assert not _rows(env, "tracks")
    assert not any(event[0] == "track_downloaded" for event in env.events)
    assert env.context.get("_release_import_note")
    if failure == "corrupt":
        assert len(env.contexts) == 2
        assert env.contexts[0]["_release_checks_passed"] is True
        assert env.contexts[1].get("_integrity_failure_msg")
        assert not env.contexts[1].get("_release_checks_passed")
    else:
        assert env.contexts == []  # catalogue completeness is checked before imports


def test_complete_album_publishes_real_lossy_companions_without_deleting_flac(album_environment):
    env = album_environment
    _request(env, lossy=True)
    hashes = [_digest(path) for path in env.originals]
    result = _expand(env)
    assert result and result["status"] == "imported", env.context.get("_release_import_note")
    assert result["count"] == 2
    for flac, mp3 in zip(_expected_paths(env), _expected_paths(env, "mp3")):
        assert flac.exists() and mp3.exists()
        assert read_file_tags(str(mp3))["title"] == read_file_tags(str(flac))["title"]
        assert mp3.stat().st_size > 10240
    assert [_digest(path) for path in env.originals] == hashes
    assert len(_rows(env, "tracks")) == 2  # companions are retained outputs, not extra requested songs
    assert not list(env.library.glob(".soulsync_atomic_staging/release-*"))


def test_existing_better_library_track_is_preserved_and_album_expansion_falls_back(album_environment):
    env = album_environment
    _request(env)
    existing = _make_flac(_expected_paths(env)[0], "First", 1, hires=True)
    existing_hash = _digest(existing)
    source_hashes = [_digest(path) for path in env.originals]
    result = _expand(env)
    assert result is None
    assert _digest(existing) == existing_hash
    assert FLAC(str(existing)).info.bits_per_sample == 24
    assert [_digest(path) for path in env.originals] == source_hashes
    assert not _expected_paths(env)[1].exists()
    assert not any(event[0] == "track_downloaded" for event in env.events)
    assert env.context.get("_release_import_note")


def test_native_library_history_and_provenance_point_to_published_paths(album_environment):
    env = album_environment
    _request(env)
    result = _expand(env)
    assert result and result["status"] == "imported", env.context.get("_release_import_note")
    expected = {str(path) for path in _expected_paths(env)}
    for table in ("tracks", "library_history", "track_downloads"):
        rows = _rows(env, table)
        assert len(rows) == 2, (table, rows)
        assert {row["file_path"] for row in rows} == expected
        assert all(Path(row["file_path"]).is_file() for row in rows)
        assert all(".soulsync_atomic_staging" not in row["file_path"] for row in rows)
    tracks = _rows(env, "tracks")
    assert {row["title"] for row in tracks} == {"First", "Second"}
    assert {row["deezer_id"] for row in tracks} == {"1001", "1002"}
    assert all(row["quality_profile_id"] == env.qp_id for row in tracks)
    assert all(row["owner_profile_id"] == env.profile_id for row in tracks)
    history = _rows(env, "library_history")
    assert all(row["download_source"] == "Usenet" for row in history)
    assert all(row["origin"] == "watchlist" and row["origin_context"] == ARTIST for row in history)
    provenance = _rows(env, "track_downloads")
    assert all(row["source_service"] == "usenet" for row in provenance)
    assert {row["deezer_track_id"] for row in provenance} == {"1001", "1002"}


def test_cross_provider_initiating_wishlist_is_settled_after_full_publication(album_environment):
    env = album_environment
    _request(env)
    assert env.db.add_to_wishlist(track_data=deepcopy(env.context["track_info"]),
                                  profile_id=env.profile_id, quality_profile_id=env.qp_id,
                                  source_type="album", user_initiated=True)
    assert env.db.get_wishlist_track("spotify-request", profile_id=env.profile_id)
    result = _expand(env)
    assert result and result["status"] == "imported", env.context.get("_release_import_note")
    assert env.db.get_wishlist_track("spotify-request", profile_id=env.profile_id) is None
    removals = env.db.get_wishlist_removals("spotify-request")
    assert len(removals) == 1
    assert removals[0]["source"] == "spotify"
    assert removals[0]["final_path"] == str(_expected_paths(env)[0])


def test_track_success_events_follow_all_checks_and_complete_publication(album_environment):
    env = album_environment
    _request(env)
    result = _expand(env)
    assert result and result["status"] == "imported", env.context.get("_release_import_note")
    phases = [(ctx["track_info"]["name"], bool(ctx.get("_release_preflight_only"))) for ctx in env.contexts]
    assert phases == [("First", True), ("Second", True), ("First", False), ("Second", False)]
    publish_start = next(index for index, entry in enumerate(env.timeline) if entry[0] == "publish_start")
    publish_done = next(index for index, entry in enumerate(env.timeline) if entry[0] == "publish_done")
    assert all(index < publish_start for index, entry in enumerate(env.timeline) if entry[0] in {"verify", "tag"})
    assert all(index > publish_done for index, entry in enumerate(env.timeline) if entry[0] == "event" and entry[1] == "track_downloaded")
    success_events = [event for event in env.events if event[0] == "track_downloaded"]
    assert [event[1]["title"] for event in success_events] == ["First", "Second"]
    expected = sorted(str(path.relative_to(env.library)) for path in _expected_paths(env))
    assert all(event[2] == expected for event in success_events)
    assert env.completions == []
