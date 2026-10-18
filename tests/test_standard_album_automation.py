"""Exercise the real album inventory scan against an isolated fixture database."""
import json
import sqlite3
from unittest.mock import Mock

import pytest

from core.repair_jobs.album_completeness import AlbumCompletenessJob
from core.repair_jobs.base import JobContext
import core.metadata.standard_completion as standard


@pytest.fixture
def harness(tmp_path, monkeypatch):
    path = tmp_path / 'fixture.sqlite'
    conn = sqlite3.connect(path)
    conn.executescript('''
        CREATE TABLE profiles (id INTEGER PRIMARY KEY);
        INSERT INTO profiles VALUES (7);
        CREATE TABLE artists (id TEXT, name TEXT, thumb_url TEXT);
        CREATE TABLE albums (id TEXT, artist_id TEXT, title TEXT, spotify_album_id TEXT,
                             thumb_url TEXT, owner_profile_id INTEGER, metadata_locked INTEGER);
        CREATE TABLE tracks (id TEXT, album_id TEXT, title TEXT, track_number INTEGER,
                             disc_number INTEGER, duration INTEGER);
        INSERT INTO artists VALUES ('artist', 'Fixture Artist', NULL);
        INSERT INTO albums VALUES ('album', 'artist', 'Fixture Album', NULL, NULL, 7, 0);
        INSERT INTO tracks VALUES ('owned', 'album', 'First', 1, 1, 180000);
    ''')
    conn.commit()
    conn.close()
    db = Mock()
    db._get_connection.side_effect = lambda: sqlite3.connect(path)
    queued = {}

    def add(data, **kwargs):
        key = (data['id'], kwargs['profile_id'])
        created = key not in queued
        queued[key] = (data, kwargs)
        return created
    db.add_to_wishlist.side_effect = add
    config = Mock()
    settings = {'standard_only': True, 'dry_run': True, 'min_owned_tracks': 1}
    config.get.side_effect = lambda key, default=None: settings if key.endswith('.settings') else default
    report = Mock(return_value=True)
    context = JobContext(db=db, transfer_folder=str(tmp_path / 'MUST_NOT_CREATE'),
                         config_manager=config, create_finding=report, report_progress=Mock())
    monkeypatch.setattr(AlbumCompletenessJob, '_get_primary_source', lambda self: 'deezer')
    songs = [{'id': str(i), 'name': n, 'track_number': i, 'disc_number': 1,
              'duration_ms': 180000} for i, n in enumerate(('First', 'Second', 'Third'), 1)]
    monkeypatch.setattr(standard, 'get_artist_albums_for_source', lambda *a, **k: [{'id': 'std', 'name': 'Fixture Album'}])
    monkeypatch.setattr(standard, 'get_album_for_source', lambda *a: {
        'id': 'std', 'name': 'Fixture Album', 'artists': [{'name': 'Fixture Artist'}], 'total_tracks': 3})
    monkeypatch.setattr(standard, 'get_album_tracks_for_source', lambda *a: songs)
    return context, settings, queued, report, path


def test_playlist_import_without_source_id_feeds_scheduled_preview(harness, tmp_path):
    context, _, queued, report, path = harness
    before = path.read_bytes()
    assert AlbumCompletenessJob().estimate_scope(context) == 1
    result = AlbumCompletenessJob().scan(context)
    assert result.scanned == 1
    plan = report.call_args.kwargs['details']
    assert plan['expected_track_count'] == 3
    assert [t['name'] for t in plan['existing_tracks']] == ['First']
    assert [t['name'] for t in plan['proposed_downloads']] == ['Second', 'Third']
    assert report.call_args.kwargs['finding_type'] == 'standard_album_preview'
    assert not queued
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ['fixture.sqlite']


def test_live_queues_missing_only_and_repeated_scans_deduplicate(harness):
    context, settings, queued, _, _ = harness
    settings['dry_run'] = False
    assert AlbumCompletenessJob().scan(context).auto_fixed == 2
    assert AlbumCompletenessJob().scan(context).auto_fixed == 0
    assert set(queued) == {('2', 7), ('3', 7)}
    assert all('playlist_id' not in kw['source_info'] for _, kw in queued.values())


