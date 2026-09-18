"""Target-aware Soulseek path interpretation and release coverage."""

from types import SimpleNamespace

import pytest

from core.downloads.soulseek_identity import assign_album_tracks, match_track, title_interpretations
from core.soulseek_client import SoulseekClient


def _track(title, number=None):
    return {'name': title, 'artists': ['Justin Timberlake'], 'album': 'FutureSex/LoveSounds', 'track_number': number}


def _file(path):
    return SimpleNamespace(filename=path)


def test_embedded_album_and_number_can_precede_title():
    target = _track('SexyBack', 2)
    candidate = _file(r'Justin Timberlake\FutureSex+LoveSounds (2006)\Justin Timberlake - FutureSex+LoveSounds - 02 - SexyBack.flac')
    result = match_track(target, candidate)
    assert result.matches
    assert result.source == 'embedded-number'
    assert result.number == 2


def test_sibling_title_does_not_match_even_under_same_artist_album():
    target = {'name': 'Lost Souls', 'artists': ['Doves'], 'album': 'Lost Souls'}
    candidate = _file(r'Doves\(2000) Lost Souls\05 - Doves - Rise.flac')
    assert not match_track(target, candidate).matches


def test_unknown_layout_does_not_accept_album_words_as_title():
    assert not match_track(_track('SexyBack'), _file('Justin Timberlake - FutureSex+LoveSounds.flac')).matches


def test_mastering_tag_is_not_a_different_song_but_remix_is():
    target = _track('SexyBack', 2)
    assert match_track(target, _file('02 - SexyBack (Remastered 2011).flac')).matches
    assert not match_track(target, _file('02 - SexyBack (Live).flac')).matches


def test_explicitly_preferred_version_survives_identity_gate():
    candidate = _file('01 - Song (Extended Mix).flac')
    target = {'name': 'Song', 'artists': ['Artist']}
    assert not match_track(target, candidate).matches
    candidate.preferred_version_hit = True
    result = match_track(target, candidate)
    assert result.matches and result.reason == 'preferred-version-title'
    wrong_sibling = _file('01 - Song Two (Extended Mix).flac')
    assert not match_track(target, wrong_sibling).matches


def test_disc_evidence_rejects_same_title_on_another_disc():
    target = {'name': 'Intro', 'artists': ['Artist'], 'disc_number': 2,
              'track_number': 1}
    assert not match_track(target, _file('Artist/Album/CD1/01 - Intro.flac')).matches
    assert match_track(target, _file('Artist/Album/CD2/01 - Intro.flac')).matches
    assert not match_track(target, _file('Artist/Album/1-01 - Intro.flac')).matches


def test_fractional_track_number_still_rejects_wrong_number():
    target = _track('Intro', '1/12')
    assert match_track(target, _file('01 - Intro.flac')).matches
    assert not match_track(target, _file('02 - Intro.flac')).matches


def test_assignment_does_not_count_one_file_twice():
    expected = [_track('SexyBack', 2), _track('SexyBack', 2), _track('My Love', 3)]
    candidates = [
        _file('Justin Timberlake - FutureSex+LoveSounds - 02 - SexyBack.flac'),
        _file('Justin Timberlake - FutureSex+LoveSounds - 03 - My Love.flac'),
    ]
    assignment = assign_album_tracks(expected, candidates)
    assert len(assignment.pairs) == 2
    assert assignment.coverage == 2 / 3


def test_interpretations_are_bounded_and_deterministic():
    path = 'Artist/Album/Artist - Album - 02 - Title.flac'
    assert title_interpretations(path, 'Artist', 'Album') == title_interpretations(path, 'Artist', 'Album')
    assert len(title_interpretations(path, 'Artist', 'Album')) <= 16


def test_year_prefixed_album_and_artist_parent_are_preserved():
    client = object.__new__(SoulseekClient)
    path = 'Doves/(2000) Lost Souls'
    assert client._extract_album_title(path) == 'Lost Souls'
    assert client._determine_album_artist([], path) == 'Doves'
    identity = match_track(
        {'name': 'Rise', 'artists': ['Doves'], 'album': 'Lost Souls'},
        _file('Doves/(2000) Lost Souls/05 - Rise.flac'),
    )
    assert identity.artist_path_evidence
    assert identity.album_path_evidence


def test_direct_album_picker_uses_distinct_requested_titles():
    client = object.__new__(SoulseekClient)
    client.filter_results_by_quality_preference = lambda tracks, profile_id=None: tracks
    expected = [
        {'name': 'First', 'artists': ['Artist'], 'track_number': 1},
        {'name': 'Second', 'artists': ['Artist'], 'track_number': 2},
    ]
    wrong = SimpleNamespace(
        album_title='Album', album_path='Artist/Album', artist='Artist',
        track_count=2, quality_score=1.0,
        tracks=[_file('Artist/Album/01 - First.flac'),
                _file('Artist/Album/02 - Different.flac')],
    )
    correct = SimpleNamespace(
        album_title='Album', album_path='Artist/Album', artist='Artist',
        track_count=2, quality_score=0.5,
        tracks=[_file('Artist/Album/01 - First.flac'),
                _file('Artist/Album/02 - Second.flac')],
    )
    picked = client._pick_album_bundle_folder(
        [wrong, correct], 'Album', 'Artist', expected_tracks=expected,
    )
    assert picked is correct


@pytest.mark.parametrize(('title', 'path', 'matches'), [
    ('One', 'Artist/(2000) Album/CD1/01 - One.flac', True),
    ('1999', 'Artist/Album (2000)/02 - 1999.flac', True),
    ('AC/DC', 'Artist/Album/03 - AC DC.flac', True),
    ('Song A / Song B', 'Artist/Album/04 - Song A - Song B.flac', True),
    ('Title', 'Artist/Artist/05 - Title (feat. Guest).flac', True),
    ('Title', 'Artist/Album/06 - Title (Live).flac', False),
    ('Title', 'Artist/Album/07 - Title Two.flac', False),
])
def test_structural_path_corpus(title, path, matches):
    target = {'name': title, 'artists': ['Artist'], 'album': 'Album'}
    assert match_track(target, _file(path)).matches is matches
