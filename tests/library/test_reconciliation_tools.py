"""Old maintenance findings retire only after current evidence disproves them."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from core.reconciliation.tools import ToolEvidence


@pytest.fixture
def evidence_db(tmp_path):
    path = tmp_path / 'findings.db'
    db = SimpleNamespace(_get_connection=lambda: sqlite3.connect(path))
    with db._get_connection() as conn:
        conn.execute('''CREATE TABLE repair_findings (
            id INTEGER PRIMARY KEY, finding_type TEXT, file_path TEXT, title TEXT,
            details_json TEXT, status TEXT DEFAULT 'pending', user_action TEXT,
            resolved_at TEXT, updated_at TEXT)''')
    return db


def finding(db, kind, path, details):
    with db._get_connection() as conn:
        conn.execute('INSERT INTO repair_findings (id,finding_type,file_path,title,details_json) VALUES (1,?,?,?,?)',
                     (kind, str(path), 'Earlier maintenance finding', json.dumps(details)))


def status(db):
    with db._get_connection() as conn:
        return conn.execute('SELECT status FROM repair_findings WHERE id=1').fetchone()[0]


def local(path, title='Song', album='Album'):
    return {'path': str(path), 'tags': {'title': title, 'album': album,
        'duration_ms': 200000, 'track_number': 1, 'disc_number': 1},
        'db': {'track_number': 1, 'disc_number': 1}}


def test_numbering_finding_requires_file_library_and_filename_agreement(evidence_db, tmp_path):
    path = tmp_path / '01 - Song.flac'
    finding(evidence_db, 'track_number_mismatch', path,
            {'correct_track_num': 1, 'disc_number': 1, 'new_filename': path.name})
    file = local(path)
    file['db']['track_number'] = 2
    evidence = ToolEvidence(evidence_db)
    evidence.compare([file], [], {'pairs': []}, 'itunes')
    evidence.finish()
    assert status(evidence_db) == 'pending'
    file['db']['track_number'] = 1
    evidence.compare([file], [], {'pairs': []}, 'itunes')
    assert status(evidence_db) == 'pending'  # Comparison alone cannot retire anything.
    evidence.finish()
    assert status(evidence_db) == 'resolved'


def test_album_finding_requires_every_member_and_readable_tags(evidence_db, tmp_path):
    files = [local(tmp_path / f'{i}.flac') for i in range(2)]
    finding(evidence_db, 'album_tag_inconsistency', files[0]['path'], {
        'tracks': [{'file_path': file['path']} for file in files],
        'inconsistencies': [{'field': 'album'}]})
    evidence = ToolEvidence(evidence_db)
    evidence.compare(files[:1], [], {'pairs': []}, 'itunes')
    evidence.finish()
    assert status(evidence_db) == 'pending'
    files[1]['tags']['title'] = ''
    evidence.compare(files, [], {'pairs': []}, 'itunes')
    evidence.finish()
    assert status(evidence_db) == 'pending'
    files[1]['tags']['title'] = 'Song'
    evidence.compare(files, [], {'pairs': []}, 'itunes')
    evidence.finish()
    assert status(evidence_db) == 'resolved'


@pytest.mark.parametrize('titles,expected', [
    (['Song', 'Song'], 'pending'),
    (['Song', 'Song (demo)'], 'resolved'),
])
def test_duplicate_finding_needs_distinct_versions_and_slots(evidence_db, tmp_path, titles, expected):
    files = [local(tmp_path / f'{i}.flac', title) for i, title in enumerate(titles)]
    finding(evidence_db, 'duplicate_tracks', files[0]['path'], {
        'tracks': [{'file_path': file['path']} for file in files]})
    slots = [{'title': title} for title in titles]
    plan = {'pairs': [{'file_index': i, 'slot_index': i, 'confidence': 'clear'} for i in range(2)]}
    evidence = ToolEvidence(evidence_db)
    evidence.compare(files, slots, plan, 'itunes')
    evidence.finish()
    assert status(evidence_db) == expected


def test_audio_alias_recheck_uses_file_identity_and_preserves_concurrent_updates(evidence_db, tmp_path):
    path = tmp_path / 'song.flac'
    path.write_bytes(b'file identity fixture')
    alias = tmp_path / 'alias.flac'
    alias.symlink_to(path)
    finding(evidence_db, 'acoustid_mismatch', alias,
            {'expected_title': 'Song', 'expected_artist': 'Artist'})
    mbid = '80951433-fbbf-45e8-b906-e820652b9ca5'
    file = local(path)
    file['acoustid'] = {'status': 'ok', 'recordings': [
        {'mbid': mbid, 'title': 'Song', 'artist': 'Artist', 'score': .99}]}
    evidence = ToolEvidence(evidence_db)
    evidence.compare([file], [{'title': 'Song', 'artist': 'Artist', 'recording_mbid': mbid}],
                     {'pairs': [{'file_index': 0, 'slot_index': 0, 'confidence': 'clear'}]}, 'musicbrainz')
    assert file['tool_checks'][0]['state'] == 'obsolete'
    with evidence_db._get_connection() as conn:
        conn.execute('UPDATE repair_findings SET details_json=?', (json.dumps({'new': 'evidence'}),))
    evidence.finish()
    assert status(evidence_db) == 'pending'
