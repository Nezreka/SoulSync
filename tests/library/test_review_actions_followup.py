"""Apply real plans in isolated audio/SQLite fixtures, including explicit remediation."""
import json
from pathlib import Path
import pytest
from tests.reconciliation.support.acceptance import build_album, scan_case, RELEASE
from core.library.reconciliation_fulfillment import record_request_on_connection
from core.repair_jobs.library_reconciliation import LibraryReconciliationJob


def test_wishlist_and_exact_wanted_slot_commit_together(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'partial')
    case = scan_case(album)
    intent = next(item for item in case['items'] if item['finding_type'] == 'reconciliation_intent')
    slot = intent['details']['missing_tracks'][0]
    monkeypatch.setattr('core.library.manual_library_match.get_match_for_track', lambda *a: {'existing': True})
    payload = {'id': slot['source_track_id'], 'name': slot['title'], 'provider': 'musicbrainz',
               'artists': [{'name': 'Acceptance Artist'}], 'album': {'id': RELEASE, 'name': 'Northbound'}}
    result = album.db.add_to_wishlist_detailed(payload, user_initiated=True,
        reconciliation_request={'details': intent['details'], 'slot': slot})
    assert result['applied'], result
    with album.db._get_connection() as conn:
        assert json.loads(conn.execute('SELECT slots_json FROM reconcile_wanted_slots').fetchone()[0]) == [slot['wanted_key']]
    case = scan_case(album)
    # Only the other genuinely undecided missing slot is asked about.
    question = next(item for item in case['items'] if item['finding_type'] == 'reconciliation_intent')
    assert [item['title'] for item in question['details']['missing_tracks']] == ['Afterglow']
    missing = next(item for item in case['items'] if item['finding_type'] == 'reconciliation_missing')
    assert missing['details']['fulfillment_state'] == 'wishlisted'
    with album.db._get_connection() as conn:
        record_request_on_connection(conn, intent['details'], intent['details']['missing_tracks'][1])
    case = scan_case(album)
    assert not any(item['finding_type'] == 'reconciliation_intent' for item in case['items'])
    # A restart reads the exact same choices, independent of the album's old path.
    assert LibraryReconciliationJob()._intents(album.context)['10'][3]


