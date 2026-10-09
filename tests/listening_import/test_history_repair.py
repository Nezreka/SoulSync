"""one clock, no echoes: the listening history repair.

plex plays and web-player plays were stored in the server's local time with a
T; last.fm and listenbrainz imports in utc. so each scrobble soulsync sent
came back on the next import as a second play, 7 hours off for a pacific
server, and never matched. boulder had 9,499 of 9,787 plex plays doubled, and
his kids' plays echoed back into his own pile through his last.fm.

real MusicDatabase on a tmp file, the server's zone pinned to pacific.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from core.listening_import.dedup import canonical_played_at, insert_import_events
from core.listening_import.history_repair import DONE_KEY, repair_once
from core.listening_scope import SHARED_OWNER
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path, server_tz):
    server_tz('America/Los_Angeles')  # utc-7 in october
    return MusicDatabase(str(tmp_path / "music.db"))


def _raw(db, title, artist, played_at, source, *, owner=SHARED_OWNER, track_id=None):
    """a row exactly as an older soulsync wrote it"""
    conn = db._get_connection()
    cur = conn.execute(
        "INSERT INTO listening_history (track_id, title, artist, album, played_at, duration_ms, "
        "server_source, profile_id) VALUES (?, ?, ?, 'Album', ?, 1, ?, ?)",
        (track_id or f'{source}-{title}-{played_at}', title, artist, played_at, source, owner))
    conn.commit()
    conn.close()
    return cur.lastrowid


def _lastfm(db, title, artist, played_at, source='lastfm', owner=SHARED_OWNER):
    """an import, the way the importer brings one in (utc)"""
    return insert_import_events(db, [{'title': title, 'artist': artist, 'album': 'Album',
                                      'played_at': played_at, 'track_id': f'{source}-{title}'}],
                                source, profile_id=owner)


def _rows(db):
    conn = db._get_connection()
    try:
        return conn.execute("SELECT id, title, played_at, server_source, profile_id, scrobbled_lastfm "
                            "FROM listening_history ORDER BY id").fetchall()
    finally:
        conn.close()


def _aliases(db, history_id):
    conn = db._get_connection()
    try:
        return sorted(r[0] for r in conn.execute(
            "SELECT source FROM listening_import_events WHERE history_id = ?", (history_id,)).fetchall())
    finally:
        conn.close()


# -- the clock --

def test_times_with_and_without_a_zone():
    assert canonical_played_at('2026-10-06T17:00:00.1234567Z') == '2026-10-06 17:00:00'   # jellyfin
    assert canonical_played_at('2026-10-06T17:00:00+02:00') == '2026-10-06 15:00:00'
    assert canonical_played_at('2026-10-06 17:00:00') == '2026-10-06 17:00:00'           # already utc
    assert canonical_played_at('nonsense') is None


def test_a_bare_local_time_moves_by_the_zone_of_that_date(server_tz):
    server_tz('America/Los_Angeles')
    assert canonical_played_at('2026-10-06T10:00:00', naive_is_local=True) == '2026-10-06 17:00:00'  # pdt
    assert canonical_played_at('2026-12-06T10:00:00', naive_is_local=True) == '2026-12-06 18:00:00'  # pst


# -- the repair --

def test_a_plex_play_and_its_lastfm_echo_become_one_play(db):
    play = _raw(db, 'Hot N Cold', 'Katy Perry', '2026-10-06T10:00:00', 'plex')   # 10am pacific
    _lastfm(db, 'Hot N Cold', 'Katy Perry', '2026-10-06 17:00:00')                # same play, utc
    assert len(_rows(db)) == 2  # the double count

    counts = repair_once(db)
    rows = _rows(db)
    assert [(r[0], r[2], r[3]) for r in rows] == [(play, '2026-10-06 17:00:00', 'plex')]
    assert rows[0][5] == 1                       # known scrobbled to last.fm, never re-sent
    assert _aliases(db, play) == ['lastfm']      # the import's link moved to the play
    assert counts['merged'] == 1 and counts['converted'] == 1

    # the next last.fm import knows that scrobble and adds nothing
    assert _lastfm(db, 'Hot N Cold', 'Katy Perry', '2026-10-06 17:00:00') == 0
    assert len(_rows(db)) == 1


def test_a_kids_play_echoed_into_the_admins_lastfm_leaves_the_admins_pile(db):
    kids = db.create_profile(name='Kids')
    play = _raw(db, '7 rings', 'Ariana Grande', '2026-10-06T10:00:00', 'plex', owner=kids)
    _lastfm(db, '7 rings', 'Ariana Grande', '2026-10-06 17:00:03')  # admin's last.fm

    repair_once(db)
    assert [(r[0], r[4]) for r in _rows(db)] == [(play, kids)]


def test_both_services_fold_into_the_one_play(db):
    play = _raw(db, 'Roar', 'Katy Perry', '2026-10-06T10:00:00', 'plex')
    _lastfm(db, 'Roar', 'Katy Perry', '2026-10-06 17:00:00', source='lastfm')
    _lastfm(db, 'Roar', 'Katy Perry', '2026-10-06 17:00:01', source='listenbrainz')
    repair_once(db)
    assert [r[0] for r in _rows(db)] == [play]
    assert _aliases(db, play) == ['lastfm', 'listenbrainz']


def test_nothing_else_is_merged(db):
    _raw(db, 'Roar', 'Katy Perry', '2026-10-06T10:00:00', 'plex')
    _lastfm(db, 'Firework', 'Katy Perry', '2026-10-06 17:00:00')        # another song
    _lastfm(db, 'Roar', 'Katy Perry', '2026-10-06 17:00:30')            # outside the tolerance
    _lastfm(db, 'Roar', 'Katy Perry', '2026-10-06 10:00:00')            # the old 7h-off clock
    repair_once(db)
    assert len(_rows(db)) == 4


def test_two_plays_of_a_song_each_keep_one_echo(db):
    """a repeat, played twice back to back: two plays, two scrobbles, never
    one play swallowing both"""
    a = _raw(db, 'Roar', 'Katy Perry', '2026-10-06T10:00:00', 'plex', track_id='rk-roar')
    b = _raw(db, 'Roar', 'Katy Perry', '2026-10-06T10:00:08', 'plex', track_id='rk-roar')
    _lastfm(db, 'Roar', 'Katy Perry', '2026-10-06 17:00:00')
    _lastfm(db, 'Roar', 'Katy Perry', '2026-10-06 17:00:08')
    repair_once(db)
    assert sorted(r[0] for r in _rows(db)) == sorted([a, b])
    assert _aliases(db, a) == ['lastfm'] and _aliases(db, b) == ['lastfm']


def test_two_echoes_nearest_one_play_still_split_across_both_plays(db):
    """both scrobbles sit closer to the first play (3s and 4s away, the
    second play is 8s in). taking the nearest each time would hand the first
    play two copies and leave the second's copy behind as a double"""
    a = _raw(db, 'Roar', 'Katy Perry', '2026-10-06T10:00:00', 'plex', track_id='rk-roar')
    b = _raw(db, 'Roar', 'Katy Perry', '2026-10-06T10:00:08', 'plex', track_id='rk-roar')
    _lastfm(db, 'Roar', 'Katy Perry', '2026-10-06 17:00:03')
    _lastfm(db, 'Roar', 'Katy Perry', '2026-10-06 17:00:04')
    repair_once(db)
    assert sorted(r[0] for r in _rows(db)) == sorted([a, b])
    assert _aliases(db, a) == ['lastfm'] and _aliases(db, b) == ['lastfm']


