"""recording versions in fresh and persisted matching modes."""
import pytest

from core.reconciliation.plan import (
    audio_contradicts_local_title,
    file_version_markers,
    reconcile_folder,
    slot_version_markers,
    title_difference_kind,
)
from tests.reconciliation.support.audio import _file

pytestmark = pytest.mark.usefixtures("matching_parity")


def test_bare_audio_label_uses_same_version_rule_in_matching_and_final_validation():
    from core.reconciliation.projection import _audio_conflicts_with_slot
    file = _file('Home - Live', '/album/01 - Home - Live.flac', 200000,
                 {'mbid': 'bare-id', 'title': 'Home', 'artist': 'Artist', 'score': .99})
    file['tags']['artist'] = 'Artist'
    slot = {'title': 'Home (Live) [Bonus Track]', 'artist': 'Artist',
            'recording_mbid': 'live-id', 'duration_ms': 200100, 'track_number': 1}
    assert reconcile_folder([file], [slot])['pairs'][0]['confidence'] == 'clear'
    assert not _audio_conflicts_with_slot(file, slot)
    file['acoustid']['recordings'][0]['title'] = 'Home (studio version)'
    assert all(pair['confidence'] != 'clear' for pair in reconcile_folder([file], [slot])['pairs'])
    assert _audio_conflicts_with_slot(file, slot)


@pytest.mark.parametrize(('local', 'remote'), [
    ('Home (Alpha remix)', 'Home (Beta remix)'),
    ('Home (Alpha remix)', 'Home (Alpha Beta remix)'),
    ('Home (Alpha mix)', 'Home (Beta mix)'),
    ('Home (Abraham remix)', 'Home (Abrahams remix)'),
    ('Home (live at Paris)', 'Home (live at London)'),
    ('Home (radio edit)', 'Home (album version)'),
])
def test_named_versions_cannot_fill_each_others_release_slots(local, remote):
    from core.reconciliation.plan import display_title_compatible
    file = _file(local, f'/album/01 - {local}.flac', 200000,
                 {'mbid': 'local-id', 'title': local, 'artist': 'Artist', 'score': .99})
    slot = {'title': remote, 'artist': 'Artist', 'recording_mbid': 'remote-id', 'duration_ms': 200000}
    plan = reconcile_folder([file], [slot])
    assert not plan['pairs']
    assert plan['missing_slots'] == [0]
    assert plan['extra_files'] == [0]
    assert title_difference_kind(local, slot) == 'version'
    assert not display_title_compatible(local, slot)


def test_generic_version_label_can_omit_a_named_performance():
    file = _file('Home (live at Paris)', '/album/01 - Home (live at Paris).flac', 200000,
                 {'mbid': 'live-id', 'title': 'Home', 'artist': 'Artist', 'score': .99})
    slot = {'title': 'Home (live)', 'artist': 'Artist', 'recording_mbid': 'live-id', 'duration_ms': 200000}
    assert reconcile_folder([file], [slot])['pairs'][0]['confidence'] == 'clear'


@pytest.mark.parametrize(('artist', 'local', 'remote'), [
    ('Cheap Trick', 'ELO Kiddies - Single Version', 'ELO Kiddies (single version)'),
    ('Lynyrd Skynyrd', 'Four Walls of Raiford - Undubbed Demo Version', 'Four Walls of Raiford (undubbed demo)'),
    ('*NSYNC', 'Girlfriend (feat. Nelly) - The Neptunes Remix', 'Girlfriend (The Neptunes remix)'),
    ('Sixpence None the Richer', 'Kiss Me - Japanese Commercial Single Version Import', 'Kiss Me (Japanese Commercial single version)'),
])
def test_artist_prefixes_and_import_labels_do_not_create_named_version_conflicts(artist, local, remote):
    file = _file(local, f'/album/01 - {artist} - {local}.flac', 200000,
                 {'mbid': 'recording', 'title': remote, 'artist': artist, 'score': .99})
    file['tags'].update(artist=artist, mbid='recording')
    slot = {'title': remote, 'artist': artist, 'recording_mbid': 'recording', 'duration_ms': 200000}
    assert reconcile_folder([file], [slot])['pairs'][0]['confidence'] == 'clear'


