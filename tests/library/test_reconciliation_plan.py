"""Library-shaped examples from the user's reconciliation investigations."""

from core.library.reconciliation_plan import reconcile_folder


def _file(title, path, duration_ms, recording=None):
    return {'path': path, 'tags': {'title': title, 'duration_ms': duration_ms},
            'acoustid': {'status': 'ok', 'recordings': [recording]} if recording else
                        {'status': 'no_match', 'recordings': []}}


def test_ok_computer_lucky_keeps_its_title_even_when_earlier_song_has_same_length():
    files = [
        _file('The Tourist', '/Radiohead/OK Computer/12 - The Tourist.flac', 323000),
        _file('Lucky', '/Radiohead/OK Computer/11 - Lucky.flac', 259370),
        _file('No Surprises', '/Radiohead/OK Computer/10 - No Surprises.flac', 229000),
    ]
    slots = [
        {'title': 'No Surprises', 'duration_ms': 229000},
        {'title': 'Lucky', 'duration_ms': 260000},
        {'title': 'The Tourist', 'duration_ms': 323000},
    ]
    result = reconcile_folder(files, slots)
    assert {(p['file_index'], p['slot_index']) for p in result['pairs']} == {
        (0, 2), (1, 1), (2, 0),
    }
    assert result['missing_slots'] == []


def test_ashes_of_the_wake_swapped_tags_do_not_override_audio_and_filename():
    files = [
        _file('Laid to Rest', '/Lamb of God/Ashes of the Wake/02 - Hourglass.flac',
              240000, {'mbid': 'hourglass-id', 'title': 'Hourglass',
                       'artist': 'Lamb of God', 'score': 0.98}),
        _file('Hourglass', '/Lamb of God/Ashes of the Wake/01 - Laid to Rest.flac',
              230000, {'mbid': 'laid-id', 'title': 'Laid to Rest',
                       'artist': 'Lamb of God', 'score': 0.97}),
    ]
    slots = [
        {'title': 'Laid to Rest', 'recording_mbid': 'laid-id', 'duration_ms': 230000},
        {'title': 'Hourglass', 'recording_mbid': 'hourglass-id', 'duration_ms': 240000},
    ]
    result = reconcile_folder(files, slots)
    assert {(p['file_index'], p['slot_index']) for p in result['pairs']} == {(0, 1), (1, 0)}


def test_numbered_parts_remain_separate_when_only_title_is_available():
    files = [_file('Ultima Esperanza, Pt. 1', '/album/01.flac', 200000),
             _file('Ultima Esperanza, Pt. 2', '/album/02.flac', 201000)]
    slots = [{'title': 'Ultima Esperanza, Pt. 2', 'duration_ms': 201000},
             {'title': 'Ultima Esperanza, Pt. 1', 'duration_ms': 200000}]
    pairs = reconcile_folder(files, slots)['pairs']
    assert {(p['file_index'], p['slot_index']) for p in pairs} == {(0, 1), (1, 0)}


def test_near_exact_duration_breaks_tie_between_otherwise_identical_titles():
    files = [_file('Song', '/album/01 - Song.flac', 200000)]
    slots = [
        {'title': 'Song', 'duration_ms': 200010},
        {'title': 'Song', 'duration_ms': 202990},
    ]

    pair = reconcile_folder(files, slots)['pairs'][0]

    assert pair['slot_index'] == 0
    assert pair['confidence'] == 'likely'
    assert pair['duration_delta_ms'] == 10
    assert pair['ambiguity_reasons'] == []


def test_duration_does_not_hide_two_equally_plausible_slots():
    files = [_file('Song', '/album/01 - Song.flac', 200000)]
    slots = [
        {'title': 'Song', 'duration_ms': 200010},
        {'title': 'Song', 'duration_ms': 200500},
    ]

    pair = reconcile_folder(files, slots)['pairs'][0]

    assert pair['confidence'] == 'ambiguous'
    assert pair['ambiguity_reasons'] == ['another release track fits nearly as well']


def test_tied_acoustid_links_are_resolved_by_complete_album_evidence():
    files = [
        _file('Tautou', '/album/01 - Tautou.mp3', 102453,
              {'mbid': 'play-id', 'title': 'Play Crack the Sky',
               'artist': 'Brand New', 'score': 0.999}),
        _file('Play Crack the Sky', '/album/11 - Play Crack the Sky.mp3', 327880,
              {'mbid': 'play-id', 'title': 'Play Crack the Sky',
               'artist': 'Brand New', 'score': 0.999}),
    ]
    files[0]['acoustid']['recordings'].append(
        {'mbid': 'tautou-id', 'title': 'Tautou',
         'artist': 'Brand New', 'score': 0.999})
    files[1]['acoustid']['recordings'].append(
        {'mbid': 'guernica-id', 'title': 'Guernica',
         'artist': 'Brand New', 'score': 0.999})
    slots = [
        {'title': 'Tautou', 'artist': 'Brand New',
         'recording_mbid': 'tautou-id', 'duration_ms': 102453},
        {'title': 'Play Crack the Sky', 'artist': 'Brand New',
         'recording_mbid': 'play-id', 'duration_ms': 327880},
    ]

    result = reconcile_folder(files, slots)

    assert {(pair['file_index'], pair['slot_index']) for pair in result['pairs']} == {
        (0, 0), (1, 1),
    }
    assert all(pair['confidence'] == 'clear' for pair in result['pairs'])
    assert result['missing_slots'] == []
    assert result['extra_files'] == []


