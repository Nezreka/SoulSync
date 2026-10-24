"""General matching rules derived from edition and catalogue disagreements."""
from copy import deepcopy

import pytest

from core.library.reconciliation_plan import reconcile_folder
from core.repair_jobs.library_reconciliation import (
    _release_display_metadata, _supported_current_edition, ScoredCandidate,
)
from tests.library.test_review_acceptance import build_album, scan_case


def version_pair():
    slots = [{'title': title, 'artist': 'Artist', 'artist_names': ['Artist'],
              'recording_mbid': f'recording-{i}', 'source_track_id': f'recording-{i}',
              'track_number': i, 'disc_number': 1, 'duration_ms': 200000}
             for i, title in enumerate(('Song', 'Song [DLX]'), 1)]
    files = [{'path': f"/album/{slot['track_number']:02} - {slot['title']}.flac",
              'tags': {'title': slot['title'], 'artist': 'Artist',
                       'mbid': slot['recording_mbid'], 'track_number': slot['track_number'],
                       'disc_number': 1, 'duration_ms': 200000},
              'acoustid': {'status': 'ok', 'recordings': [
                  {'mbid': 'recording-1', 'title': 'Song', 'artist': 'Artist', 'score': .99}]}}
             for slot in slots]
    return files, slots


@pytest.mark.parametrize('weakness', ['', 'id', 'position', 'duration', 'duplicate', 'performance'])
def test_joint_evidence_distinguishes_named_versions_without_hiding_weakness(weakness):
    files, slots = version_pair()
    if weakness == 'id': files[1]['tags']['mbid'] = ''
    if weakness == 'position': files[1]['tags']['track_number'] = 3
    if weakness == 'duration': files[1]['tags']['duration_ms'] += 10000
    if weakness == 'duplicate':
        files.append(deepcopy(files[0]))
        files[-1]['path'] = '/other/01 - Song.flac'
    if weakness == 'performance': files[1]['tags']['title'] = 'Song (live)'
    plan = reconcile_folder(files, slots)
    if not weakness:
        assert [(pair['file_index'], pair['slot_index']) for pair in plan['pairs']] == [(0, 0), (1, 1)]
        assert all(pair['confidence'] == 'clear' for pair in plan['pairs'])
    else:
        assert plan['extra_files'] or any(pair['confidence'] != 'clear' for pair in plan['pairs'])


def test_competitor_explanation_names_the_file_and_slot():
    files, slots = version_pair()
    files[1]['tags']['mbid'] = ''
    plan = reconcile_folder(files, slots)
    original = next(pair for pair in plan['pairs'] if pair['slot_index'] == 0)
    assert original['competitors'][0]['file_path'] == files[1]['path']
    assert original['competitors'][0]['assigned_title'] == slots[1]['title']


def test_selected_release_can_review_credit_disagreement_without_equating_artists(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    for recording in album.context.acoustid_client.matches.values():
        recording.update(artist='Different Catalogue Credit', mbid='other-recording')
    original_bytes = [path.read_bytes() for path in album.paths]
    case = scan_case(album)
    assert case['album_plan']['state'] != 'release_conflict'
    assert len(case['album_plan']['assignments']) == 2
    assert all(item['details'].get('catalogue_disagreement') for item in case['items'])
    result = album.worker.apply_review_decision_group(case['case_id'], 'catalogue_assignments', action='fix')
    assert result['success'] and result['saved'] == 2, result
    assert [path.read_bytes() for path in album.paths] == original_bytes
    refreshed = scan_case(album)
    assert all(row['confidence'] == 'manual' for row in refreshed['album_plan']['assignments'])
    assert all(item['finding_type'] == 'reconciliation_metadata' for item in refreshed['items']), [
        (item['finding_type'], item['description'], item['details'].get('ambiguity_reasons'))
        for item in refreshed['items']]


def test_catalogue_disagreement_never_becomes_an_automatic_alias():
    files, slots = version_pair()
    files, slots = files[:1], slots[:1]
    files[0]['acoustid']['recordings'][0].update(artist='Someone Else', mbid='another')
    assert not reconcile_folder(files, slots)['pairs']
    reviewed = reconcile_folder(files, slots, catalogue_review=True)
    assert reviewed['pairs'][0]['confidence'] == 'ambiguous'
    assert reviewed['pairs'][0]['catalogue_disagreement']
    files[0]['acoustid']['recordings'][0]['title'] = 'Unrelated Song'
    assert not reconcile_folder(files, slots, catalogue_review=True)['pairs']


def test_provider_display_uses_full_credit_and_native_artwork():
    itunes = _release_display_metadata('itunes', '1', {
        'collectionName': 'Album', 'artistName': 'Artist', 'artworkUrl100': 'https://example.com/100x100bb.jpg'})
    assert itunes['release_artist'] == 'Artist'
    assert itunes['image_url']
    deezer = _release_display_metadata('deezer', '2', {
        'title': 'Album', 'artist': {'name': 'Artist'}, 'cover_xl': 'https://example.com/cover.jpg'})
    assert deezer['image_url'] == 'https://example.com/cover.jpg'
    mb = _release_display_metadata('musicbrainz', '3', {
        'title': 'Album', 'artist-credit': [{'name': 'Artist', 'joinphrase': ' & '}, {'name': 'Guest'}]})
    assert mb['release_artist'] == 'Artist & Guest'


def test_current_edition_requires_consistent_tags_and_complete_supported_coverage():
    plan = {'extra_files': [], 'missing_slots': []}
    current = ScoredCandidate(2, 2, 0, 'musicbrainz', 'current', [], plan)
    alternative = ScoredCandidate(2, 2, 0, 'musicbrainz', 'digital', [], plan)
    files = [{'tags': {'musicbrainz_release_id': 'current'}} for _ in range(2)]
    album = {'canonical_source': 'musicbrainz', 'canonical_album_id': 'current'}
    assert _supported_current_edition(files, album, [alternative, current]) == current
    files[1]['tags']['musicbrainz_release_id'] = 'other'
    assert _supported_current_edition(files, album, [alternative, current]) is None
