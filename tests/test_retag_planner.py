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