def test_actual_duplicate_leaves_the_wrongly_labelled_slot_missing():
    tied = [
        {'mbid': 'play-id', 'title': 'Play Crack the Sky',
         'artist': 'Brand New', 'score': 0.999},
        {'mbid': 'tautou-id', 'title': 'Tautou',
         'artist': 'Brand New', 'score': 0.999},
    ]
    files = [
        _file('Tautou', '/album/01 - Tautou.mp3', 327880, tied[0]),
        _file('Play Crack the Sky', '/album/11 - Play Crack the Sky.mp3', 327880, tied[0]),
    ]
    files[0]['acoustid']['recordings'] = tied
    slots = [
        {'title': 'Tautou', 'artist': 'Brand New',
         'recording_mbid': 'tautou-id', 'duration_ms': 102453},
        {'title': 'Play Crack the Sky', 'artist': 'Brand New',
         'recording_mbid': 'play-id', 'duration_ms': 327880},
    ]

    result = reconcile_folder(files, slots)

    assert [(pair['file_index'], pair['slot_index']) for pair in result['pairs']] == [(1, 1)]
    assert result['missing_slots'] == [0]
    assert result['extra_files'] == [0]


def test_ambiguous_acoustid_result_does_not_authorize_clear_repair():
    files = [_file('Mystery', '/album/01.flac', 200000,
                   {'mbid': 'one', 'title': 'Song One', 'artist': 'Artist', 'score': 0.9})]
    files[0]['acoustid']['recordings'].append(
        {'mbid': 'two', 'title': 'Song Two', 'artist': 'Artist', 'score': 0.9})
    slots = [{'title': 'Song One', 'recording_mbid': 'one', 'duration_ms': 200000}]
    pairs = reconcile_folder(files, slots)['pairs']
    assert pairs[0]['confidence'] == 'ambiguous'


def test_unrelated_acoustid_link_does_not_override_complete_album_evidence():
    files = [_file(
        'Lay Your Hands on Me', '/album/01 - Lay Your Hands on Me.flac', 357170,
        {'mbid': 'album-id', 'title': 'Lay Your Hands on Me',
         'artist': 'Bon Jovi', 'score': 0.99},
    )]
    files[0]['tags']['mbid'] = 'album-id'
    files[0]['acoustid']['recordings'].append(
        {'mbid': 'bad-link', 'title': 'Different Song',
         'artist': 'Other Artist', 'score': 0.99})
    slots = [{
        'title': 'Lay Your Hands on Me', 'artist': 'Bon Jovi',
        'recording_mbid': 'album-id', 'duration_ms': 358900,
    }]

    pair = reconcile_folder(files, slots)['pairs'][0]

    assert pair['confidence'] == 'clear'
    assert pair['duration_delta_ms'] == 1730
    assert pair['ambiguity_reasons'] == []


def test_acoustid_link_that_fits_another_release_slot_remains_ambiguous():
    files = [_file(
        'First Song', '/album/01 - First Song.flac', 200000,
        {'mbid': 'first-id', 'title': 'First Song', 'artist': 'Artist', 'score': 0.99},
    )]
    files[0]['tags']['mbid'] = 'first-id'
    files[0]['acoustid']['recordings'].append(
        {'mbid': 'second-id', 'title': 'Second Song', 'artist': 'Artist', 'score': 0.99})
    slots = [
        {'title': 'First Song', 'artist': 'Artist',
         'recording_mbid': 'first-id', 'duration_ms': 200000},
        {'title': 'Second Song', 'artist': 'Artist',
         'recording_mbid': 'second-id', 'duration_ms': 201000},
    ]

    pair = reconcile_folder(files, slots)['pairs'][0]

    assert pair['confidence'] == 'ambiguous'
    assert pair['ambiguity_reasons'] == [
        'AcoustID returned competing identities not resolved by the other evidence']