def test_missing_provider_metadata_never_enqueues(harness, monkeypatch):
    context, settings, queued, report, _ = harness
    settings['dry_run'] = False
    monkeypatch.setattr(standard, 'get_album_for_source', lambda *a: None)
    assert AlbumCompletenessJob().scan(context).skipped == 1
    assert not queued
    assert 'metadata unavailable' in report.call_args.kwargs['details']['reason']


def test_fixture_preview_json(harness, capsys):
    context, _, _, report, _ = harness
    AlbumCompletenessJob().scan(context)
    print(json.dumps(report.call_args.kwargs['details'], indent=2))
    assert 'Second' in capsys.readouterr().out


@pytest.mark.parametrize('owner', [None, 0, -1, 'invalid', 7.5, 999])
@pytest.mark.parametrize('dry_run', [True, False])
def test_unverified_owner_skips_before_provider_lookup(harness, monkeypatch, owner, dry_run):
    context, settings, queued, report, path = harness
    settings['dry_run'] = dry_run
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE albums SET owner_profile_id = ?', (owner,))
    lookup = Mock(side_effect=AssertionError('Unowned albums must not reach providers'))
    monkeypatch.setattr(standard, 'get_artist_albums_for_source', lookup)
    result = AlbumCompletenessJob().scan(context)
    assert result.skipped == 1
    assert 'Ownership could not be verified' in report.call_args.kwargs['details']['reason']
    assert not queued
    lookup.assert_not_called()


def test_missing_owner_column_skips_without_profile_one_fallback(harness):
    context, settings, queued, report, path = harness
    settings['dry_run'] = False
    with sqlite3.connect(path) as conn:
        conn.execute('ALTER TABLE albums DROP COLUMN owner_profile_id')
    assert AlbumCompletenessJob().scan(context).skipped == 1
    assert 'ownership' in report.call_args.kwargs['details']['reason'].lower()
    assert not queued


def test_owner_reassigned_during_lookup_is_not_queued(harness, monkeypatch):
    context, settings, queued, report, path = harness
    settings['dry_run'] = False
    original = standard.get_album_for_source

    def reassign(*args):
        with sqlite3.connect(path) as conn:
            conn.execute('UPDATE albums SET owner_profile_id = NULL')
        return original(*args)
    monkeypatch.setattr(standard, 'get_album_for_source', reassign)
    assert AlbumCompletenessJob().scan(context).errors == 1
    assert 'ownership changed' in report.call_args.kwargs['details']['reason'].lower()
    assert not queued


def complete_fixture(path):
    with sqlite3.connect(path) as conn:
        conn.executemany('INSERT INTO tracks VALUES (?, ?, ?, ?, ?, ?)', [
            ('second', 'album', 'Second', 2, 1, 180000), ('third', 'album', 'Third', 3, 1, 180000)])


def test_unchanged_complete_album_skips_all_provider_lookups(harness, monkeypatch):
    context, _, queued, report, path = harness
    complete_fixture(path)
    job = AlbumCompletenessJob()
    job.scan(context)
    lookup = Mock(side_effect=AssertionError('Must reuse verified complete report'))
    for name in ('get_artist_albums_for_source', 'get_album_for_source', 'get_album_tracks_for_source'):
        monkeypatch.setattr(standard, name, lookup)
    job.scan(context)
    assert report.call_args.kwargs['details']['verification_cached'] is True
    assert not queued
    lookup.assert_not_called()