def test_filename_version_still_blocks_a_different_named_mix_when_tags_omit_it():
    file = _file('Home (remix)', '/album/01 - Artist - Home (Alpha remix).flac', 200000,
                 {'mbid': 'recording', 'title': 'Home (remix)', 'artist': 'Artist', 'score': .99})
    file['tags']['artist'] = 'Artist'
    slot = {'title': 'Home (Beta remix)', 'artist': 'Artist', 'recording_mbid': 'other', 'duration_ms': 200000}
    assert not reconcile_folder([file], [slot])['pairs']


@pytest.mark.parametrize(('local', 'remote'), [
    ("Horology (live in Utrecht ’19)", "Horology (Live in Utrech '19)"),
    ('Golden Hair - Peel Session', 'Golden Hair (BBC Peel session)'),
    ('Song (live at Mill St Brewery, Toronto 2004)', 'Song (live at Mill St Brewery, Toronto 20/9/04)'),
    ('Song (live at Mill St Brewery, Toronto 2004-09-20)', 'Song (live at Mill St Brewery, Toronto 20/9/04)'),
    ('Song (live at the Paradise, June 1980)', 'Song (live at the Paradise) (June 1980)'),
])
def test_named_performance_detail_allows_catalogue_spelling_and_date_precision(local, remote):
    from core.reconciliation.plan import versions_compatible
    file = _file(local, f'/album/01 - {local}.flac', 200000)
    assert versions_compatible(file, {'title': remote})


@pytest.mark.parametrize(('local', 'remote'), [
    ('Song (live at Paris 2004-09-20)', 'Song (live at Paris 2004-09-21)'),
    ('Song (live at Paris 2004-09-20)', 'Song (live at Paris 20/8/04)'),
    ('Song (live at Paris 2004)', 'Song (live at Paris 20/9/05)'),
    ('Song (live at Paris 2004)', 'Song (live at London 20/9/04)'),
])
def test_named_performance_dates_cannot_disagree(local, remote):
    from core.reconciliation.plan import versions_compatible
    assert not versions_compatible(_file(local, '/album/song.flac', 200000), {'title': remote})


@pytest.mark.parametrize(('local', 'remote'), [
    ('Home (live at Wembley)', 'Home (live at Wembley, London)'),
    ('Home (live at Wembley 1986)', 'Home (live at Wembley, 12 July 1986)'),
    ('Home (live at Wembley, 12 July 1986)', 'Home (live at Wembley, 1986-07-12)'),
    ('Home (live at Wembley, July 12, 1986)', 'Home (live at Wembley, 12/7/86)'),
    ('Home (live at Wembley, July 1986)', 'Home (live at Wembley, 1986-07-12)'),
    ('Home (live at Wembley) (July 12, 1986)', 'Home (live at Wembley, 1986-07-12)'),
    ('Home (live at Wembley ’86)', 'Home (live at Wembley, 1986)'),
    ('Bring Me to Life (live on Triple M’s Garage Session, Australia – June 2020)',
     'Bring Me to Life (live on Triple M’s Garage Session/2020)'),
    ('My Immortal (live at O2 Arena, London – November 14, 2022)',
     'My Immortal (live at O2 Arena/2022)'),
    ('Kick Out the Jams - Live At Columbia Records Radio Hour, New York, NY, June 4, 1995',
     'Kick Out the Jams (live, Columbia radio Hour)'),
    ('Somewhere Only We Know - Live At Forum, London/2008',
     'Somewhere Only We Know (live at The Forum, London)'),
    ('Overprotected (the Darkchild remix)', 'Overprotected (The Darkchild Remix – Remastered)'),
])
@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('with_audio', [False, True])
def test_compatible_performance_detail_preserves_assignment_and_local_title(local, remote, reverse, with_audio):
    from core.reconciliation.plan import display_title_compatible
    if reverse:
        local, remote = remote, local
    # Use a real filename-safe date; tags and filename are independent layers.
    filename = local.replace('/', '-')
    file = _file(local, f'/album/01 - {filename}.flac', 200000,
                 {'mbid': 'recording', 'title': remote, 'artist': 'Artist', 'score': .99})
    file['tags'].update(artist='Artist', mbid='recording', track_number=1)
    if not with_audio:
        file['acoustid'] = {'status': 'no_match', 'recordings': []}
    slot = {'title': remote, 'artist': 'Artist', 'duration_ms': 200000,
            'recording_mbid': 'recording', 'track_number': 1}
    result = reconcile_folder([file], [slot])
    # Removing independent audio may lower confidence, but must not turn
    # compatible qualifier detail into an extra/missing version conflict.
    assert result['pairs'][0]['confidence'] in (('clear',) if with_audio else ('clear', 'likely'))
    assert not result['extra_files'] and not result['missing_slots']
    assert display_title_compatible(local, slot)
    assert title_difference_kind(local, slot) == 'version_detail'


