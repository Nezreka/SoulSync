"""Scrobbling decoupled from listening-stats polling + Last.fm-rule eligibility.

Covers cin's two findings:

1. ``listening_stats.enabled`` must NOT gate outbound scrobbling. The scrobble
   flush runs on its own cadence in ``ListeningStatsWorker._run()``, gated only
   by each service's ``scrobble_enabled`` + credentials.
2. A 10s web-player log is a "play" for SoulSync's own stats, not a scrobble.
   Rows carry ``scrobble_eligible``; only eligible rows are scrobbled.
   Media-server and import rows are eligible on insert (the source attests the
   play); web-player rows become eligible only once the player reports the
   Last.fm threshold was reached.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from core.lastfm_client import LastFMClient as _RealLastFMClient
from core.listenbrainz_client import ListenBrainzClient as _RealListenBrainzClient
from core.listening_scope import SHARED_OWNER
from core.listening_stats_worker import ListeningStatsWorker
from core.playback.play_log import build_play_event
from database.music_database import MusicDatabase


class _FakeConfig:
    def __init__(self, values):
        self._values = dict(values)

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_active_media_server(self):
        return 'navidrome'


class _FakeClient:
    """Media-server client stub for _poll()."""

    def __init__(self, history):
        self._history = history

    def get_play_history(self, limit=500):
        return self._history

    def get_track_play_counts(self):
        return {}


class _FakeEngine:
    def __init__(self, client):
        self._client = client

    def client(self, name):
        return self._client


class _FakeLastFMClient:
    instances = []

    def __init__(self, api_key='', api_secret='', session_key=''):
        self.tracks = []
        _FakeLastFMClient.instances.append(self)

    def scrobble_tracks(self, tracks):
        self.tracks.extend(tracks)
        return True


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


@pytest.fixture
def scrobble_config():
    return _FakeConfig({
        'listening_stats.enabled': True,
        'lastfm.scrobble_enabled': True,
        'lastfm.api_key': 'k',
        'lastfm.api_secret': 's',
        'lastfm.session_key': 'sess',
        'listenbrainz.scrobble_enabled': False,
    })


@pytest.fixture(autouse=True)
def _clean_fakes(monkeypatch):
    _FakeLastFMClient.instances.clear()
    monkeypatch.setattr('core.lastfm_client.LastFMClient', _FakeLastFMClient)
    # The worker does a local import, so patching the module attribute works.
    import core.listening_stats_worker as worker_mod
    monkeypatch.setattr(worker_mod, 'interruptible_sleep',
                        lambda stop_event, seconds, step=0.5: stop_event.is_set())
    yield


def _insert_row(db, title, source, eligible, played_at='2026-10-11 12:00:00'):
    conn = db._get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO listening_history
                (track_id, title, artist, album, played_at, duration_ms,
                 server_source, db_track_id, profile_id, scrobble_eligible)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ('tid-' + title, title, 'artist', 'album', played_at, 180000,
              source, None, SHARED_OWNER, eligible))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _scrobbled_lastfm(db, row_id):
    conn = db._get_connection()
    try:
        row = conn.execute(
            "SELECT scrobbled_lastfm FROM listening_history WHERE id = ?",
            (row_id,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_only_eligible_rows_are_scrobbled(db, scrobble_config):
    """_scrobble_new_events skips rows that never reached the Last.fm threshold."""
    eligible_id = _insert_row(db, 'eligible song', 'soulsync_web', 1)
    ineligible_id = _insert_row(db, 'skipped song', 'soulsync_web', 0)

    worker = ListeningStatsWorker(db, scrobble_config)
    worker._scrobble_new_events()

    assert _scrobbled_lastfm(db, eligible_id) == 1
    assert _scrobbled_lastfm(db, ineligible_id) == 0
    assert len(_FakeLastFMClient.instances) == 1
    sent = _FakeLastFMClient.instances[0].tracks
    assert [t['track'] for t in sent] == ['eligible song']


def test_scrobble_flush_runs_with_stats_disabled(db):
    """The stats toggle gates media-server polling, not outbound scrobbling."""
    config = _FakeConfig({
        'listening_stats.enabled': False,
        'lastfm.scrobble_enabled': True,
        'lastfm.api_key': 'k',
        'lastfm.api_secret': 's',
        'lastfm.session_key': 'sess',
        'listenbrainz.scrobble_enabled': False,
    })
    row_id = _insert_row(db, 'web play', 'soulsync_web', 1)

    worker = ListeningStatsWorker(db, config)
    worker._build_stats_cache = lambda *a, **k: None
    thread = threading.Thread(target=worker._run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while time.time() < deadline and _scrobbled_lastfm(db, row_id) != 1:
            time.sleep(0.05)
        assert _scrobbled_lastfm(db, row_id) == 1, \
            "scrobble flush did not run with listening_stats.enabled=False"
    finally:
        worker.should_stop = True
        worker._stop_event.set()
        thread.join(timeout=5)


def test_poll_inserts_server_rows_as_eligible_and_does_not_scrobble(db, scrobble_config):
    """_poll() no longer contains the scrobble step; its rows are eligible."""
    history = [{
        'track_id': 'srv-1',
        'track_title': 'server song',
        'artist': 'artist',
        'album': 'album',
        'played_at': '2026-10-11 11:00:00',
        'duration_ms': 200000,
        'account_id': None,
    }]
    worker = ListeningStatsWorker(db, scrobble_config, _FakeEngine(_FakeClient(history)))
    worker._build_stats_cache = lambda *a, **k: None
    worker._tag_old_plays_once = lambda *a, **k: None

    seed_id = _insert_row(db, 'already here', 'soulsync_web', 1)
    worker._poll()

    # the scrobble step is gone from _poll: nothing was scrobbled
    assert _FakeLastFMClient.instances == []
    assert _scrobbled_lastfm(db, seed_id) == 0

    # ...but the freshly polled server row is eligible for the next flush
    conn = db._get_connection()
    try:
        row = conn.execute(
            "SELECT scrobble_eligible FROM listening_history WHERE title = 'server song'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row[0] == 1


def test_web_player_play_ineligible_until_marked(db):
    """record_web_player_play returns the row id; the row starts ineligible.

    played_at uses the production format (isoformat with microseconds) to
    prove the row-id lookup survives insert_import_events' canonicalization.
    """
    from datetime import datetime
    event = build_play_event(
        {'id': 7, 'title': 'web song', 'artist': 'artist', 'album': 'album'},
        datetime.now().isoformat(), 200000)
    event['profile_id'] = SHARED_OWNER

    play_id = db.record_web_player_play(event)
    assert play_id is not None

    conn = db._get_connection()
    try:
        row = conn.execute(
            "SELECT scrobble_eligible, server_source FROM listening_history WHERE id = ?",
            (play_id,)).fetchone()
    finally:
        conn.close()
    assert row[0] == 0
    assert row[1] == 'soulsync_web'

    # the eligibility ping flips it; a second flip is a no-op
    assert db.mark_play_scrobble_eligible(play_id, profile_id=SHARED_OWNER) is True
    assert db.mark_play_scrobble_eligible(play_id, profile_id=SHARED_OWNER) is False

    # garbage input is rejected, and server rows can never be flipped here
    assert db.mark_play_scrobble_eligible('nope') is False
    assert db.mark_play_scrobble_eligible(True) is False
    assert db.mark_play_scrobble_eligible(999999) is False
    server_id = _insert_row(db, 'server song', 'plex', 0)
    assert db.mark_play_scrobble_eligible(server_id, profile_id=SHARED_OWNER) is False

    # another pile's row can't be flipped from this pile...
    other_id = _insert_row(db, 'other pile song', 'soulsync_web', 0)
    conn = db._get_connection()
    try:
        conn.execute("UPDATE listening_history SET profile_id = 2 WHERE id = ?", (other_id,))
        conn.commit()
    finally:
        conn.close()
    assert db.mark_play_scrobble_eligible(other_id, profile_id=SHARED_OWNER) is False
    assert db.mark_play_scrobble_eligible(other_id, profile_id=2) is True

    # ...and tracks of 30s or less never become eligible (Last.fm rule)
    short_id = _insert_row(db, 'jingle', 'soulsync_web', 0)
    conn = db._get_connection()
    try:
        conn.execute("UPDATE listening_history SET duration_ms = 20000 WHERE id = ?", (short_id,))
        conn.commit()
    finally:
        conn.close()
    assert db.mark_play_scrobble_eligible(short_id, profile_id=SHARED_OWNER) is False


def test_migration_adds_column_and_backfills_server_rows(tmp_path):
    """Old DBs gain scrobble_eligible; server-attested rows are backfilled."""
    path = str(tmp_path / "music.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE listening_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id TEXT,
            title TEXT NOT NULL,
            artist TEXT,
            album TEXT,
            played_at TIMESTAMP NOT NULL,
            duration_ms INTEGER DEFAULT 0,
            server_source TEXT,
            db_track_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            scrobbled_lastfm INTEGER DEFAULT 0,
            scrobbled_listenbrainz INTEGER DEFAULT 0,
            profile_id INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute(
        "INSERT INTO listening_history (title, played_at, server_source)"
        " VALUES ('old plex play', '2026-01-01 00:00:00', 'plex')")
    conn.execute(
        "INSERT INTO listening_history (title, played_at, server_source)"
        " VALUES ('old web play', '2026-01-01 00:00:00', 'soulsync_web')")
    conn.execute(
        "INSERT INTO listening_history (title, played_at, server_source)"
        " VALUES ('old lastfm import', '2026-01-01 00:00:00', 'lastfm')")
    conn.execute(
        "INSERT INTO listening_history (title, played_at, server_source)"
        " VALUES ('old listenbrainz import', '2026-01-01 00:00:00', 'listenbrainz')")
    conn.commit()
    conn.close()

    db = MusicDatabase(path)

    conn = db._get_connection()
    try:
        cols = [c[1] for c in conn.execute("PRAGMA table_info(listening_history)")]
        assert 'scrobble_eligible' in cols
        rows = dict(conn.execute(
            "SELECT title, scrobble_eligible FROM listening_history").fetchall())
    finally:
        conn.close()
    assert rows['old plex play'] == 1
    # attested imports keep their cross-service forwarding after migration
    assert rows['old lastfm import'] == 1
    assert rows['old listenbrainz import'] == 1
    # the old web play can't be verified against the Last.fm rule: it stays
    # ineligible rather than emitting a scrobble that was never earned
    assert rows['old web play'] == 0


def test_insert_events_carries_eligibility_flag(db):
    """insert_listening_events persists the producer's scrobble_eligible."""
    db.insert_listening_events([{
        'track_id': 'e1', 'title': 'flagged', 'artist': 'a', 'album': 'b',
        'played_at': '2026-10-11 13:00:00', 'duration_ms': 1000,
        'server_source': 'plex', 'db_track_id': None,
        'profile_id': SHARED_OWNER, 'scrobble_eligible': 1,
    }])
    db.insert_listening_events([{
        'track_id': 'e2', 'title': 'unflagged', 'artist': 'a', 'album': 'b',
        'played_at': '2026-10-11 13:01:00', 'duration_ms': 1000,
        'server_source': 'soulsync_web', 'db_track_id': None,
        'profile_id': SHARED_OWNER,
    }])
    conn = db._get_connection()
    try:
        rows = dict(conn.execute(
            "SELECT title, scrobble_eligible FROM listening_history").fetchall())
    finally:
        conn.close()
    assert rows['flagged'] == 1
    assert rows['unflagged'] == 0


