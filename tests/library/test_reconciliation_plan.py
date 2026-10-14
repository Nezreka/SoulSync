"""Library-shaped examples from the user's reconciliation investigations."""

from core.library.reconciliation_plan import (
    audio_contradicts_local_title, reconcile_folder, title_difference_kind,
    file_version_markers, slot_version_markers,
)


def _file(title, path, duration_ms, recording=None):
    return {'path': path, 'tags': {'title': title, 'duration_ms': duration_ms},
            'acoustid': {'status': 'ok', 'recordings': [recording]} if recording else
                        {'status': 'no_match', 'recordings': []}}


def test_acoustic_session_and_alternative_wording_match_equivalent_versions():
    assert file_version_markers(_file('Acts of Man - Acoustic',
                                      '/album/Acts of Man - Acoustic.flac', 200000)) == (
        slot_version_markers({'title': 'Acts of Man (Acoustic Session)'})
    )
    assert file_version_markers(_file('Fortune - Alternative Version',
                                      '/album/Fortune - Alternative Version.flac', 200000)) == (
        slot_version_markers({'title': 'Fortune (Alternate Version)'})
    )


def test_adjacent_complementary_length_errors_require_review_of_both_tracks():
    files = [
        _file('Faith', '/album/03 - Faith.flac', 239560,
              {'mbid': 'faith', 'title': 'Faith', 'artist': 'Ghost', 'score': 0.99}),
        _file('See the Light', '/album/04 - See the Light.flac', 275240,
              {'mbid': 'light', 'title': 'See the Light', 'artist': 'Ghost', 'score': 0.99}),
    ]
    slots = [
        {'title': 'Faith', 'artist': 'Ghost', 'recording_mbid': 'faith',
         'disc_number': 1, 'track_number': 3, 'duration_ms': 269707},
        {'title': 'See the Light', 'artist': 'Ghost', 'recording_mbid': 'light',
         'disc_number': 1, 'track_number': 4, 'duration_ms': 245160},
    ]

    pairs = reconcile_folder(files, slots)['pairs']

    assert len(pairs) == 2
    assert all(pair['confidence'] == 'ambiguous' for pair in pairs)
    assert all(any('boundary' in reason for reason in pair['ambiguity_reasons'])
               for pair in pairs)


def test_unmatched_adjacent_track_also_flags_its_confirmed_neighbor():
    files = [
        _file('Faith', '/album/03 - Faith.flac', 239560,
              {'mbid': 'faith', 'title': 'Faith', 'artist': 'Ghost', 'score': 0.99}),
        _file('See the Light', '/album/04 - See the Light.flac', 275240),
    ]
    slots = [
        {'title': 'Faith', 'artist': 'Ghost', 'recording_mbid': 'faith',
         'disc_number': 1, 'track_number': 3, 'duration_ms': 269707},
        {'title': 'See the Light', 'artist': 'Ghost', 'recording_mbid': 'light',
         'disc_number': 1, 'track_number': 4, 'duration_ms': 245160},
    ]

    result = reconcile_folder(files, slots)

    assert result['missing_slots'] == [1]
    assert result['extra_files'] == [1]
    assert result['pairs'][0]['confidence'] == 'ambiguous'
    assert 'boundary' in result['pairs'][0]['ambiguity_reasons'][0]


def test_large_duration_error_remains_unsettled_after_neighbor_is_removed():
    files = [_file('Faith', '/album/03 - Faith.flac', 239560,
                   {'mbid': 'faith', 'title': 'Faith', 'artist': 'Ghost', 'score': 0.99})]
    slots = [
        {'title': 'Faith', 'artist': 'Ghost', 'recording_mbid': 'faith',
         'disc_number': 1, 'track_number': 3, 'duration_ms': 269707},
        {'title': 'See the Light', 'artist': 'Ghost', 'recording_mbid': 'light',
         'disc_number': 1, 'track_number': 4, 'duration_ms': 245160},
    ]

    result = reconcile_folder(files, slots)

    assert result['pairs'][0]['confidence'] == 'ambiguous'
    assert any('30.1 seconds' in reason for reason in result['pairs'][0]['ambiguity_reasons'])


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