@pytest.mark.parametrize(('local', 'remote'), [
    ('Home (live at Wembley, July 12, 1986)', 'Home (live at Wembley, 1986-07-13)'),
    ('Home (live at Wembley, July 1986)', 'Home (live at Wembley, June 1986)'),
    ('Home (live at Wembley 1986)', 'Home (live at Wembley, 12 July 1987)'),
    ('Home (live at Paris, 1986-07-12)', 'Home (live at London, July 12, 1986)'),
    ('Home (Alpha remix)', 'Home (Alpha Beta remix)'),
    ('Home (live Alpha remix)', 'Home (live Alpha Beta remix)'),
    ('Home (live at Hall 1, 1986-07-12)', 'Home (live at Hall 2, July 12, 1986)'),
    ('Home (live, take 1)', 'Home (live, take 2)'),
])
def test_conflicting_performance_facts_are_not_overridden_by_same_recording_id(local, remote):
    file = _file(local, f'/album/01 - {local}.flac', 200000,
                 {'mbid': 'recording', 'title': remote, 'artist': 'Artist', 'score': .99})
    slot = {'title': remote, 'artist': 'Artist', 'recording_mbid': 'recording', 'duration_ms': 200000}
    assert not reconcile_folder([file], [slot])['pairs']


@pytest.mark.parametrize('conflicting_layer', ['filename', 'database_title', 'disambiguation'])
def test_additional_detail_does_not_hide_an_independent_venue_conflict(conflicting_layer):
    local = 'Home (live at Wembley)'
    file = _file(local, f'/album/01 - {local}.flac', 200000,
                 {'mbid': 'recording', 'title': local, 'artist': 'Artist', 'score': .99})
    if conflicting_layer == 'filename':
        file['path'] = '/album/01 - Home (live at Paris).flac'
    elif conflicting_layer == 'database_title':
        file['db'] = {'title': 'Home (live at Paris)'}
    else:
        file['db'] = {'recording_disambiguation': 'live at Paris'}
    slot = {'title': 'Home (live at Wembley, London)', 'artist': 'Artist',
            'recording_mbid': 'recording', 'duration_ms': 200000}
    if conflicting_layer == 'disambiguation':
        slot['recording_disambiguation'] = 'live at Wembley, London'
    assert not reconcile_folder([file], [slot])['pairs']


def test_recording_comments_allow_compatible_performance_details():
    file = _file('Home', '/album/01 - Home.flac', 200000,
                 {'mbid': 'recording', 'title': 'Home', 'artist': 'Artist', 'score': .99})
    file['db'] = {'recording_disambiguation': 'live at Wembley, July 1986'}
    slot = {'title': 'Home', 'artist': 'Artist', 'recording_mbid': 'recording', 'duration_ms': 200000,
            'recording_disambiguation': 'live at Wembley, London, 1986-07-12'}
    assert reconcile_folder([file], [slot])['pairs'][0]['confidence'] == 'clear'


