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
    fake_db.check_track_exists.return_value = (None, 0.0)
    fake_cm = MagicMock()
    fake_cm.get_active_media_server.return_value = "plex"
    with patch("database.music_database.MusicDatabase", return_value=fake_db), \
         patch("config.settings.config_manager", fake_cm):
        return asyncio.run(sync_mod._database_only_find_track(track, **kw))


def test_called_with_candidate_pool_no_match():
    track = SimpleNamespace(name="Song", artists=["Artist"], id="sp1")
    # The exact call sync_service makes — must not raise TypeError.
    assert _run(track, candidate_pool={}) == (None, 0.0)


def test_returns_match_when_db_has_it():
    track = SimpleNamespace(name="HUMBLE.", artists=["Kendrick Lamar"], id="sp2")
    fake_db = MagicMock()
    fake_db.read_sync_match_cache.return_value = None
    fake_db.check_track_exists.return_value = (SimpleNamespace(id="t1", title="HUMBLE."), 0.95)
    fake_cm = MagicMock()
    fake_cm.get_active_media_server.return_value = "plex"
    with patch("database.music_database.MusicDatabase", return_value=fake_db), \
         patch("config.settings.config_manager", fake_cm):
        match, conf = asyncio.run(sync_mod._database_only_find_track(track, candidate_pool={}))
    assert conf == 0.95 and match.id == "t1"
