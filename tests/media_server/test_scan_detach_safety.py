"""Transient empty server inventories must not detach live catalogue mappings."""

from __future__ import annotations

import pytest

import core.database_update_worker as duw
from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_album, seed_artist, seed_track


class _Server:
    last_api_error = None

    def __init__(self, *, scanning=False, probe_raises=False):
        self.scanning = scanning
        self.probe_raises = probe_raises

    def ensure_connection(self):
        return True

    def clear_cache(self):
        pass

    def is_library_scanning(self):
        if self.probe_raises:
            raise RuntimeError("scan status unavailable")
        return self.scanning

    def get_all_artist_ids(self):
        self.last_fetch_failed = False
        return set()

    def get_all_album_ids(self):
        self.last_fetch_failed = False
        return set()


def _worker(tmp_path, monkeypatch, *, scanning=False, probe_raises=False):
    db = MusicDatabase(str(tmp_path / "music.db"))
    with db._get_connection() as conn:
        artist = seed_artist(conn, name="Artist", server_id="artist-one", server_source="navidrome")
        album = seed_album(conn, title="Album", artist_id=artist,
                           server_id="album-one", server_source="navidrome")
        seed_track(conn, title="Song", album_id=album, artist_id=artist,
                   server_id="track-one", server_source="navidrome",
                   file_path="/library/song.flac")
        conn.commit()
    monkeypatch.setattr(duw, "get_database", lambda path: db)
    monkeypatch.setattr("core.library2.migration_gate.migration_required", lambda database: False)
    worker = duw.DatabaseUpdateWorker(
        _Server(scanning=scanning, probe_raises=probe_raises),
        database_path=db.database_path, server_type="navidrome", full_refresh=True)
    worker._get_all_artists = lambda: ([], setattr(worker, "_artists_fetch_verified", True))[0]
    worker._process_all_artists = lambda artists: None
    worker._deep_scan_process_all_artists = lambda artists, seen: None
    worker._repair_navidrome_identities = lambda: True
    worker._clear_phantom_artist_thumbs = lambda: None
    return db, worker


@pytest.mark.parametrize("scanning,probe_raises", [(True, False), (False, True)])
def test_full_refresh_keeps_mappings_when_empty_inventory_cannot_be_trusted(
        tmp_path, monkeypatch, scanning, probe_raises):
    db, worker = _worker(tmp_path, monkeypatch, scanning=scanning, probe_raises=probe_raises)
    worker.run()
    assert db.get_all_track_ids_for_server("navidrome") == {"track-one"}


@pytest.mark.parametrize("unsafe_reason", ["stopped", "identity_repair_failed"])
@pytest.mark.parametrize("mode", ["deep", "full"])
def test_empty_inventory_keeps_mappings_when_scan_did_not_finish_safely(
        tmp_path, monkeypatch, unsafe_reason, mode):
    db, worker = _worker(tmp_path, monkeypatch)
    if unsafe_reason == "stopped":
        worker._deep_scan_process_all_artists = lambda artists, seen: setattr(worker, "should_stop", True)
        worker._process_all_artists = lambda artists: setattr(worker, "should_stop", True)
    else:
        worker._repair_navidrome_identities = lambda: False
    if mode == "deep":
        worker.run_deep_scan()
    else:
        worker.run()
    assert db.get_all_track_ids_for_server("navidrome") == {"track-one"}


@pytest.mark.parametrize("mode", ["deep", "full"])
def test_inventory_read_during_rescan_stays_untrusted_after_server_finishes(
        tmp_path, monkeypatch, mode):
    db, worker = _worker(tmp_path, monkeypatch, scanning=True)

    def empty_inventory():
        worker._artists_fetch_verified = True
        worker.media_client.scanning = False
        return []

    worker._get_all_artists = empty_inventory
    if mode == "deep":
        worker.run_deep_scan()
    else:
        worker.run()
    assert db.get_all_track_ids_for_server("navidrome") == {"track-one"}


@pytest.mark.parametrize("scanning,probe_raises", [(True, False), (False, True)])
def test_removal_detection_keeps_mappings_when_server_scan_is_active_or_unknown(
        tmp_path, monkeypatch, scanning, probe_raises):
    db, worker = _worker(tmp_path, monkeypatch, scanning=scanning, probe_raises=probe_raises)
    worker.database = db
    worker._detect_and_remove_stale_content()
    assert db.get_all_track_ids_for_server("navidrome") == {"track-one"}


def test_removal_detection_rechecks_rescan_after_fetching_verified_empty_inventory(
        tmp_path, monkeypatch):
    db, worker = _worker(tmp_path, monkeypatch)
    worker.database = db

    def empty_artist_ids():
        worker.media_client.last_fetch_failed = False
        worker.media_client.scanning = True
        return set()

    worker.media_client.get_all_artist_ids = empty_artist_ids
    worker._detect_and_remove_stale_content()
    assert db.get_all_track_ids_for_server("navidrome") == {"track-one"}


@pytest.mark.parametrize("mode", ["full", "deep", "removal"])
def test_verified_empty_quiet_server_still_detaches_old_mappings(tmp_path, monkeypatch, mode):
    db, worker = _worker(tmp_path, monkeypatch)
    if mode == "full":
        worker.run()
    elif mode == "deep":
        worker.run_deep_scan()
    else:
        worker.database = db
        worker._detect_and_remove_stale_content()
    assert db.get_all_track_ids_for_server("navidrome") == set()
