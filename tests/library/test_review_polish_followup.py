"""Regression boundaries for playlist overrides, kept albums, and path aliases."""
import json
import os

from core.library.file_identity import same_file, planned_metadata
from core.library.reconciliation_organization import organization_context
from core.library.reconciliation_plan import audio_contradicts_local_title, versions_compatible, _same_base_recording_title, reconcile_folder
from core.library_reorganize import _rename_track_in_place, _display_relative_to_root, _plan_from_tags
from core.library import playlist_membership
from core.repair_review_queue import list_cases
from tests.reconciliation.support.acceptance import build_album, scan_case, ARTIST


def test_alias_rename_is_noop_but_distinct_collision_is_preserved(tmp_path):
    root = tmp_path / 'real'
    root.mkdir()
    path = root / 'song.flac'
    path.write_bytes(b'audio')
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    before = path.stat()
    assert _rename_track_in_place(str(alias / path.name), str(path)) == (True, None)
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert path.read_bytes() == b'audio'
    assert _display_relative_to_root(str(alias / path.name), str(root)) == path.name
    other = root / 'other.flac'
    other.write_bytes(b'different')
    assert _rename_track_in_place(str(path), str(other)) == (False, 'destination already exists')
    assert other.read_bytes() == b'different'


def test_disc_overlay_follows_verified_alias_and_stable_id(tmp_path, monkeypatch):
    path = tmp_path / 'song.flac'
    path.write_bytes(b'audio')
    alias = tmp_path / 'stored.flac'
    os.link(path, alias)
    seen = []
    def read(path, **kwargs):
        seen.append(kwargs['planned_metadata'])
        return {'name': 'Album'}, {'disc_number': kwargs['planned_metadata']['disc_number'], 'name': 'Song'}, None
    monkeypatch.setattr('core.library.reorganize_tag_source.read_album_track_from_file', read)
    plan = _plan_from_tags({}, [{'id': 'track', 'file_path': str(alias)}], lambda _: str(alias), {str(path): {'disc_number': 2}})
    assert plan['items'][0]['api_track']['disc_number'] == 2
    assert plan['total_discs'] == 2
    assert planned_metadata({'track:track': {'disc_number': 3}}, {'id': 'track'}, str(path)) == {'disc_number': 3}
    assert not same_file(path, tmp_path / 'missing')


