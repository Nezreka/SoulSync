"""Fixed scan → persisted queue → browser acceptance cases.

Only catalogue/fingerprint responses and duration (the tiny FLAC has no audio
frames) are controlled. Inventory, tags, matching, proposals, SQLite persistence,
decision authority and queue projection use production code.
"""
from __future__ import annotations

from tests.reconciliation.support.acceptance import (
    recording_id, build_album, scan_case, RELEASE, OTHER_RELEASE, ARTIST, ALBUM,
)

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from mutagen.flac import FLAC
from mutagen.easyid3 import EasyID3

from core.repair_jobs.base import JobContext
from core.repair_jobs.library_reconciliation import LibraryReconciliationJob
from core.repair_worker import RepairWorker
from core.reconciliation.queue import ready_batch_preview
from database.music_database import MusicDatabase
from tests.reconciliation.support.audio import _Acoustid, _minimal_flac


CORPUS = Path(__file__).resolve().parents[2] / 'webui/tests/fixtures/review-acceptance'
SCENARIOS = (
    'supported', 'metadata_only', 'recording_conflict', 'compatible_qualifier',
    'different_performance', 'repeated_recording', 'missing_metadata',
    'contradictory_metadata', 'numbering_metadata_conflict', 'duplicate', 'companion', 'unmatched_companion',
    'partial',
)








def assert_snapshot(name, case, tmp_path, album):
    """Only nondeterministic locations, timestamps and opaque tokens are masked.

    Recommendations, explanations, changes, inventory and catalogue identities
    are kept verbatim. Never update these files as an incidental test side effect.
    """
    hashes = {}
    opaque = {'signature', 'plan_signature', 'evidence_signature', 'evidence_token',
              'fingerprint_key', 'folder_audio_path_digest'}

    def normalize(value, key=''):
        if key == 'case_ids':
            return [normalize(item, 'case_id') for item in value]
        if key == 'case_id':
            return ('album:10:relocated' if case['folder'].endswith('reorganized')
                    else 'album:10:acceptance')
        if key in ('created_at', 'updated_at', 'mtime_ns') and value is not None:
            return 1 if key == 'mtime_ns' else '2025-01-13 10:00:00'
        if key in opaque and value:
            return hashes.setdefault((key, value), f'{name}:{key}:{len(hashes)}')
        if key == 'directory_observations':
            return {normalize(k): 1 for k in value}
        if isinstance(value, dict):
            # Filesystem identity is an apply guard, not display evidence.
            # Its device/inode/ctime are specific to this temporary fixture.
            return {normalize(k): normalize(v, k) for k, v in value.items()
                    if k != 'file_identity'}
        if isinstance(value, list):
            return [normalize(v) for v in value]
        if isinstance(value, str):
            return value.replace(str(tmp_path / 'music'), '/music')
        return value

    result = normalize({
        "case": case,
        "queue": album.worker.get_review_cases(job_id="library_reconciliation"),
        "summary": album.worker.get_review_summary(job_id="library_reconciliation"),
        "ready_preview": ready_batch_preview(album.db, case_id=case["case_id"]),
        "finding_types": [row for row in album.worker.get_finding_type_catalog()
                          if row['type'] in {item['finding_type'] for item in case['items']}],
    })
    path = CORPUS / f'{name}.json'
    if os.environ.get('UPDATE_REVIEW_ACCEPTANCE') == '1':
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    assert path.exists(), 'Generate and review the acceptance corpus explicitly (see README)'
    assert result == json.loads(path.read_text()), (
        f'{name}: scan/queue output changed; inspect the semantic and rendered differences')


