"""Boundaries for reducing unnecessary queue decisions without partial apply."""
import pytest

from core.library.reconciliation_metadata import credit_preserves_artists
from core.repair_review_policy import _finding_tier, finding_category


@pytest.mark.parametrize(('old', 'new', 'names', 'aliases', 'supported'), [
    ('Bonobo', 'Bonobo; Andreya Triana', ['Bonobo', 'Andreya Triana'], [], True),
    ('Marco Decimo & Ludovico Einaudi', 'Ludovico Einaudi; Marco Decimo', ['Ludovico Einaudi', 'Marco Decimo'], [], True),
    ('The Goo Goo Dolls', 'Goo Goo Dolls', ['Goo Goo Dolls'], ['The Goo Goo Dolls'], True),
    ('Aenigmatum', 'Ænigmatum', ['Ænigmatum'], ['Aenigmatum'], True),
    ('Earth, Wind & Fire', 'Earth, Wind & Fire; Guest', ['Earth, Wind & Fire', 'Guest'], [], True),
    ('Royal Philharmonic Orchestra', 'Royal Liverpool Philharmonic Orchestra', ['Royal Liverpool Philharmonic Orchestra'], [], False),
    ('Artist & Guest', 'Artist', ['Artist'], [], False),
    ('Artist & Guest', 'Artist; New Guest', ['Artist', 'New Guest'], [], False),
    ('Bryan Adams', 'Ryan Adams', ['Ryan Adams'], [], False),
    ('The Artist', 'Artist', ['Artist'], [], False),
])
def test_credit_updates_preserve_every_existing_artist(old, new, names, aliases, supported):
    assert credit_preserves_artists(old, new, names, aliases) == supported


def test_additive_credit_still_requires_supported_assignment():
    details = {'confidence': 'clear', 'assignment_evidence': 'audio',
        'expected_artist_names': ['Artist', 'Guest'],
        'changes': {'track_artist': {'old': 'Artist', 'new': 'Artist; Guest'}}}
    item = {'finding_type': 'reconciliation_metadata', 'details': details}
    assert _finding_tier(item)[0] == 'ready'
    details['assignment_evidence'] = 'metadata'
    assert _finding_tier(item)[0] == 'manual'


def test_cosmetic_title_changes_need_exact_recording_and_duration():
    details = {'confidence': 'clear', 'title_difference_kind': 'cosmetic',
        'recording_mbid': 'expected', 'current_tags': {'duration_ms': 200000},
        'expected_duration_ms': 200100,
        'acoustid': {'status': 'ok', 'recordings': [{'mbid': 'expected', 'score': .99}]},
        'changes': {'title': {'old': 'Part 1', 'new': 'Part One'}}}
    item = {'finding_type': 'reconciliation_metadata', 'details': details}
    assert finding_category(item) == 'title_conventions'
    assert _finding_tier(item)[0] == 'ready'
    details['acoustid']['recordings'][0]['mbid'] = 'another'
    assert _finding_tier(item)[0] == 'manual'
    details['acoustid']['recordings'][0]['mbid'] = 'expected'
    details['expected_duration_ms'] = 210000
    assert _finding_tier(item)[0] == 'manual'


def test_routine_numbering_still_exposes_independent_metadata_and_recovery():
    details = {'confidence': 'clear', 'routine_numbering': True,
               'changes': {'track_number': {'old': 9, 'new': 1}}}
    item = {'finding_type': 'reconciliation_metadata', 'details': details}
    assert _finding_tier(item)[0] == 'ready'
    assert finding_category(item) == 'numbering'
    details['changes']['track_artist'] = {'old': 'Artist', 'new': 'Another Artist'}
    assert _finding_tier(item)[0] == 'manual'
    assert finding_category(item) == 'metadata'
    details['changes'].pop('track_artist')
    details['numbering_recovery'] = True
    assert 'recovery' in _finding_tier(item)[1]
    details['numbering_choice'] = 'fix'
    assert _finding_tier(item)[0] == 'ready'


