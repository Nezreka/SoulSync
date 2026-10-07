"""Plex plays go in the pile of whoever played them.

Boulder's kids listen to Katy Perry on the Kids Plex Home user. Every Plex play
landed in the admin's shared pile, so their plays filled his stats, his mixes,
and were scrobbled to his Last.fm. Plex's history names the account behind
each play (1 = the server owner, anyone else = their plex.tv user id, checked
against a live server), so:

- the owner's plays are the shared pile, as before
- a profile linked to a Plex account (sign-in record or home-user link) owns
  a pile, and that account's plays are its own
- an account linked to nobody: kept, claimed by no one, never the admin's
- linking or unlinking later re-files the plays
- rows from before accounts were kept get tagged once from Plex's history

real MusicDatabase on a tmp file throughout.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.listening_scope import (
    SHARED_OWNER,
    UNCLAIMED,
    listening_owner,
    listening_owners,
    media_account_owner,
    media_account_owners,
    reattribute_media_plays,
)
from core.listening_stats_worker import ListeningStatsWorker
from database.music_database import MusicDatabase

KIDS_PLEX = '10995858'
FRIEND_PLEX = '11165868'


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _kids(db):
    pid = db.create_profile(name='Kids')
    db.set_profile_plex_home_user(pid, KIDS_PLEX, 'Kids', 'kids-server-token')
    return pid


def _play(title, artist, played_at, account, track_id=None):
    return {
        'track_title': title, 'artist': artist, 'album': 'Album', 'played_at': played_at,
        'duration_ms': 200000, 'track_id': track_id or f'rk-{title}', 'account_id': account,
    }


HISTORY = [
    _play('This Dream of You', 'Martin Garrix', '2026-10-06T20:00:00', '1'),
    _play('Be The One', 'Bree Runway', '2026-10-06T20:05:00', '1'),
    _play('Hot N Cold', 'Katy Perry', '2026-10-06T17:00:00', KIDS_PLEX),
    _play('Swish Swish', 'Katy Perry', '2026-10-06T17:04:00', KIDS_PLEX),
    _play('7 rings', 'Ariana Grande', '2026-10-06T17:08:00', KIDS_PLEX),
    _play('Some Song', 'Friend Band', '2026-10-06T12:00:00', FRIEND_PLEX),
]


class _Plex:
    def __init__(self, history):
        self.history = history
        self.full_calls = 0

    def get_play_history(self, limit=500):
        if limit is None:
            self.full_calls += 1
        return list(self.history)

    def get_track_play_counts(self):
        return {}


class _Config:
    def get(self, key, default=None):
        return default

    def get_active_media_server(self):
        return 'plex'


def _worker(db, plex):
    w = ListeningStatsWorker(db, _Config(), SimpleNamespace(client=lambda name: plex))
    w._build_stats_cache = lambda owners=None: None  # stats read straight from the db below
    w._scrobble_new_events = lambda: None
    return w


@pytest.fixture(autouse=True)
def _no_curation(monkeypatch):
    import core.library.curation_sync as cs
    monkeypatch.setattr(cs, 'curation_sweep_due', lambda *a, **k: False)


def _pile(db, owner):
    conn = db._get_connection()
    try:
        return sorted(r[0] for r in conn.execute(
            "SELECT artist FROM listening_history WHERE profile_id = ?", (owner,)).fetchall())
    finally:
        conn.close()


# -- who owns a pile, who a play belongs to --

def test_a_profile_linked_to_a_plex_account_owns_its_pile(db):
    kids = _kids(db)
    plain = db.create_profile(name='Plain')
    assert listening_owner(db, kids) == kids
    assert listening_owner(db, plain) == SHARED_OWNER
    assert kids in listening_owners(db)


def test_plex_accounts_map_to_piles(db):
    kids = _kids(db)
    owners = media_account_owners(db, 'plex')
    assert media_account_owner(owners, '1') == SHARED_OWNER       # the server owner
    assert media_account_owner(owners, KIDS_PLEX) == kids
    assert media_account_owner(owners, FRIEND_PLEX) == UNCLAIMED  # linked to nobody
    assert media_account_owner(owners, None) == SHARED_OWNER      # a server that doesn't say
    assert media_account_owners(db, 'navidrome') == {}


def test_plex_sign_in_record_wins_over_a_home_user_link(db):
    linked = db.create_profile(name='Linked')
    db.set_profile_plex_home_user(linked, KIDS_PLEX, 'Kids', 'tok')
    signed = db.create_profile(name='Signed')
    db.set_profile_plex_account(signed, KIDS_PLEX)
    assert media_account_owners(db, 'plex')[KIDS_PLEX] == signed


# -- the poll --

def test_the_poll_files_each_play_in_its_players_pile(db):
    kids = _kids(db)
    _worker(db, _Plex(HISTORY))._poll()
    assert _pile(db, SHARED_OWNER) == ['Bree Runway', 'Martin Garrix']
    assert _pile(db, kids) == ['Ariana Grande', 'Katy Perry', 'Katy Perry']
    assert _pile(db, UNCLAIMED) == ['Friend Band']


def test_the_admins_stats_no_longer_count_the_kids(db):
    kids = _kids(db)
    _worker(db, _Plex(HISTORY))._poll()
    admin_top = [a['name'] for a in db.get_top_artists('all', limit=10, profile_id=SHARED_OWNER)]
    kids_top = [a['name'] for a in db.get_top_artists('all', limit=10, profile_id=kids)]
    assert 'Katy Perry' not in admin_top and 'Martin Garrix' in admin_top
    assert kids_top[0] == 'Katy Perry'


def test_an_unclaimed_play_never_falls_back_to_the_admin(db):
    db.insert_listening_events([{
        'track_id': 'rk-x', 'title': 'X', 'artist': 'Y', 'album': '', 'played_at': '2026-10-06T10:00:00',
        'duration_ms': 1, 'server_source': 'plex', 'profile_id': UNCLAIMED, 'server_account_id': FRIEND_PLEX,
    }])
    assert _pile(db, SHARED_OWNER) == []
    assert _pile(db, UNCLAIMED) == ['Y']


# -- re-filing when links change --

def test_linking_an_account_later_claims_its_plays_and_unlinking_gives_them_back(db):
    _worker(db, _Plex(HISTORY))._poll()            # nobody linked to the friend (or kids) yet
    assert 'Friend Band' in _pile(db, UNCLAIMED)
    friend = db.create_profile(name='Friend')
    db.set_profile_plex_home_user(friend, FRIEND_PLEX, 'Max n Rose', 'tok')
    reattribute_media_plays(db, 'plex')
    assert _pile(db, friend) == ['Friend Band'] and 'Friend Band' not in _pile(db, UNCLAIMED)
    db.set_profile_plex_home_user(friend, None, None, None)
    reattribute_media_plays(db, 'plex')
    assert 'Friend Band' in _pile(db, UNCLAIMED) and _pile(db, friend) == []


def test_refiling_onto_a_play_already_in_the_target_pile_leaves_one_copy(db):
    kids = _kids(db)
    for owner in (SHARED_OWNER, kids):
        db.insert_listening_events([{
            'track_id': 'rk-dup', 'title': 'Dup', 'artist': 'Katy Perry', 'album': '',
            'played_at': '2026-10-06T09:00:00', 'duration_ms': 1, 'server_source': 'plex',
            'profile_id': owner, 'server_account_id': KIDS_PLEX,
        }])
    reattribute_media_plays(db, 'plex')
    assert _pile(db, SHARED_OWNER) == []
    assert _pile(db, kids) == ['Katy Perry']


# -- rows from before accounts were kept --

def test_old_plays_are_tagged_once_and_moved_out_of_the_admins_pile(db):
    kids = _kids(db)
    # recorded the old way: no account, everything in the shared pile
    db.insert_listening_events([{
        'track_id': p['track_id'], 'title': p['track_title'], 'artist': p['artist'], 'album': 'Album',
        'played_at': p['played_at'], 'duration_ms': 1, 'server_source': 'plex', 'profile_id': SHARED_OWNER,
    } for p in HISTORY])
    assert len(_pile(db, SHARED_OWNER)) == 6

    plex = _Plex(HISTORY)
    _worker(db, plex)._poll()
    assert _pile(db, SHARED_OWNER) == ['Bree Runway', 'Martin Garrix']
    assert _pile(db, kids) == ['Ariana Grande', 'Katy Perry', 'Katy Perry']
    assert _pile(db, UNCLAIMED) == ['Friend Band']
    assert plex.full_calls == 1

    _worker(db, plex)._poll()
    assert plex.full_calls == 1  # never twice


def test_imported_plays_without_an_account_stay_where_they_are(db):
    """last.fm / listenbrainz imports carry no account; only plex rows move"""
    _kids(db)
    db.insert_listening_events([{
        'track_id': 'lfm-1', 'title': 'Roar', 'artist': 'Katy Perry', 'album': '',
        'played_at': '2026-10-01T10:00:00', 'duration_ms': 1, 'server_source': 'lastfm', 'profile_id': SHARED_OWNER,
    }])
    _worker(db, _Plex(HISTORY))._poll()
    assert 'Katy Perry' in _pile(db, SHARED_OWNER)


def test_a_kids_play_is_never_matched_onto_the_admins_lastfm_row(db):
    """plays are de-duplicated at insert, inside one pile. filed into the
    admin's pile first, a kids play next to an admin last.fm scrobble of the
    same song would be taken as that scrobble and never become its own row,
    so there'd be nothing to move. filing by account at insert is what
    prevents it, not the later re-file"""
    kids = _kids(db)
    db.insert_listening_events([{
        'track_id': 'lfm-roar', 'title': 'Hot N Cold', 'artist': 'Katy Perry', 'album': 'Album',
        'played_at': '2026-10-06T17:00:05', 'duration_ms': 1, 'server_source': 'lastfm', 'profile_id': SHARED_OWNER,
    }])
    _worker(db, _Plex([_play('Hot N Cold', 'Katy Perry', '2026-10-06T17:00:00', KIDS_PLEX)]))._poll()
    assert _pile(db, kids) == ['Katy Perry']
