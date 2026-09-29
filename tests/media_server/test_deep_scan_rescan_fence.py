"""Deep-scan stale-track removal must never run on listings pulled while the
media server itself is mid-rescan — for EVERY server type whose client can
report its scan state, not just Navidrome.

PR #1346 fenced the mid-rescan hazard for Navidrome only
(``server_type == "navidrome"``), but Plex and Jellyfin ship the same
``is_library_scanning()`` probe. A weekly deep scan racing the server's own
rescan sees HTTP-200-but-transiently-incomplete listings: every partial
answer looks "fully trusted", the 50% guard is bypassed, and live tracks are
deleted as stale — the exact 2500-track shape from Specialmed's report.

The probe must also fail CLOSED: if ``is_library_scanning()`` raises while
the stale set is large, deletion must be skipped, not attempted.
"""

from __future__ import annotations

import pytest

import core.database_update_worker as duw
from core.database_update_worker import DatabaseUpdateWorker


class _FakeMediaClient:
    """Plex/Jellyfin-shaped client: mid-rescan, incomplete listings, and a
    working is_library_scanning() probe."""

    def __init__(self, scanning: bool = True, probe_raises: bool = False):
        self._scanning = scanning
        self._probe_raises = probe_raises
        self.last_api_error = None

    def ensure_connection(self):
        return True

    def get_all_artists(self):
        return [object()]  # one artist; its listing is transiently incomplete

    def is_library_scanning(self):
        if self._probe_raises:
            raise RuntimeError("probe blew up mid-run")
        return self._scanning


class _FakeDB:
    def __init__(self):
        self.deleted = None

    def get_all_track_ids_for_server(self, server_type, owner_profile_id=None):
        return {f"track-{i}" for i in range(1000)}

    def get_track_ids_under_scopes(self, server_type, artist_ids, album_ids):
        return set()

    def delete_stale_tracks(self, stale, server_type):
        self.deleted = (set(stale), server_type)
        return len(stale)

    def cleanup_orphaned_records(self, **kwargs):
        return {}

    def ensure_norm_backfilled(self):
        pass


def _run_deep_scan(server_type, client, monkeypatch):
    """Run the REAL Phase-3 decision code in DatabaseUpdateWorker.run_deep_scan
    with stubbed network/database edges. Returns the set of deleted tracks."""
    fake_db = _FakeDB()
    monkeypatch.setattr(duw, "get_database", lambda path: fake_db)

    worker = DatabaseUpdateWorker(client, server_type=server_type)
    worker._clear_media_cache = lambda when: None
    worker._get_all_artists = lambda: ([object()], setattr(worker, "_artists_fetch_verified", True))[0]
    # Mid-rescan: only 10 of the 1000 tracks are visible, NO failures recorded —
    # every partial answer looks "fully trusted".
    worker._deep_scan_process_all_artists = lambda artists, seen: seen.update(
        f"track-{i}" for i in range(10))
    worker._repair_navidrome_identities = lambda: True
    worker._absorb_superseded_tracks = lambda: 0
    worker.failed_operations = 0
    worker.should_stop = False

    worker.run_deep_scan()

    deleted, _server = fake_db.deleted or (set(), None)
    return deleted


@pytest.mark.parametrize("server_type", ["plex", "jellyfin"])
def test_midrescan_skips_stale_removal_for_all_probed_server_types(server_type, monkeypatch):
    """REGRESSION (C1): with the server mid-rescan, the 990 unseen tracks are
    unscanned, not gone — for Plex and Jellyfin exactly as for Navidrome."""
    deleted = _run_deep_scan(server_type, _FakeMediaClient(scanning=True), monkeypatch)
    assert deleted == set(), (
        f"server_type={server_type}: deep scan deleted {len(deleted)} tracks "
        "while the server was mid-rescan"
    )


def test_navidrome_midrescan_still_skips_stale_removal(monkeypatch):
    """Control: the #1346 Navidrome fence keeps working."""
    deleted = _run_deep_scan("navidrome", _FakeMediaClient(scanning=True), monkeypatch)
    assert deleted == set()


def test_quiet_server_still_deletes_truly_stale_tracks(monkeypatch):
    """Control: when the probe says the server is NOT scanning, a fully
    trusted scan may still remove genuinely stale tracks (no over-fencing)."""
    deleted = _run_deep_scan("plex", _FakeMediaClient(scanning=False), monkeypatch)
    assert len(deleted) == 990


def test_probe_error_fails_closed_when_stale_set_is_large(monkeypatch):
    """REGRESSION (C1): a probe that raises mid-run must not let a
    mass-deletion proceed — unknown scan state + 990 unseen tracks = skip."""
    deleted = _run_deep_scan(
        "plex", _FakeMediaClient(scanning=True, probe_raises=True), monkeypatch
    )
    assert deleted == set(), (
        f"probe raised but deep scan deleted {len(deleted)} tracks anyway"
    )


def test_probe_error_with_small_stale_set_still_deletes(monkeypatch):
    """The fail-closed rule only kicks in for mass deletions: a handful of
    unseen tracks with a broken probe still goes through the normal path."""
    fake_db = _FakeDB()
    # shrink the DB so only a few tracks are stale
    fake_db.get_all_track_ids_for_server = lambda server_type, owner_profile_id=None: {
        f"track-{i}" for i in range(10)
    }
    monkeypatch.setattr(duw, "get_database", lambda path: fake_db)

    client = _FakeMediaClient(scanning=False, probe_raises=True)
    worker = DatabaseUpdateWorker(client, server_type="plex")
    worker._clear_media_cache = lambda when: None
    worker._get_all_artists = lambda: ([object()], setattr(worker, "_artists_fetch_verified", True))[0]
    worker._deep_scan_process_all_artists = lambda artists, seen: seen.update(
        f"track-{i}" for i in range(7))  # 3 unseen of 10
    worker._repair_navidrome_identities = lambda: True
    worker._absorb_superseded_tracks = lambda: 0
    worker.failed_operations = 0
    worker.should_stop = False
    worker.run_deep_scan()

    deleted, _server = fake_db.deleted or (set(), None)
    assert len(deleted) == 3