@pytest.mark.parametrize(('local', 'remote', 'comment'), [
    ('Bring Me to Life (live on Triple M’s Garage Session, Australia – June, 2020)',
     'Bring Me to Life (live on Triple M’s Garage Session / 2020)',
     'live, 2020‐06: Triple M’s Garage Session, Australia'),
    ('This Is The Last Time - Live At Mill St Brewery, Toronto / 2004 / Acoustic',
     'This Is the Last Time (acoustic, Mill St Brewery, Toronto 20/9/04)',
     'live, 2004-09-20: Mill Street Brewery, Toronto, ON, Canada'),
])
@pytest.mark.parametrize('comment_layer', ['local', 'remote', 'both'])
def test_live_catalogue_comments_preserve_compatible_assignments(local, remote, comment, comment_layer):
    from core.reconciliation.plan import display_title_compatible
    file = _file(local, f"/album/01 - {local.replace('/', '_')}.flac", 200000,
                 {'mbid': 'recording', 'title': local, 'artist': 'Artist', 'score': .99})
    file['tags'].update(artist='Artist', mbid='recording')
    slot = {'title': remote, 'artist': 'Artist', 'recording_mbid': 'recording', 'duration_ms': 200000}
    if comment_layer in ('local', 'both'):
        file['db'] = {'title': local, 'recording_disambiguation': comment}
    if comment_layer in ('remote', 'both'):
        slot['recording_disambiguation'] = comment
    # Acoustic/live markers agree only when the recording comment supplies
    # the local title's live context on the remote side.
    if 'Acoustic' in local and comment_layer == 'local':
        slot['title'] = remote.replace('(acoustic,', '(live acoustic,')
    plan = reconcile_folder([file], [slot])
    assert plan['pairs'][0]['confidence'] == 'clear'
    assert not plan['extra_files'] and not plan['missing_slots']
    assert display_title_compatible(local, slot)


@pytest.mark.parametrize('comment', [
    'live, 2020‐07: Triple M’s Garage Session, Australia',
    'live, 2021‐06: Triple M’s Garage Session, Australia',
    'live, 2020‐06: Another Garage Session, Australia',
])
def test_short_iso_recording_comments_still_protect_real_conflicts(comment):
    local = 'Bring Me to Life (live on Triple M’s Garage Session, Australia – June, 2020)'
    file = _file(local, f'/album/01 - {local}.flac', 200000,
                 {'mbid': 'recording', 'title': local, 'artist': 'Artist', 'score': .99})
    file['db'] = {'recording_disambiguation': 'live, June 2020: Triple M’s Garage Session, Australia'}
    slot = {'title': local, 'artist': 'Artist', 'recording_mbid': 'recording', 'duration_ms': 200000,
            'recording_disambiguation': comment}
    assert not reconcile_folder([file], [slot])['pairs']


@pytest.mark.parametrize(('local', 'remote', 'comment'), [
    ('Turn The Page - Live In Detroit/1975', 'Turn the Page',
     'live, 1975-9-4/5: Cobo Hall, Detroit, MI, USA'),
    ('I Want You to Want Me - Live at Nippon Budokan, Tokyo, JPN - April 1978',
     'I Want You to Want Me (live)', 'live, 1978-04-27, 28 & 29: Nippon Budokan, Tokyo, Japan'),
    ('Free Bird - Live At The Fox Theater/1976', 'Free Bird - Live At The Fox Theater/1976',
     'live, 1976‐07‐08: Fox Theatre, Atlanta, GA, USA'),
    ('Triassic (live at Roadburn Redux)', 'Triassic',
     'live, 2021‐03‐27: Die Mühle, Gyhum, Germany'),
    ('Flaws (Acoustic Version / Live From Unit 24, London, United Kingdom / 2012)',
     'Flaws (Acoustic Version / Live From Unit 24, London, United Kingdom / 2012)',
     'live, 2012: Unit 24, London, UK'),
])
def test_event_titles_are_not_cross_compared_with_recording_venue_comments(local, remote, comment):
    file = _file(local, f"/album/01 - {local.replace('/', '_')}.flac", 200000,
                 {'mbid': 'recording', 'title': local, 'artist': 'Artist', 'score': .99})
    file['tags'].update(artist='Artist', mbid='recording')
    slot = {'title': remote, 'artist': 'Artist', 'recording_mbid': 'recording', 'duration_ms': 200000,
            'recording_disambiguation': comment}
    plan = reconcile_folder([file], [slot])
    assert plan['pairs'][0]['confidence'] == 'clear'
    assert not plan['extra_files'] and not plan['missing_slots']


