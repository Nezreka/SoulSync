"""The download path for an exact SoundCloud upload.

The task resolves the stored url, keeps only the upload with the stored
SoundCloud id, and starts that one file. It never searches, never reuses a
batch source, never matches staging, and never falls back to anything else.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from core.downloads import soundcloud_exact_task as sct
from core.downloads import task_worker as tw
from core.download_plugins.types import TrackResult
from core.runtime_state import download_batches, download_tasks
from core.spotify_client import Track as SpotifyTrack

PERMALINK = "https://soundcloud.com/djuser/song-flip"
PRIVATE = "https://soundcloud.com/djuser/song-flip/s-AbCdEf"


@pytest.fixture(autouse=True)
def reset_state():
    download_tasks.clear()
    download_batches.clear()
    yield
    download_tasks.clear()
    download_batches.clear()


def _block(**kw):
    return {"sc_id": "123", "permalink_url": PERMALINK, "request_url": PERMALINK,
            "permalink_url_norm": PERMALINK, "request_url_norm": PERMALINK,
            "uploader": "djuser", "upload_title": "Artist A - Song", "duration_ms": 201000,
            "artwork_url": "", "official_download": False, "added_via": "companion", **kw}


def _track_info(block=None, **kw):
    data = {"id": "soundcloud_exact_123", "name": "Song", "artists": [{"name": "Artist A"}],
            "album": {"name": "Song"}, "duration_ms": 201000}
    if block is not None:
        data["soundcloud_exact"] = block
    return {**data, "track_id": "soundcloud_exact_123", "spotify_track_id": "soundcloud_exact_123",
            "track_data": dict(data), "profile_id": 1, **kw}


def _result(sc_id="123", url=PERMALINK, duration_ms=201000):
    return TrackResult(
        username="soundcloud", filename=f"{sc_id}||{url}||Artist A - Song", size=0, bitrate=128,
        duration=duration_ms, quality="mp3", free_upload_slots=999, upload_speed=999_999,
        queue_length=0, artist="Artist A", title="Song",
        _source_metadata={"source": "soundcloud", "track_id": sc_id, "permalink_url": url})


class FakeSC:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def is_available(self):
        return True

    async def resolve_url(self, url, timeout=None, progress_callback=None):
        self.calls.append(url)
        return list(self.results), []


class FakeOrch:
    def __init__(self, sc, mode="hybrid"):
        self._sc = sc
        self.mode = mode
        self.hybrid_order = ["soulseek", "soundcloud"]
        self.hybrid_primary = "soulseek"
        self.hybrid_secondary = ""
        self.registry = SimpleNamespace(names=lambda: ["soulseek", "soundcloud"])

    def client(self, name):
        return self._sc if name == "soundcloud" else None

    def _normalize_source_name(self, name):
        return name if name in ("soulseek", "soundcloud") else None

    async def search(self, *a, **k):
        raise AssertionError("an exact upload must never search")


def _forbidden(name):
    def _raise(*a, **k):
        raise AssertionError(f"an exact upload must never call {name}")
    return _raise


def _deps(sc, *, started=True, mode="hybrid"):
    attempts, completed = [], []

    def attempt(task_id, candidates, track, batch_id, **kw):
        attempts.append((task_id, list(candidates), kw))
        if not started:
            download_tasks[task_id].setdefault("start_failures", []).append("peer said no")
        return started

    deps = tw.TaskWorkerDeps(
        download_orchestrator=FakeOrch(sc, mode=mode),
        matching_engine=None,
        run_async=lambda coro: asyncio.run(coro),
        try_source_reuse=_forbidden("try_source_reuse"),
        store_batch_source=_forbidden("store_batch_source"),
        try_staging_match=_forbidden("try_staging_match"),
        get_valid_candidates=_forbidden("get_valid_candidates"),
        attempt_download_with_candidates=attempt,
        on_download_completed=lambda b, t, ok: completed.append((b, t, ok)),
        recover_worker_slot=_forbidden("recover_worker_slot"),
    )
    return deps, attempts, completed


def _seed(track_info, task_id="t1", batch_id="b1"):
    download_tasks[task_id] = {"status": "pending", "track_info": track_info, "batch_id": batch_id}
    download_batches[batch_id] = {"profile_id": 1}


def _track(info):
    return SpotifyTrack(id=info["id"], name=info["name"], artists=["Artist A"], album="Song",
                        duration_ms=info.get("duration_ms") or 0, popularity=0)


def _run(info, deps, db=None):
    sct.run_exact_soundcloud_task("t1", "b1", info, _track(info), deps, db=db)


def test_success_starts_only_the_id_matched_upload():
    sc = FakeSC([_result(sc_id="999"), _result()])
    deps, attempts, completed = _deps(sc)
    info = _track_info(_block())
    _seed(info)
    tw.download_track_worker("t1", "b1", deps)
    assert sc.calls == [PERMALINK]
    assert len(attempts) == 1
    (_tid, candidates, _kw) = attempts[0]
    assert [c._source_metadata["track_id"] for c in candidates] == ["123"]
    assert candidates[0].confidence == 1.0
    assert download_tasks["t1"]["cached_candidates"] == candidates
    assert download_tasks["t1"]["current_source"] == "soundcloud"
    assert completed == []  # the monitor finishes it


def test_a_different_id_fails_without_an_attempt():
    deps, attempts, completed = _deps(FakeSC([_result(sc_id="456")]))
    _seed(_track_info(_block()))
    _run(_track_info(_block()), deps)
    assert attempts == []
    assert download_tasks["t1"]["status"] == "failed"
    assert completed == [("b1", "t1", False)]


def test_an_empty_resolve_says_unavailable():
    deps, attempts, completed = _deps(FakeSC([]))
    _seed(_track_info(_block()))
    _run(_track_info(_block()), deps)
    assert "unavailable" in download_tasks["t1"]["error_message"]
    assert attempts == [] and completed == [("b1", "t1", False)]


# formats as yt-dlp's SoundCloud extractor lists them (a full extraction):
# format_id is protocol + preset, '_preview' and preference -10 when the
# transcoding is snipped; 'download' is the uploader's original file. the
# entry's duration is the track's full length either way.
_FULL = [{"format_id": "http_mp3_1_0", "ext": "mp3", "abr": 128, "preference": None},
         {"format_id": "hls_mp3_0_0", "ext": "mp3", "abr": 128, "preference": None},
         {"format_id": "hls_opus_0_0", "ext": "opus", "abr": 64, "preference": None}]
_PREVIEW = [{**f, "format_id": f["format_id"] + "_preview", "preference": -10} for f in _FULL]
_DOWNLOAD = {"format_id": "download", "ext": "wav", "filesize": 40_000_000, "quality": 10}


def _resolved(formats, duration=201.0, url=PERMALINK):
    """a resolve result through the real client conversion."""
    from core.soundcloud_client import SoundcloudClient
    client = SoundcloudClient(download_path="/nonexistent-sc")
    return client._sc_to_track_result({
        "id": "123", "title": "Artist A - Song", "uploader": "djuser", "url": url,
        "webpage_url": url, "duration": duration, "formats": formats})


def test_the_client_reads_the_preview_formats():
    meta = lambda r: r._source_metadata["preview_only"]  # noqa: E731
    assert meta(_resolved(_PREVIEW)) is True
    assert meta(_resolved(_FULL)) is False
    assert meta(_resolved(_FULL + _PREVIEW)) is False
    assert meta(_resolved([_DOWNLOAD, *_PREVIEW])) is False   # the original is the full file
    assert meta(_resolved([])) is None                        # a flat entry can't say
    # the duration is the full length even when only the snippet streams
    assert _resolved(_PREVIEW).duration == 201000


def test_a_preview_only_upload_fails():
    deps, attempts, completed = _deps(FakeSC([_resolved(_PREVIEW)]))
    _seed(_track_info(_block()))
    _run(_track_info(_block()), deps)
    assert download_tasks["t1"]["error_message"] == sct.PREVIEW_ONLY
    assert attempts == [] and completed == [("b1", "t1", False)]


@pytest.mark.parametrize("formats", [_FULL, _FULL + _PREVIEW, [_DOWNLOAD, *_PREVIEW], []],
                         ids=["full", "full+preview", "original+preview", "no-formats"])
def test_an_upload_with_a_full_stream_proceeds(formats):
    deps, attempts, completed = _deps(FakeSC([_resolved(formats)]))
    _seed(_track_info(_block()))
    _run(_track_info(_block()), deps)
    assert len(attempts) == 1 and completed == []


def test_a_genuinely_short_upload_proceeds():
    deps, attempts, completed = _deps(FakeSC([_resolved(_FULL, duration=25.0)]))
    info = _track_info(_block(duration_ms=25000), duration_ms=25000)
    _seed(info)
    _run(info, deps)
    assert len(attempts) == 1 and completed == []


def test_a_disabled_source_never_resolves():
    sc = FakeSC([_result()])
    deps, attempts, completed = _deps(sc, mode="soulseek")
    _seed(_track_info(_block()))
    _run(_track_info(_block()), deps)
    assert sc.calls == [] and attempts == []
    assert download_tasks["t1"]["error_message"] == sct.SOURCE_DISABLED
    assert completed == [("b1", "t1", False)]


def test_the_private_link_is_what_downloads():
    # the resolve's permalink dropped the token: the download still uses it
    sc = FakeSC([_result(url=PERMALINK)])
    deps, attempts, _ = _deps(sc)
    info = _track_info(_block(request_url=PRIVATE, request_url_norm=PRIVATE))
    _seed(info)
    _run(info, deps)
    assert sc.calls == [PRIVATE]
    candidate = attempts[0][1][0]
    assert candidate.filename.split("||")[1] == PRIVATE


SHORT = "https://on.soundcloud.com/AbC123"


@pytest.mark.parametrize("request_url,want", [
    (PRIVATE, PRIVATE), (SHORT, SHORT), (PERMALINK, PERMALINK)], ids=["share", "short", "public"])
def test_the_download_url_keeps_what_can_carry_the_token(request_url, want):
    from core.wishlist import soundcloud_exact as exact
    assert exact.download_url(_block(request_url=request_url, request_url_norm=request_url)) == want


def test_a_private_track_added_by_its_short_link_downloads_through_it():
    # added via on.soundcloud.com: the stored permalink has no token, and the
    # resolve's permalink may not either
    sc = FakeSC([_result(url=PERMALINK)])
    deps, attempts, completed = _deps(sc)
    info = _track_info(_block(request_url=SHORT, request_url_norm=SHORT))
    _seed(info)
    _run(info, deps)
    assert sc.calls == [SHORT]
    candidate = attempts[0][1][0]
    assert candidate.filename.split("||")[1] == SHORT
    assert completed == []


def test_a_public_permalink_is_left_as_resolved():
    sc = FakeSC([_result(url=PERMALINK)])
    deps, attempts, _ = _deps(sc)
    info = _track_info(_block())
    _seed(info)
    _run(info, deps)
    assert sc.calls == [PERMALINK]
    assert attempts[0][1][0].filename.split("||")[1] == PERMALINK


def test_a_start_failure_fails_with_the_reason():
    deps, attempts, completed = _deps(FakeSC([_result()]), started=False)
    _seed(_track_info(_block()))
    _run(_track_info(_block()), deps)
    assert len(attempts) == 1
    assert "peer said no" in download_tasks["t1"]["error_message"]
    assert completed == [("b1", "t1", False)]


def test_a_cancel_before_entry_is_not_resurrected_and_frees_the_slot():
    """a cancel landing between the worker's checkpoint 1 and the hook: the
    status stays 'cancelled' and a legacy batch slot is freed, the way the
    worker's checkpoint 1 frees it."""
    sc = FakeSC([_result()])
    deps, attempts, completed = _deps(sc)
    _seed(_track_info(_block()))
    download_tasks["t1"]["status"] = "cancelled"
    _run(_track_info(_block()), deps)
    assert download_tasks["t1"]["status"] == "cancelled"
    assert "current_source" not in download_tasks["t1"]
    assert sc.calls == [] and attempts == []
    assert completed == [("b1", "t1", False)]


