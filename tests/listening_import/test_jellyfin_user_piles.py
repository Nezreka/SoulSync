"""Phonk_Ben_67: per-profile listening stats from Jellyfin.

A profile can already be linked to its own Jellyfin user (#1265, for its
library and playlists), but the poll read plays as the app's one Jellyfin
user only, so every profile's stats were the admin's. Jellyfin answers
history per user, so the poll now reads each linked profile's user too and
files those plays in that profile's pile, the way Plex accounts already work:

- the app's own user is the shared pile, as before
- a profile linked to the same user as the app shares it (no empty pile)
- with Jellyfin not the server being read, a Jellyfin link owns nothing
- a user read for the first time is read wider, so the pile starts filled
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.listening_scope import SHARED_OWNER, listening_owner, listening_owners
from core.listening_stats_worker import ListeningStatsWorker
from database.music_database import MusicDatabase

APP_USER, KID_USER = 'app-user-guid', 'kid-user-guid'


def _play(title, artist, played_at):
    return {'track_title': title, 'artist': artist, 'album': 'A', 'played_at': played_at,
            'duration_ms': 200000, 'track_id': f'jf-{title}'}


class _Jellyfin:
    def __init__(self, by_user):
        self.user_id = APP_USER
        self.by_user = by_user
        self.limits = []

    def _history(self, user, limit):
        self.limits.append((user, limit))
        return list(self.by_user.get(user, []))

    def get_play_history(self, limit=500):
        return self._history(APP_USER, limit)

    def as_user(self, user):
        return SimpleNamespace(get_play_history=lambda limit=500: self._history(user, limit))

    def get_track_play_counts(self):
        return {}


class _Config:
    def __init__(self, server='jellyfin'):
        self.server = server

    def get(self, key, default=None):
        return default

    def get_active_media_server(self):
        return self.server


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


@pytest.fixture(autouse=True)
def _no_curation(monkeypatch):
    import core.library.curation_sync as cs
    monkeypatch.setattr(cs, 'curation_sweep_due', lambda *a, **k: False)


def _worker(db, client, server='jellyfin'):
    w = ListeningStatsWorker(db, _Config(server), SimpleNamespace(client=lambda name: client))
    w._build_stats_cache = lambda owners=None: None
    w._scrobble_new_events = lambda: None
    return w


def _pile(db, owner):
    conn = db._get_connection()
    try:
        return sorted(r[0] for r in conn.execute(
            "SELECT artist FROM listening_history WHERE profile_id = ?", (owner,)).fetchall())
    finally:
        conn.close()


HISTORY = {
    APP_USER: [_play('Sicko Mode', 'Travis Scott', '2026-10-31T20:00:00Z')],
    KID_USER: [_play('Hot N Cold', 'Katy Perry', '2026-10-31T17:00:00Z'),
               _play('7 rings', 'Ariana Grande', '2026-10-31T17:05:00Z')],
}


def _kid(db, user=KID_USER):
    pid = db.create_profile(name='Kid')
    db.set_profile_server_library(pid, 'jellyfin', 'lib', user_id=user)
    return pid


def test_each_profiles_jellyfin_plays_land_in_its_own_pile(db):
    kid = _kid(db)
    _worker(db, _Jellyfin(HISTORY))._poll()
    assert _pile(db, SHARED_OWNER) == ['Travis Scott']
    assert _pile(db, kid) == ['Ariana Grande', 'Katy Perry']
    assert listening_owner(db, kid) == kid and kid in listening_owners(db)
    kid_top = [a['name'] for a in db.get_top_artists('all', limit=10, profile_id=kid)]
    assert 'Travis Scott' not in kid_top and 'Katy Perry' in kid_top


def test_a_new_user_is_read_wide_once(db):
    _kid(db)
    client = _Jellyfin(HISTORY)
    _worker(db, client)._poll()
    _worker(db, client)._poll()
    kid_limits = [limit for user, limit in client.limits if user == KID_USER]
    assert kid_limits == [5000, 500]


def test_a_profile_on_the_apps_own_user_keeps_the_shared_pile(db):
    same = _kid(db, user=APP_USER)
    _worker(db, _Jellyfin(HISTORY))._poll()
    assert listening_owner(db, same) == SHARED_OWNER
    assert _pile(db, SHARED_OWNER) == ['Travis Scott']


def test_a_jellyfin_link_owns_nothing_while_another_server_is_read(db):
    kid = _kid(db)
    _worker(db, _Jellyfin(HISTORY))._poll()
    assert listening_owner(db, kid) == kid
    plex = SimpleNamespace(get_play_history=lambda limit=500: [], get_track_play_counts=lambda: {})
    _worker(db, plex, server='plex')._poll()
    assert listening_owner(db, kid) == SHARED_OWNER
