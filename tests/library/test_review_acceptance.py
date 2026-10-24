"""Fixed scan → persisted queue → browser acceptance cases.

Only catalogue/fingerprint responses and duration (the tiny FLAC has no audio
frames) are controlled. Inventory, tags, matching, proposals, SQLite persistence,
decision authority and queue projection use production code.
"""
from __future__ import annotations

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
from core.repair_review_queue import ready_batch_preview
from database.music_database import MusicDatabase
from tests.library.test_reconciliation_end_to_end import _Acoustid, _minimal_flac


CORPUS = Path(__file__).resolve().parents[2] / 'webui/tests/fixtures/review-acceptance'
RELEASE = '11111111-1111-4111-8111-111111111111'
OTHER_RELEASE = '22222222-2222-4222-8222-222222222222'
ARTIST = 'Acceptance Artist'
ALBUM = 'Northbound'
SCENARIOS = (
    'supported', 'metadata_only', 'recording_conflict', 'compatible_qualifier',
    'different_performance', 'repeated_recording', 'missing_metadata',
    'contradictory_metadata', 'numbering_metadata_conflict', 'duplicate', 'companion', 'unmatched_companion',
    'partial',
)


def recording_id(index):
    return f'00000000-0000-4000-8000-{index:012d}'


def build_album(tmp_path, monkeypatch, scenario):
    import core.repair_jobs.library_reconciliation as job

    root = tmp_path / 'music'
    folder = root / ARTIST / ALBUM
    folder.mkdir(parents=True)
    slots = [
        {'id': recording_id(i), 'name': title, 'artists': [{'name': ARTIST}],
         'track_number': i, 'disc_number': 1, 'duration_ms': 190000 + i * 10000}
        for i, title in enumerate(('Home', 'Open Water'), 1)
    ]
    local_titles = ['Home', 'Open Water']
    if scenario in ('compatible_qualifier', 'different_performance'):
        local_titles[0] = 'Home (live at Wembley, 12 July 1986)'
        slots[0]['name'] = ('Home (live at Wembley, London, 1986-07-12)'
                            if scenario == 'compatible_qualifier'
                            else 'Home (live at Wembley, 1986-07-13)')
    if scenario == 'repeated_recording':
        slots.append({**slots[0], 'track_number': 3})
        local_titles.append(local_titles[0])
    if scenario == 'partial':
        slots.append({**slots[1], 'id': recording_id(3), 'name': 'Afterglow',
                      'track_number': 3, 'duration_ms': 220000})
        local_titles = local_titles[:1]

    db = MusicDatabase(str(tmp_path / 'music.db'))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists(id,name,server_source) VALUES(1,?,'test')", (ARTIST,))
        conn.execute("""INSERT INTO albums(id,title,artist_id,server_source,
            canonical_source,canonical_album_id) VALUES(10,?,1,'test','musicbrainz',?)""",
                     (ALBUM, RELEASE))
    db.set_album_canonical(10, 'musicbrainz', RELEASE, score=1, locked=True)
    paths, matches, durations = [], {}, {}
    for i, title in enumerate(local_titles, 1):
        path = folder / f'{i:02d} - {title}.flac'
        tags = {'title': title, 'artist': ARTIST, 'albumartist': ARTIST,
                'album': ALBUM, 'tracknumber': str(i), 'discnumber': '1',
                'musicbrainz_albumid': RELEASE}
        if scenario == 'metadata_only':
            tags['musicbrainz_trackid'] = slots[i - 1]['id']
            if i == 2:
                tags.pop('album')
        if scenario == 'missing_metadata' and i == 2:
            tags.pop('album')
            tags.pop('musicbrainz_albumid')
        if scenario in ('contradictory_metadata', 'numbering_metadata_conflict') and i == 2:
            tags.update(album='Northbound (Deluxe)', musicbrainz_albumid=OTHER_RELEASE)
            if scenario == 'numbering_metadata_conflict':
                tags['tracknumber'] = '7'
        _minimal_flac(path, tags)
        paths.append(path)
        slot = slots[i - 1]
        matches[path.name] = {'mbid': slot['id'], 'title': title, 'artist': ARTIST, 'score': .99}
        durations[str(path)] = slot['duration_ms']
        if scenario == 'metadata_only':
            matches[path.name] = []
        if scenario == 'recording_conflict' and i == 1:
            matches[path.name] = {'mbid': recording_id(9), 'title': 'Unrelated Song',
                                  'artist': 'Another Artist', 'score': .99}
        with db._get_connection() as conn:
            conn.execute("""INSERT INTO tracks(id,album_id,artist_id,title,track_number,
                disc_number,duration,file_path) VALUES(?,10,1,?,?,1,?,?)""",
                         (i, title, i, slot['duration_ms'], str(path)))
    if scenario in ('duplicate', 'companion', 'unmatched_companion'):
        path = paths[0].with_suffix('.mp3' if 'companion' in scenario else '.copy.flac')
        if 'companion' in scenario:
            # Parseable MPEG headers plus real ID3 tags; fingerprinting is
            # controlled, as it is for the frame-less FLAC fixtures.
            path.write_bytes((b'\xff\xfb\x90\x00' + bytes(413)) * 4)
            tags = EasyID3()
            for key, value in FLAC(str(paths[0])).items():
                tags[key] = value
            tags.save(str(path))
        else:
            path.write_bytes(paths[0].read_bytes())
        matches[path.name] = deepcopy(matches[paths[0].name])
        durations[str(path)] = durations[str(paths[0])]
        paths.append(path)
        with db._get_connection() as conn:
            conn.execute("""INSERT INTO tracks(id,album_id,artist_id,title,track_number,
                disc_number,duration,file_path) VALUES(3,10,1,'Home',1,1,200000,?)""", (str(path),))
    if scenario == 'unmatched_companion':
        slots[0]['name'] = 'Different Song'
        slots[0]['id'] = recording_id(8)

    client = _Acoustid(matches)
    worker = RepairWorker(database=db, transfer_folder=str(root))
    monkeypatch.setattr(RepairWorker, '_schedule_album_refresh', lambda *a, **k: True)
    monkeypatch.setattr(RepairWorker, 'acoustid_client', property(lambda self: client))
    monkeypatch.setattr(job, 'all_library_roots', lambda context: [str(root)])
    reader = job._read_file_tags
    monkeypatch.setattr(job, '_read_file_tags',
                        lambda path: {**reader(path), 'duration_ms': durations[str(path)]})
    monkeypatch.setattr(job, 'get_album_tracks_for_source', lambda *a, **k: deepcopy(slots))
    monkeypatch.setattr(job, 'get_album_for_source', lambda *a, **k: {
        'name': ALBUM, 'artists': [{'name': ARTIST}], 'musicbrainz_release_id': RELEASE,
        'country': 'GB', 'date': '2001-01-01', 'media': [{'format': 'Digital Media'}],
    })
    monkeypatch.setattr(job, '_search_release_candidates', lambda *a, **k: [])
    monkeypatch.setattr(job, '_release_group_candidates', lambda *a, **k: [])
    config = SimpleNamespace(get=lambda key, default=None:
        True if key == 'lossy_copy.enabled' and 'companion' in scenario else default)
    context = JobContext(db=db, transfer_folder=str(root), config_manager=config,
                         acoustid_client=client, create_finding=worker._create_finding)
    return SimpleNamespace(db=db, worker=worker, context=context, paths=paths,
                           folder=folder, root=root, slots=slots, durations=durations)


def scan_case(album):
    result = LibraryReconciliationJob().scan(album.context)
    assert not result.errors
    with album.db._get_connection() as conn:
        case_id = conn.execute(
            "SELECT case_id FROM reconcile_album_plans WHERE state!='obsolete'").fetchone()[0]
    case = album.worker.get_review_case(case_id)
    assert case is not None
    return case


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
            return {normalize(k): normalize(v, k) for k, v in value.items()}
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