def test_web_player_plays_move_to_utc_and_server_times_with_a_zone_dont_shift(db):
    web = _raw(db, 'A', 'X', '2026-10-06T10:00:00.123456', 'soulsync_web')
    jf = _raw(db, 'B', 'Y', '2026-10-06T17:00:00.0000000Z', 'jellyfin')
    old_utc = _raw(db, 'C', 'Z', '2026-10-06T17:00:00', 'lastfm')     # a T but utc by the rule
    repair_once(db)
    times = {r[0]: r[2] for r in _rows(db)}
    assert times == {web: '2026-10-06 17:00:00', jf: '2026-10-06 17:00:00', old_utc: '2026-10-06 17:00:00'}


def test_a_local_row_landing_on_its_own_utc_twin_folds_into_it(db):
    """the same plex play stored twice, once per clock (a poll after the fix
    but before the repair). one stays, with the import links of both"""
    twin = _raw(db, 'Roar', 'Katy Perry', '2026-10-06 17:00:00', 'plex', track_id='rk-roar')
    _raw(db, 'Roar', 'Katy Perry', '2026-10-06T10:00:00', 'plex', track_id='rk-roar')
    counts = repair_once(db)
    assert [r[0] for r in _rows(db)] == [twin]
    assert counts['dropped'] == 1


def test_an_echo_whose_link_cant_move_stays(db):
    """the play already holds a last.fm link. dropping a second copy along
    with its link would let the next import bring it straight back"""
    play = _raw(db, 'Roar', 'Katy Perry', '2026-10-06T10:00:00', 'plex')
    conn = db._get_connection()
    conn.execute("INSERT INTO listening_import_events (source, title, artist, listened_at, history_id, profile_id) "
                 "VALUES ('lastfm', 'roar', 'katy perry', 1, ?, 1)", (play,))
    conn.commit()
    conn.close()
    _lastfm(db, 'Roar', 'Katy Perry', '2026-10-06 17:00:00')
    repair_once(db)
    assert len(_rows(db)) == 2


def test_it_runs_once_and_backs_up_first(db, tmp_path):
    _raw(db, 'Roar', 'Katy Perry', '2026-10-06T10:00:00', 'plex')
    _lastfm(db, 'Roar', 'Katy Perry', '2026-10-06 17:00:00')
    assert repair_once(db) is not None
    assert db.get_metadata(DONE_KEY)
    backups = [f for f in os.listdir(tmp_path) if f.startswith('listening_backup_')]
    assert len(backups) == 1
    bak = sqlite3.connect(str(tmp_path / backups[0]))
    assert bak.execute("SELECT COUNT(*) FROM listening_history").fetchone()[0] == 2   # before the repair
    bak.close()

    _raw(db, 'Later', 'Band', '2026-10-07T10:00:00', 'plex')
    assert repair_once(db) is None
    assert any(r[2] == '2026-10-07T10:00:00' for r in _rows(db))  # untouched: it doesn't run again