def test_failed_intent_write_rolls_back_wishlist(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    def fail(*args):
        raise RuntimeError('intent write failed')
    monkeypatch.setattr('core.library.reconciliation_fulfillment.record_request_on_connection', fail)
    result = album.db.add_to_wishlist_detailed({'id': 'wanted', 'name': 'New', 'artists': [{'name': 'Artist'}],
        'album': {'id': RELEASE, 'name': 'Northbound'}}, user_initiated=True,
        reconciliation_request={'details': {}, 'slot': {}})
    assert not result['applied']
    with album.db._get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM wishlist_tracks').fetchone()[0] == 0


def test_requested_replacement_stays_visible_when_the_old_file_is_already_matched(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    item = next(row for row in case['items'] if row.get('file_path') == str(album.paths[0]))
    result = album.worker._fix_reconciliation_extra('track', 1, str(album.paths[0]),
        {**item['details'], '_fix_action': 'request_missing'})
    assert result['success'], result
    case = scan_case(album)
    waiting = [row for row in case['items'] if row['details'].get('replacement_request')]
    assert len(waiting) == 1
    assert waiting[0]['category'] == 'pending_fulfillment'
    assert waiting[0]['details']['fulfillment_state'] == 'wishlisted'
    assert case['album_plan']['counts'].get('missing', 0) == 0
    with album.db._get_connection() as conn:
        assert conn.execute('SELECT state FROM reconcile_fulfillment').fetchone()[0] == 'requested'


@pytest.mark.parametrize('mode', ['keep', 'quarantine'])
def test_existing_file_choice_is_staged_then_applied_with_both_files_guarded(tmp_path, monkeypatch, mode):
    album = build_album(tmp_path, monkeypatch, 'supported')
    from tests.reconciliation.support.audio import _minimal_flac
    path = album.folder / 'unidentified.flac'
    _minimal_flac(path, {'title': 'Unidentified', 'artist': 'Unknown', 'album': 'Northbound'})
    album.durations[str(path)] = 200000
    album.context.acoustid_client.matches[path.name] = []
    with album.db._get_connection() as conn:
        conn.execute("INSERT INTO tracks(id,album_id,artist_id,title,file_path) VALUES(3,10,1,'Unidentified',?)", (str(path),))
    case = scan_case(album)
    first = next(item for item in case['items'] if item['details'].get('track_id') == '1' or item['details'].get('track_id') == 1)
    alternative = first['details']['replacement_files'][0]
    choice = 'existing:' + mode + ':' + alternative['track_id']
    result = album.worker.apply_review_decision(case['case_id'], first['id'], evidence_token=first['evidence_token'],
        action='fix', fix_action=choice, schedule_refresh=False)
    assert result['success'], result
    assert all(path.exists() for path in album.paths)
    monkeypatch.setattr('core.repair_worker._server_playlist_membership', lambda: {})
    result = album.worker._apply_reviewed_finding(first['id'])
    assert result['success'], result
    assert Path(alternative['file_path']).exists()
    assert album.paths[0].exists() == (mode == 'keep')
    with album.db._get_connection() as conn:
        assert conn.execute('SELECT file_path FROM reconcile_track_assignment').fetchone()[0] == alternative['file_path']


def test_exact_target_allows_small_duration_conflict_replacement(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    item = case['items'][0]
    details = item['details']
    details.update(file_duration_ms=162190, expected_duration_ms=156000)
    # Exercise server capability validation, independent of a severity threshold.
    with album.db._get_connection() as conn:
        conn.execute('UPDATE repair_findings SET finding_type=?, details_json=? WHERE id=?',
                     ('reconciliation_unresolved', json.dumps(details), item['id']))
    case = album.worker.get_review_case(case['case_id'])
    item = next(row for row in case['items'] if row['id'] == item['id'])
    result = album.worker.apply_review_decision(case['case_id'], item['id'], evidence_token=item['evidence_token'],
        action='fix', fix_action='replace', schedule_refresh=False)
    assert result['success'], result
    assert all(path.exists() for path in album.paths)


def test_tag_writer_reuses_the_open_file_for_placeholder_guards(tmp_path, monkeypatch):
    import core.tag_writer as writer
    from tests.reconciliation.support.audio import _minimal_flac
    path = tmp_path / 'song.flac'
    _minimal_flac(path, {'title': 'Song', 'artist': 'Artist', 'album': 'Album'})
    original, opens = writer.MutagenFile, []
    def open_audio(path):
        opens.append(path)
        return original(path)
    monkeypatch.setattr(writer, 'MutagenFile', open_audio)
    result = writer.write_tags_to_file(str(path), {'title': 'Corrected'}, embed_cover=False)
    assert result['success'], result
    assert opens.count(str(path)) == 1
    assert len(opens) == 2  # The atomic writer separately validates its new temporary file.


def test_byte_preserving_move_retains_tag_cache_but_tag_writes_invalidate_it(tmp_path, monkeypatch):
    from core.library.reconciliation_evidence import ReconciliationEvidence, evidence_path_before_move, preserve_audio_evidence
    album = build_album(tmp_path, monkeypatch, 'supported')
    scan_case(album)
    old = album.paths[0]
    alias = tmp_path / 'alias'
    alias.symlink_to(album.root, target_is_directory=True)
    alternate = alias / old.relative_to(album.root)
    new = old.with_name('Moved.flac')
    stat = old.stat()
    with album.db._get_connection() as conn:
        cache_path = evidence_path_before_move(conn.cursor(), str(alternate))
        assert cache_path == str(old)
        old.rename(new)
        preserve_audio_evidence(conn.cursor(), cache_path, str(new), size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns, preserve_tags=True)
        assert conn.execute('SELECT tags_json FROM reconcile_file_tags WHERE file_path=?', (str(new),)).fetchone()
        preserve_audio_evidence(conn.cursor(), str(new), str(new), size_bytes=new.stat().st_size, mtime_ns=new.stat().st_mtime_ns)
        assert not conn.execute('SELECT 1 FROM reconcile_file_tags WHERE file_path=?', (str(new),)).fetchone()