def test_a_cancelled_v2_task_leaves_the_slot_to_the_atomic_cancel():
    deps, attempts, completed = _deps(FakeSC([_result()]))
    _seed(_track_info(_block()))
    download_tasks["t1"].update(status="cancelled", playlist_id="pl1")
    _run(_track_info(_block()), deps)
    assert download_tasks["t1"]["status"] == "cancelled"
    assert attempts == [] and completed == []


def test_a_deleted_task_just_stops():
    deps, attempts, completed = _deps(FakeSC([_result()]))
    _run(_track_info(_block()), deps)
    assert attempts == [] and completed == [] and "t1" not in download_tasks


class _CancellingSC(FakeSC):
    """the user cancels while the resolve is in flight."""

    async def resolve_url(self, url, timeout=None, progress_callback=None):
        download_tasks["t1"]["status"] = "cancelled"
        return await super().resolve_url(url, timeout, progress_callback)


def test_a_cancel_during_the_resolve_exits_through_the_completion_callback():
    sc = _CancellingSC([_result()])
    deps, attempts, completed = _deps(sc)
    _seed(_track_info(_block()))
    _run(_track_info(_block()), deps)
    assert sc.calls == [PERMALINK]
    assert attempts == []
    assert download_tasks["t1"]["status"] == "cancelled"
    assert "cached_candidates" not in download_tasks["t1"]
    assert completed == [("b1", "t1", False)]


