"""Phonk_Ben_67: a profile's plays scrobble to its own Last.fm / ListenBrainz.

Only the shared pile used to scrobble, and only to the admin's accounts. A
profile with its own pile can now scrobble that pile to accounts it connected
for scrobbling, starting from when it turned scrobbling on. Its plays never
reach the admin's accounts, and the admin's never reach its.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.listening_stats_worker import ListeningStatsWorker
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


@pytest.fixture()
def sent(monkeypatch):
    import core.lastfm_client as lfm_mod
    import core.listenbrainz_client as lb_mod
    out = []

    class _LB:
        def __init__(self, token, base_url=None):
            self.token = token

        def is_authenticated(self):
            return True

        def submit_listens(self, listens):
            out.append(('lb', self.token, sorted(l['artist'] for l in listens)))
            return True

    class _LFM:
        def __init__(self, api_key='', api_secret='', session_key=''):
            self.session = session_key

        def scrobble_tracks(self, tracks):
            out.append(('lfm', self.session, sorted(t['artist'] for t in tracks)))
            return True

    monkeypatch.setattr(lb_mod, 'ListenBrainzClient', _LB)
    monkeypatch.setattr(lfm_mod, 'LastFMClient', _LFM)
    return out


def _worker(db, admin_scrobbles=True):
    cfg = {'lastfm.api_key': 'k', 'lastfm.api_secret': 's'}
    if admin_scrobbles:
        cfg.update({'listenbrainz.scrobble_enabled': True, 'listenbrainz.token': 'admin-lb',
                    'lastfm.scrobble_enabled': True, 'lastfm.session_key': 'admin-fm'})
    config = SimpleNamespace(get=lambda key, default=None: cfg.get(key, default))
    return ListeningStatsWorker(db, config, None)


def _play(db, artist, played_at, owner):
    db.insert_listening_events([{'track_id': f't-{artist}', 'title': 'Song', 'artist': artist, 'album': 'A',
                                 'played_at': played_at, 'duration_ms': 1, 'server_source': 'plex',
                                 'profile_id': owner, 'scrobble_eligible': 1}])


def _kid(db):
    pid = db.create_profile(name='Kid')
    db.set_profile_listenbrainz(pid, 'kid-lb', '', 'kid')   # its own pile
    return pid


def test_a_profile_scrobbles_its_own_plays_to_its_own_accounts(db, sent):
    kid = _kid(db)
    db.set_profile_listenbrainz_scrobble(kid, True)
    db.set_profile_lastfm_session(kid, 'kid-fm', 'kidfm')
    _play(db, 'Admin Band', '2099-01-01 10:00:00', 1)
    _play(db, 'Katy Perry', '2099-01-01 11:00:00', kid)
    _worker(db)._scrobble_new_events()
    assert ('lb', 'admin-lb', ['Admin Band']) in sent
    assert ('lfm', 'admin-fm', ['Admin Band']) in sent
    assert ('lb', 'kid-lb', ['Katy Perry']) in sent
    assert ('lfm', 'kid-fm', ['Katy Perry']) in sent
    assert len(sent) == 4   # nothing crossed over


def test_only_plays_from_when_scrobbling_was_turned_on(db, sent):
    kid = _kid(db)
    _play(db, 'Old Play', '2020-01-01 10:00:00', kid)
    db.set_profile_lastfm_session(kid, 'kid-fm', 'kidfm')
    _play(db, 'New Play', '2099-01-01 10:00:00', kid)
    _worker(db, admin_scrobbles=False)._scrobble_new_events()
    assert sent == [('lfm', 'kid-fm', ['New Play'])]


def test_scrobbling_off_or_signed_out_sends_nothing(db, sent):
    kid = _kid(db)
    db.set_profile_lastfm_session(kid, 'kid-fm', 'kidfm')
    db.set_profile_lastfm_session(kid, None)
    _play(db, 'Katy Perry', '2099-01-01 11:00:00', kid)
    _worker(db, admin_scrobbles=False)._scrobble_new_events()
    assert sent == []


def test_listenbrainz_scrobbling_needs_the_profiles_token(db):
    plain = db.create_profile(name='Plain')
    assert db.set_profile_listenbrainz_scrobble(plain, True) is False
    kid = _kid(db)
    assert db.set_profile_listenbrainz_scrobble(kid, True) is True
    db.clear_profile_listenbrainz(kid)          # disconnecting stops it too
    assert db.get_profile_scrobbling(kid)['listenbrainz']['enabled'] is False


def test_the_session_is_stored_encrypted(db):
    kid = _kid(db)
    db.set_profile_lastfm_session(kid, 'kid-fm', 'kidfm')
    conn = db._get_connection()
    try:
        raw = conn.execute("SELECT lastfm_session FROM profiles WHERE id = ?", (kid,)).fetchone()[0]
    finally:
        conn.close()
    assert raw and raw != 'kid-fm'
    assert [p['lastfm_session'] for p in db.profiles_scrobbling()] == ['kid-fm']