def test_primary_artist_audio_supports_a_full_local_remixer_credit():
    file = _file('Home', '/album/Home.flac', 200000,
                 {'mbid': 'recording', 'title': 'Home', 'artist': 'Röyksopp', 'score': .99})
    file['tags']['track_artist'] = 'Röyksopp, Maceo Plex'
    assert not audio_contradicts_local_title(file)


def test_named_mix_audio_supports_the_same_remix_on_another_provider():
    from core.reconciliation.projection import _audio_conflicts_with_slot
    file = _file('Sordid Affair - Maceo Plex Remix', '/album/02 - Sordid Affair - Maceo Plex Remix.flac', 478266,
                 {'mbid': 'recording', 'title': 'Sordid Affair (Maceo Plex mix)', 'artist': 'Röyksopp', 'score': .99})
    file['tags']['track_artist'] = 'Röyksopp, Maceo Plex'
    slot = {'title': 'Sordid Affair (Maceo Plex Remix)', 'artist': 'Röyksopp', 'duration_ms': 478000}
    assert reconcile_folder([file], [slot])['pairs'][0]['confidence'] == 'clear'
    assert not _audio_conflicts_with_slot(file, slot)


@pytest.mark.parametrize('qualifier', ['stereo mix', '2009 remastered stereo mix', 'original album mix'])
def test_generic_mix_labels_do_not_become_named_remixes(qualifier):
    from core.reconciliation.plan import _version_markers
    assert 'remix' not in _version_markers(f'Song ({qualifier})')


def test_acoustic_session_and_alternative_wording_match_equivalent_versions():
    assert file_version_markers(_file('Acts of Man - Acoustic',
                                      '/album/Acts of Man - Acoustic.flac', 200000)) == (
        slot_version_markers({'title': 'Acts of Man (Acoustic Session)'})
    )
    assert file_version_markers(_file('Fortune - Alternative Version',
                                      '/album/Fortune - Alternative Version.flac', 200000)) == (
        slot_version_markers({'title': 'Fortune (Alternate Version)'})
    )


def test_continuous_multidisc_position_does_not_make_noisy_versions_out_of_range():
    file = _file('Girl', '/album/30 - Girl.flac', 151440,
                 {'mbid': 'girl', 'title': 'Girl', 'artist': 'The Beatles', 'score': 1})
    file['tags'].update(track_number=30, disc_number=2, artist='The Beatles', mbid='girl')
    file['acoustid']['recordings'].append(
        {'mbid': 'alternate', 'title': 'Girl (take 2)', 'artist': 'The Beatles', 'score': 1})
    slots = [{'title': f'Other song {i}', 'disc_number': 1 if i <= 19 else 2,
              'track_number': i if i <= 19 else i - 19, 'duration_ms': 200000}
             for i in range(1, 31)]
    slots[-1].update(title='Girl', artist='The Beatles', recording_mbid='girl', duration_ms=151440)

    pair = reconcile_folder([file], slots)['pairs'][0]

    assert pair['slot_index'] == 29
    assert pair['confidence'] == 'clear'
    assert not pair['ambiguity_reasons']


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


