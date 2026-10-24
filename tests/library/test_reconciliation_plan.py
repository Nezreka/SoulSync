"""Library-shaped examples from the user's reconciliation investigations."""

import sqlite3
from types import SimpleNamespace

import pytest

from core.library.reconciliation_match_cache import MatchingCache
from core.library.reconciliation_plan import (
    audio_contradicts_local_title, reconcile_folder, title_difference_kind,
    file_version_markers, slot_version_markers,
)


@pytest.fixture(autouse=True, params=['fresh', 'persisted'])
def matching_parity(request, monkeypatch, tmp_path):
    """Run every real-world matcher regression through a persisted round trip."""
    if request.param == 'fresh':
        return
    original = reconcile_folder
    db = SimpleNamespace(_get_connection=lambda: sqlite3.connect(tmp_path / 'matching.db'))

    def checked(files, slots, forced_pairs=None):
        expected = original(files, slots, forced_pairs=forced_pairs)
        cold = MatchingCache(db, 'shadow')
        assert cold.match(files, slots, forced_pairs, 'regression', original) == expected
        warm = MatchingCache(db, 'reuse')
        actual = warm.match(files, slots, forced_pairs, 'regression', original)
        assert actual == expected
        assert not cold.counts['cache_errors'] and not warm.counts['cache_errors']
        if all((f.get('acoustid') or {}).get('status') in ('ok', 'no_match') for f in files):
            assert warm.counts['reused'] == 1
        return actual

    monkeypatch.setattr(request.module, 'reconcile_folder', checked)


def _file(title, path, duration_ms, recording=None):
    return {'path': path, 'tags': {'title': title, 'duration_ms': duration_ms},
            'acoustid': {'status': 'ok', 'recordings': [recording]} if recording else
                        {'status': 'no_match', 'recordings': []}}


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


def test_unique_metadata_match_is_clear_without_a_fingerprint():
    file = _file('Home', '/album/01 - Home.flac', 200000)
    file['tags']['artist'] = 'Artist'
    slot = {'title': 'Home', 'artist': 'Artist', 'duration_ms': 200500, 'track_number': 1}
    pair = reconcile_folder([file], [slot])['pairs'][0]
    assert pair['confidence'] == 'clear'
    assert not pair['ambiguity_reasons']
    assert 'Unique title, artist, version and close duration support the assignment' in pair['reasons']


@pytest.mark.parametrize('weakness', ['artist', 'duration', 'decode', 'short', 'slot', 'copy', 'version'])
def test_metadata_route_keeps_uncertain_evidence_manual(weakness):
    file = _file('Home', '/album/01 - Home.flac', 200000)
    file['tags']['artist'] = 'Artist'
    slot = {'title': 'Home', 'artist': 'Artist', 'duration_ms': 200500, 'track_number': 1}
    files, slots = [file], [slot]
    if weakness == 'artist':slot['artist'] = 'Another Artist'
    if weakness == 'duration':slot['duration_ms'] = 204000
    if weakness == 'decode':file['acoustid']['status'] = 'fingerprint_error'
    if weakness == 'short':file['tags']['duration_ms'], slot['duration_ms'] = 5000, 6500
    if weakness == 'slot':slots.append({**slot, 'track_number': 2})
    if weakness == 'copy':files.append({**file, 'path': '/album/copy.flac'})
    if weakness == 'version':slot['title'] = 'Home (live)'
    assert all(pair['confidence'] != 'clear' for pair in reconcile_folder(files, slots)['pairs'])


def test_bare_audio_label_uses_same_version_rule_in_matching_and_final_validation():
    from core.repair_jobs.library_reconciliation import _audio_conflicts_with_slot
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


@pytest.mark.parametrize(('local', 'remote'), [
    ('Home (Alpha remix)', 'Home (Beta remix)'),
    ('Home (Alpha remix)', 'Home (Alpha Beta remix)'),
    ('Home (Alpha mix)', 'Home (Beta mix)'),
    ('Home (Abraham remix)', 'Home (Abrahams remix)'),
    ('Home (live at Paris)', 'Home (live at London)'),
    ('Home (radio edit)', 'Home (album version)'),
])
def test_named_versions_cannot_fill_each_others_release_slots(local, remote):
    from core.library.reconciliation_plan import display_title_compatible
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
    from core.library.reconciliation_plan import versions_compatible
    file = _file(local, f'/album/01 - {local}.flac', 200000)
    assert versions_compatible(file, {'title': remote})