def test_verified_recording_length_filters_an_unrelated_fingerprint_link():
    file = _file('TV Dinners', '/album/02 - TV Dinners.mp3', 230616,
                 {'mbid': 'tv', 'title': 'TV Dinners', 'artist': 'ZZ Top',
                  'score': 0.99, 'duration_ms': 230573})
    file['acoustid']['recordings'].append(
        {'mbid': 'gun', 'title': 'Gun Love', 'artist': 'ZZ Top',
         'score': 0.99, 'duration_ms': 180000})
    slot = {'title': 'TV Dinners', 'artist': 'ZZ Top', 'recording_mbid': 'tv',
            'track_number': 2, 'disc_number': 1, 'duration_ms': 230573}

    result = reconcile_folder([file], [slot])

    assert result['pairs'][0]['confidence'] == 'clear'


def test_out_of_range_position_and_live_fingerprint_block_renumbering():
    file = _file('Come Back Down', '/album/16 - Come Back Down.mp3', 274000,
                 {'mbid': 'album', 'title': 'Come Back Down',
                  'artist': 'Lifehouse', 'score': 0.97})
    file['tags'].update({'track_number': 16, 'disc_number': 1, 'artist': 'Lifehouse'})
    file['acoustid']['recordings'].append(
        {'mbid': 'live', 'title': 'Come Back Down (live in Portland)',
         'artist': 'Lifehouse', 'score': 0.97})
    slots = [{'title': 'Come Back Down', 'recording_mbid': 'album',
              'artist': 'Lifehouse', 'track_number': 1, 'disc_number': 1,
              'duration_ms': 274000}]
    result = reconcile_folder([file], slots)
    assert result['pairs'][0]['confidence'] == 'ambiguous'
    assert any('outside this release' in reason
               for reason in result['pairs'][0]['ambiguity_reasons'])


def test_repeated_exchange_titles_do_not_swap_numbered_files_on_fuzzy_audio():
    files = [
        _file('Exchange', '/album/05 - Exchange.flac', 253258,
              {'mbid': 'other-exchange', 'title': 'Exchange',
               'artist': 'Massive Attack', 'score': 1.0}),
        _file('(Exchange)', '/album/11 - (Exchange).flac', 216768,
              {'mbid': 'another-exchange', 'title': 'Exchange',
               'artist': 'Zao', 'score': 1.0}),
    ]
    for number, file in ((5, files[0]), (11, files[1])):
        file['tags'].update({'track_number': number, 'disc_number': 1})
    slots = [
        {'title': 'Exchange', 'artist': 'Massive Attack',
         'recording_mbid': 'release-five', 'track_number': 5,
         'disc_number': 1, 'duration_ms': 251200},
        {'title': '(Exchange)', 'artist': 'Massive Attack',
         'recording_mbid': 'release-eleven', 'track_number': 11,
         'disc_number': 1, 'duration_ms': 250346},
    ]

    result = reconcile_folder(files, slots)

    assert {(pair['file_index'], pair['slot_index']) for pair in result['pairs']} == {
        (0, 0),
    }
    assert result['extra_files'] == [1]
    assert result['missing_slots'] == [1]


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


def test_manual_assignment_is_one_to_one_and_requires_followup_review():
    files = [_file('Studio Song', '/album/studio.flac', 200000,
                   {'mbid': 'studio', 'title': 'Studio Song', 'score': 0.99}),
             _file('Live Song', '/album/live.flac', 210000,
                   {'mbid': 'live', 'title': 'Live Song', 'score': 0.99})]
    slots = [{'title': 'Studio Song', 'recording_mbid': 'studio', 'duration_ms': 200000},
             {'title': 'Live Song', 'recording_mbid': 'live', 'duration_ms': 210000}]
    result = reconcile_folder(files, slots, forced_pairs={0: 1})
    assert any(pair['file_index'] == 0 and pair['slot_index'] == 1 and
               pair['confidence'] == 'manual' for pair in result['pairs'])
    assert all(pair['slot_index'] != 1 or pair['file_index'] == 0
               for pair in result['pairs'])
    assert 1 in result['extra_files']


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