def assert_recommendation(scenario, album, case):
    rows = case['album_plan']['assignments']
    local = {row['file_path']: row for row in rows if row.get('file_path')}
    assert sorted(local) == sorted(str(path) for path in album.paths)
    assert len(local) == sum(bool(row.get('file_path')) for row in rows)
    expected_ready = scenario in {
        'supported', 'metadata_only', 'compatible_qualifier', 'missing_metadata', 'companion',
    }
    assert case['plan']['can_apply_ready'] is expected_ready
    preview = ready_batch_preview(album.db, case_id=case['case_id'])
    assert bool(preview['albums']) is expected_ready  # No partial album bulk apply.

    for i, path in enumerate(album.paths, 1):
        row = local[str(path)]
        recording = recording_id(1 if i == 3 else i)
        expected_changes = {
            'musicbrainz_recording_id': {'old': '', 'new': recording},
            'library_recording_mbid': {'old': '', 'new': recording},
        }
        if scenario == 'metadata_only':
            expected_changes = {'album': {'old': '', 'new': ALBUM}} if i == 2 else {}
            assert row['assignment_evidence'] == 'metadata'
        if scenario in ('recording_conflict', 'different_performance', 'unmatched_companion') and i != 2:
            expected_changes = {}
            assert row['status'] == 'extra'
        if scenario in ('duplicate', 'repeated_recording') and i != 2:
            expected_changes = {}
        if scenario == 'missing_metadata' and i == 2:
            expected_changes.update(album={'old': '', 'new': ALBUM},
                                    musicbrainz_release_id={'old': '', 'new': RELEASE})
        if scenario in ('contradictory_metadata', 'numbering_metadata_conflict') and i == 2:
            expected_changes.update(album={'old': 'Northbound (Deluxe)', 'new': ALBUM},
                                    musicbrainz_release_id={'old': OTHER_RELEASE, 'new': RELEASE})
            if scenario == 'numbering_metadata_conflict':
                expected_changes['track_number'] = {'old': '7', 'new': '2'}
            assert row['action_state'] == 'manual'
        assert (row.get('changes') or {}) == expected_changes

    if scenario == 'compatible_qualifier':
        assert local[str(album.paths[0])]['confidence'] == 'clear'
        assert local[str(album.paths[0])]['current_title'] != local[str(album.paths[0])]['slot_title']
    if scenario == 'repeated_recording':
        assert [r['slot_track_number'] for r in rows] == [1, 2, 3]
        assert all(r['status'] != 'duplicate' for r in rows)
        assert not any(i['finding_type'] == 'reconciliation_duplicate' for i in case['items'])
    if scenario == 'recording_conflict':
        wrong = local[str(album.paths[0])]
        assert wrong['audio_conflict'] == 'different_recording'
        assert not wrong.get('relationship')  # Not presented as a version of Home.
        assert any(r['status'] == 'missing' and r['slot_title'] == 'Home' for r in rows)
    if scenario == 'duplicate':
        duplicate = next(i for i in case['items'] if i['finding_type'] == 'reconciliation_duplicate')
        assert {r['file_path'] for r in duplicate['details']['duplicate_copies']} == {
            str(album.paths[0]), str(album.paths[2])}
    if 'companion' in scenario:
        assert local[str(album.paths[2])]['companion_of'] == str(album.paths[0])
        assert not any(i['finding_type'] == 'reconciliation_duplicate' for i in case['items'])
    if scenario == 'partial':
        assert [r['slot_title'] for r in rows if r['status'] == 'missing'] == ['Open Water', 'Afterglow']
        with album.db._get_connection() as conn:
            assert conn.execute('SELECT COUNT(*) FROM wishlist_tracks').fetchone()[0] == 0


@pytest.mark.parametrize('scenario', SCENARIOS)
def test_review_acceptance(tmp_path, monkeypatch, scenario):
    album = build_album(tmp_path, monkeypatch, scenario)
    original = {path: path.read_bytes() for path in album.paths}
    case = scan_case(album)
    assert_recommendation(scenario, album, case)
    assert {path: path.read_bytes() for path in album.paths} == original
    assert_snapshot(scenario, case, tmp_path, album)


