"""The album's other tracks through real audio, SQLite and ordinary imports.

Only catalogue/fingerprint/enrichment services and external event receivers are
faked. Guard decisions, quarantine, tags, moves, database writes and wishlist
settlement use their production implementations.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import threading
from types import SimpleNamespace

from mutagen.flac import FLAC
import pytest

import core.acoustid_verification as acoustid
from core.downloads import release_import
from core.downloads.post_processing import _copy_release_audio_to_transfer
from core.downloads.release_import import import_album_tracks, read_release_file
import core.imports.pipeline as pipeline
from core.imports.quarantine import approve_quarantine_entry
from core.quality.selection import load_profile_by_id
from core.runtime_state import matched_downloads_context
from core.settings import config_manager
from core.tag_writer import read_file_tags, write_tags_to_file
import core.wishlist.service as wishlist_service
from core.wishlist.identity import wishlist_row_key
import database.music_database as database


ARTIST = "Integration Artist"
ALBUM = "Integration Album"
TITLES = ("First", "Second", "Third")


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _make_flac(path, title, number):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-ar", "44100", "-sample_fmt", "s16",
        "-c:a", "flac", "-compression_level", "0", "-y", str(path),
    ], check=True, capture_output=True)
    result = write_tags_to_file(str(path), {
        "title": title, "artist_name": ARTIST, "album_title": ALBUM,
        "track_number": number, "disc_number": 1, "total_tracks": len(TITLES), "year": "1999",
    }, embed_cover=False)
    assert result["success"], result
    audio = FLAC(str(path))
    audio["DEEZER_TRACK_ID"] = [f"foreign-{number}"]
    audio.save()
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
    monkeypatch.setattr(release_import, "_recent_imports", type(release_import._recent_imports)())
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
    # These tests exercise import guards, tags and ownership. Background DSP
    # would outlive the fixture and read the next test's temporary database.
    monkeypatch.setattr("core.sample.worker.enqueue_analysis", lambda track_id: None)
    contexts, events = [], []
    outcomes = {title: acoustid.VerificationResult.PASS for title in TITLES}

    class Verifier:
        def quick_check_available(self):
            return True, "test fingerprint backend"

        def verify_audio_file(self, path, title, artist, context):
            assert artist == ARTIST
            return outcomes[title], f"fingerprint diagnostic for {title}"

    monkeypatch.setattr(acoustid, "AcoustIDVerification", Verifier)

    def enrich(path, context, artist, album_info, runtime=None):
        # Replaces the REMOTE metadata lookup while keeping the actual writer.
        track = context["track_info"]
        result = write_tags_to_file(path, {
            "title": track["name"], "artist_name": artist["name"],
            "album_title": context["album"]["name"],
            "track_number": track["track_number"], "disc_number": track["disc_number"],
            "total_tracks": len(TITLES), "year": "2024-06-01",
        }, embed_cover=False)
        assert result["success"], result
        audio = FLAC(path)
        audio["DEEZER_TRACK_ID"] = [str(track["id"])]
        audio.save()
        context["_embedded_id_tags"] = {"DEEZER_TRACK_ID": str(track["id"])}
        return True

    monkeypatch.setattr(pipeline, "enhance_file_metadata", enrich)
    engine = SimpleNamespace(emit=lambda name, payload: events.append((name, payload["title"])),
                             is_event_action_enabled=lambda *args: False)
    runtime = pipeline.build_import_pipeline_runtime(automation_engine=engine)

    def process(key, context, path):
        pipeline.post_process_matched_download(key, context, path, runtime)
        contexts.append(deepcopy(context))

    tracks = [{
        "id": str(1000 + number), "name": title,
        "artists": [{"id": "11", "name": ARTIST}],
        "track_number": number, "disc_number": 1, "duration_ms": 3000,
        "isrc": f"USAAA240000{number}",
    } for number, title in enumerate(TITLES, 1)]
    album = {
        "id": "9001", "name": ALBUM, "artists": [{"id": "11", "name": ARTIST}],
        "album_type": "album", "release_date": "2024-06-01", "total_tracks": len(TITLES),
        "total_discs": 1, "duration_ms": 9000, "images": [],
    }
    files = [_make_flac(tmp_path / "Client" / f"{number:02d}.flac", title, number)
             for number, title in enumerate(TITLES, 1)]
    return SimpleNamespace(
        db=db, profile_id=profile_id, library=library, downloads=downloads, settings=settings,
        contexts=contexts, events=events, outcomes=outcomes, tracks=tracks, album=album,
        originals=files, process=process, runtime=runtime,
    )


def _request(env, *, mode="album_tracks", required=False):
    qp_id = env.db.create_quality_profile("Album import profile", {
        "release_import_mode": mode,
        "ranked_targets": [{"label": "FLAC", "format": "flac", "bit_depth": 16}],
        "fallback_enabled": False, "acoustid_required": required,
        "deep_audio_verify": True, "downsample_enabled": False,
    })
    assert qp_id and load_profile_by_id(qp_id)["release_import_mode"] == mode
    env.qp_id = qp_id
    env.context = {
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
    return env.context


def _expand(env, *, files=None, requested=0, **kwargs):
    release_files = [read_release_file(str(path)) for path in (files if files is not None else env.originals)]
    kwargs.setdefault("lookup_album", lambda *args, **kw: {
        "success": True, "source": "deezer", "album": deepcopy(env.album), "tracks": deepcopy(env.tracks),
    })
    return import_album_tracks("requested", env.context, release_files, release_files[requested], str(env.library),
                               env.process, _copy_release_audio_to_transfer, **kwargs)


def _path(env, number):
    return env.library / ARTIST / ALBUM / f"{number:02d} - {TITLES[number - 1]}.flac"


def _rows(env, table):
    assert table in {"tracks", "library_history", "track_downloads"}
    with env.db._get_connection() as connection:
        if table == "tracks":
            # Ours: the catalogue is Library v2, one row per file.
            return [dict(row) for row in connection.execute(
                "SELECT t.id, f.path AS file_path, t.quality_profile_id, f.owner_profile_id"
                "  FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id = t.id").fetchall()]
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table}").fetchall()]


def _library_files(env):
    return sorted(str(p.relative_to(env.library)) for p in env.library.rglob("*") if p.is_file())


def test_default_profile_imports_only_the_requested_track(album_environment):
    env = album_environment
    _request(env, mode="requested_tracks")
    assert _expand(env) == 0
    assert env.contexts == []
    assert not env.library.exists() or _library_files(env) == []


def test_other_tracks_run_the_full_pipeline_with_their_own_metadata(album_environment):
    env = album_environment
    _request(env)
    originals = [_digest(path) for path in env.originals]
    requested = deepcopy(env.context["track_info"])

    assert _expand(env) == 2
    assert _library_files(env) == [f"{ARTIST}/{ALBUM}/02 - Second.flac", f"{ARTIST}/{ALBUM}/03 - Third.flac"]
    for number in (2, 3):
        tags = read_file_tags(str(_path(env, number)))
        assert tags["title"] == TITLES[number - 1] and tags["track_number"] == number
        assert tags["verification_status"] == "verified"
        assert FLAC(str(_path(env, number)))["DEEZER_TRACK_ID"] == [str(1000 + number)]
    assert all(ctx["track_info"]["quality_profile_id"] == env.qp_id for ctx in env.contexts)
    assert all(ctx["profile_id"] == env.profile_id for ctx in env.contexts)
    assert all("task_id" not in ctx and "batch_id" not in ctx for ctx in env.contexts)
    # The request itself is the initiating task's job, never imported twice.
    assert [ctx["track_info"]["name"] for ctx in env.contexts] == ["Second", "Third"]
    assert env.context["track_info"] == requested
    assert [_digest(path) for path in env.originals] == originals
    assert [event for event in env.events if event[0] == "track_downloaded"] == [
        ("track_downloaded", "Second"), ("track_downloaded", "Third")]
    assert not any(":album-track:" in key for key in matched_downloads_context)


def test_library_rows_point_to_the_imported_files(album_environment):
    env = album_environment
    _request(env)
    assert _expand(env) == 2
    expected = {str(_path(env, 2)), str(_path(env, 3))}
    for table in ("tracks", "library_history", "track_downloads"):
        assert {row["file_path"] for row in _rows(env, table)} == expected, table
    assert all(row["quality_profile_id"] == env.qp_id for row in _rows(env, "tracks"))
    # Ours: a file's owner is the library it sits in; the requester keeps no
    # library of its own, so it is the shared one.
    assert all(row["owner_profile_id"] is None for row in _rows(env, "tracks"))
    history = _rows(env, "library_history")
    assert all(row["download_source"] == "Usenet" for row in history)
    assert all(row["origin"] == "watchlist" and row["origin_context"] == ARTIST for row in history)


@pytest.mark.parametrize("required", [False, True])
def test_failing_track_is_quarantined_alone_and_approval_imports_it(album_environment, required):
    env = album_environment
    _request(env, required=required)
    env.outcomes["Second"] = acoustid.VerificationResult.FAIL

    assert _expand(env) == 1
    assert _library_files(env) == [f"{ARTIST}/{ALBUM}/03 - Third.flac"]
    quarantine = env.downloads / "ss_quarantine"
    sidecars = list(quarantine.glob("*.json"))
    assert len(sidecars) == 1
    sidecar = json.loads(sidecars[0].read_text())
    assert sidecar["original_filename"] == "02.flac"
    assert sidecar["context"]["track_info"]["name"] == "Second"
    assert not any(key.startswith("_release") for key in sidecar["context"])

    # Approve exactly like the quarantine manager does.
    restored, context, trigger = approve_quarantine_entry(str(quarantine), sidecars[0].stem, str(env.downloads / "Transfer"))
    context["_skip_quarantine_check"] = "all"
    context["_approved_quarantine_trigger"] = trigger
    pipeline.post_process_matched_download(f"approve_{sidecars[0].stem}", context, restored, env.runtime)
    assert context.get("_pipeline_import_succeeded") is True
    assert _path(env, 2).is_file()


def test_tracks_already_in_the_library_are_not_imported_again(album_environment):
    env = album_environment
    _request(env)
    env.context["profile_id"] = None  # the shared library, read like the missing-track analysis does
    assert _expand(env) == 2
    first_rows = {(row["id"], row["file_path"]) for row in _rows(env, "tracks")}
    # A new process: only the library itself knows these tracks now.
    release_import._recent_imports.clear()
    env.contexts.clear()
    assert _expand(env) == 0
    assert env.contexts == []
    assert {(row["id"], row["file_path"]) for row in _rows(env, "tracks")} == first_rows


def test_owned_check_skips_a_track_owned_in_another_format(album_environment):
    env = album_environment
    _request(env)
    owned = env.library / ARTIST / ALBUM / "02 - Second.mp3"
    owned.parent.mkdir(parents=True)
    owned.write_bytes(b"owned")
    assert _expand(env, is_owned=lambda track, album: track["name"] == "Second") == 1
    assert _library_files(env) == [f"{ARTIST}/{ALBUM}/02 - Second.mp3", f"{ARTIST}/{ALBUM}/03 - Third.flac"]


def test_wishlist_row_of_an_other_track_is_settled(album_environment):
    env = album_environment
    _request(env)
    wanted = dict(deepcopy(env.tracks[1]), source="deezer", album=deepcopy(env.album))
    assert env.db.add_to_wishlist(track_data=wanted, profile_id=env.profile_id, quality_profile_id=env.qp_id,
                                  source_type="album", user_initiated=True)
    # Ours: a wishlist row is keyed per release, ``<track>::<album>``.
    key = wishlist_row_key("1002", env.album["id"])
    assert env.db.get_wishlist_track(key, profile_id=env.profile_id)
    assert _expand(env) == 2
    assert env.db.get_wishlist_track(key, profile_id=env.profile_id) is None


def test_a_release_without_some_tracks_imports_what_it_has(album_environment):
    env = album_environment
    _request(env)
    assert _expand(env, files=env.originals[:2]) == 1
    assert _library_files(env) == [f"{ARTIST}/{ALBUM}/02 - Second.flac"]


def test_corrupt_track_is_rejected_without_blocking_the_rest(album_environment):
    env = album_environment
    _request(env)
    path = env.originals[1]
    path.write_bytes(path.read_bytes()[:path.stat().st_size // 2])
    assert _expand(env) == 1
    assert _library_files(env) == [f"{ARTIST}/{ALBUM}/03 - Third.flac"]
    assert not list(env.library.glob("*.flac"))  # no stray copies in the library root


@pytest.mark.parametrize("case", ["no_album_id", "wrong_edition", "lookup_failed"])
def test_unconfirmed_album_imports_nothing(album_environment, case):
    env = album_environment
    _request(env)
    lookup = None
    if case == "no_album_id":
        env.context["album"].pop("id")
        env.context["track_info"]["album"].pop("id")
    elif case == "wrong_edition":
        lookup = lambda *a, **k: {"success": True, "album": dict(env.album, name=ALBUM + " (Deluxe)"),
                                  "tracks": deepcopy(env.tracks)}
    else:
        lookup = lambda *a, **k: {"success": False}
    assert _expand(env, **({"lookup_album": lookup} if lookup else {})) == 0
    assert env.contexts == []


def test_a_second_download_of_the_same_album_does_not_wait_or_import(album_environment):
    env = album_environment
    _request(env)
    started = threading.Event()
    release = threading.Event()
    process = env.process

    def slow_process(key, context, path):
        started.set()
        release.wait(10)
        process(key, context, path)

    env.process = slow_process
    first = threading.Thread(target=_expand, args=(env,))
    first.start()
    assert started.wait(10)
    env.process = process
    assert _expand(env) == 0  # same album: returns at once instead of waiting
    other = deepcopy(env.context)
    other["album"]["name"] = other["track_info"]["album"]["name"] = "Another Album"
    env.context, original = other, env.context
    lookups = []
    assert _expand(env, lookup_album=lambda *a, **k: lookups.append(k["album_name"]) or {"success": False}) == 0
    assert lookups == ["Another Album"]  # a different album is not held up
    env.context = original
    release.set()
    first.join(10)
    assert not first.is_alive()
    assert sorted(ctx["track_info"]["name"] for ctx in env.contexts) == ["Second", "Third"]
    assert not release_import._active_albums


def test_a_second_format_of_the_requested_track_is_not_imported(album_environment, tmp_path):
    env = album_environment
    _request(env)
    mp3 = tmp_path / "Client" / "MP3" / "01.mp3"
    mp3.parent.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-c:a", "libmp3lame", "-b:a", "128k", "-y", str(mp3)], check=True, capture_output=True)
    assert write_tags_to_file(str(mp3), {
        "title": "First", "artist_name": ARTIST, "album_title": ALBUM, "track_number": 1, "disc_number": 1,
    }, embed_cover=False)["success"]
    # The request took the MP3; the profile would pick the FLAC of the same slot.
    assert _expand(env, files=[*env.originals, mp3], requested=3) == 2
    assert [ctx["track_info"]["name"] for ctx in env.contexts] == ["Second", "Third"]
