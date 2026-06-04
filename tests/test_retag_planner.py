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