def test_poll_keeps_configured_cadence_while_scrobble_flushes(db):
    """A scrobble flush must not drag _poll() with it: with a huge poll
    interval, _poll runs once at startup and never again, while scrobbles
    keep flushing on their own cadence."""
    config = _FakeConfig({
        'listening_stats.enabled': True,
        'listening_stats.poll_interval': 100000,  # minutes: never due in-test
        'lastfm.scrobble_enabled': True,
        'lastfm.api_key': 'k',
        'lastfm.api_secret': 's',
        'lastfm.session_key': 'sess',
        'listenbrainz.scrobble_enabled': False,
    })
    row_id = _insert_row(db, 'pending', 'soulsync_web', 1)

    worker = ListeningStatsWorker(db, config)
    worker._build_stats_cache = lambda *a, **k: None
    polls = []
    worker._poll = lambda: polls.append(1)
    worker.scrobble_interval = 1

    thread = threading.Thread(target=worker._run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while time.time() < deadline and _scrobbled_lastfm(db, row_id) != 1:
            time.sleep(0.05)
        assert _scrobbled_lastfm(db, row_id) == 1
        # one startup poll is expected; several more flush cycles must not
        # trigger another
        assert polls == [1]
        time.sleep(2.5)
        assert polls == [1], "scrobble flush dragged _poll() with it"
    finally:
        worker.should_stop = True
        worker._stop_event.set()
        thread.join(timeout=5)


def test_scrobble_clients_treat_naive_timestamps_as_utc(monkeypatch):
    """listening_history stores UTC; a naive stamp must not shift by the
    machine's local UTC offset when converted for the scrobble APIs."""
    from datetime import datetime, timezone

    # NB: the module-level autouse fixture swaps LastFMClient for a fake;
    # the real classes were captured at import time above.
    naive = '2026-10-11 17:11:11'
    expected = int(datetime(2026, 10, 11, 17, 11, 11, tzinfo=timezone.utc).timestamp())

    lfm = _RealLastFMClient(api_key='k', api_secret='s', session_key='sk')
    posted = {}

    class _Resp:
        def json(self):
            return {'scrobbles': {'@attr': {'accepted': 1}}}

    monkeypatch.setattr(
        lfm.session, 'post',
        lambda url, data=None, timeout=None: posted.update(data) or _Resp())
    assert lfm.scrobble_tracks(
        [{'artist': 'a', 'track': 't', 'timestamp': naive}]) is True
    assert posted['timestamp[0]'] == str(expected)

    monkeypatch.setattr(_RealListenBrainzClient, '_validate_and_get_username',
                        lambda self: True)
    lb = _RealListenBrainzClient(token='t')
    lb.username = 'testuser'
    captured = {}

    class _LBResp:
        status_code = 200

    monkeypatch.setattr(
        lb, '_make_request_with_retry',
        lambda method, url, **kw: captured.update(kw) or _LBResp())
    assert lb.submit_listens(
        [{'artist': 'a', 'track': 't', 'timestamp': naive}]) is True
    assert captured['json']['payload'][0]['listened_at'] == expected


def test_attested_import_match_upgrades_web_row_eligibility(db):
    """A Last.fm import matched onto a web-player 10s log upgrades the row
    to scrobble-eligible (the import attests the play). The row is also
    marked scrobbled for lastfm, so it can only ever go to ListenBrainz."""
    from core.listening_import.dedup import insert_import_events

    event = build_play_event(
        {'id': 9, 'title': 'Song', 'artist': 'Artist', 'album': 'Album'},
        '2026-10-11 14:00:00', 200000)
    event['profile_id'] = SHARED_OWNER
    play_id = db.record_web_player_play(event)
    assert play_id is not None

    # the user's own Last.fm client scrobbled the same play moments later;
    # the import matches it onto the web row (same title/artist, ±10s)
    insert_import_events(db, [{
        'track_id': 'lastfm:artist:song:123',
        'title': 'Song', 'artist': 'Artist', 'album': 'Album',
        'played_at': '2026-10-11 14:00:05', 'duration_ms': 200000,
        'server_source': 'lastfm', 'db_track_id': None,
        'profile_id': SHARED_OWNER, 'scrobble_eligible': 1,
    }], 'lastfm', profile_id=SHARED_OWNER)

    conn = db._get_connection()
    try:
        row = conn.execute(
            "SELECT scrobble_eligible, scrobbled_lastfm, scrobbled_listenbrainz"
            " FROM listening_history WHERE id = ?", (play_id,)).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM listening_history").fetchone()[0]
    finally:
        conn.close()
    assert count == 1, "the import should have matched, not inserted a second row"
    assert row[0] == 1, "attested match should upgrade eligibility"
    assert row[1] == 1, "must never scrobble back to the import's origin"
    assert row[2] == 0