def test_kept_metadata_does_not_influence_folder_name(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    context = organization_context(album.db, '10', [{'plan_keep': True, 'db_album_title': 'Wrong collection', 'track_id': '1', 'changes': {'filename': {'new': 'wrong.flac'}}}])
    assert context['album_title'] != 'Wrong collection'
    assert context['filenames']['1'] != 'wrong.flac'


def test_unplugged_punctuation_and_recording_context_are_equivalent():
    file = {'path': '08 - Song - MTV Unplugged.mp3', 'tags': {'title': 'Song - MTV Unplugged', 'artist': ARTIST, 'duration_ms': 271080},
            'acoustid': {'status': 'ok', 'recordings': [{'mbid': 'recording', 'title': 'Song', 'artist': ARTIST, 'score': .98}]}}
    slot = {'title': 'Song (MTV Unplugged)', 'artist': ARTIST, 'duration_ms': 271200, 'recording_mbid': 'recording', 'recording_disambiguation': 'live'}
    assert _same_base_recording_title('Song', file['tags']['title'])
    assert not audio_contradicts_local_title(file)
    assert versions_compatible(file, slot)
    assert reconcile_folder([file], [slot])['pairs'][0]['confidence'] == 'clear'
    assert not versions_compatible(file, {**slot, 'title': 'Song (live at another venue)'})
    assert not _same_base_recording_title('Song', 'Song - Part Two')


def test_leave_album_closes_review_without_touching_files_or_membership(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    originals = [path.read_bytes() for path in album.paths]
    assert not album.worker.leave_review_album(case['case_id'], 'stale')['success']
    result = album.worker.leave_review_album(case['case_id'], case['plan_signature'])
    assert result['success'], result
    assert album.worker.get_review_case(case['case_id'])['status'] == 'completed'
    assert [path.read_bytes() for path in album.paths] == originals
    scan_case(album)
    assert not list_cases(album.db)['items']


def test_playlist_override_is_scoped_to_saved_action_and_current_references(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'recording_conflict')
    case = scan_case(album)
    finding = next(row for row in case['items'] if row.get('file_path') == str(album.paths[0]))
    assert album.worker.apply_review_decision(case['case_id'], finding['id'], evidence_token=finding['evidence_token'], action='fix', fix_action='quarantine')['success']
    case = album.worker.get_review_case(case['case_id'])
    finding = next(row for row in case['items'] if row['id'] == finding['id'])
    refs = {str(finding['details']['track_id']): [{'id': 'playlist', 'title': 'My playlist'}]}
    monkeypatch.setattr(playlist_membership, 'server_playlist_references', lambda **kwargs: refs)
    assert not album.worker.acknowledge_review_playlists(case['case_id'], finding['id'], evidence_token=finding['evidence_token'], playlist_ids=['old'])['success']
    result = album.worker.acknowledge_review_playlists(case['case_id'], finding['id'], evidence_token=finding['evidence_token'], playlist_ids=['playlist'])
    assert result['success'], result
    after = album.worker.get_review_case(case['case_id'])
    details = next(row for row in after['items'] if row['id'] == finding['id'])['details']
    assert not album.worker._playlist_removal_blocked(details)
    refs[str(details['track_id'])].append({'id': 'new', 'title': 'New playlist'})
    assert album.worker._playlist_removal_blocked(details)
    assert album.paths[0].exists()


def test_failed_organization_report_survives_relocation_and_allows_only_folder_retry(tmp_path, monkeypatch):
    from core.repair_operation_reports import save_report
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    for item in case['items']:
        assert album.worker._apply_reviewed_finding(item['id'])['success']
    with album.db._get_connection() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS reconcile_case_redirects (old_case_id TEXT PRIMARY KEY,new_case_id TEXT NOT NULL)')
        conn.execute('INSERT INTO reconcile_case_redirects VALUES (?,?)', ('previous-case', case['case_id']))
    save_report(album.db, {'operation_id': 'failed-organization', 'review_case_ids': ['previous-case'], 'running': False, 'fixed': 2, 'organize_failed': 1, 'error': 'destination already exists'})
    completed = album.worker.get_review_case(case['case_id'])
    assert completed['last_operation']['organize_failed'] == 1
    assert completed['organization_retry_signature']
    calls = []
    monkeypatch.setattr(album.worker, 'start_bulk_fix', lambda **kwargs: calls.append(kwargs) or {'started': True})
    assert not album.worker.retry_review_organization(case['case_id'], 'stale')['started']
    assert album.worker.retry_review_organization(case['case_id'], completed['organization_retry_signature'])['started']
    assert calls[0]['finding_ids'] == [] and calls[0]['organization_retry']
    # A changed file invalidates the previously reviewed folder retry.
    album.paths[0].write_bytes(album.paths[0].read_bytes() + b'changed')
    assert not album.worker.retry_review_organization(case['case_id'], completed['organization_retry_signature'])['started']


def test_folder_retry_preserves_approved_title_and_current_filenames(tmp_path, monkeypatch):
    from core.library.reconciliation_organization import retry_organization_context
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    finding = case['items'][0]
    details = {**finding['details'], 'db_album_title': 'Approved album',
               'changes': {'filename': {'new': 'obsolete-filename.flac'}}}
    report = {'review_case_ids': [case['case_id']]}
    with album.db._get_connection() as conn:
        conn.execute("UPDATE repair_findings SET details_json=?,status='resolved',user_action='reconciliation_metadata' WHERE id=?",
                     (json.dumps(details), finding['id']))
    legacy = retry_organization_context(album.db, '10', report)
    assert legacy['album_title'] == 'Approved album'
    assert 'obsolete-filename.flac' not in legacy['filenames'].values()
    durable = retry_organization_context(album.db, '10', {**report, 'organization_titles': {'10': 'Saved operation title'}})
    assert durable['album_title'] == 'Saved operation title'
    with album.db._get_connection() as conn:
        conn.execute("UPDATE repair_findings SET user_action='plan_keep' WHERE id=?", (finding['id'],))
    assert retry_organization_context(album.db, '10', report)['album_title'] != 'Approved album'


def test_leave_album_also_closes_old_folder_retry_without_erasing_failure(tmp_path, monkeypatch):
    from core.repair_operation_reports import save_report, load_report
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    for item in case['items']:
        assert album.worker._apply_reviewed_finding(item['id'])['success']
    save_report(album.db, {'operation_id': 'failed-folder', 'review_case_ids': [case['case_id']],
                          'running': False, 'fixed': 2, 'organize_failed': 1, 'error': 'collision'})
    case = album.worker.get_review_case(case['case_id'])
    assert case['organization_retry_signature']
    assert album.worker.leave_review_album(case['case_id'], case['plan_signature'])['success']
    after = album.worker.get_review_case(case['case_id'])
    assert not after.get('organization_retry_signature')
    assert not after.get('last_operation')
    assert load_report(album.db, 'failed-folder')['error'] == 'collision'


def test_queue_summary_preserves_current_album_title(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    summary = next(item for item in list_cases(album.db)['items'] if item['case_id'] == case['case_id'])
    assert summary['current_album_title'] == case['current_album_title']
    with album.db._get_connection() as conn:
        for finding in case['items']:
            details = {**finding['details'], 'library_album_title': 'Current collection',
                       'album_title': 'Proposed edition'}
            conn.execute('UPDATE repair_findings SET details_json=? WHERE id=?',
                         (json.dumps(details), finding['id']))
    found = list_cases(album.db, q='current collection')['items']
    assert len(found) == 1 and found[0]['current_album_title'] == 'Current collection'


def test_mixed_release_ids_are_ready_when_one_edition_clearly_fits_every_file(tmp_path, monkeypatch):
    from mutagen.flac import FLAC
    from tests.reconciliation.support.acceptance import OTHER_RELEASE
    album = build_album(tmp_path, monkeypatch, 'supported')
    tags = FLAC(str(album.paths[1]))
    tags['musicbrainz_albumid'] = OTHER_RELEASE
    tags.save()
    case = scan_case(album)
    assert case['plan']['can_apply_ready']
    assert any(row['details'].get('album_consistency', {}).get('musicbrainz_release_id') for row in case['items'])
    assert all(not row['details'].get('metadata_review_reason') for row in case['items'])


def test_playlist_override_is_consumed_by_guarded_apply(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'recording_conflict')
    case = scan_case(album)
    finding = next(row for row in case['items'] if row.get('file_path') == str(album.paths[0]))
    assert album.worker.apply_review_decision(case['case_id'], finding['id'], evidence_token=finding['evidence_token'], action='fix', fix_action='quarantine')['success']
    case = album.worker.get_review_case(case['case_id'])
    finding = next(row for row in case['items'] if row['id'] == finding['id'])
    monkeypatch.setattr(playlist_membership, 'server_playlist_references', lambda **kwargs: {str(finding['details']['track_id']): [{'id': 'p', 'title': 'Playlist'}]})
    assert album.worker.acknowledge_review_playlists(case['case_id'], finding['id'], evidence_token=finding['evidence_token'], playlist_ids=['p'])['success']
    result = album.worker._apply_reviewed_finding(finding['id'])
    assert result['success'], result
    assert not album.paths[0].exists()
    assert album.paths[1].exists()