def _cancel_after_the_resolve(monkeypatch, **task_fields):
    """the user cancels once the resolve is back and checked, before the
    task gets to fail (here: while the preview filter runs)."""
    import core.downloads.validation as validation

    def cancelling_filter(results, expected):
        download_tasks["t1"].update(status="cancelled", **task_fields)
        return []
    monkeypatch.setattr(validation, "filter_soundcloud_previews", cancelling_filter)


def test_a_cancel_before_the_fail_is_not_overwritten(monkeypatch):
    notified = []
    monkeypatch.setattr(tw, "_notify_acquisition_retry_exhausted", lambda *a: notified.append(a))
    _cancel_after_the_resolve(monkeypatch)
    deps, attempts, completed = _deps(FakeSC([_result()]))
    _seed(_track_info(_block()))
    _run(_track_info(_block()), deps)
    assert download_tasks["t1"]["status"] == "cancelled"
    assert "error_message" not in download_tasks["t1"]
    assert attempts == [] and notified == []
    assert completed == [("b1", "t1", False)]   # the legacy slot, exactly once


def test_a_cancelled_v2_task_before_the_fail_leaves_the_slot_to_the_atomic_cancel(monkeypatch):
    _cancel_after_the_resolve(monkeypatch, playlist_id="pl1")
    deps, attempts, completed = _deps(FakeSC([_result()]))
    _seed(_track_info(_block()))
    _run(_track_info(_block()), deps)
    assert download_tasks["t1"]["status"] == "cancelled"
    assert attempts == [] and completed == []


