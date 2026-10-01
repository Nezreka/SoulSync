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


# ── #1420: a deleted mirror doesn't hand its server playlist to another ──────

def test_a_kept_name_stays_when_its_sibling_is_deleted():
    kept = m(4, 1, 'Blumple', 'deezer')
    kept.update(server_sync_name='Blumple (Deezer)', server_sync_base='shared|blumple')
    assert resolve_sync_names([kept], {1: 'shared'}, NAMES) == {4: 'Blumple (Deezer)'}


def test_a_kept_name_is_dropped_when_the_mirror_is_renamed():
    renamed = m(4, 1, 'Blumple', 'deezer', custom='Road Trip')
    renamed.update(server_sync_name='Blumple (Deezer)', server_sync_base='shared|blumple')
    assert resolve_sync_names([renamed], {1: 'shared'}, NAMES) == {4: 'Road Trip'}


def test_a_kept_name_is_dropped_when_the_profile_gets_its_own_login():
    mine = m(5, 2, 'Release Radar')
    mine.update(server_sync_name='Release Radar - ThomasClan', server_sync_base='shared|release radar')
    assert resolve_sync_names([mine, m(9, 1, 'Release Radar')],
                              {1: 'shared', 2: 'user:thomas'}, NAMES) == {
        5: 'Release Radar', 9: 'Release Radar'}


def test_a_new_mirror_never_takes_a_kept_name():
    """profile 2 synced "Bedtime" on its own first; the admin's later one
    must not take it over."""
    theirs = m(1, 2, 'Bedtime')
    theirs.update(server_sync_name='Bedtime', server_sync_base='shared|bedtime')
    out = resolve_sync_names([theirs, m(2, 1, 'Bedtime')], {1: 'shared', 2: 'shared'}, NAMES)
    assert out[1] == 'Bedtime'
    assert out[2].lower() != 'bedtime'


def test_deleting_and_remaking_a_mirror_reconnects_both(db):
    """the reporter's case: two "Blumple" mirrors, the plain one deleted and made
    again. the survivor stays on its own server playlist and the new one lands
    back on the deleted one's."""
    sp = db.mirror_playlist('spotify', 'b-sp', 'Blumple', _track(), profile_id=1)
    dz = db.mirror_playlist('deezer', 'b-dz', 'Blumple', _track(), profile_id=1)
    assert _run_sync(db, sp) == 'Blumple'
    assert _run_sync(db, dz) == 'Blumple (Deezer)'

    assert db.delete_mirrored_playlist(sp)
    assert _run_sync(db, dz) == 'Blumple (Deezer)'

    again = db.mirror_playlist('spotify', 'b-sp', 'Blumple', _track(), profile_id=1)
    assert again != sp
    assert _run_sync(db, again) == 'Blumple'
    assert _run_sync(db, dz) == 'Blumple (Deezer)'


def test_the_discovery_modal_sync_uses_the_same_name(db, monkeypatch):
    """the mirror card's "Sync This Playlist" synced under the upstream name, so
    it wrote over the other mirror's playlist and skipped renames."""
    from flask import Flask
    import api.source_playlists as sp_api

    db.mirror_playlist('spotify', 'b-sp', 'Blumple', _track(), profile_id=1)
    dz = db.mirror_playlist('deezer', 'b-dz', 'Blumple', _track(), profile_id=1)
    cm = MagicMock()
    cm.get_active_media_server.return_value = 'navidrome'
    monkeypatch.setattr(sp_api, 'get_database', lambda: db)
    monkeypatch.setattr(sp_api, 'config_manager', cm)
    monkeypatch.setattr(sp_api, '_get_automation_deps', MagicMock(side_effect=RuntimeError('no engine')))
    seen = {}
    monkeypatch.setattr(sp_api, '_start_source_sync',
                        lambda states, key, **kw: seen.update(kw) or ('ok', 200))

    with Flask(__name__).test_request_context():
        sp_api.start_youtube_sync(f'mirrored_{dz}')
    assert seen['name_getter']({'playlist': {'name': 'Blumple'}}) == 'Blumple (Deezer)'
    # and it's kept, so the automation sync agrees
    assert _run_sync(db, dz) == 'Blumple (Deezer)'


def test_a_deleted_mirrors_syncs_stop_marking_its_server_playlist(db):
    keep = db.mirror_playlist('spotify', 'k', 'Keep', _track(), profile_id=1)
    gone = db.mirror_playlist('spotify', 'g', 'Gone', _track(), profile_id=1)
    for sync_id, name in ((f'auto_mirror_{keep}', 'Keep'), (f'youtube_mirrored_{gone}', 'Gone'),
                          (f'auto_mirror_{gone}', 'Gone'), ('deezer_123', 'Direct')):
        db.add_sync_history_entry('b', sync_id, name, 'mirrored', 'playlist', '[]', profile_id=1)
    assert set(db.get_sync_history_playlist_names(profile_id=1)) == {'Keep', 'Gone', 'Direct'}

    assert db.delete_mirrored_playlist(gone)
    assert set(db.get_sync_history_playlist_names(profile_id=1)) == {'Keep', 'Direct'}