def test_a_failed_repair_rolls_back_and_is_retried(db, monkeypatch):
    import core.listening_import.history_repair as hr
    _raw(db, 'Roar', 'Katy Perry', '2026-10-06T10:00:00', 'plex')

    def boom(conn):
        raise RuntimeError('disk')
    monkeypatch.setattr(hr, 'merge_echoes', boom)
    with pytest.raises(RuntimeError):
        repair_once(db)
    assert _rows(db)[0][2] == '2026-10-06T10:00:00'   # the time move rolled back too
    assert not db.get_metadata(DONE_KEY)


# -- from now on --

def test_new_plays_and_their_echoes_match_without_any_repair(db):
    """the writers store utc now, so a scrobble coming back lines up"""
    played = datetime(2026, 10, 6, 10, 0).astimezone()  # 10am pacific, as plexapi hands it over
    db.insert_listening_events([{
        'track_id': 'rk-1', 'title': 'Roar', 'artist': 'Katy Perry', 'album': 'Album',
        'played_at': played.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
        'duration_ms': 1, 'server_source': 'plex',
    }])
    _lastfm(db, 'Roar', 'Katy Perry', '2026-10-06 17:00:02')
    assert len(_rows(db)) == 1


def test_a_server_time_in_any_shape_is_stored_on_the_one_clock(db):
    db.insert_listening_events([{
        'track_id': 'jf-1', 'title': 'Roar', 'artist': 'Katy Perry', 'album': 'Album',
        'played_at': '2026-10-06T17:00:00.0000000Z', 'duration_ms': 1, 'server_source': 'jellyfin',
    }])
    assert _rows(db)[0][2] == '2026-10-06 17:00:00'
    _lastfm(db, 'Roar', 'Katy Perry', '2026-10-06 17:00:04')
    assert len(_rows(db)) == 1


def test_the_plex_client_hands_over_utc(server_tz):
    server_tz('America/Los_Angeles')
    from core.plex_client import PlexClient
    client = PlexClient.__new__(PlexClient)
    item = SimpleNamespace(type='track', title='Roar', grandparentTitle='Katy Perry', parentTitle='Album',
                           viewedAt=datetime(2026, 10, 6, 10, 0), duration=1, ratingKey=5, accountID=1)
    client.ensure_connection = lambda: True
    client.server = SimpleNamespace(history=lambda **k: [item])
    client.music_library = None
    out = client.get_play_history(limit=1)
    assert out[0]['played_at'] == '2026-10-06 17:00:00'  # plexapi's bare 10am pacific


def test_scrobbles_are_sent_as_the_utc_instant():
    from core.listening_stats_worker import _play_epoch
    assert _play_epoch('2026-10-06 17:00:00') == int(datetime(2026, 10, 6, 17, tzinfo=timezone.utc).timestamp())
    assert _play_epoch('junk') is None


def test_the_worker_scrobbles_the_utc_instant(db, monkeypatch):
    """the scrobble clients read a bare time as local. handed a stored utc
    time as text, a pacific server would scrobble every play 7 hours early"""
    import core.lastfm_client as lfm_mod
    import core.listenbrainz_client as lb_mod
    from core.listening_stats_worker import ListeningStatsWorker

    sent = {}

    class _LB:
        def __init__(self, token):
            pass

        def is_authenticated(self):
            return True

        def submit_listens(self, listens):
            sent['lb'] = [l['timestamp'] for l in listens]
            return True

    class _LFM:
        def __init__(self, **k):
            pass

        def scrobble_tracks(self, tracks):
            sent['lfm'] = [t['timestamp'] for t in tracks]
            return True

    monkeypatch.setattr(lb_mod, 'ListenBrainzClient', _LB)
    monkeypatch.setattr(lfm_mod, 'LastFMClient', _LFM)
    cfg = {'listenbrainz.scrobble_enabled': True, 'listenbrainz.token': 't',
           'lastfm.scrobble_enabled': True, 'lastfm.api_key': 'k', 'lastfm.api_secret': 's',
           'lastfm.session_key': 'sk'}
    config = SimpleNamespace(get=lambda key, default=None: cfg.get(key, default))
    db.insert_listening_events([{'track_id': 'rk-1', 'title': 'Roar', 'artist': 'Katy Perry', 'album': 'A',
                                 'played_at': '2026-10-06 17:00:00', 'duration_ms': 1, 'server_source': 'plex'}])
    ListeningStatsWorker(db, config, None)._scrobble_new_events()
    instant = int(datetime(2026, 10, 6, 17, tzinfo=timezone.utc).timestamp())
    assert sent == {'lb': [instant], 'lfm': [instant]}
