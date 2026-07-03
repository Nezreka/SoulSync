"""Regression: the database-only sync matcher must accept candidate_pool.

sync_service calls _find_track_in_media_server(track, candidate_pool=...). When
no media server is connected, discovery/sync patches in a database-only matcher.
That override dropped the candidate_pool kwarg, so every Spotify sync failed with
"unexpected keyword argument 'candidate_pool'". These pin the contract.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import core.discovery.sync as sync_mod


def test_matcher_signature_accepts_candidate_pool():
    sig = inspect.signature(sync_mod._database_only_find_track)
    assert 'candidate_pool' in sig.parameters


def _run(track, **kw):
    fake_db = MagicMock()
    fake_db.read_sync_match_cache.return_value = None
    fake_db.find_manual_library_match_by_source_track_id.return_value = None
    fake_db.check_track_exists.return_value = (None, 0.0)
    fake_cm = MagicMock()
    fake_cm.get_active_media_server.return_value = "plex"
    with patch("database.music_database.MusicDatabase", return_value=fake_db), \
         patch("config.settings.config_manager", fake_cm), \
         patch("core.artists.map.get_current_profile_id", return_value=1):
        return asyncio.run(sync_mod._database_only_find_track(track, **kw))


def test_called_with_candidate_pool_no_match():
    track = SimpleNamespace(name="Song", artists=["Artist"], id="sp1")
    # The exact call sync_service makes — must not raise TypeError.
    assert _run(track, candidate_pool={}) == (None, 0.0)


def test_returns_match_when_db_has_it():
    track = SimpleNamespace(name="HUMBLE.", artists=["Kendrick Lamar"], id="sp2")
    fake_db = MagicMock()
    fake_db.read_sync_match_cache.return_value = None
    fake_db.find_manual_library_match_by_source_track_id.return_value = None
    fake_db.check_track_exists.return_value = (SimpleNamespace(id="t1", title="HUMBLE."), 0.95)
    fake_cm = MagicMock()
    fake_cm.get_active_media_server.return_value = "plex"
    with patch("database.music_database.MusicDatabase", return_value=fake_db), \
         patch("config.settings.config_manager", fake_cm), \
         patch("core.artists.map.get_current_profile_id", return_value=1):
        match, conf = asyncio.run(sync_mod._database_only_find_track(track, candidate_pool={}))
    assert conf == 0.95 and match.id == "t1"


# ── durable manual match (#787) survives a rescan that wipes sync_match_cache ──
# (#895 follow-up: Find & Add was forgotten on the next auto-sync after a library
# scan, because the matcher only consulted the volatile cache.)

def _run_with_db(track, fake_db):
    fake_cm = MagicMock()
    fake_cm.get_active_media_server.return_value = "plex"
    with patch("database.music_database.MusicDatabase", return_value=fake_db), \
         patch("config.settings.config_manager", fake_cm), \
         patch("core.artists.map.get_current_profile_id", return_value=1):
        return asyncio.run(sync_mod._database_only_find_track(track, candidate_pool={}))


def test_durable_match_used_when_volatile_cache_is_empty():
    track = SimpleNamespace(name="Valió la Pena - Salsa Version", artists=["Marc Anthony"], id="sp16")
    dt = SimpleNamespace(id="t99", title="Valió la Pena (Salsa Version)")
    db = MagicMock()
    db.read_sync_match_cache.return_value = None                 # cache wiped by a rescan
    db.check_track_exists.return_value = (None, 0.0)             # fuzzy would FAIL
    db.find_manual_library_match_by_source_track_id.return_value = {
        "library_track_id": "t99", "library_file_path": "/m/x.flac"}
    db.get_track_by_id.side_effect = lambda i: dt if str(i) == "t99" else None
    match, conf = _run_with_db(track, db)
    assert conf == 1.0 and match.id == "t99"                    # manual pick honored, not re-matched


def test_durable_match_self_heals_a_stale_library_id():
    track = SimpleNamespace(name="X", artists=["Y"], id="sp1")
    dt = SimpleNamespace(id="newid", title="X")
    db = MagicMock()
    db.read_sync_match_cache.return_value = None
    db.check_track_exists.return_value = (None, 0.0)
    db.find_manual_library_match_by_source_track_id.return_value = {
        "library_track_id": "staleid", "library_file_path": "/m/x.flac"}
    db.get_track_by_id.side_effect = lambda i: dt if str(i) == "newid" else None   # stale id misses
    db.find_track_id_by_file_path.return_value = "newid"         # re-resolve via path
    match, conf = _run_with_db(track, db)
    assert conf == 1.0 and match.id == "newid"
    db.find_track_id_by_file_path.assert_called_once_with("/m/x.flac")


def test_no_durable_match_falls_through_to_fuzzy():
    track = SimpleNamespace(name="X", artists=["Y"], id="sp1")
    db = MagicMock()
    db.read_sync_match_cache.return_value = None
    db.find_manual_library_match_by_source_track_id.return_value = None
    db.check_track_exists.return_value = (None, 0.0)
    assert _run_with_db(track, db) == (None, 0.0)