@pytest.fixture()
def db(tmp_path):
    from database.music_database import MusicDatabase
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _wishlist_row(db, block):
    payload = {"id": "soundcloud_exact_123", "name": "Song", "artists": [{"name": "Artist A"}],
               "album": {"name": "Song"}, "soundcloud_exact": block}
    assert db.add_to_wishlist_detailed(payload, profile_id=1, user_initiated=True)["status"] == "created"


def test_a_lost_block_comes_back_from_the_wishlist_row(db):
    _wishlist_row(db, _block())
    deps, attempts, _ = _deps(FakeSC([_result()]))
    info = _track_info(None)
    _seed(info)
    _run(info, deps, db=db)
    assert len(attempts) == 1


def test_then_from_the_playlist_row(db):
    from core.playlists import user_playlists
    pid = user_playlists.create_playlist(db, 1, "Flips")
    user_playlists.add_exact_track(db, db.get_mirrored_playlist(pid), {
        "id": "soundcloud_exact_123", "name": "Song", "artists": [{"name": "Artist A"}],
        "album": {"name": "Song", "images": []}, "soundcloud_exact": _block()})
    deps, attempts, _ = _deps(FakeSC([_result()]))
    info = _track_info(None)
    _seed(info)
    _run(info, deps, db=db)
    assert len(attempts) == 1


