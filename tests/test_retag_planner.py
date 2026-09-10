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


# ── per-track diff (overwrite) ──

ALBUM = {'name': 'Real Album', 'artists': [{'name': 'Real Artist'}],
         'year': '2021-05-01', 'genres': ['Rock', 'Indie'], 'total_tracks': 10}
SRC = {'name': 'Real Title', 'track_number': 3, 'disc_number': 1,
       'artists': [{'name': 'Real Artist'}]}


def test_overwrite_reports_changed_fields_only():
    current = {'title': 'Old Title', 'artist': 'Real Artist',
               'album_artist': 'Real Artist',
               'album': 'Real Album', 'year': '2021', 'genre': 'Rock, Indie',
               'track_number': 3, 'disc_number': 1}
    plan = rp.plan_track(current, SRC, ALBUM, mode=rp.MODE_OVERWRITE)
    # Only the title differs; everything else already matches → single change.
    assert set(plan['changes']) == {'title'}
    assert plan['changes']['title'] == {'old': 'Old Title', 'new': 'Real Title'}
    assert plan['db_data'].get('title') == 'Real Title'
    # Unchanged fields must NOT be in the write payload.
    assert 'album_title' not in plan['db_data']


def test_wrong_album_artist_writes_via_artist_name_key():
    current = {'title': 'Real Title', 'artist': 'Real Artist',
               'album_artist': 'WRONG Artist',
               'album': 'Real Album', 'year': '2021', 'genre': 'Rock, Indie',
               'track_number': 3, 'disc_number': 1}
    plan = rp.plan_track(current, SRC, ALBUM, mode=rp.MODE_OVERWRITE)
    assert plan['changes']['album_artist'] == {'old': 'WRONG Artist', 'new': 'Real Artist'}
    assert plan['db_data']['artist_name'] == 'Real Artist'      # writer uses artist_name = album artist
    assert 'artist' not in plan['changes']                      # the track artist was already right


def test_track_number_write_carries_track_count():
    current = {'title': 'Real Title', 'album_artist': 'Real Artist', 'album': 'Real Album',
               'year': '2021', 'genre': 'Rock, Indie', 'track_number': 99, 'disc_number': 1}
    plan = rp.plan_track(current, SRC, ALBUM, mode=rp.MODE_OVERWRITE)
    assert plan['db_data']['track_number'] == 3
    assert plan['db_data']['track_count'] == 10                 # carried alongside


def test_no_changes_when_everything_matches():
    current = {'title': 'Real Title', 'artist': 'Real Artist',
               'album_artist': 'Real Artist', 'album': 'Real Album',
               'year': '2021', 'genre': 'Rock, Indie', 'track_number': 3, 'disc_number': 1}
    plan = rp.plan_track(current, SRC, ALBUM, mode=rp.MODE_OVERWRITE)
    assert plan['changes'] == {}
    assert plan['db_data'] == {}


def test_source_blank_field_never_written():
    album = {'name': 'Real Album', 'artists': [{'name': 'Real Artist'}]}  # no year/genres
    current = {'title': 'Real Title', 'artist': 'Real Artist',
               'album_artist': 'Real Artist', 'album': 'Real Album',
               'year': '', 'genre': '', 'track_number': 3, 'disc_number': 1}
    plan = rp.plan_track(current, SRC, album, mode=rp.MODE_OVERWRITE)
    assert 'year' not in plan['changes'] and 'year' not in plan['db_data']
    assert 'genres' not in plan['db_data']


# ── fill-missing mode ──

def test_fill_missing_only_writes_blanks():
    current = {'title': 'Keep My Title', 'artist': '', 'album_artist': '',
               'album': 'Real Album',
               'year': '', 'genre': 'Rock, Indie', 'track_number': 3, 'disc_number': 1}
    plan = rp.plan_track(current, SRC, ALBUM, mode=rp.MODE_FILL_MISSING)
    # title is present (kept), both artist fields + year are blank (filled).
    # genre present (kept).
    assert set(plan['changes']) == {'artist', 'album_artist', 'year'}
    assert 'title' not in plan['db_data']            # not overwritten in fill-missing
    assert plan['db_data']['artist_name'] == 'Real Artist'
    assert plan['db_data']['track_artist'] == 'Real Artist'
    assert plan['db_data']['year'] == '2021'


# ── the shared tag engine's guards (core/tag_writer) ──
#
# The planner used to carry its own diff, so every rule `build_tag_diff` and
# `write_tags_to_file` share was invisible to it: it promised changes the
# writer then refused, and the finding came back every scan because
# `_create_finding` refreshes a pending row in place.

