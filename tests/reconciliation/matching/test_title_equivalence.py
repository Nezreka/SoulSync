"""title equivalence in fresh and persisted matching modes."""
import pytest

from core.reconciliation.plan import (
    audio_contradicts_local_title,
    reconcile_folder,
    title_difference_kind,
)
from tests.reconciliation.support.audio import _file

pytestmark = pytest.mark.usefixtures("matching_parity")


@pytest.mark.parametrize(('local', 'remote'), [
    ('Drift And Die', 'Drift & Die'), ('80s Comedown Machine', '80’s Comedown Machine'),
    ('Bitch Please II', 'Bitch Please 2'),
    ('A Love Supreme, Part 1: Acknowledgement', 'A Love Supreme, Pt. I – Acknowledgement'),
    ('The Forgotten, Part 1', 'The Forgotten (Part One)'),
    ('Noncompliance to Astm F899-12 Standard', 'Noncompliance to ASTM F 899–12 Standard'),
    ('River Run:Lvl1', 'River Run: Lvl 1'),
    ('Untrustable / Part 2 (About Someone Else)', 'Untrustable (Pt. 2) [About Someone Else]'),
])
def test_cosmetic_titles_retain_words_and_numeric_identity(local, remote):
    assert title_difference_kind(local, {'title': remote}) == 'cosmetic'


@pytest.mark.parametrize(('local', 'remote'), [
    ('The Forgotten, Part 1', 'The Forgotten (Part Two)'),
    ('Part I', 'Part'), ('I', '1'), ('A B', 'AB'),
    ('Home (live)', 'Home'), ('Home (Alpha remix)', 'Home (Beta remix)'),
    ('F.O.D.', 'F.O.D. / All by Myself'),
])
def test_cosmetic_equivalence_does_not_erase_identity(local, remote):
    assert title_difference_kind(local, {'title': remote}) in ('material', 'version')


def test_same_title_different_artist_is_not_positive_audio_evidence():
    file = _file('Home', '/album/01 - Home.flac', 200000,
                 {'mbid': 'other', 'title': 'Home', 'artist': 'Unrelated Performer', 'score': .99})
    file['tags'].update(artist='Expected Artist', mbid='expected')
    slot = {'title': 'Home', 'artist': 'Expected Artist',
            'recording_mbid': 'expected', 'duration_ms': 200000}
    assert not reconcile_folder([file], [slot])['pairs']
    assert audio_contradicts_local_title(file)
    # A known alias supplies the missing artist evidence, without changing IDs.
    slot['artist_aliases'] = ['Unrelated Performer']
    assert reconcile_folder([file], [slot])['pairs'][0]['confidence'] == 'clear'


def test_similar_artist_names_require_an_actual_alias_and_compilations_use_track_credit():
    file = _file('Home', '/album/01 - Home.flac', 200000,
                 {'mbid': 'other', 'title': 'Home', 'artist': 'Ryan Adams', 'score': .99})
    file['tags'].update(artist='Bryan Adams')
    slot = {'title': 'Home', 'artist': 'Bryan Adams', 'duration_ms': 200000}
    assert not reconcile_folder([file], [slot])['pairs']
    file['tags'].update(artist='Various Artists', track_artist='Ryan Adams')
    assert not audio_contradicts_local_title(file)


@pytest.mark.parametrize('credit', ['Lïve', 'Live feat. Guest'])
def test_artist_spelling_and_collaboration_are_preserved(credit):
    file = _file('Home', '/album/01 - Home.flac', 200000,
                 {'mbid': 'alternate-id', 'title': 'Home', 'artist': credit, 'score': .99})
    slot = {'title': 'Home', 'artist': 'Live', 'recording_mbid': 'expected', 'duration_ms': 200000}
    assert reconcile_folder([file], [slot])['pairs'][0]['confidence'] == 'clear'