def test_tied_acoustid_identity_is_resolved_when_another_file_owns_that_slot():
    tied = [
        {'mbid': 'first-id', 'title': 'First Song', 'artist': 'Artist', 'score': 0.99},
        {'mbid': 'second-id', 'title': 'Second Song', 'artist': 'Artist', 'score': 0.99},
    ]
    files = [
        _file('First Song', '/album/01 - First Song.flac', 200000, tied[0]),
        _file('Second Song', '/album/02 - Second Song.flac', 201000, tied[1]),
    ]
    files[0]['acoustid']['recordings'] = tied
    slots = [
        {'title': 'First Song', 'artist': 'Artist',
         'recording_mbid': 'first-id', 'duration_ms': 200000},
        {'title': 'Second Song', 'artist': 'Artist',
         'recording_mbid': 'second-id', 'duration_ms': 201000},
    ]

    result = reconcile_folder(files, slots)

    assert all(pair['confidence'] == 'clear' for pair in result['pairs'])


def test_tied_acoustid_identity_stays_ambiguous_when_other_slot_has_no_owner():
    tied = [
        {'mbid': 'first-id', 'title': 'First Song', 'artist': 'Artist', 'score': 0.99},
        {'mbid': 'second-id', 'title': 'Second Song', 'artist': 'Artist', 'score': 0.99},
    ]
    file = _file('First Song', '/album/01 - First Song.flac', 200000, tied[0])
    file['acoustid']['recordings'] = tied
    slots = [
        {'title': 'First Song', 'artist': 'Artist',
         'recording_mbid': 'first-id', 'duration_ms': 200000},
        {'title': 'Second Song', 'artist': 'Artist',
         'recording_mbid': 'second-id', 'duration_ms': 201000},
    ]

    pair = reconcile_folder([file], slots)['pairs'][0]

    assert pair['confidence'] == 'ambiguous'


def test_metadata_only_owner_does_not_resolve_a_competing_audio_identity():
    tied = [
        {'mbid': 'first-id', 'title': 'First Song', 'artist': 'Artist', 'score': 0.99},
        {'mbid': 'second-id', 'title': 'Second Song', 'artist': 'Artist', 'score': 0.99},
    ]
    files = [
        _file('First Song', '/album/01 - First Song.flac', 200000, tied[0]),
        _file('Second Song', '/album/02 - Second Song.flac', 201000),
    ]
    files[0]['acoustid']['recordings'] = tied
    slots = [
        {'title': 'First Song', 'artist': 'Artist',
         'recording_mbid': 'first-id', 'duration_ms': 200000},
        {'title': 'Second Song', 'artist': 'Artist',
         'recording_mbid': 'second-id', 'duration_ms': 201000},
    ]

    first_pair = next(pair for pair in reconcile_folder(files, slots)['pairs']
                      if pair['file_index'] == 0)

    assert first_pair['confidence'] == 'ambiguous'


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


def test_file_strongly_owned_by_another_slot_is_not_a_competing_copy():
    files = [
        _file('Silent Movie', '/album/07 - Silent Movie.mp3', 220000,
              {'mbid': 'silent', 'title': 'Silent Movie',
               'artist': 'Natasha Bedingfield', 'score': 0.97}),
        _file("If You're Gonna Jump", "/album/06 - If You're Gonna Jump.mp3", 220000,
              {'mbid': 'jump', 'title': "If You're Gonna Jump",
               'artist': 'Natasha Bedingfield', 'score': 0.97}),
    ]
    files[0]['acoustid']['recordings'].append(
        {'mbid': 'jump', 'title': "If You're Gonna Jump",
         'artist': 'Natasha Bedingfield', 'score': 0.97})
    files[1]['acoustid']['recordings'].append(
        {'mbid': 'silent', 'title': 'Silent Movie',
         'artist': 'Natasha Bedingfield', 'score': 0.97})
    slots = [
        {'title': 'Silent Movie', 'artist': 'Natasha Bedingfield',
         'recording_mbid': 'silent', 'duration_ms': 220250},
        {'title': "If You're Gonna Jump", 'artist': 'Natasha Bedingfield',
         'recording_mbid': 'jump', 'duration_ms': 220100},
    ]

    pairs = reconcile_folder(files, slots)['pairs']

    assert all(pair['confidence'] == 'clear' for pair in pairs)
    assert all('another local file fits this release track'
               not in pair['ambiguity_reasons'] for pair in pairs)