def test_saved_relocated_and_stale_acceptance(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'contradictory_metadata')
    initial = scan_case(album)
    original = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in album.paths}
    saved = album.worker.apply_review_decision_group(initial['case_id'], 'metadata', action='fix')
    assert saved['success']
    case = album.worker.get_review_case(initial['case_id'])
    assert case['plan']['can_apply_ready']
    assert all(i['details']['plan_approved'] for i in case['items'])
    assert_snapshot('saved', case, tmp_path, album)
    assert scan_case(album)['plan']['can_apply_ready']  # An unchanged rescan retains choices.

    moved = album.folder.with_name('Northbound reorganized')
    album.folder.rename(moved)
    with album.db._get_connection() as conn:
        for path in album.paths:
            target = moved / path.name
            album.durations[str(target)] = album.durations.pop(str(path))
            conn.execute('UPDATE tracks SET file_path=? WHERE file_path=?', (str(target), str(path)))
    album.paths = [moved / path.name for path in album.paths]
    album.folder = moved
    relocated = scan_case(album)
    assert relocated['case_id'] != initial['case_id']
    assert relocated['plan']['can_apply_ready']
    assert all(i['details']['plan_approved'] for i in relocated['items'])
    redirected = album.worker.get_review_case(initial['case_id'])
    assert redirected['case_id'] == relocated['case_id']
    assert_snapshot('relocated', relocated, tmp_path, album)
    reviewed_signature = ready_batch_preview(album.db, case_id=relocated['case_id'])['signature']
    assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in album.paths} == original

    old_item = next(i for i in relocated['items'] if i['file_path'] == str(album.paths[1]))
    audio = FLAC(str(album.paths[1]))
    audio['album'] = ['Another album']
    audio.save()
    changed = scan_case(album)
    changed_item = next(i for i in changed['items'] if i['file_path'] == str(album.paths[1]))
    assert not changed['plan']['can_apply_ready']
    assert not changed_item['details'].get('plan_approved')
    assert changed_item['evidence_token'] != old_item['evidence_token']
    rejected = album.worker.apply_review_decision(
        changed['case_id'], old_item['id'], evidence_token=old_item['evidence_token'], action='fix')
    assert rejected['stale'] and not rejected['success']
    rejected_batch = album.worker.start_review_safe(
        case_id=changed['case_id'], expected_signature=reviewed_signature)
    assert rejected_batch['stale'] and not rejected_batch['started']
    assert_snapshot('stale', changed, tmp_path, album)


def test_selective_request_and_cancellation_acceptance(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'partial')
    case = scan_case(album)
    intent = next(i for i in case['items'] if i['finding_type'] == 'reconciliation_intent')
    wanted = intent['details']['missing_tracks'][0]['wanted_key']
    result = album.worker.apply_review_decision(
        case['case_id'], intent['id'], evidence_token=intent['evidence_token'],
        action='fix', fix_action='partial:' + json.dumps([wanted]))
    assert result['success'], result
    selected = scan_case(album)
    missing = next(i for i in selected['items'] if i['finding_type'] == 'reconciliation_missing')
    assert [r['slot_title'] for r in selected['album_plan']['assignments']
            if r['status'] == 'not_owned'] == ['Afterglow']
    assert_snapshot('selective', selected, tmp_path, album)
    result = album.worker.apply_review_decision(
        selected['case_id'], missing['id'], evidence_token=missing['evidence_token'], action='fix')
    assert result['success'], result
    waiting = scan_case(album)
    assert any(d['category'] == 'pending_fulfillment' for d in waiting['decisions'])
    with album.db._get_connection() as conn:
        wishes = conn.execute('SELECT spotify_track_id,source_info FROM wishlist_tracks').fetchall()
    assert len(wishes) == 1 and wishes[0][0] == recording_id(2)
    assert json.loads(wishes[0][1])['source_album_id'] == RELEASE
    assert_snapshot('waiting', waiting, tmp_path, album)
    assert album.db.clear_wishlist()
    cancelled = scan_case(album)
    assert cancelled['album_plan']['intent_scope'] == 'partial'
    assert any(r.get('fulfillment_state') == 'cancelled' for r in cancelled['album_plan']['assignments'])
    assert [r['slot_title'] for r in cancelled['album_plan']['assignments']
            if r['status'] == 'not_owned'] == ['Afterglow']
    assert_snapshot('cancelled', cancelled, tmp_path, album)


