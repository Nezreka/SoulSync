"""No credentials, music paths or live providers: all release data is synthetic."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from core.metadata.standard_completion import plan_standard_album, enqueue_standard_plan


ROW = {'artist_name': 'Fixture Artist', 'album_title': 'Fixture Album (Deluxe Edition)',
       'canonical_source': 'spotify', 'canonical_album_id': 'pinned-deluxe', 'canonical_locked': 1}


def tracks(names=('First', 'Second', 'Third')):
    return [{'id': f't{i}', 'name': name, 'track_number': i, 'disc_number': 1,
             'duration_ms': 180000, 'artists': [{'name': 'Fixture Artist'}]}
            for i, name in enumerate(names, 1)]


def local(names=('First',)):
    return [{'title': n, 'duration_ms': 180000, 'track_number': i, 'disc_number': 1}
            for i, n in enumerate(names, 1)]


def fixture_plan(*, names=('Fixture Album', 'Fixture Album (Deluxe Edition)'),
                 owned=None, lists=None, metadata=None, **kwargs):
    releases = [{'id': str(i), 'name': name} for i, name in enumerate(names)]
    lists = lists or {str(i): tracks() for i in range(len(names))}
    metas = {r['id']: {'id': r['id'], 'name': r['name'], 'total_tracks': len(lists[r['id']]),
                      'artists': [{'name': 'Fixture Artist'}]} for r in releases}
    if metadata:
        metadata(metas)
    return plan_standard_album(ROW, local() if owned is None else owned, 'deezer',
                               fetch_releases=lambda *a, **k: releases,
                               fetch_album=lambda s, i: metas.get(i),
                               fetch_tracks=lambda s, i: lists[i], **kwargs)


def test_standard_beats_even_locked_deluxe():
    plan = fixture_plan(lists={'0': tracks(), '1': tracks(('First', 'Second', 'Third', 'Bonus'))})
    assert plan['album_id'] == '0'
    assert plan['release'] == 'Fixture Album'
    assert [t['name'] for t in plan['missing_tracks']] == ['Second', 'Third']
    assert plan['source'] == 'deezer'


@pytest.mark.parametrize('owned,missing', [
    (('First', 'Second', 'Third'), []),
    (('First', 'Second'), ['Third']),
    (('First', 'Second', 'Third', 'Bonus'), []),
    (('Bonus', 'First'), ['Second', 'Third']),
])
def test_ownership_is_reference_songs_not_local_count_or_numbers(owned, missing):
    plan = fixture_plan(owned=local(owned))
    assert [t['name'] for t in plan['missing_tracks']] == missing


@pytest.mark.parametrize('label', ['Deluxe', 'Expanded', '25th Anniversary', 'Bonus Track',
                                  'Super Deluxe', 'Special Edition', 'Extended', 'Collector'])
def test_no_confident_standard_skips(label):
    assert fixture_plan(names=(f'Fixture Album ({label})',))['status'] == 'skipped'


def test_remaster_is_allowed_when_tracklists_agree():
    assert fixture_plan(names=('Fixture Album', 'Fixture Album (Remastered)'))['status'] == 'ready'


def test_regional_and_unlabelled_conflicts_fail_closed():
    assert fixture_plan(names=('Fixture Album (Japanese Edition)',))['status'] == 'skipped'
    plan = fixture_plan(names=('Fixture Album', 'Fixture Album (Remastered)'),
                        lists={'0': tracks(), '1': tracks(('First', 'Second', 'Other'))})
    assert 'Conflicting' in plan['reason']


@pytest.mark.parametrize('change', [
    lambda m: m.clear(),
    lambda m: m['0'].update(total_tracks=20),
    lambda m: m['0'].update(artists=[{'name': 'Wrong Artist'}]),
])
def test_missing_or_conflicting_metadata_skips(change):
    assert fixture_plan(metadata=change)['status'] == 'skipped'


def test_provider_failure_is_not_local_count_fallback():
    def fail(*a, **k):
        raise ConnectionError('offline')
    plan = plan_standard_album(dict(ROW, api_track_count=20), local(), 'spotify', fetch_releases=fail)
    assert plan['status'] == 'skipped'
    assert not plan['proposed_downloads']
    assert 'ConnectionError' in plan['reason']


def test_queue_payload_and_dedup_contract():
    plan = fixture_plan()
    db = Mock()
    db.add_to_wishlist.side_effect = [True, True, False, False]
    assert enqueue_standard_plan(db, plan, profile_id=7) == 2
    assert enqueue_standard_plan(db, plan, profile_id=7) == 0
    data = db.add_to_wishlist.call_args.args[0]
    kwargs = db.add_to_wishlist.call_args.kwargs
    assert data['album']['id'] == '0'
    assert data['album']['name'] == 'Fixture Album'
    assert kwargs['profile_id'] == 7
    assert kwargs['source_info']['source'] == 'deezer'
    assert kwargs['source_type'] == 'album'
    assert 'playlist_id' not in kwargs['source_info']


def test_planning_does_not_mutate_inputs():
    owned = local()
    before = deepcopy(owned)
    fixture_plan(owned=owned)
    assert owned == before


@pytest.mark.parametrize('duration', [0, 240000])
def test_uncertain_song_version_skips_instead_of_downloading_duplicate(duration):
    owned = local()
    owned[0]['duration_ms'] = duration
    assert fixture_plan(owned=owned)['status'] == 'skipped'


def test_standard_requests_never_promote_to_album_bundle():
    from core.wishlist.album_grouping import group_wishlist_tracks_by_album
    db = Mock()
    db.add_to_wishlist.return_value = True
    enqueue_standard_plan(db, fixture_plan())
    rows = [{'spotify_data': c.args[0]} for c in db.add_to_wishlist.call_args_list]
    grouped = group_wishlist_tracks_by_album(rows, min_tracks_per_album=1)
    assert not grouped.album_groups
    assert grouped.residual_tracks == rows


def test_real_wishlist_persists_one_request_per_missing_song(tmp_path):
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / 'wishlist-fixture.sqlite'))
    plan = fixture_plan()
    assert enqueue_standard_plan(db, plan) == 2
    assert enqueue_standard_plan(db, plan) == 0
    with db._get_connection() as conn:
        rows = conn.execute('SELECT spotify_data FROM wishlist_tracks').fetchall()
    assert len(rows) == 2
    import json
    assert all(json.loads(row[0])['standard_completion_per_track'] for row in rows)


def test_remaster_without_original_cannot_establish_standard_tracklist():
    assert fixture_plan(names=('Fixture Album (Remastered)',))['status'] == 'skipped'


def test_explicit_preference_breaks_equivalent_standard_release_tie():
    def mark(m):
        m['1']['explicit'] = True
    assert fixture_plan(names=('Fixture Album', 'Fixture Album'), metadata=mark)['album_id'] == '1'
    assert fixture_plan(names=('Fixture Album', 'Fixture Album'), metadata=mark, prefer_explicit=False)['album_id'] == '0'


def test_truncated_catalogue_never_picks_first_standard():
    p = plan_standard_album(ROW, local(), 'deezer', fetch_releases=lambda *a, **k: [{}] * 50)
    assert p['status'] == 'skipped'
    assert 'truncated' in p['reason']


def test_gapped_track_numbers_are_not_a_verified_tracklist():
    songs = tracks()
    songs[2]['track_number'] = 4
    assert fixture_plan(lists={'0': songs, '1': tracks()})['status'] == 'skipped'


def test_local_remaster_title_drift_skips_instead_of_downloading_duplicate():
    assert fixture_plan(owned=local(('First - Remastered', 'Second')))['status'] == 'skipped'


def test_completion_preserves_existing_playlist_wishlist_context(tmp_path):
    import json
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / 'playlist-fixture.sqlite'))
    plan = fixture_plan()
    song = dict(plan['missing_tracks'][0], album={'id': plan['album_id'], 'name': plan['release']})
    assert db.add_to_wishlist(song, source_type='playlist', source_info={'playlist_id': 'intended-songs'})
    with db._get_connection() as conn:
        before = tuple(conn.execute('SELECT spotify_data, source_type, source_info FROM wishlist_tracks').fetchone())
    assert enqueue_standard_plan(db, plan) == 1
    with db._get_connection() as conn:
        after = tuple(conn.execute('SELECT spotify_data, source_type, source_info FROM wishlist_tracks WHERE spotify_track_id = ?',
                                   (song['id'],)).fetchone())
    assert before == after
    assert json.loads(after[2])['playlist_id'] == 'intended-songs'


def test_different_provider_edition_request_prevents_duplicate_completion(tmp_path):
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / 'cross-edition-fixture.sqlite'))
    plan = fixture_plan()
    song = dict(plan['missing_tracks'][0], id='other-provider-deluxe-id',
                album={'id': 'deluxe', 'name': 'Fixture Album (Deluxe Edition)'})
    assert db.add_to_wishlist(song, source_type='playlist', source_info={'playlist_id': 'original'})
    assert enqueue_standard_plan(db, plan) == 1
    with db._get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM wishlist_tracks').fetchone()[0] == 2


def test_completion_respects_global_no_duplicate_songs_setting(tmp_path, monkeypatch):
    from core.settings import config_manager
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / 'no-duplicates-fixture.sqlite'))
    plan = fixture_plan(owned=local(('First', 'Third')))
    song = dict(plan['missing_tracks'][0], id='other-album-song',
                album={'id': 'unrelated', 'name': 'Different Album'})
    assert db.add_to_wishlist(song, source_type='album')
    original_get = config_manager.get
    monkeypatch.setattr(config_manager, 'get', lambda key, default=None:
                        False if key == 'wishlist.allow_duplicate_tracks' else original_get(key, default))
    assert enqueue_standard_plan(db, plan) == 0