@pytest.mark.parametrize(('local', 'remote'), [
    ('Song (live at Paris 2004-09-20)', 'Song (live at Paris 2004-09-21)'),
    ('Song (live at Paris 2004-09-20)', 'Song (live at Paris 20/8/04)'),
    ('Song (live at Paris 2004)', 'Song (live at Paris 20/9/05)'),
    ('Song (live at Paris 2004)', 'Song (live at London 20/9/04)'),
])
def test_named_performance_dates_cannot_disagree(local, remote):
    from core.library.reconciliation_plan import versions_compatible
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
    from core.library.reconciliation_plan import display_title_compatible
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


def test_date_normalization_does_not_hide_song_numbers():
    from core.library.reconciliation_plan import display_title_compatible, _title_score
    local = 'Song 1986 (live at Wembley, July 12, 1986)'
    remote = 'Song 1987 (live at Wembley, 1986-07-12)'
    assert _title_score(local, remote) == 0
    assert not display_title_compatible(local, {'title': remote})
    assert title_difference_kind(local, {'title': remote}) == 'material'


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
    from core.library.reconciliation_plan import display_title_compatible
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
    from core.repair_jobs.library_reconciliation import _audio_conflicts_with_slot
    file = _file('Sordid Affair - Maceo Plex Remix', '/album/02 - Sordid Affair - Maceo Plex Remix.flac', 478266,
                 {'mbid': 'recording', 'title': 'Sordid Affair (Maceo Plex mix)', 'artist': 'Röyksopp', 'score': .99})
    file['tags']['track_artist'] = 'Röyksopp, Maceo Plex'
    slot = {'title': 'Sordid Affair (Maceo Plex Remix)', 'artist': 'Röyksopp', 'duration_ms': 478000}
    assert reconcile_folder([file], [slot])['pairs'][0]['confidence'] == 'clear'
    assert not _audio_conflicts_with_slot(file, slot)


@pytest.mark.parametrize('qualifier', ['stereo mix', '2009 remastered stereo mix', 'original album mix'])
def test_generic_mix_labels_do_not_become_named_remixes(qualifier):
    from core.library.reconciliation_plan import _version_markers
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


def test_exact_session_title_and_fingerprint_can_supply_live_comment():
    from copy import deepcopy
    from core.library.reconciliation_plan import versions_compatible
    file = {'path': '/17 - Laura Palmer (Abbey Road Sessions).flac',
            'tags': {'title': 'Laura Palmer (Abbey Road Sessions)',
                     'artist': 'Bastille', 'duration_ms': 182440},
            'acoustid': {'status': 'ok', 'recordings': [{'mbid': 'session-id', 'title': 'Laura Palmer', 'artist': 'Bastille', 'score': 0.977}]}}
    slot = {'title': 'Laura Palmer (Abbey Road Sessions)', 'artist': 'Bastille',
            'recording_mbid': 'session-id', 'duration_ms': 182000,
            'recording_disambiguation': 'live, Abbey Road Studios, London, UK'}
    assert versions_compatible(file, slot)
    plan = reconcile_folder([file], [slot])
    assert not plan['missing_slots'] and not plan['extra_files']
    assert plan['pairs'][0]['confidence'] == 'clear'
    for mutation in ['wrong_recording', 'longer_audio', 'conflicting_comment', 'other_session']:
        other = deepcopy(file)
        if mutation == 'wrong_recording':
            other['acoustid']['recordings'][0]['mbid'] = 'studio-id'
        elif mutation == 'longer_audio':
            other['tags']['duration_ms'] = 190000
        elif mutation == 'conflicting_comment':
            other['db'] = {'recording_disambiguation': 'studio recording'}
        else:
            other['tags']['title'] = 'Laura Palmer (Other Sessions)'
        assert not versions_compatible(other, slot), mutation


def test_verified_manual_assignment_is_settled_in_track_matrix(monkeypatch):
    from core.repair_jobs import library_reconciliation as job
    files = [_file('Song', '/album/01 - Song.flac', 200000)]
    files[0]['tags'].update(track_number=1, disc_number=1)
    slots = [{'title': 'Song', 'track_number': 1, 'disc_number': 1,
              'duration_ms': 200000}]
    plan = reconcile_folder(files, slots, forced_pairs={0: 0})
    rows, categories = job._album_plan_assignments(files, slots, plan)
    assert rows[0]['confidence'] == 'manual'
    assert rows[0]['status'] == 'confirmed'
    assert 'audio_conflicts' not in categories
    monkeypatch.setattr(job, '_audio_conflicts_with_slot', lambda *args: True)
    rows, categories = job._album_plan_assignments(files, slots, plan)
    assert rows[0]['status'] == 'uncertain'
    assert 'audio_conflicts' in categories


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