def test_no_block_anywhere_fails(db):
    deps, attempts, completed = _deps(FakeSC([_result()]))
    info = _track_info(None)
    _seed(info)
    _run(info, deps, db=db)
    assert attempts == []
    assert download_tasks["t1"]["error_message"] == sct.MISSING_BLOCK
    assert completed == [("b1", "t1", False)]


def test_the_worker_hands_exact_tracks_to_the_branch(monkeypatch):
    seen = []
    monkeypatch.setattr(sct, "run_exact_soundcloud_task",
                        lambda task_id, batch_id, data, track, deps: seen.append((task_id, data["id"], track.name)))
    deps, _, _ = _deps(FakeSC([]))
    info = _track_info(_block())
    _seed(info)
    tw.download_track_worker("t1", "b1", deps)
    assert seen == [("t1", "soundcloud_exact_123", "Song")]


def test_an_exact_upload_is_never_the_catalogue_recording():
    catalogue = {"id": "sp1", "source": "spotify", "name": "Song", "artists": [{"name": "Artist A"}],
                 "album": {"name": "Song"}, "duration_ms": 201000}
    mine = {**catalogue, "id": "soundcloud_exact_123", "source": "soundcloud_exact"}
    assert tw._same_recording(mine, catalogue) is False
    assert tw._same_recording(catalogue, mine) is False
    plain = {k: v for k, v in catalogue.items() if k not in ("id", "source")}
    assert tw._same_recording(mine, plain) is False
    assert tw._same_recording(mine, dict(mine)) is True
    assert tw._same_recording(mine, {**mine, "id": "soundcloud_exact_999"}) is False


def _owned_upload_db(tmp_path):
    """a real database where the upload's import landed: its library file on
    disk, its lib2 track and file rows, and the id match the import records
    (wishlist/resolution._record_exact_match)."""
    from database.music_database import MusicDatabase
    from tests import lib2_seed
    db = MusicDatabase(database_path=str(tmp_path / "music.db"))
    path = tmp_path / "lib" / "Song [123].mp3"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    with db._get_connection() as conn:
        lib_id = lib2_seed.track(conn, "Artist A", "Song", "Song", path=str(path))
        conn.commit()
    db.save_manual_library_match(1, "soundcloud_exact", "soundcloud_exact_123", str(lib_id),
                                 library_file_path=str(path))
    return db


def test_a_playlist_run_never_finds_an_exact_upload_by_its_names(monkeypatch, tmp_path):
    """a playlist download-missing run (not a forced wishlist batch) matches
    the library by title and artist; that must not count the catalogue copy
    as this upload, or clear its wishlist row."""
    from tests.downloads.test_downloads_master import _FakeDB, _build_deps, _seed_batch
    from core.downloads import master as mw

    owned = _owned_upload_db(tmp_path)   # built before the name is patched
    db = _FakeDB(found_tracks={("song", "artist a"): 1.0})
    monkeypatch.setattr("database.music_database.MusicDatabase", lambda: db)
    removed = []
    _seed_batch("B1", profile_id=1)
    deps = _build_deps(wishlist_remove=lambda td: removed.append(td.get("id")))
    tracks = [{"id": "soundcloud_exact_123", "name": "Song", "artists": ["Artist A"]},
              {"id": "sp1", "name": "Song", "artists": ["Artist A"]}]
    mw.run_full_missing_tracks_process("B1", "P1", tracks, deps)
    found = {r["track"]["id"]: r["found"] for r in download_batches["B1"]["analysis_results"]}
    assert found == {"soundcloud_exact_123": False, "sp1": True}
    assert removed == ["sp1"]

    # its own id match still counts
    download_batches.clear()
    monkeypatch.setattr("database.music_database.MusicDatabase", lambda: owned)
    _seed_batch("B2", profile_id=1)
    mw.run_full_missing_tracks_process("B2", "P1", tracks[:1], deps)
    assert download_batches["B2"]["analysis_results"][0]["found"] is True


