"""Unit tests for the library re-tag planner (pure match + diff + payload)."""

from __future__ import annotations

from core.library import retag_planner as rp


# ── track matching ──

def test_match_by_disc_and_track_number():
    src = [
        {'name': 'A', 'track_number': 1, 'disc_number': 1},
        {'name': 'B', 'track_number': 2, 'disc_number': 1},
    ]
    lib = [
        {'title': 'wrong title', 'track_number': 2, 'disc_number': 1},
        {'title': 'whatever', 'track_number': 1, 'disc_number': 1},
    ]
    pairs = rp.match_source_tracks(src, lib)
    assert pairs[0][1]['name'] == 'B'   # lib track #2 → source B
    assert pairs[1][1]['name'] == 'A'


def test_match_by_title_when_no_track_number():
    src = [{'name': 'Bohemian Rhapsody', 'track_number': 1, 'disc_number': 1}]
    lib = [{'title': 'Bohemian Rhapsody (Remastered)', 'track_number': None, 'disc_number': 1}]
    pairs = rp.match_source_tracks(src, lib)
    assert pairs[0][1]['name'] == 'Bohemian Rhapsody'


def test_unmatched_library_track_is_none():
    src = [{'name': 'A', 'track_number': 1, 'disc_number': 1}]
    lib = [{'title': 'Completely Different', 'track_number': 9, 'disc_number': 1}]
    pairs = rp.match_source_tracks(src, lib)
    assert pairs[0][1] is None


def test_source_track_consumed_once():
    src = [{'name': 'A', 'track_number': 1, 'disc_number': 1}]
    lib = [
        {'title': 'A', 'track_number': 1, 'disc_number': 1},
        {'title': 'A again', 'track_number': 1, 'disc_number': 1},
    ]
    pairs = rp.match_source_tracks(src, lib)
    assert pairs[0][1] is not None
    assert pairs[1][1] is None          # the one source track was already used


TAKE_OVER = [{'name': n, 'track_number': i, 'disc_number': 1} for i, n in enumerate(
    ['The Taking', 'Geek to the Beat', 'Take Over', 'DJ DJ', 'Antenna', 'Caged Bird, Pt. 1',
     "In the Mornin' (Caged Bird, Pt. 2)", 'Radio', 'Gumbo', 'Country Baked Yams', "Coastin'",
     'Juicy Juice', 'Peppermint Patty', 'Bring in the Light', 'Legacy'], start=1)]


def test_a_title_naming_another_track_beats_a_wrong_position():
    """#1610 (kevin2xk): "Coastin'" carried track number 5 and was re-tagged as
    track 5 "Antenna". the title clearly names track 11, so it pairs there and
    the plan fixes the number instead of the title."""
    lib = [{'title': "Coastin'", 'track_number': 5, 'disc_number': 1}]
    pairs = rp.match_source_tracks(TAKE_OVER, lib)
    assert pairs[0][1]['name'] == "Coastin'"

    plan = rp.plan_track({'title': "Coastin'", 'track_number': 5, 'disc_number': 1},
                         pairs[0][1], {'name': 'The Take Over'})
    assert 'title' not in plan['changes']
    assert plan['changes']['track_number'] == {'old': '5', 'new': '11'}


def test_a_wrong_position_doesnt_steal_the_track_that_belongs_there():
    # antenna (5) and coastin' (tagged 5 too) in the same album
    lib = [{'title': "Coastin'", 'track_number': 5, 'disc_number': 1},
           {'title': 'Antenna', 'track_number': 5, 'disc_number': 1}]
    pairs = rp.match_source_tracks(TAKE_OVER, lib)
    assert [p[1]['name'] for p in pairs] == ["Coastin'", 'Antenna']


def test_a_junk_title_still_pairs_by_position():
    lib = [{'title': 'Track 05', 'track_number': 5, 'disc_number': 1}]
    assert rp.match_source_tracks(TAKE_OVER, lib)[0][1]['name'] == 'Antenna'


def test_a_close_title_keeps_its_position():
    lib = [{'title': 'Coastin (Album Version)', 'track_number': 11, 'disc_number': 1}]
    assert rp.match_source_tracks(TAKE_OVER, lib)[0][1]['name'] == "Coastin'"


# ── per-track diff (overwrite) ──

ALBUM = {'name': 'Real Album', 'artists': [{'name': 'Real Artist'}],
         'year': '2021-05-01', 'genres': ['Rock', 'Indie'], 'total_tracks': 10}
SRC = {'name': 'Real Title', 'track_number': 3, 'disc_number': 1,
       'artists': [{'name': 'Real Artist'}]}