def test_material_version_marker_is_distinct_but_subtitle_flavor_can_match():
    live = [_file('Song (Live)', '/album/01 - Song (Live).flac', 200000)]
    studio_slot = [{'title': 'Song', 'duration_ms': 200000}]
    assert reconcile_folder(live, studio_slot)['pairs'] == []
    subtitle = [_file('Song (Serenade From the Stars)',
                      '/album/01 - Song (Serenade From the Stars).flac', 200000)]
    assert len(reconcile_folder(subtitle, studio_slot)['pairs']) == 1


def test_matching_musicbrainz_disambiguation_preserves_useful_version_detail():
    slot = {'title': 'Home', 'recording_disambiguation': 'live'}

    assert title_difference_kind('Home (Live)', slot) == 'version_detail'


def test_alternate_extended_exact_identity_pairs_but_requires_version_review():
    file = _file('If You Want My Love - Alternate Extended Version',
                 '/album/02 - If You Want My Love - Alternate Extended Version.flac',
                 266800, {'mbid': 'alternate', 'title': 'If You Want My Love (alternate, extra bridge version)',
                          'artist': 'Cheap Trick', 'score': 0.98})
    slot = {'title': 'If You Want My Love (alternate, extra bridge version)',
            'artist': 'Cheap Trick', 'recording_mbid': 'alternate',
            'duration_ms': 266800, 'disc_number': 2, 'track_number': 6}
    result = reconcile_folder([file], [slot])
    assert not result['extra_files'] and not result['missing_slots']
    assert result['pairs'][0]['confidence'] == 'ambiguous'
    assert any('wording' in reason for reason in result['pairs'][0]['ambiguity_reasons'])
    # Duration alone or a conflicting performance must not join the pair.
    file['acoustid']['recordings'][0]['mbid'] = 'another-recording'
    assert reconcile_folder([file], [slot])['missing_slots'] == [0]
    file['acoustid']['recordings'][0]['mbid'] = 'alternate'
    file['tags']['duration_ms'] = 290000
    assert reconcile_folder([file], [slot])['missing_slots'] == [0]
    file['tags']['duration_ms'] = 266800
    file['tags']['title'] += ' (live)'
    assert reconcile_folder([file], [slot])['missing_slots'] == [0]


@pytest.mark.parametrize('weakness', ['', 'id', 'credit', 'duration', 'position', 'same_credit'])
def test_featured_versions_use_full_credit_with_recording_and_position(weakness):
    recordings = [{'title': 'Northern Attitude', 'artist': artist, 'mbid': mbid, 'score': .997}
                  for artist, mbid in [('Noah Kahan', 'original'), ('Noah Kahan with Hozier', 'featured')]]
    slots = [{'title': 'Northern Attitude', 'artist': artist, 'artist_names': names,
              'recording_mbid': mbid, 'track_number': position, 'disc_number': 1, 'duration_ms': 267256}
             for artist, names, mbid, position in [
                 ('Noah Kahan', ['Noah Kahan'], 'original', 1),
                 ('Noah Kahan & Hozier', ['Noah Kahan', 'Hozier'], 'featured', 26)]]
    files = []
    for slot in slots:
        file = _file('Northern Attitude', f"/album/{slot['track_number']:02} - Northern Attitude.flac", 267256)
        file['tags'].update(track_artist=slot['artist'], mbid=slot['recording_mbid'], track_number=slot['track_number'])
        file['acoustid'] = {'status': 'ok', 'recordings': recordings}
        files.append(file)
    if weakness == 'id': files[1]['tags']['mbid'] = ''
    if weakness == 'credit': files[1]['tags']['track_artist'] = 'Noah Kahan'
    if weakness == 'duration': files[1]['tags']['duration_ms'] += 10000
    if weakness == 'position': files[1]['tags']['track_number'] = 27
    if weakness == 'same_credit':
        slots[1]['artist_names'] = slots[0]['artist_names']
    plan = reconcile_folder(files, slots)
    assert [(pair['file_index'], pair['slot_index']) for pair in plan['pairs']] == [(0, 0), (1, 1)]
    featured = plan['pairs'][1]
    assert (featured['confidence'] == 'clear') == (not weakness)
    assert featured['reasons'][0] == 'AcoustID recording ID matches release slot'
