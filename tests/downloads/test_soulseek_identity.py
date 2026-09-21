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
    mismatch = match_track(target, _file('02 - Intro.flac'))
    assert not mismatch.matches
    assert mismatch.contradicts
    assert mismatch.reason == 'number-or-disc-mismatch'


@pytest.mark.parametrize(('title', 'artist', 'album', 'number', 'filename'), [
    ('Superman', 'Eminem', 'The Eminem Show', 13, '13 - Eminem feat. Dina Rae - Superman.flac'),
    ('Superman', 'Eminem', 'The Eminem Show', 13,
     '13 - Eminem feat. Dina-Rae - Superman.flac'),
    ("Stacy's Mom", 'Fountains of Wayne', 'Welcome Interstate Managers',
     3, '03-fountains_of_wayne-stacys_mom.flac'),
    ('Peacock', 'Katy Perry', 'Teenage Dream', 13, '0113 - Katy Perry - Peacock.flac'),
    ('Title', 'Artist', 'Album', 1, 'Artist_Album_01_Title.flac'),
    ('7 rings', 'Ariana Grande', 'thank u, next', None, '7 rings.flac'),
    ('Song - Remastered 2011', 'Artist', 'Album', 1, '01 - Song.flac'),
])
def test_real_world_filename_layouts_match(title, artist, album, number, filename):
    target = {'name': title, 'artists': [artist], 'album': album, 'track_number': number}
    result = match_track(target, _file(filename))
    assert result.matches
    assert not result.contradicts


def test_unrecognized_layout_and_sibling_title_are_inconclusive():
    target = {'name': 'Rise', 'artists': ['Doves'], 'album': 'Lost Souls'}
    unknown = match_track(target, _file('05-d0ves__rise.flac'))
    sibling = match_track(target, _file('Doves/Lost Souls/05 - Doves - Sea Song.flac'))
    assert not unknown.matches and not unknown.contradicts
    assert not sibling.matches and not sibling.contradicts
    assert sibling.reason == 'parsed-title-mismatch'


@pytest.mark.parametrize(('title', 'filename'), [
    ('Duvet - Acoustic', '12. Duvet (acoustic version).flac'),
    ('Black Ice', '05 - Black Ice (original mix).flac'),
    ('Where\'d All the Time Go?', "05. Dr. Dog - Where'd All the Time Go&#x3f;.flac"),
    ('Scatterbrain', '14-split_chain-scatterbrain-3b92e63f.mp3'),
    ('Bring On The Night - Remastered 2003',
     '10 - Bring On The Night (Remastered 2003.mp3'),
])
def test_completed_history_title_variants_are_not_hard_contradictions(title, filename):
    result = match_track(
        {'name': title, 'artists': ['Artist'], 'album': 'Album'},
        _file(filename),
    )

    assert not result.contradicts


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


def test_direct_album_picker_accepts_complete_compilation_without_artist_in_path():
    client = object.__new__(SoulseekClient)
    client.filter_results_by_quality_preference = lambda tracks, profile_id=None: tracks
    expected = [
        {'name': f'Track {number}', 'artists': [f'Artist {number}'], 'track_number': number}
        for number in range(1, 4)
    ]
    album = SimpleNamespace(
        album_title='Now 50', album_path='Music/Various Artists/Now 50',
        artist='Kylie Minogue', track_count=3, quality_score=0.8,
        tracks=[_file(f'Music/Various Artists/Now 50/{number:02d} - Track {number}.flac')
                for number in range(1, 4)],
    )

    assert client._pick_album_bundle_folder(
        [album], 'Now 50', 'Various Artists', expected_tracks=expected,
    ) is album


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