def test_strong_fingerprint_titles_can_identify_mislabeled_audio():
    file = _file(
        'Who Wants to Live Forever', '/album/26 - Who Wants to Live Forever.flac',
        300000, {'mbid': 'teo', 'title': 'Teo Torriatte',
                 'artist': 'Queen', 'score': 1.0})
    file['acoustid']['recordings'].append(
        {'mbid': 'rain', 'title': 'Here Comes the Rain Again',
         'artist': 'Eurythmics', 'score': 1.0})

    assert audio_contradicts_local_title(file) is True


def test_fingerprint_without_a_recording_title_is_not_proof_of_wrong_audio():
    file = _file(
        'Who Wants to Live Forever', '/album/26 - Who Wants to Live Forever.flac',
        300000, {'mbid': 'unknown', 'title': '', 'artist': 'Queen', 'score': 1.0})

    assert audio_contradicts_local_title(file) is False


def test_top_acoustid_aliases_for_one_release_slot_are_not_ambiguous():
    files = [_file(
        'Angeleyes', '/album/21 - Angeleyes.flac', 260000,
        {'mbid': 'joined-title', 'title': 'Angeleyes',
         'artist': 'ABBA', 'score': 0.94},
    )]
    files[0]['acoustid']['recordings'].append({
        'mbid': 'spaced-title', 'title': 'Angel Eyes',
        'artist': 'ABBA', 'score': 0.94,
    })
    slots = [{
        'title': 'Angeleyes', 'artist': 'ABBA',
        'recording_mbid': 'joined-title', 'duration_ms': 264400,
    }]

    pair = reconcile_folder(files, slots)['pairs'][0]

    assert pair['confidence'] == 'clear'
    assert pair['duration_delta_ms'] == 4400
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


def test_bare_acoustid_title_does_not_veto_agreed_instrumental_slot():
    shared = {'mbid': 'shared', 'title': 'Epipelagic',
              'artist': 'The Ocean', 'score': 1.0}
    files = [
        _file('Epipelagic', '/album/01 - Epipelagic.flac', 72440, shared),
        _file('Epipelagic (instrumental)',
              '/album/12 - Epipelagic (instrumental).flac', 72440, shared),
    ]
    slots = [
        {'title': 'Epipelagic', 'artist': 'The Ocean', 'duration_ms': 72440,
         'track_number': 1, 'disc_number': 1},
        {'title': 'Epipelagic (Instrumental)', 'artist': 'The Ocean',
         'duration_ms': 72440, 'track_number': 1, 'disc_number': 2},
    ]

    result = reconcile_folder(files, slots)

    assert {(pair['file_index'], pair['slot_index']) for pair in result['pairs']} == {
        (0, 0), (1, 1)}
    assert all(pair['confidence'] == 'clear' for pair in result['pairs'])
    assert any('base recording title' in reason
               for reason in result['pairs'][1]['reasons'])
    assert result['extra_files'] == []
    assert result['missing_slots'] == []


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


def test_title_differences_separate_display_flavor_from_recording_identity():
    bare = {'title': 'Song'}
    assert title_difference_kind('Song (Serenade From the Stars)', bare) == 'flavor'
    assert title_difference_kind('SONG', bare) == 'typography'
    assert title_difference_kind('Song (Live)', bare) == 'version'
    assert title_difference_kind('Song (Live 1977)', {'title': 'Song (Live 1978)'}) == 'material'


def test_matching_musicbrainz_disambiguation_preserves_useful_version_detail():
    slot = {'title': 'Home', 'recording_disambiguation': 'live'}

    assert title_difference_kind('Home (Live)', slot) == 'version_detail'
