"""two mirrors with the same name that sync as one server account wrote over each
other every sync: the server playlist is found by name. the one that would
collide gets a distinct name; nothing changes for anyone else."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from core.playlists.sync_names import resolve_sync_names, sync_name_for

NAMES = {1: 'BoulderBadgeDad', 2: 'ThomasClan', 3: 'Kids'}


def m(mid, pid, name, source='spotify', custom=None):
    return {'id': mid, 'profile_id': pid, 'name': name, 'source': source, 'custom_name': custom}


def test_no_collision_keeps_every_name():
    out = resolve_sync_names([m(1, 1, 'Release Radar'), m(2, 2, 'Chill')],
                             {1: 'shared', 2: 'shared'}, NAMES)
    assert out == {1: 'Release Radar', 2: 'Chill'}


def test_a_profile_on_the_shared_account_gets_its_name_on_the_end():
    out = resolve_sync_names([m(5, 2, 'Release Radar'), m(9, 1, 'release radar')],
                             {1: 'shared', 2: 'shared'}, NAMES)
    assert out == {9: 'release radar', 5: 'Release Radar - ThomasClan'}


def test_profiles_on_their_own_server_users_never_clash():
    out = resolve_sync_names([m(1, 1, 'Discover Weekly'), m(2, 2, 'Discover Weekly')],
                             {1: 'shared', 2: 'user:thomas'}, NAMES)
    assert out == {1: 'Discover Weekly', 2: 'Discover Weekly'}


def test_one_profile_two_sources_the_oldest_keeps_the_name():
    out = resolve_sync_names([m(4, 1, 'Blumple', 'deezer'), m(3, 1, 'Blumple', 'spotify')],
                             {1: 'shared'}, NAMES)
    assert out == {3: 'Blumple', 4: 'Blumple (Deezer)'}


def test_two_non_admins_the_lowest_profile_keeps_the_name():
    out = resolve_sync_names([m(1, 3, 'Bedtime'), m(2, 2, 'Bedtime')],
                             {2: 'shared', 3: 'shared'}, NAMES)
    assert out == {2: 'Bedtime', 1: 'Bedtime - Kids'}


def test_a_rename_counts_not_the_upstream_name():
    out = resolve_sync_names([m(1, 1, 'A', custom='Road Trip'), m(2, 2, 'Road Trip')],
                             {1: 'shared', 2: 'shared'}, NAMES)
    assert out == {1: 'Road Trip', 2: 'Road Trip - ThomasClan'}


def test_names_stay_distinct_even_when_a_suffix_meets_a_real_name():
    out = resolve_sync_names([m(1, 1, 'Blumple'), m(2, 1, 'Blumple', 'deezer'),
                              m(3, 1, 'Blumple (Deezer)')], {1: 'shared'}, NAMES)
    assert len({v.lower() for v in out.values()}) == 3


# ── through the real sync handler and database ───────────────────────────────

@pytest.fixture()
def db(tmp_path):
    from database.music_database import MusicDatabase
    return MusicDatabase(str(tmp_path / 'm.db'))


def _run_sync(db, mirror_id):
    from core.automation.handlers.sync_playlist import auto_sync_playlist

    started, calls = threading.Event(), []

    def _run_sync_task(*a, **k):
        calls.append(a)
        started.set()

    deps = MagicMock()
    deps.get_database.return_value = db
    deps.run_sync_task = _run_sync_task
    deps.load_sync_status_file.return_value = {}
    deps.config_manager.get_active_media_server.return_value = 'navidrome'
    auto_sync_playlist({'playlist_id': str(mirror_id)}, deps)
    assert started.wait(5)
    return calls[0][1]


def _track():
    return [{'track_name': 'Song', 'artist_name': 'Artist', 'source_track_id': 'sp1'}]


def test_the_sync_uses_the_distinct_name(db):
    thomas = db.create_profile('ThomasClan')
    db.mirror_playlist('spotify', 'rr-admin', 'Release Radar', _track(), profile_id=1)
    mine = db.mirror_playlist('spotify', 'rr-thomas', 'Release Radar', _track(), profile_id=thomas)
    assert _run_sync(db, mine) == 'Release Radar - ThomasClan'


def test_a_profile_with_its_own_login_syncs_under_the_plain_name(db):
    thomas = db.create_profile('ThomasClan')
    assert db.set_profile_navidrome_login(thomas, 'thomas', 'pw')
    db.mirror_playlist('spotify', 'rr-admin', 'Release Radar', _track(), profile_id=1)
    mine = db.mirror_playlist('spotify', 'rr-thomas', 'Release Radar', _track(), profile_id=thomas)
    assert _run_sync(db, mine) == 'Release Radar'


def test_a_shared_account_profile_can_see_its_suffixed_playlist(db):
    """the #1414 visibility check must know the final name, or the profile's own
    playlist would vanish from its list."""
    from core.sync.server_playlist_access import own_playlist_names

    thomas = db.create_profile('ThomasClan')
    db.mirror_playlist('spotify', 'rr-admin', 'Release Radar', _track(), profile_id=1)
    db.mirror_playlist('spotify', 'rr-thomas', 'Release Radar', _track(), profile_id=thomas)
    assert 'release radar - thomasclan' in own_playlist_names(db, thomas, 'navidrome')
    assert 'release radar' not in own_playlist_names(db, thomas, 'navidrome')


def test_a_mirror_on_its_own_keeps_its_name(db):
    mid = db.mirror_playlist('deezer', 'b1', 'Blumple', _track(), profile_id=1)
    assert sync_name_for(db, 'plex', {'id': mid, 'name': 'Blumple'}) == 'Blumple'
