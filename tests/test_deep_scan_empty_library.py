"""Deep scan vs an empty/shrunken library selection (#stale-artists, 5BILLION).

Switching the library selection (All Libraries → one) and deep-scanning used to
fail two ways:
  • an EMPTY selected library errored out ("No artists found") before stale
    removal ever ran — the old selection's artists stayed forever
  • a SMALL selected library tripped the >50%-stale safety guard, silently
    skipping removal

The fix makes the fetch verifiable: _get_all_artists marks whether the server
actually ANSWERED (a real list) vs the fetch failing. A verified-empty answer
is confirmed with a second fetch, then stale removal proceeds; the 50% guard
is bypassed only for a fully-trusted scan (verified fetch + zero per-artist
failures + not stopped). Every failure path keeps the old conservative
behavior: error out, remove nothing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.database_update_worker import DatabaseUpdateWorker
from database.music_database import MusicDatabase


class _FakeClient:
    """Media client whose get_all_artists yields scripted answers per call."""

    def __init__(self, answers, connected=True):
        self.answers = list(answers)
        self.connected = connected

    def ensure_connection(self):
        return self.connected

    def get_all_artists(self):
        a = self.answers.pop(0) if self.answers else self.answers_default()
        if isinstance(a, Exception):
            raise a
        return a

    def answers_default(self):
        return []

    def set_progress_callback(self, cb):
        pass

    def clear_cache(self):
        pass


@pytest.fixture()
def dbpath(tmp_path, monkeypatch):
    p = str(tmp_path / "music.db")
    db = MusicDatabase(p)   # create schema
    # get_database() is a process-wide singleton bound to the FIRST path it
    # sees — later workers would silently write to another test's db. Bind the
    # worker to THIS test's db explicitly.
    monkeypatch.setattr("core.database_update_worker.get_database",
                        lambda path=None: db)
    return p


def _seed(dbpath, n_tracks=10, server="navidrome"):
    db = MusicDatabase(dbpath)
    with db._get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO artists (id, name, server_source) VALUES ('a1', 'Old Artist', ?)", (server,))
        conn.execute("INSERT OR REPLACE INTO albums (id, title, artist_id) VALUES (900, 'Old Album', 'a1')")
        for i in range(n_tracks):
            conn.execute(
                "INSERT OR REPLACE INTO tracks (id, album_id, artist_id, title, track_number, duration, "
                "file_path, server_source) VALUES (?, 900, 'a1', ?, 1, 100, ?, ?)",
                (f"t{i}", f"T{i}", f"/m/t{i}.flac", server))
        conn.commit()
    return db


def _worker(dbpath, client):
    w = DatabaseUpdateWorker(media_client=client, database_path=dbpath,
                             server_type="navidrome", force_sequential=True)
    w.events = {"error": [], "finished": []}
    w.callbacks["error"].append(lambda *a: w.events["error"].append(a))
    w.callbacks["finished"].append(lambda *a: w.events["finished"].append(a))
    return w


def _counts(dbpath, server="navidrome"):
    db = MusicDatabase(dbpath)
    with db._get_connection() as conn:
        artists = conn.execute("SELECT COUNT(*) FROM artists WHERE server_source = ?", (server,)).fetchone()[0]
        tracks = conn.execute("SELECT COUNT(*) FROM tracks WHERE server_source = ?", (server,)).fetchone()[0]
    return artists, tracks


def test_verified_empty_library_removes_stale_artists(dbpath):
    # THE reported bug: selected library is empty; the old selection's data
    # must be removed, not left behind an error.
    _seed(dbpath, n_tracks=10)
    w = _worker(dbpath, _FakeClient(answers=[[], []]))   # two verified-empty answers
    w.run_deep_scan()
    assert not w.events["error"], f"unexpected error: {w.events['error']}"
    assert w.events["finished"], "scan should complete"
    artists, tracks = _counts(dbpath)
    assert tracks == 0
    assert artists == 0, "stale artists must be cleaned up"


def test_failed_connection_still_errors_and_removes_nothing(dbpath):
    _seed(dbpath, n_tracks=10)
    w = _worker(dbpath, _FakeClient(answers=[[]], connected=False))
    w.run_deep_scan()
    assert w.events["error"], "a failed connection must surface as an error"
    assert _counts(dbpath) == (1, 10)


def test_fetch_exception_still_errors_and_removes_nothing(dbpath):
    _seed(dbpath, n_tracks=10)
    w = _worker(dbpath, _FakeClient(answers=[RuntimeError("api down")]))
    w.run_deep_scan()
    assert w.events["error"]
    assert _counts(dbpath) == (1, 10)


def test_none_answer_is_not_trusted_as_empty(dbpath):
    # a client that swallowed an error into None must not wipe anything
    _seed(dbpath, n_tracks=10)
    w = _worker(dbpath, _FakeClient(answers=[None]))
    w.run_deep_scan()
    assert w.events["error"]
    assert _counts(dbpath) == (1, 10)


def test_transient_empty_answer_recovers_on_second_fetch(dbpath):
    # first answer empty, second answer has the artist — scan proceeds with
    # the real list and removes nothing it saw
    _seed(dbpath, n_tracks=3)
    artist = SimpleNamespace(title="Old Artist")
    w = _worker(dbpath, _FakeClient(answers=[[], [artist]]))
    w._deep_scan_process_all_artists = lambda artists, seen: seen.update({"t0", "t1", "t2"})
    w.run_deep_scan()
    assert not w.events["error"]
    assert _counts(dbpath) == (1, 3)


def test_trusted_scan_may_exceed_the_50pct_guard(dbpath):
    # the shrunken-library case: >100 tracks in db, server (verified, clean)
    # only has 2 of them → mass staleness is REAL and must be removed
    _seed(dbpath, n_tracks=120)
    artist = SimpleNamespace(title="Old Artist")
    w = _worker(dbpath, _FakeClient(answers=[[artist]]))
    w._deep_scan_process_all_artists = lambda artists, seen: seen.update({"t0", "t1"})
    w.run_deep_scan()
    assert not w.events["error"]
    _, tracks = _counts(dbpath)
    assert tracks == 2, "stale tracks beyond 50% must be removed on a trusted scan"


def test_untrusted_scan_keeps_the_50pct_guard(dbpath):
    # same mass staleness but one artist failed to process → guard holds
    _seed(dbpath, n_tracks=120)
    artist = SimpleNamespace(title="Old Artist")
    w = _worker(dbpath, _FakeClient(answers=[[artist]]))

    def _process(artists, seen):
        seen.update({"t0", "t1"})
        w.failed_operations = 1
    w._deep_scan_process_all_artists = _process
    w.run_deep_scan()
    _, tracks = _counts(dbpath)
    assert tracks == 120, "guard must hold when any artist failed to process"


def test_stopped_scan_keeps_the_50pct_guard(dbpath):
    # a scan stopped mid-run has a partial seen-set — never mass-remove
    _seed(dbpath, n_tracks=120)
    artist = SimpleNamespace(title="Old Artist")
    w = _worker(dbpath, _FakeClient(answers=[[artist]]))

    def _process(artists, seen):
        seen.update({"t0", "t1"})
        w.should_stop = True
    w._deep_scan_process_all_artists = _process
    w.run_deep_scan()
    _, tracks = _counts(dbpath)
    assert tracks == 120