def test_recording_disambiguation_separates_same_titled_versions():
    file = _file('Song', '/album/01 - Song.flac', 200000)
    file['db'] = {'title': 'Song', 'recording_disambiguation': 'instrumental'}
    file['acoustid'] = {'status': 'ok', 'recordings': [
        {'mbid': 'vocal-id', 'title': 'Song', 'artist': 'Artist', 'score': 0.99},
    ]}
    slot = {'title': 'Song', 'recording_mbid': 'vocal-id', 'duration_ms': 200000,
            'recording_disambiguation': 'vocal version'}

    result = reconcile_folder([file], [slot])

    assert result['pairs'] == []
    assert result['extra_files'] == [0]
    assert result['missing_slots'] == [0]


def test_recording_disambiguation_separates_differently_named_remixes():
    file = _file('Song', '/album/01 - Song.flac', 200000)
    file['db'] = {'title': 'Song', 'recording_disambiguation': 'Foo remix'}
    file['acoustid'] = {'status': 'ok', 'recordings': [
        {'mbid': 'bar-id', 'title': 'Song', 'artist': 'Artist', 'score': 0.99},
    ]}
    slot = {'title': 'Song', 'recording_mbid': 'bar-id', 'duration_ms': 200000,
            'recording_disambiguation': 'Bar remix'}

    result = reconcile_folder([file], [slot])

    assert result['pairs'] == []
    assert result['extra_files'] == [0]
    assert result['missing_slots'] == [0]


def test_unknown_recording_disambiguation_remains_explicit_identity_evidence():
    file = _file('Song', '/album/01 - Song.flac', 200000)
    file['db'] = {'title': 'Song', 'recording_disambiguation': 'first studio take'}
    file['acoustid'] = {'status': 'ok', 'recordings': [
        {'mbid': 'other-id', 'title': 'Song', 'artist': 'Artist', 'score': 0.99},
    ]}
    slot = {'title': 'Song', 'recording_mbid': 'other-id', 'duration_ms': 200000,
            'recording_disambiguation': 'second studio take'}

    result = reconcile_folder([file], [slot])

    assert result['pairs'] == []
    assert result['extra_files'] == [0]
    assert result['missing_slots'] == [0]


def test_recording_disambiguation_corroborates_same_titled_version():
    file = _file('Song', '/album/01 - Song.flac', 200000)
    file['db'] = {'title': 'Song', 'recording_disambiguation': 'acoustic'}
    slot = {'title': 'Song', 'duration_ms': 200000,
            'recording_disambiguation': 'acoustic'}

    pair = reconcile_folder([file], [slot])['pairs'][0]

    assert 'recording disambiguation agrees' in pair['reasons']


def test_wrong_audio_is_extra_and_intended_slot_is_missing():
    files = [_file('Southbound', '/album/01 - Southbound.flac', 200000,
                   {'mbid': 'wrong', 'title': 'Different Song',
                    'artist': 'Other', 'score': 0.99})]
    slots = [{'title': 'Wanted Song', 'recording_mbid': 'wanted',
              'duration_ms': 300000}]
    result = reconcile_folder(files, slots)
    assert result['pairs'] == []
    assert result['extra_files'] == [0]
    assert result['missing_slots'] == [0]


def test_wrong_audio_cannot_be_rescued_by_two_wrong_metadata_layers():
    files = [_file('Wanted Song', '/album/01 - Wanted Song.flac', 300000,
                   {'mbid': 'other', 'title': 'Different Song',
                    'artist': 'Other', 'score': 0.99})]
    slots = [{'title': 'Wanted Song', 'recording_mbid': 'wanted',
              'duration_ms': 300000, 'track_number': 1}]
    result = reconcile_folder(files, slots)
    assert result['pairs'] == []
    assert result['extra_files'] == [0]
    assert result['missing_slots'] == [0]


def test_two_copies_competing_for_one_slot_are_not_clear_repairs():
    recording = {'mbid': 'same-id', 'title': 'Hourglass',
                 'artist': 'Lamb of God', 'score': 0.99}
    files = [_file('Hourglass', '/album/01 - Hourglass.flac', 200000, recording),
             _file('Hourglass', '/album/copy - Hourglass.flac', 200000, recording)]
    slots = [{'title': 'Hourglass', 'recording_mbid': 'same-id',
              'duration_ms': 200000}]
    result = reconcile_folder(files, slots)
    assert len(result['pairs']) == 1
    assert result['pairs'][0]['confidence'] == 'ambiguous'
    assert len(result['extra_files']) == 1
    assert result['competing_files'][result['extra_files'][0]] == [result['pairs'][0]['file_index']]


def test_material_version_marker_is_distinct_but_subtitle_flavor_can_match():
    live = [_file('Song (Live)', '/album/01 - Song (Live).flac', 200000)]
    studio_slot = [{'title': 'Song', 'duration_ms': 200000}]
    assert reconcile_folder(live, studio_slot)['pairs'] == []
    subtitle = [_file('Song (Serenade From the Stars)',
                      '/album/01 - Song (Serenade From the Stars).flac', 200000)]
    assert len(reconcile_folder(subtitle, studio_slot)['pairs']) == 1
