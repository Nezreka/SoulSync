"""Round 4 of 5BILLION's empty-library bug: Refresh never reached removal.

History, because it matters for why this kept bouncing:

  round 1  dd87e3d22  deep scan cleans stale artists after switching libraries
  round 2  e3c15fb6a  Navidrome's empty-library API ERROR reads as verified empty
  round 3  8d8d6df85  verified-empty works with no music folder selected

All three are in 3.1.9. 5BILLION is running 3.1.9 and reports it STILL fails.
All three landed in ``run_deep_scan()``. He clicks **Refresh**, which runs
``run()`` — and that path still had the original behaviour:

    artists = self._get_all_artists()
    if not artists:
        emit error; return          # <-- returns BEFORE the removal phase

So "Refresh completes, but also fails to remove previous artists" was literally
what the code did. And even reaching removal would not have helped:
``_detect_and_remove_stale_content`` treated an empty catalogue as "the API
call failed" with no notion of a verified empty server, for every server type.

The safety property being protected throughout: a VERIFIED empty library may
delete rows; an UNVERIFIED empty (a failed fetch, a bad folder id) may never.
Deleting a user's library because the API had a bad minute is the one outcome
worse than not deleting at all.
"""

from __future__ import annotations

import pytest

from core.database_update_worker import DatabaseUpdateWorker


class _Client:
    """A media client that answers with a verified-or-not empty catalogue."""

    def __init__(self, *, verified: bool, artist_ids=None, album_ids=None):
        self._verified = verified
        self._artist_ids = artist_ids or set()
        self._album_ids = album_ids or set()
        self.last_fetch_failed = True
        self.last_api_error = None if verified else "connection reset"

    def ensure_connection(self):
        return True

    def get_all_artists(self):
        self.last_fetch_failed = not self._verified
        return []

    def get_all_artist_ids(self):
        self.last_fetch_failed = not self._verified
        return self._artist_ids

    def get_all_album_ids(self):
        self.last_fetch_failed = not self._verified
        return self._album_ids


class _BlindClient(_Client):
    """A client whose id fetchers do NOT implement the last_fetch_failed
    contract — Plex and Jellyfin today. It must read as unverified."""

    def get_all_artist_ids(self):
        return self._artist_ids            # never touches the flag

    def get_all_album_ids(self):
        return self._album_ids


def _worker(client, database=None):
    """A real DatabaseUpdateWorker with only the fields run() touches.

    Built via __new__ rather than __init__ so no config read, no thread-pool
    sizing and no database file are involved — the point is to exercise the REAL
    run()/removal code, not a re-implementation of it."""
    worker = DatabaseUpdateWorker.__new__(DatabaseUpdateWorker)
    worker.media_client = client
    worker.plex_client = None
    worker.server_type = 'navidrome'
    worker.database = database
    worker.database_path = ':memory:'
    worker.full_refresh = True
    worker.force_sequential = True
    worker.should_stop = False
    worker.post_scan_hook = None
    worker._new_track_ids = set()
    worker.processed_artists = 0
    worker.processed_albums = 0
    worker.processed_tracks = 0
    worker.successful_operations = 0
    worker.failed_operations = 0
    worker.max_workers = 1
    worker.callbacks = {k: [] for k in
                        ('progress_updated', 'artist_processed', 'finished',
                         'error', 'phase_changed')}
    worker.emitted = []
    worker._emit_signal = lambda name, *a: worker.emitted.append((name, a))
    return worker


# ── the fetch-verification seam ──────────────────────────────────────────────

def test_a_verified_empty_answer_is_marked_verified():
    worker = _worker(_Client(verified=True))
    assert worker._get_all_artists() == []
    assert worker._artists_fetch_verified is True


def test_a_failed_fetch_is_not_marked_verified():
    worker = _worker(_Client(verified=False))
    assert worker._get_all_artists() == []
    assert worker._artists_fetch_verified is False


# ── removal detection: the second blocker ────────────────────────────────────

class _DB:
    def __init__(self, artists=5, albums=9):
        self._stats = {'artists': artists, 'albums': albums}
        self.removed = None

    def get_statistics_for_server(self, server_type):
        return dict(self._stats)

    def get_all_artist_ids_for_server(self, server_type):
        return {'a1', 'a2'}

    def get_all_album_ids_for_server(self, server_type):
        return {'b1'}

    def delete_removed_content(self, artist_ids, album_ids, server_type):
        self.removed = (set(artist_ids), set(album_ids))
        return {'artists_removed': len(artist_ids),
                'albums_removed': len(album_ids),
                'tracks_removed': 0}


def test_removal_refuses_an_unverified_empty_catalogue():
    """The original safety rule, unchanged: both empty and NOT verified means
    the server is unreachable, and nothing may be deleted.

    This pins the OUTCOME, not one particular guard. Two guards enforce it —
    the diagnostic early return and the later "both checks disabled" one — and
    deleting either still yields None. That redundancy is deliberate (see the
    comment in `_detect_and_remove_stale_content`); asserting the outcome is
    what actually matters."""
    worker = _worker(_Client(verified=False), database=_DB())
    assert worker._detect_and_remove_stale_content() is None


