"""share listening and the private session, on a real MusicDatabase.

sharing is off for everyone until they say yes, a pile only shares when every
profile playing into it said yes, and a private session runs out by itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.personalized import listening_privacy as P
from database.music_database import MusicDatabase

NOW = datetime(2026, 11, 20, 12, 0)


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'm.db'))


def _person(db, pid, name, own=False):
    with db._get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO profiles (id, name, listenbrainz_token) VALUES (?, ?, ?)",
                     (pid, name, 'tok' if own else None))
        conn.commit()


def test_everyone_starts_not_sharing_and_unasked(db):
    _person(db, 2, 'Kim', own=True)
    for pid in (1, 2):
        got = P.get_privacy(db, pid, NOW)
        assert got == {'share_listening': False, 'asked': False,
                       'private_session': False, 'private_until': None}
    assert P.sharing_piles(db) == set()


def test_the_columns_default_off_on_an_existing_profile(tmp_path):
    # an install from before this: the profile rows exist, the columns don't
    import sqlite3
    path = str(tmp_path / 'old.db')
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE profiles (id INTEGER PRIMARY KEY, name TEXT, listenbrainz_token TEXT, "
                 "lastfm_username TEXT)")
    conn.execute("INSERT INTO profiles (id, name) VALUES (1, 'Admin'), (2, 'Kim')")
    P.ensure_schema(conn.cursor())
    P.ensure_schema(conn.cursor())   # twice is fine
    rows = conn.execute("SELECT share_listening, share_listening_asked, private_session_until "
                        "FROM profiles").fetchall()
    assert rows == [(0, 0, None), (0, 0, None)]


def test_saying_yes_or_no_both_stop_the_asking(db):
    _person(db, 2, 'Kim', own=True)
    assert P.set_share_listening(db, 2, True)
    assert P.get_privacy(db, 2)['share_listening'] is True
    assert P.get_privacy(db, 2)['asked'] is True
    P.set_share_listening(db, 2, False)
    assert P.get_privacy(db, 2)['share_listening'] is False
    _person(db, 3, 'Bo', own=True)
    P.mark_asked(db, 3)
    assert P.get_privacy(db, 3) == {'share_listening': False, 'asked': True,
                                    'private_session': False, 'private_until': None}


def test_a_pile_shares_only_when_everyone_on_it_does(db):
    _person(db, 2, 'Kim', own=True)
    _person(db, 3, 'Kid')            # no account: plays into the shared pile
    P.set_share_listening(db, 1, True)
    P.set_share_listening(db, 2, True)
    assert P.sharing_piles(db) == {2}
    P.set_share_listening(db, 3, True)
    assert P.sharing_piles(db) == {1, 2}
    assert P.global_shelf_piles(db) == [1, 2]


def test_global_shelves_keep_the_shared_pile_and_leave_private_piles_out(db):
    _person(db, 2, 'Kim', own=True)
    assert P.global_shelf_piles(db) == [1]


def test_a_private_session_runs_out_by_itself(db):
    until = P.set_private_session(db, 1, True, now=NOW)
    assert until == (NOW + timedelta(hours=P.PRIVATE_SESSION_HOURS)).strftime('%Y-%m-%d %H:%M:%S')
    assert P.in_private_session(db, 1, now=NOW)
    assert P.get_privacy(db, 1, NOW)['private_until'] == until
    later = NOW + timedelta(hours=P.PRIVATE_SESSION_HOURS, minutes=1)
    assert not P.in_private_session(db, 1, now=later)
    P.set_private_session(db, 1, True, now=NOW)
    assert P.set_private_session(db, 1, False, now=NOW) is None
    assert not P.in_private_session(db, 1, now=NOW)


def test_a_private_session_is_one_profiles(db):
    _person(db, 2, 'Kim', own=True)
    P.set_private_session(db, 2, True)
    assert P.in_private_session(db, 2)
    assert not P.in_private_session(db, 1)