def test_duplicate_keeper_is_a_saved_plan_not_a_file_mutation(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'duplicate')
    case = scan_case(album)
    original = {path: path.read_bytes() for path in album.paths}
    duplicate = next(i for i in case['items'] if i['finding_type'] == 'reconciliation_duplicate')
    result = album.worker.apply_review_decision(
        case['case_id'], duplicate['id'], evidence_token=duplicate['evidence_token'],
        action='fix', fix_action='1')
    assert result['success']
    saved = album.worker.get_review_case(case['case_id'])
    assert saved['plan']['can_apply_ready']
    preview = ready_batch_preview(album.db, case_id=case['case_id'])
    assert preview['file_actions'] == 1
    action = next(a for a in preview['albums'][0]['actions'] if a['file_action'])
    assert action['description'] == (
        f'Keep {album.paths[0]}; move to recoverable quarantine: {album.paths[2]}')
    saved_duplicate = next(i for i in saved['items'] if i['id'] == duplicate['id'])
    assert saved_duplicate['planned_file_action'] == action['description']
    assert {path: path.read_bytes() for path in album.paths} == original
    assert_snapshot('duplicate_saved', saved, tmp_path, album)


def test_keeping_numbering_does_not_approve_remaining_metadata(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'numbering_metadata_conflict')
    case = scan_case(album)
    before = {path: path.read_bytes() for path in album.paths}
    result = album.worker.apply_review_decision_group(case['case_id'], 'numbering', action='dismiss')
    assert result['success']
    kept = album.worker.get_review_case(case['case_id'])
    assert not kept['plan']['can_apply_ready']
    row = next(row for row in kept['album_plan']['assignments'] if row['file_path'] == str(album.paths[1]))
    assert row['action_state'] == 'manual'
    assert row['planned_choice']['category'] == 'numbering'
    assert not row.get('numbering_preview')
    assert 'track_number' not in row['changes']
    assert row['changes']['musicbrainz_release_id'] == {'old': OTHER_RELEASE, 'new': RELEASE}
    assert kept['decisions'][0]['label'] == 'Resolve conflicting album metadata'
    assert {path: path.read_bytes() for path in album.paths} == before
    assert_snapshot('numbering_kept_metadata_conflict', kept, tmp_path, album)


@pytest.mark.parametrize('scenario', ['supported', 'metadata_only', 'missing_metadata',
                                     'compatible_qualifier'])
def test_acceptance_applies_only_proposed_metadata(tmp_path, monkeypatch, scenario):
    album = build_album(tmp_path, monkeypatch, scenario)
    case = scan_case(album)
    assert_recommendation(scenario, album, case)
    before = {path: dict(FLAC(str(path))) for path in album.paths}
    reviewed = {item['id']: json.dumps(item['details']) for item in case['items']}
    remaining = set(reviewed)
    for item in case['items']:
        result = album.worker._apply_reviewed_finding(
            item['id'], reviewed_details=reviewed, remaining_ids=remaining)
        assert result['success'], result
        remaining.remove(item['id'])
    for i, path in enumerate(album.paths, 1):
        expected = deepcopy(before[path])
        if scenario != 'metadata_only':
            expected['musicbrainz_trackid'] = [recording_id(i)]
        if scenario in ('metadata_only', 'missing_metadata') and i == 2:
            expected['album'] = [ALBUM]
        if scenario == 'missing_metadata' and i == 2:
            expected['musicbrainz_albumid'] = [RELEASE]
        assert dict(FLAC(str(path))) == expected  # No unrelated tag rewrite.
    assert all(path.exists() for path in album.paths)
    assert not LibraryReconciliationJob().scan(album.context).errors
    assert album.worker.get_review_cases()['items'] == []