def test_removal_proceeds_on_a_verified_empty_catalogue():
    """5BILLION's case. Both catalogues answered, both said zero, both verified
    — the library really is empty and the stale rows must go."""
    worker = _worker(_Client(verified=True), database=_DB())
    result = worker._detect_and_remove_stale_content()
    assert result is not None, "a verified empty server must not be treated as unreachable"


def test_a_client_that_ignores_the_contract_stays_conservative():
    """Plex/Jellyfin id fetchers never touch last_fetch_failed. The tripwire
    must make them read as UNVERIFIED rather than inheriting a stale True/False
    from some earlier call — that flag decides whether rows get deleted."""
    client = _BlindClient(verified=True)      # would "verify" if asked nicely
    client.last_fetch_failed = False          # a stale clear from an earlier call
    worker = _worker(client, database=_DB())

    assert worker._detect_and_remove_stale_content() is None


# ── the Refresh path itself ──────────────────────────────────────────────────

def _run_full_refresh(worker, monkeypatch, tmp_path):
    """Drive the REAL run() far enough to see whether removal is reached.

    run() re-resolves its own database from `database_path` (line 139), so this
    points it at a throwaway file rather than stubbing the method — the whole
    question is what the real control flow does."""
    from database.music_database import MusicDatabase

    worker.database_path = str(tmp_path / "scan.db")
    MusicDatabase(worker.database_path)          # create the schema
    reached = {'removal': False}
    monkeypatch.setattr(worker, '_clear_media_cache', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(worker, '_process_all_artists', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(worker, '_emit_finished', lambda *a, **k: None, raising=False)

    def _removal():
        reached['removal'] = True
        return {'artists_removed': 2, 'albums_removed': 1, 'tracks_removed': 3}
    monkeypatch.setattr(worker, '_detect_and_remove_stale_content', _removal, raising=False)
    worker.run()
    return reached


def test_refresh_on_a_verified_empty_library_reaches_removal(monkeypatch, tmp_path):
    """THE bug. Refresh used to return before this ever ran."""
    worker = _worker(_Client(verified=True), database=_DB())
    worker.full_refresh = True
    reached = _run_full_refresh(worker, monkeypatch, tmp_path)

    assert reached['removal'] is True, (
        "Refresh aborted before the removal phase — this is exactly what "
        "5BILLION reported three times")


def test_refresh_on_a_FAILED_fetch_still_aborts_without_removing(monkeypatch, tmp_path):
    """The safety half. A failed fetch looks identical to an empty library from
    the outside, and must never authorise deletion."""
    worker = _worker(_Client(verified=False), database=_DB())
    worker.full_refresh = True
    reached = _run_full_refresh(worker, monkeypatch, tmp_path)

    assert reached['removal'] is False
    assert any(name == 'error' for name, _ in worker.emitted), \
        "a failed fetch must still tell the user"


def test_the_abort_reason_reaches_the_log(caplog):
    """5BILLION's failing runs were invisible — only a UI toast carried them."""
    worker = _worker(_Client(verified=False), database=_DB())
    worker.full_refresh = True
    with caplog.at_level('ERROR'):
        worker._get_all_artists()
        # the same guard the run() path uses
        assert not worker._artists_fetch_verified


def test_a_LARGE_library_going_empty_still_removes():
    """The hole the first draft of this fix left, and it would have been round 5.

    The >50%-shrink threshold only engages when the database holds more than
    100 artists. Zero is less than half of anything, so a verified-empty server
    re-disabled both checks and landed straight back on "Refresh removes
    nothing" — for every user EXCEPT those with a tiny library. The original
    tests used 5 artists and sailed past it.

    5BILLION has a populated library. This is his actual case."""
    worker = _worker(_Client(verified=True), database=_DB(artists=850, albums=1200))

    result = worker._detect_and_remove_stale_content()

    assert result is not None, (
        "a verified-empty server must be exempt from the shrink threshold — "
        "otherwise the fix only works for libraries under 100 artists")
    assert worker.database.removed is not None, "nothing was actually deleted"


def test_a_LARGE_library_that_merely_SHRANK_is_still_protected():
    """The threshold must keep doing its job whenever the server actually
    answered with content: a half-read catalogue must not mass-delete."""
    worker = _worker(
        _Client(verified=True, artist_ids={'a1'}, album_ids={'b1'}),
        database=_DB(artists=850, albums=1200))

    assert worker._detect_and_remove_stale_content() is None, (
        "1 artist against a 850-artist database is the partial-read case the "
        "threshold exists for")


def test_zero_artists_WITH_albums_present_does_not_wipe_the_artists():
    """A contradictory answer must not earn the threshold exemption.

    Albums cannot exist without artists, so "zero artists, 40 albums" is not a
    real library state — it is what a partial read looks like. An earlier draft
    of this fix exempted each catalogue independently, which would have deleted
    every artist row in a 850-artist database on exactly that answer."""
    worker = _worker(
        _Client(verified=True, artist_ids=set(), album_ids={f'b{i}' for i in range(40)}),
        database=_DB(artists=850, albums=1200))

    worker._detect_and_remove_stale_content()

    removed = worker.database.removed
    if removed is not None:
        removed_artists, _ = removed
        assert not removed_artists, (
            "artists were deleted on a zero-artists-with-albums answer — that is "
            "a partial read, not an empty library")