def test_a_playlist_folder_file_with_its_names_is_not_the_upload(monkeypatch):
    """playlist-folder mode decides existence by an "Artist - Title" file; an
    exact upload never counts as on disk that way, and its row is not cleared."""
    from tests.downloads.test_downloads_master import _FakeDB, _build_deps, _seed_batch
    from core.downloads import master as mw
    from core.downloads import playlist_folder

    db = _FakeDB()
    monkeypatch.setattr("database.music_database.MusicDatabase", lambda: db)
    monkeypatch.setattr(playlist_folder, "resolve_playlist_folder_mode_for_batch",
                        lambda *a, **k: (True, "My Playlist"))
    monkeypatch.setattr(playlist_folder, "track_exists_in_playlist_folder_from_track_data",
                        lambda *a, **k: True)
    removed = []
    _seed_batch("B1", profile_id=1)
    deps = _build_deps(wishlist_remove=lambda td: removed.append(td.get("id")))
    tracks = [{"id": "soundcloud_exact_123", "name": "Song", "artists": ["Artist A"]},
              {"id": "sp1", "name": "Song", "artists": ["Artist A"]}]
    mw.run_full_missing_tracks_process("B1", "P1", tracks, deps)
    results = {r["track"]["id"]: r for r in download_batches["B1"]["analysis_results"]}
    assert results["soundcloud_exact_123"]["found"] is False
    assert results["sp1"]["found"] is True
    assert results["sp1"]["match_reason"] == "playlist_folder_file"
    assert removed == ["sp1"]


def test_removal_by_metadata_refuses_an_exact_track():
    """the by-metadata remover's direct-id leg would delete the exact row on a
    library match's word; exact rows complete through their own id only."""
    from core.wishlist import resolution

    class _Service:
        def __init__(self):
            self.removed = []

        def mark_track_download_result(self, track_id, success=True, audit=None, **_kw):
            self.removed.append(track_id)
            return True

        def get_wishlist_tracks_for_download(self, *a, **k):
            return []

    service = _Service()
    exact = {"id": "soundcloud_exact_123", "name": "Song", "artists": [{"name": "Artist A"}]}
    assert resolution.check_and_remove_track_from_wishlist_by_metadata(
        exact, wishlist_service=service) is False
    assert service.removed == []
    # a catalogue track still goes by its id
    assert resolution.check_and_remove_track_from_wishlist_by_metadata(
        {**exact, "id": "sp1"}, wishlist_service=service) is True
    assert service.removed == ["sp1"]


def test_ownership_preview_never_finds_an_exact_upload_by_its_names(tmp_path):
    from tests.downloads.test_downloads_master import _FakeDB
    from core.downloads import master as mw

    db = _FakeDB(found_tracks={("song", "artist a"): 1.0})
    tracks = [{"id": "soundcloud_exact_123", "name": "Song", "artists": ["Artist A"]},
              {"id": "sp1", "name": "Song", "artists": ["Artist A"]}]

    def preview(db, tracks):
        return [r["found"] for r in mw.preview_ownership(
            db, tracks, album_context=None, artist_context=None, is_album=False,
            profile_id=1, source="spotify", allow_duplicates=True)]

    assert preview(db, tracks) == [False, True]
    # its own id match still counts
    assert preview(_owned_upload_db(tmp_path), tracks[:1]) == [True]


def test_ownership_preview_reads_the_upload_history(monkeypatch):
    from tests.downloads.test_downloads_master import _FakeDB
    from core.downloads import master as mw
    from core.wishlist import soundcloud_exact as exact

    seen = []
    monkeypatch.setattr(exact, "_acquired",
                        lambda db, track_id, sc_id, pid: seen.append((track_id, sc_id, pid)) or True)
    results = mw.preview_ownership(
        _FakeDB(), [{"id": "soundcloud_exact_123", "name": "Song", "artists": ["Artist A"]}],
        album_context=None, artist_context=None, is_album=False,
        profile_id=2, source="spotify", allow_duplicates=True)
    assert results[0]["found"] is True
    assert seen == [("soundcloud_exact_123", "123", 2)]


def test_payload_json_strings_are_read():
    info = {"id": "soundcloud_exact_123", "spotify_data": json.dumps({"soundcloud_exact": _block()})}
    from core.wishlist.soundcloud_exact import exact_source_for
    assert exact_source_for(info)["sc_id"] == "123"