def test_overwrite_reports_changed_fields_only():
    current = {'title': 'Old Title', 'album_artist': 'Real Artist',
               'album': 'Real Album', 'year': '2021', 'genre': 'Rock, Indie',
               'track_number': 3, 'disc_number': 1}
    plan = rp.plan_track(current, SRC, ALBUM, mode=rp.MODE_OVERWRITE)
    # Only the title differs; everything else already matches → single change.
    assert set(plan['changes']) == {'title'}
    assert plan['changes']['title'] == {'old': 'Old Title', 'new': 'Real Title'}
    assert plan['db_data'].get('title') == 'Real Title'
    # Unchanged fields must NOT be in the write payload.
    assert 'album_title' not in plan['db_data']


def test_overwrite_writes_album_artist_via_artist_name_key():
    current = {'title': 'Real Title', 'album_artist': 'WRONG Artist',
               'album': 'Real Album', 'year': '2021', 'genre': 'Rock, Indie',
               'track_number': 3, 'disc_number': 1}
    plan = rp.plan_track(current, SRC, ALBUM, mode=rp.MODE_OVERWRITE)
    assert plan['changes']['artist'] == {'old': 'WRONG Artist', 'new': 'Real Artist'}
    assert plan['db_data']['artist_name'] == 'Real Artist'      # writer uses artist_name = album artist


def test_album_artist_update_does_not_shorten_full_track_performer_credit():
    current = {'title': 'When I Kissed the Teacher', 'album': 'Soundtrack',
               'album_artist': 'Benny Andersson, Björn Ulvaeus, Lily James',
               'track_artist': 'Lily James, Jessica Keenan Wynn, Alexa Davies & Celia Imrie'}
    source = {'name': 'When I Kissed the Teacher',
              'artists': [{'name': 'Lily James'}]}
    album = {'name': 'Soundtrack', 'artists': [{'name': 'Cast of Mamma Mia! The Movie'}]}

    plan = rp.plan_track(current, source, album)

    assert 'artist' in plan['changes']
    assert 'track_artist' not in plan['changes']
    assert 'track_artist' not in plan['db_data']


def test_track_number_write_carries_track_count():
    current = {'title': 'Real Title', 'album_artist': 'Real Artist', 'album': 'Real Album',
               'year': '2021', 'genre': 'Rock, Indie', 'track_number': 99, 'disc_number': 1}
    plan = rp.plan_track(current, SRC, ALBUM, mode=rp.MODE_OVERWRITE)
    assert plan['db_data']['track_number'] == 3
    assert plan['db_data']['track_count'] == 10                 # carried alongside


def test_no_changes_when_everything_matches():
    current = {'title': 'Real Title', 'album_artist': 'Real Artist', 'album': 'Real Album',
               'year': '2021', 'genre': 'Rock, Indie', 'track_number': 3, 'disc_number': 1}
    plan = rp.plan_track(current, SRC, ALBUM, mode=rp.MODE_OVERWRITE)
    assert plan['changes'] == {}
    assert plan['db_data'] == {}


def test_compilation_track_artist_is_repaired_independently_of_album_artist():
    album = {'name': 'Soundtrack', 'artists': [{'name': 'Various Artists'}]}
    source = {'name': 'Living Waters', 'artists': [{'name': 'Philip Glass'}],
              'track_number': 12, 'disc_number': 1}
    current = {'title': 'Living Waters', 'album': 'Soundtrack',
               'album_artist': 'Various Artists', 'track_artist': 'Frédéric Chopin',
               'track_number': 12, 'disc_number': 1}

    plan = rp.plan_track(current, source, album)

    assert plan['changes'] == {
        'track_artist': {'old': 'Frédéric Chopin', 'new': 'Philip Glass'}}
    assert plan['db_data'] == {'track_artist': 'Philip Glass'}


def test_track_artist_credit_keeps_all_source_performers():
    album = {'name': 'Soundtrack', 'artists': [{'name': 'Various Artists'}]}
    source = {'name': 'Duet', 'artists': [{'name': 'First'}, {'name': 'Second'}],
              'track_number': 1, 'disc_number': 1}
    current = {'title': 'Duet', 'album': 'Soundtrack',
               'album_artist': 'Various Artists', 'track_artist': 'First',
               'track_number': 1, 'disc_number': 1}

    plan = rp.plan_track(current, source, album)

    assert plan['db_data']['track_artist'] == 'First; Second'