def test_genre_already_containing_the_source_genres_is_not_a_change():
    """A generic source genre must not narrow a richer file tag.

    `write_tags_to_file` keeps the existing value here
    (`genre_write_value_is_subset_of_existing`), so reporting a change
    produces a finding whose fix can never resolve it.
    """
    current = {'title': 'Real Title', 'artist': 'Real Artist',
               'album_artist': 'Real Artist', 'album': 'Real Album',
               'year': '2021', 'genre': 'Rock, Indie, Shoegaze',
               'track_number': 3, 'disc_number': 1}
    plan = rp.plan_track(current, SRC, ALBUM, mode=rp.MODE_OVERWRITE)
    assert 'genre' not in plan['changes']
    assert 'genres' not in plan['db_data']


def test_wrong_track_artist_is_reported_even_when_album_artist_matches():
    """The ARTIST tag and the ALBUM ARTIST tag are two fields.

    Comparing only against album_artist left a file whose artist tag was
    wrong looking perfectly tagged.
    """
    current = {'title': 'Real Title', 'artist': 'WRONG Artist',
               'album_artist': 'Real Artist', 'album': 'Real Album',
               'year': '2021', 'genre': 'Rock, Indie',
               'track_number': 3, 'disc_number': 1}
    plan = rp.plan_track(current, SRC, ALBUM, mode=rp.MODE_OVERWRITE)
    assert plan['changes']['artist'] == {'old': 'WRONG Artist', 'new': 'Real Artist'}
    assert plan['db_data']['track_artist'] == 'Real Artist'
    assert 'album_artist' not in plan['changes']


def test_placeholder_source_value_is_held_back_not_promised():
    """#800: a compilation's "Various Artists" must not replace a real name.

    The writer refuses it, so the plan reports it as held back rather than as
    a pending change.
    """
    album = {'name': 'Real Album', 'artists': [{'name': 'Various Artists'}],
             'year': '2021', 'genres': ['Rock', 'Indie'], 'total_tracks': 10}
    src = {'name': 'Real Title', 'track_number': 3, 'disc_number': 1}
    current = {'title': 'Real Title', 'artist': 'Real Artist',
               'album_artist': 'Real Artist', 'album': 'Real Album',
               'year': '2021', 'genre': 'Rock, Indie',
               'track_number': 3, 'disc_number': 1}
    plan = rp.plan_track(current, src, album, mode=rp.MODE_OVERWRITE)
    assert 'artist' not in plan['changes']
    assert 'album_artist' not in plan['changes']
    assert 'artist_name' not in plan['db_data']
    assert set(plan['protected']) == {'artist', 'album_artist'}


def test_a_more_specific_file_date_is_preserved():
    """#824: the album gives a year, the file carries a full date.

    Overwriting 2021-05-01 with 2021 loses information the source never had.
    """
    current = {'title': 'Real Title', 'artist': 'Real Artist',
               'album_artist': 'Real Artist', 'album': 'Real Album',
               'year': '2021-05-01', 'genre': 'Rock, Indie',
               'track_number': 3, 'disc_number': 1}
    plan = rp.plan_track(current, SRC, ALBUM, mode=rp.MODE_OVERWRITE)
    assert 'year' not in plan['changes']
    assert 'year' not in plan['db_data']


def test_a_wrong_album_artist_does_not_take_the_track_artist_with_it():
    """The ARTIST tag and the ALBUM ARTIST tag are written from two db_data keys,
    and `write_tags_to_file` falls back: `track_artist or artist_name`. So a
    payload carrying only `artist_name` puts the ALBUM artist into the track's
    ARTIST tag as well.

    On a compilation or DJ mix whose per-track artists are correct, a finding
    that shows only "Album Artist" would have replaced every one of them.
    """
    album = {'name': 'Real Album', 'artists': [{'name': 'DJ Alpha'}],
             'year': '2021', 'genres': ['Rock', 'Indie'], 'total_tracks': 10}
    src = {'name': 'Real Title', 'track_number': 3, 'disc_number': 1,
           'artists': [{'name': 'Guest Band'}]}
    current = {'title': 'Real Title', 'artist': 'Guest Band',
               'album_artist': 'WRONG Artist', 'album': 'Real Album',
               'year': '2021', 'genre': 'Rock, Indie',
               'track_number': 3, 'disc_number': 1}

    plan = rp.plan_track(current, src, album, mode=rp.MODE_OVERWRITE)

    assert set(plan['changes']) == {'album_artist'}
    assert plan['db_data']['artist_name'] == 'DJ Alpha'
    # what the writer will actually put in ARTIST:
    written_artist = plan['db_data'].get('track_artist') or plan['db_data'].get('artist_name')
    assert written_artist == 'Guest Band'
