"""Queue -> Clear all -> explicit playlist sync must preserve Library intent."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from core.library2.queries import list_artists
from core.wishlist.service import WishlistService
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'music.db'))


def track(index=1, source='spotify'):
    return {'id': f'track-{index}', 'name': f'Song {index}', 'source': source,
            'artists': [{'name': 'Wishlist Artist'}], 'album': {'id': 'album-1', 'name': 'Album'}}


def state(db, profile=1):
    with db._get_connection() as conn:
        return [dict(r) for r in conn.execute(
            'SELECT t.id, t.monitored, w.wanted, r.monitored AS rule FROM lib2_tracks t '
            'JOIN lib2_wanted_tracks w ON w.track_id=t.id AND w.profile_id=? '
            'JOIN lib2_monitor_rules r ON r.entity_type=\'track\' AND r.entity_id=t.id AND r.profile_id=?', (profile, profile))]


@pytest.mark.parametrize('source_type', ['playlist', 'album', 'manual', 'enhance', 'watchlist', 'label', 'unknown'])
def test_every_database_add_monitors_and_materializes_before_return(db, source_type):
    assert db.add_to_wishlist(track(), source_type=source_type)
    assert all(r['monitored'] == r['wanted'] == r['rule'] == 1 for r in state(db))
    with db._get_connection() as conn:
        assert list_artists(conn)[1] == 1
        info = json.loads(conn.execute('SELECT source_info FROM wishlist_tracks').fetchone()[0])
        assert info['lib2_track_id'] == state(db)[0]['id']
        assert conn.execute('SELECT monitored FROM lib2_artists').fetchone()[0] == 0


def test_140_entries_clear_hide_empty_artist_and_resync_restore_same_tracks(db, monkeypatch):
    from services.sync_service import PlaylistSyncService, _sync_user_initiated
    service = WishlistService()
    service._database = db
    monkeypatch.setattr('core.wishlist_service.get_wishlist_service', lambda: service)
    sync = PlaylistSyncService.__new__(PlaylistSyncService)
    sync._original_tracks_map = {f'track-{i}': track(i) for i in range(140)}
    unmatched = [SimpleNamespace(spotify_track=SimpleNamespace(id=f'track-{i}', name=f'Song {i}', artists=['Wishlist Artist'])) for i in range(140)]
    playlist = SimpleNamespace(name='Playlist', id='playlist-1')
    assert sync._wishlist_unmatched(playlist, unmatched) == 140
    before = state(db)
    assert len(before) == 140 and all(r['wanted'] == r['monitored'] == 1 for r in before)
    assert db.clear_wishlist()
    assert all(r['monitored'] == r['wanted'] == r['rule'] == 0 for r in state(db))
    with db._get_connection() as conn:
        assert list_artists(conn)[1] == 0
    assert sync._wishlist_unmatched(playlist, unmatched) == 0  # Automatic sync respects Clear all.
    token = _sync_user_initiated.set(True)
    try:
        assert sync._wishlist_unmatched(playlist, unmatched) == 140
    finally:
        _sync_user_initiated.reset(token)
    after = state(db)
    assert [r['id'] for r in before] == [r['id'] for r in after]
    assert all(r['monitored'] == r['wanted'] == r['rule'] == 1 for r in after)
    with db._get_connection() as conn:
        assert list_artists(conn)[1] == 1


def test_clear_keeps_an_artist_with_a_present_file(db, tmp_path):
    db.add_to_wishlist(track(), source_type='playlist')
    path = tmp_path / 'song.flac'
    path.write_bytes(b'audio')
    with db._get_connection() as conn:
        conn.execute('INSERT INTO lib2_track_files(track_id,path) VALUES(?,?)', (state(db)[0]['id'], str(path)))
        conn.commit()
    assert db.clear_wishlist()
    with db._get_connection() as conn:
        assert list_artists(conn)[1] == 1
    assert path.read_bytes() == b'audio'


def test_add_and_clear_roll_back_if_monitoring_fails(db, monkeypatch):
    import core.library2.materialize as materialize
    import core.library2.monitor_rules as rules
    def fail(*args, **kwargs):
        raise RuntimeError('monitoring unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(materialize, 'materialize_wishlist_row', fail)
        assert db.add_to_wishlist_detailed(track())['status'] == 'error'
    assert db.get_wishlist_tracks() == []
    db.add_to_wishlist(track())
    monkeypatch.setattr(rules, 'record_rule', fail)
    assert not db.clear_wishlist()
    assert len(db.get_wishlist_tracks()) == 1 and state(db)[0]['wanted'] == 1
    assert not db.is_track_ignored('track-1')


def test_duplicate_add_heals_monitoring_and_other_profiles_keep_shared_intent(db):
    db.add_to_wishlist(track())
    with db._get_connection() as conn:
        conn.execute('UPDATE lib2_tracks SET monitored=0')
        conn.execute('UPDATE lib2_monitor_rules SET monitored=0')
        conn.execute('UPDATE lib2_wanted_tracks SET wanted=0')
        conn.commit()
    assert db.add_to_wishlist_detailed(track())['status'] == 'updated'
    assert state(db)[0]['wanted'] == state(db)[0]['monitored'] == 1
    assert db.add_to_wishlist(track(), profile_id=2)
    assert state(db, 2)[0]['wanted'] == 1
    assert db.clear_wishlist(profile_id=2)
    assert state(db, 2)[0]['wanted'] == 0 and state(db)[0]['wanted'] == state(db)[0]['monitored'] == 1


def test_reconcile_repairs_old_external_queue_without_pruning_it(db):
    from core.library2.monitor_sync import reconcile_track_wishlist
    with db._get_connection() as conn:
        conn.execute('INSERT INTO wishlist_tracks(spotify_track_id,spotify_data,source_type,profile_id) VALUES(?,?,\'playlist\',1)',
                     ('track-1::album-1', json.dumps(track())))
        conn.commit()
    result = reconcile_track_wishlist(db)
    assert result['pruned'] == 0 and len(db.get_wishlist_tracks()) == 1
    assert result['intent_repaired'] == 1
    assert state(db)[0]['wanted'] == state(db)[0]['monitored'] == 1
    assert reconcile_track_wishlist(db)['intent_repaired'] == 0


def test_projection_mirror_does_not_pin_inherited_monitoring(db):
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule
    from core.library2.wanted import recompute_wanted
    db.add_to_wishlist(track())
    tid = state(db)[0]['id']
    with db._get_connection() as conn:
        conn.execute('DELETE FROM lib2_monitor_rules WHERE entity_type=\'track\'')
        album_id = conn.execute('SELECT album_id FROM lib2_tracks WHERE id=?', (tid,)).fetchone()[0]
        record_rule(conn, 'album', album_id, True, PROVENANCE_USER)
        recompute_wanted(conn)
        conn.commit()
    assert db.add_to_wishlist_detailed(track(), source_info={'source': 'library_v2', 'lib2_track_id': tid})['applied']
    with db._get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM lib2_monitor_rules WHERE entity_type=\'track\'').fetchone()[0] == 0
        record_rule(conn, 'album', album_id, False, PROVENANCE_USER)
        recompute_wanted(conn)
        conn.commit()
        assert conn.execute('SELECT wanted FROM lib2_wanted_tracks WHERE track_id=?', (tid,)).fetchone()[0] == 0


def test_provider_identity_reuses_a_compilation_track_despite_different_credits(db):
    data = track(source='deezer')
    data['id'], data['album']['id'] = '1234', '5678'
    assert db.add_to_wishlist(data)
    tid = state(db)[0]['id']
    assert db.clear_wishlist()
    with db._get_connection() as conn:
        conn.execute("UPDATE lib2_artists SET name='Various Artists'")
        conn.execute("UPDATE lib2_tracks SET title='Corrected Title'")
        conn.commit()
    assert db.add_to_wishlist(data, user_initiated=True)
    assert len(state(db)) == 1 and state(db)[0]['id'] == tid and state(db)[0]['wanted'] == 1
    with db._get_connection() as conn:
        assert conn.execute('SELECT spotify_id FROM lib2_tracks').fetchone()[0] is None


def test_a_new_manual_add_during_clear_commits_after_clear_and_stays_monitored(db, monkeypatch):
    import core.library2.monitor_sync as sync
    db.add_to_wishlist(track())
    captured, release, adding = threading.Event(), threading.Event(), threading.Event()
    resolve = sync._descriptor_lib2_track_ids
    def paused(conn, descriptors):
        captured.set()
        assert release.wait(5)
        return resolve(conn, descriptors)
    def add():
        adding.set()
        return db.add_to_wishlist(track(), user_initiated=True)
    monkeypatch.setattr(sync, '_descriptor_lib2_track_ids', paused)
    with ThreadPoolExecutor(max_workers=2) as executor:
        clear = executor.submit(db.clear_wishlist)
        try:
            assert captured.wait(5)
            new = executor.submit(add)
            assert adding.wait(5)
        finally:
            release.set()
        assert clear.result(timeout=10) and new.result(timeout=10)
    assert len(db.get_wishlist_tracks()) == 1 and state(db)[0]['monitored'] == state(db)[0]['wanted'] == 1


def test_unresolvable_identity_keeps_the_add_and_does_not_abort_reconcile(db, monkeypatch):
    from core.library2.monitor_sync import reconcile_track_wishlist
    import core.library2.materialize as materialize
    real = materialize.materialize_wishlist_row
    def ambiguous(conn, row_id, **kwargs):
        if json.loads(conn.execute('SELECT spotify_data FROM wishlist_tracks WHERE id=?', (row_id,)).fetchone()[0])['id'] == 'track-1':
            raise ValueError('Wishlist identity matches multiple Library tracks')
        return real(conn, row_id, **kwargs)
    monkeypatch.setattr(materialize, 'materialize_wishlist_row', ambiguous)
    assert db.add_to_wishlist_detailed(track())['status'] == 'created'
    assert db.add_to_wishlist_detailed(track())['status'] != 'error'
    with db._get_connection() as conn:
        conn.execute('INSERT INTO wishlist_tracks(spotify_track_id,spotify_data,source_type,profile_id) VALUES(?,?,\'playlist\',1)',
                     ('track-2::album-1', json.dumps(track(2))))
        conn.commit()
    result = reconcile_track_wishlist(db)
    assert result['intent_failed'] == 1 and result['intent_repaired'] == 1
    assert len(db.get_wishlist_tracks()) == 2 and len(state(db)) == 1


def _downloaded_then_deleted(db, tmp_path, release_wish):
    """a playlist song lands, then the user deletes it from download origins"""
    db.add_to_wishlist(track(), source_type='playlist')
    track_id = state(db)[0]['id']
    path = str(tmp_path / 'song.flac')
    with db._get_connection() as conn:
        conn.execute('INSERT INTO lib2_track_files(track_id,path) VALUES(?,?)', (track_id, path))
        conn.execute('DELETE FROM wishlist_tracks')  # a finished download leaves the queue
        conn.commit()
    assert db.delete_track_by_file_path(path, release_wish=release_wish) == 1
    return track_id


def test_a_deleted_download_is_queued_again_when_the_wish_stays(db, tmp_path):
    """the review reject wants a better copy, so the reconcile re-queues it"""
    from core.library2.monitor_sync import reconcile_track_wishlist
    _downloaded_then_deleted(db, tmp_path, release_wish=False)
    assert reconcile_track_wishlist(db)['added'] == 1


def test_a_deleted_download_stays_gone_when_the_wish_is_released(db, tmp_path):
    """deleting from download origins came back on the wishlist the next hour
    and downloaded again overnight, with the playlist long gone"""
    from core.library2.monitor_sync import reconcile_track_wishlist
    track_id = _downloaded_then_deleted(db, tmp_path, release_wish=True)
    assert state(db) == [{'id': track_id, 'monitored': 0, 'wanted': 0, 'rule': 0}]
    assert reconcile_track_wishlist(db)['added'] == 0
    with db._get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM wishlist_tracks').fetchone()[0] == 0


def test_releasing_a_deleted_download_also_drops_its_requeued_wishlist_row(db, tmp_path):
    from core.library2.monitor_sync import reconcile_track_wishlist
    path = str(tmp_path / 'song.flac')
    db.add_to_wishlist(track(), source_type='playlist')
    with db._get_connection() as conn:
        conn.execute('INSERT INTO lib2_track_files(track_id,path) VALUES(?,?)', (state(db)[0]['id'], path))
        conn.commit()
    assert db.delete_track_by_file_path(path, release_wish=True) == 1
    reconcile_track_wishlist(db)
    with db._get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM wishlist_tracks').fetchone()[0] == 0


def test_an_own_library_download_is_released_for_the_profile_that_wanted_it(db, tmp_path, monkeypatch):
    """third #1062 review G11-1: the Expired Download Cleaner released the
    wish under profile 1 only; an own library's download was wanted by its
    owner, so the sync put it straight back on their wishlist (a loop)"""
    db.add_to_wishlist(track(), source_type='playlist')
    track_id = state(db)[0]['id']
    path = str(tmp_path / 'own.flac')
    with db._get_connection() as conn:
        conn.execute('INSERT INTO lib2_track_files(track_id,path,owner_profile_id) VALUES(?,?,3)',
                     (track_id, path))
        conn.commit()
    released = []
    monkeypatch.setattr(db, '_release_deleted_tracks',
                        lambda _conn, track_ids, profile_id: released.append((profile_id, track_ids)))

    assert db.delete_track_by_file_path(path, release_wish=True) == 1

    assert sorted(p for p, _ in released) == [1, 3]
    assert all(ids == [track_id] for _, ids in released)