def candidate(release, extra_title='Bonus', *, position=1, recording='home'):
    from core.library.reconciliation_candidates import ScoredCandidate
    slots = [{'title': 'Home', 'recording_mbid': recording, 'disc_number': 1, 'track_number': position},
             {'title': extra_title, 'recording_mbid': extra_title, 'disc_number': 1, 'track_number': 2}]
    return ScoredCandidate(1, 1, 0, 'musicbrainz', release, slots,
        {'pairs': [{'file_index': 0, 'slot_index': 0, 'confidence': 'clear'}],
         'missing_slots': [1], 'extra_files': []})


@pytest.mark.parametrize('boundary', ['none', 'position', 'recording', 'watched', 'complete', 'wanted'])
def test_partial_release_retention_only_ignores_unowned_tracks(boundary):
    from core.repair_jobs.library_reconciliation import _retain_partial_release
    old = candidate('current')
    alternate = candidate('alternative', 'Other bonus',
        position=3 if boundary == 'position' else 1,
        recording='different' if boundary == 'recording' else 'home')
    candidates = [alternate, old]
    saved = ('complete', 'musicbrainz', 'current', []) if boundary == 'complete' else (
             'partial', 'musicbrainz', 'current', ['bonus']) if boundary == 'wanted' else None
    kept = _retain_partial_release([{'playlists': ['Playlist']}],
        {'canonical_source': 'musicbrainz', 'canonical_album_id': 'current',
         'artist_name': 'Artist', 'album_title': 'Album'}, candidates,
        saved, {'artist'} if boundary == 'watched' else set(), lambda _: {'name': 'Album'})
    assert kept == (boundary == 'none')
    assert candidates[0] == (old if kept else alternate)


def test_release_shortlist_includes_conflict_beyond_six_equivalent_editions():
    from core.repair_jobs.library_reconciliation import _review_candidates, _candidate_difference
    editions = [candidate(f'edition-{index}') for index in range(8)]
    conflict = candidate('different', 'Another bonus', position=3)
    shortlist = _review_candidates(editions[0], [*editions, conflict], [conflict])
    assert len(shortlist) == 6
    assert shortlist[:2] == [editions[0], conflict]
    assert 'track 1 → disc 1, track 3' in _candidate_difference(editions[0], conflict)
    assert _candidate_difference(editions[0], candidate('bonus', 'Other bonus')) == 'Only unowned release tracks differ.'


def test_inferred_partial_can_be_reopened_without_losing_playlist_evidence(tmp_path, monkeypatch):
    from tests.library.test_review_acceptance import build_album, scan_case
    album = build_album(tmp_path, monkeypatch, 'partial')
    album.context.playlist_membership = lambda: {'1': ['Playlist']}
    case = scan_case(album)
    assert case['album_plan']['intent_inferred']
    assert not any(item['finding_type'] == 'reconciliation_intent' for item in case['items'])
    assert album.worker.reset_review_scope(case['case_id'])['success']
    reopened = scan_case(album)
    assert not reopened['album_plan']['intent_inferred']
    assert any(item['finding_type'] == 'reconciliation_intent' for item in reopened['items'])


def test_filename_collision_blocks_the_entire_album(tmp_path, monkeypatch):
    from tests.library.test_review_acceptance import build_album, scan_case
    import core.repair_jobs.library_reconciliation as job
    album = build_album(tmp_path, monkeypatch, 'supported')
    monkeypatch.setattr(job, '_suggest_filename',
        lambda path, slot, **kwargs: str(album.paths[1]) if path == str(album.paths[0]) else '')
    case = scan_case(album)
    assert not case['plan']['can_apply_ready']
    conflict = next(item for item in case['items'] if item['file_path'] == str(album.paths[0]))
    assert conflict['review_tier'] == 'manual'
    assert 'filename conflicts' in conflict['review_reason']
    assert not conflict['details']['routine_numbering']