@pytest.mark.parametrize(('artist', 'local', 'remote', 'audio'), [
    ('*NSYNC', 'For the Girl Who Has Everything - Radio Mix',
     'For the Girl Who Has Everything (radio mix)', 'For the Girl Who Has Everything'),
    ('Sixpence None the Richer', 'Kiss Me - Japanese Commercial Single Version Import',
     'Kiss Me (Japanese Commercial single version)', 'Kiss Me (Japanese Commercial single version)'),
    ('Stone Sour', 'Zzyzx Rd. - Pop Mix', 'Zzyzx Rd.', 'Zzyzx Rd. (Pop version)'),
    ('The Who', 'Join Together (unedited version / Steven Wilson stereo mix)',
     'Join Together (unedited version / Steven Wilson stereo mix)', 'Join Together (unedited remix)'),
])
def test_catalogue_qualifiers_and_artist_prefixed_filenames_agree(artist, local, remote, audio):
    filename = f'01 - {artist} - {local}.flac'.replace('*', '_').replace('/', '_')
    path = f'/album/{filename}'
    file = _file(local, path, 200000,
                 {'mbid': 'audio-id', 'title': audio, 'artist': artist, 'score': .99})
    file['tags']['track_artist'] = artist
    slot = {'title': remote, 'artist': artist, 'duration_ms': 200000}
    assert not audio_contradicts_local_title(file)
    plan = reconcile_folder([file], [slot])
    assert plan['pairs'][0]['confidence'] == 'clear'
    assert not plan['extra_files']


def test_date_normalization_does_not_hide_song_numbers():
    from core.reconciliation.plan import _title_score, display_title_compatible
    local = 'Song 1986 (live at Wembley, July 12, 1986)'
    remote = 'Song 1987 (live at Wembley, 1986-07-12)'
    assert _title_score(local, remote) == 0
    assert not display_title_compatible(local, {'title': remote})
    assert title_difference_kind(local, {'title': remote}) == 'material'


def test_tied_fingerprint_can_be_settled_by_recording_id_artist_and_duration():
    file = _file('TV Dinners - 2008 Remaster',
                 '/album/02 - TV Dinners - 2008 Remaster.mp3', 230616,
                 {'mbid': 'tv', 'title': 'TV Dinners', 'artist': 'ZZ Top', 'score': 0.99})
    file['tags'].update({'artist': 'ZZ Top', 'track_number': 2, 'disc_number': 1})
    file['acoustid']['recordings'].append(
        {'mbid': 'gun', 'title': 'Gun Love', 'artist': 'ZZ Top', 'score': 0.99})
    slots = [{'title': 'TV Dinners', 'artist': 'ZZ Top', 'recording_mbid': 'tv',
              'track_number': 8, 'disc_number': 1, 'duration_ms': 230573}]

    result = reconcile_folder([file], slots)

    assert result['pairs'][0]['confidence'] == 'clear'


def test_collaboration_credit_supports_exact_album_artist_slot():
    files = [_file(
        'Pompeii MMXXIII', '/album/14 - Pompeii MMXXIII.flac', 179330,
        {'mbid': 'noise', 'title': 'Apocalypse Now',
         'artist': 'Perturbator feat. Ulver', 'score': 1.0},
    )]
    files[0]['acoustid']['recordings'].append({
        'mbid': 'pompeii', 'title': 'Pompeii MMXXIII',
        'artist': 'Bastille & Hans Zimmer', 'score': 1.0,
    })
    slots = [{
        'title': 'Pompeii MMXXIII', 'artist': 'Bastille',
        'recording_mbid': 'pompeii', 'duration_ms': 180000,
    }]

    pair = reconcile_folder(files, slots)['pairs'][0]

    assert pair['confidence'] == 'clear'
    assert pair['ambiguity_reasons'] == []


def test_title_differences_separate_display_flavor_from_recording_identity():
    bare = {'title': 'Song'}
    assert title_difference_kind('Song (Serenade From the Stars)', bare) == 'flavor'
    assert title_difference_kind('SONG', bare) == 'typography'
    assert title_difference_kind('Song (Live)', bare) == 'version'
    assert title_difference_kind('Song (Live 1977)', {'title': 'Song (Live 1978)'}) == 'material'
