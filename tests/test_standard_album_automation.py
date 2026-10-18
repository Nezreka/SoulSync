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