def test_source_does_not_shorten_a_comma_separated_local_artist_credit():
    album = {'name': 'Soundtrack', 'artists': [{'name': 'Various Artists'}]}
    source = {'name': 'Dreaming of Fiji', 'artists': [{'name': 'Philip Glass'}],
              'track_number': 4, 'disc_number': 1}
    current = {'title': 'Dreaming of Fiji', 'album': 'Soundtrack',
               'album_artist': 'Various Artists',
               'track_artist': 'Philip Glass, Burkhard Dallwitz',
               'track_number': 4, 'disc_number': 1}

    plan = rp.plan_track(current, source, album)

    assert 'track_artist' not in plan['changes']


def test_equivalent_feature_credit_punctuation_does_not_create_noise():
    album = {'name': 'Album', 'artists': [{'name': 'Eminem'}]}
    source = {
        'name': 'Bitch Please II',
        'artists': [
            {'name': 'Eminem'}, {'name': 'Dr. Dre'}, {'name': 'Snoop Dogg'},
            {'name': 'Xzibit'}, {'name': 'Nate Dogg'},
        ],
        'track_number': 1, 'disc_number': 1,
    }
    current = {
        'title': 'Bitch Please II', 'album': 'Album', 'album_artist': 'Eminem',
        'track_artist': 'Eminem featuring Dr. Dre, Snoop Dogg, Xzibit and Nate Dogg',
        'track_number': 1, 'disc_number': 1,
    }

    plan = rp.plan_track(current, source, album)

    assert 'track_artist' not in plan['changes']
    assert 'track_artist' not in plan['db_data']


def test_slash_separated_fuller_credit_is_preserved():
    album = {'name': 'Alive 2007', 'artists': [{'name': 'Daft Punk'}]}
    source = {'name': 'Touch It / Technologic',
              'artists': [{'name': 'Daft Punk'}],
              'track_number': 1, 'disc_number': 1}
    current = {'title': 'Touch It / Technologic', 'album': 'Alive 2007',
               'album_artist': 'Daft Punk',
               'track_artist': 'Daft Punk / Busta Rhymes',
               'track_number': 1, 'disc_number': 1}

    plan = rp.plan_track(current, source, album)

    assert 'track_artist' not in plan['changes']


def test_new_performer_in_source_credit_remains_actionable():
    album = {'name': 'Album', 'artists': [{'name': 'Sungazer'}]}
    source = {'name': 'Whalefall',
              'artists': [{'name': 'Sungazer'}, {'name': 'Plini'}],
              'track_number': 1, 'disc_number': 1}
    current = {'title': 'Whalefall', 'album': 'Album',
               'album_artist': 'Sungazer', 'track_artist': 'Sungazer',
               'track_number': 1, 'disc_number': 1}

    plan = rp.plan_track(current, source, album)

    assert plan['changes']['track_artist']['new'] == 'Sungazer; Plini'


def test_artist_name_substring_is_not_treated_as_same_credit():
    album = {'name': 'Album', 'artists': [{'name': 'Various Artists'}]}
    source = {'name': 'Track', 'artists': [{'name': 'Drake'}],
              'track_number': 1, 'disc_number': 1}
    current = {'title': 'Track', 'album': 'Album',
               'album_artist': 'Various Artists',
               'track_artist': 'Drakeo the Ruler feat. Guest',
               'track_number': 1, 'disc_number': 1}

    plan = rp.plan_track(current, source, album)

    assert plan['changes']['track_artist']['new'] == 'Drake'


def test_source_blank_field_never_written():
    album = {'name': 'Real Album', 'artists': [{'name': 'Real Artist'}]}  # no year/genres
    current = {'title': 'Real Title', 'album_artist': 'Real Artist', 'album': 'Real Album',
               'year': '', 'genre': '', 'track_number': 3, 'disc_number': 1}
    plan = rp.plan_track(current, SRC, album, mode=rp.MODE_OVERWRITE)
    assert 'year' not in plan['changes'] and 'year' not in plan['db_data']
    assert 'genres' not in plan['db_data']


# ── fill-missing mode ──

def test_fill_missing_only_writes_blanks():
    current = {'title': 'Keep My Title', 'album_artist': '', 'album': 'Real Album',
               'year': '', 'genre': 'Rock, Indie', 'track_number': 3, 'disc_number': 1}
    plan = rp.plan_track(current, SRC, ALBUM, mode=rp.MODE_FILL_MISSING)
    # title is present (kept), artist + year are blank (filled). genre present (kept).
    assert set(plan['changes']) == {'artist', 'year'}
    assert 'title' not in plan['db_data']            # not overwritten in fill-missing
    assert plan['db_data']['artist_name'] == 'Real Artist'
    assert plan['db_data']['year'] == '2021'