@pytest.mark.parametrize('change', ['import', 'remove', 'retag', 'expiry', 'explicit', 'source', 'profile_deleted'])
def test_completed_cache_invalidation(harness, monkeypatch, change):
    context, _, _, report, path = harness
    complete_fixture(path)
    job = AlbumCompletenessJob()
    job.scan(context)
    lookup = Mock(wraps=standard.get_artist_albums_for_source)
    monkeypatch.setattr(standard, 'get_artist_albums_for_source', lookup)
    with sqlite3.connect(path) as conn:
        if change == 'import':
            conn.execute("INSERT INTO tracks VALUES ('bonus','album','Bonus',4,1,180000)")
        elif change == 'remove':
            conn.execute("DELETE FROM tracks WHERE id = 'third'")
        elif change == 'retag':
            conn.execute("UPDATE tracks SET title = 'New title' WHERE id = 'third'")
        elif change == 'profile_deleted':
            conn.execute('DELETE FROM profiles')
    if change == 'expiry':
        monkeypatch.setattr(job, '_standard_complete_ttl', 0)
    elif change == 'explicit':
        original = context.config_manager.get.side_effect
        context.config_manager.get.side_effect = lambda key, default=None: False if key == 'watchlist.prefer_explicit_edition' else original(key, default)
    elif change == 'source':
        monkeypatch.setattr(job, '_get_primary_source', lambda: 'spotify')
    job.scan(context)
    assert not report.call_args.kwargs['details'].get('verification_cached')
    if change == 'profile_deleted':
        lookup.assert_not_called()
        assert report.call_args.kwargs['details']['status'] == 'skipped'
    else:
        lookup.assert_called_once()


def test_new_playlist_album_is_considered_while_complete_album_uses_cache(harness, monkeypatch):
    context, _, _, report, path = harness
    complete_fixture(path)
    job = AlbumCompletenessJob()
    job.scan(context)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO albums VALUES ('new','artist','New Album',NULL,NULL,7,0)")
        conn.execute("INSERT INTO tracks VALUES ('new-song','new','New Song',1,1,180000)")
    lookup = Mock(wraps=standard.get_artist_albums_for_source)
    monkeypatch.setattr(standard, 'get_artist_albums_for_source', lookup)
    assert job.scan(context).scanned == 2
    lookup.assert_called_once()
    plans = [c.kwargs['details'] for c in report.call_args_list[-2:]]
    assert any(p['album'] == 'New Album' for p in plans)
    assert any(p.get('verification_cached') for p in plans)


def test_catalogue_lookup_is_shared_per_artist_but_retried_next_scan(harness, monkeypatch):
    context, _, _, _, path = harness
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO albums VALUES ('new','artist','New Album',NULL,NULL,7,0)")
        conn.execute("INSERT INTO tracks VALUES ('new-song','new','New Song',1,1,180000)")
    lookup = Mock(wraps=standard.get_artist_albums_for_source)
    monkeypatch.setattr(standard, 'get_artist_albums_for_source', lookup)
    job = AlbumCompletenessJob()
    job.scan(context)
    assert lookup.call_count == 1
    job.scan(context)
    assert lookup.call_count == 2  # partial/ambiguous plans aren't cached between runs


def test_complete_cache_is_bound_to_database_instance(harness, monkeypatch):
    context, _, _, _, path = harness
    complete_fixture(path)
    job = AlbumCompletenessJob()
    job.scan(context)
    other = Mock()
    other._get_connection.side_effect = lambda: sqlite3.connect(path)
    context.db = other
    lookup = Mock(wraps=standard.get_artist_albums_for_source)
    monkeypatch.setattr(standard, 'get_artist_albums_for_source', lookup)
    job.scan(context)
    lookup.assert_called_once()


def test_complete_cache_is_bounded(harness, monkeypatch):
    context, _, _, _, path = harness
    complete_fixture(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO artists VALUES ('other-artist','Fixture Artist',NULL)")
        conn.execute("INSERT INTO albums VALUES ('other-album','other-artist','Fixture Album',NULL,NULL,7,0)")
        conn.execute("INSERT INTO tracks SELECT id || '-copy','other-album',title,track_number,disc_number,duration FROM tracks")
    job = AlbumCompletenessJob()
    monkeypatch.setattr(job, '_standard_complete_limit', 1)
    assert job.scan(context).scanned == 2
    assert len(job._standard_complete_cache) == 1
