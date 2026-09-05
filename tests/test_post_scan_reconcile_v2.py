"""The post-scan reconcile, on the Library-v2 catalogue.

Upstream runs a gap-fill after every library scan: read the tags of the rows
the run newly INSERTED and fill any provider IDs the file carries but the DB
does not (`_reconcile_after_scan`, scoped by `worker._new_track_ids`). It keeps
newly-added music's Spotify/MusicBrainz/Deezer ids current without anyone
pressing a backfill button.

None of that could work here, and it failed silently in three separate places:

  * this worker never called `post_scan_hook` at all;
  * `_new_track_ids` was initialised and never filled;
  * "newly inserted" is not an event this catalogue has — a media scan cannot
    create a track. Ownership is import-controlled: the scan MAPS a server id
    onto a row the importer already wrote.

The v2 event with the same meaning is the row the library just LEARNED about
from the server: the run created its media-server mapping. Those are exactly
the rows whose tags have not been read in a scan context before, so that is
what the ledger records and what the reconcile is scoped to.
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _imported_track(db, *, title="Xtal", path="/music/xtal.flac",
                    server_album="al-1", server_artist="ar-1"):
    """One imported track with a live file, under a server-mapped album/artist.

    The scan can only map onto rows that already exist — building the fixture
    any other way would test a path the product does not have.
    """
    with db._get_connection() as conn:
        artist_id = conn.execute(
            "INSERT INTO lib2_artists (name, name_key, server_source, server_id) "
            "VALUES ('Aphex Twin','aphex twin','plex',?)", (server_artist,)).lastrowid
        album_id = conn.execute(
            "INSERT INTO lib2_albums (primary_artist_id, title, server_source, server_id) "
            "VALUES (?,'SAW','plex',?)", (artist_id, server_album)).lastrowid
        track_id = conn.execute(
            "INSERT INTO lib2_tracks (album_id, title, track_number, disc_number) "
            "VALUES (?,?,1,1)", (album_id, title)).lastrowid
        conn.execute(
            "INSERT INTO lib2_track_files (track_id, path, is_primary, file_state) "
            "VALUES (?,?,1,'active')", (track_id, path))
        conn.commit()
    return track_id


def _server_track(server_id="srv-1", *, title="Xtal", path="/music/xtal.flac"):
    return _Obj(ratingKey=server_id, title=title, trackNumber=1, discNumber=1,
                duration=292000, path=path)


# ── the ledger: what "new" means on this catalogue ──────────────────────────


class TestNewlyMapped:
    def test_the_first_sight_of_a_server_track_reports_inserted(self, db):
        """Not 'the row was created' — nothing creates rows here. This is the
        run connecting an imported file to the server for the first time."""
        _imported_track(db)
        result = db.insert_or_update_media_track(
            _server_track(), "al-1", "ar-1", server_source="plex")
        assert result == "inserted"

    def test_seeing_it_again_reports_updated(self, db):
        _imported_track(db)
        db.insert_or_update_media_track(_server_track(), "al-1", "ar-1", server_source="plex")
        again = db.insert_or_update_media_track(
            _server_track(), "al-1", "ar-1", server_source="plex")
        assert again == "updated", (
            "a re-scan would otherwise re-read the tags of the whole library "
            "every run, which is the backfill job, not a post-scan tail")

    def test_a_track_with_no_imported_file_is_neither(self, db):
        """The scan cannot create ownership, so there is nothing to reconcile."""
        assert db.insert_or_update_media_track(
            _server_track("ghost"), "al-1", "ar-1", server_source="plex") is False


# ── the worker: does the ledger actually get filled ─────────────────────────


def _bare_worker(server_type="plex"):
    w = object.__new__(__import__(
        "core.database_update_worker", fromlist=["DatabaseUpdateWorker"]).DatabaseUpdateWorker)
    w.callbacks = {'finished': [], 'error': [], 'progress_updated': [],
                   'phase_changed': [], 'artist_processed': []}
    w.server_type = server_type
    w._new_track_ids = set()
    w._touched_artist_ids = set()
    w._touched_album_ids = set()
    w.post_scan_hook = None
    w.should_stop = False
    return w


class TestTheHookRuns:
    def test_the_hook_runs_before_completion_is_announced(self):
        """Order is the point: while the hook runs the scan must still read as
        'running', or an automation polling for completion sees 'finished' and
        walks away mid-reconcile."""
        w = _bare_worker()
        order = []
        w.post_scan_hook = lambda worker: order.append('hook')
        w.callbacks['finished'].append(lambda *_: order.append('finished'))
        w._emit_finished(1, 2, 3, 4, 5)
        assert order == ['hook', 'finished']

    def test_the_hook_is_handed_the_worker_that_holds_the_ledger(self):
        w = _bare_worker()
        w._new_track_ids.add('srv-1')
        seen = []
        w.post_scan_hook = lambda worker: seen.append(worker)
        w._emit_finished()
        assert seen == [w]

    def test_a_broken_hook_never_swallows_the_completion_signal(self):
        """A gap-fill is a nice-to-have; the scan finishing is not."""
        w = _bare_worker()
        got = []
        w.post_scan_hook = lambda worker: (_ for _ in ()).throw(RuntimeError("boom"))
        w.callbacks['finished'].append(lambda *a: got.append(a))
        w._emit_finished(1, 2, 3, 4, 5)
        assert got == [(1, 2, 3, 4, 5)]

    def test_no_hook_configured_still_finishes(self):
        w = _bare_worker()
        got = []
        w.callbacks['finished'].append(lambda *a: got.append(a))
        w._emit_finished(9)
        assert got == [(9,)]


# ── the reconcile: scoped to the run, on the v2 catalogue ───────────────────


class TestReconcileAfterScan:
    def test_it_reconciles_exactly_what_the_run_mapped(self, monkeypatch):
        import web_server

        w = _bare_worker("navidrome")
        w._new_track_ids = {'srv-1', 'srv-2'}
        calls = {}

        def _fake(conn, track_ids=None, on_progress=None, should_stop=None,
                  server_source=None):
            calls['ids'] = sorted(track_ids or [])
            calls['server_source'] = server_source
            return _Totals()

        monkeypatch.setattr(web_server, "_reconcile_library_tracks", _fake)
        monkeypatch.setattr(web_server, "get_database", lambda: _FakeDb())
        web_server._reconcile_after_scan(w)

        assert calls['ids'] == ['srv-1', 'srv-2']
        # The ledger holds SERVER ids; without the source the reconcile would
        # read them as catalogue ids and touch the wrong rows (or none).
        assert calls['server_source'] == 'navidrome'

    def test_an_empty_ledger_does_no_work_at_all(self, monkeypatch):
        import web_server

        w = _bare_worker()
        called = []
        monkeypatch.setattr(web_server, "_reconcile_library_tracks",
                            lambda *a, **k: called.append(1))
        monkeypatch.setattr(web_server, "get_database",
                            lambda: (_ for _ in ()).throw(AssertionError("no db needed")))
        web_server._reconcile_after_scan(w)
        assert called == []

    def test_a_failing_reconcile_is_swallowed(self, monkeypatch):
        import web_server

        w = _bare_worker()
        w._new_track_ids = {'srv-1'}
        monkeypatch.setattr(web_server, "get_database", lambda: _FakeDb())
        monkeypatch.setattr(web_server, "_reconcile_library_tracks",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
        web_server._reconcile_after_scan(w)  # must not raise

    def test_it_reports_progress_as_a_scan_phase(self, monkeypatch):
        """The user watches one progress bar. A tail that reports nothing looks
        like the scan hung at 100%."""
        import web_server

        w = _bare_worker()
        w._new_track_ids = {'srv-1'}
        phases = []
        monkeypatch.setattr(web_server, "get_database", lambda: _FakeDb())
        monkeypatch.setattr(web_server, "_db_update_phase_callback", phases.append)

        def _fake(conn, track_ids=None, on_progress=None, should_stop=None,
                  server_source=None):
            return _Totals()

        monkeypatch.setattr(web_server, "_reconcile_library_tracks", _fake)
        web_server._reconcile_after_scan(w)
        assert phases and 'tag' in phases[0].lower()


class _Totals:
    total = 1
    processed = 1
    entities_updated = 1
    ids_filled = 2
    unreadable = 0
    conflicts = 0


class _FakeConn:
    def close(self):
        pass


class _FakeDb:
    def _get_connection(self):
        return _FakeConn()


# ── end to end: a real file, a real fill ────────────────────────────────────


def test_the_scan_tail_fills_a_provider_id_from_the_file(db, tmp_path, monkeypatch):
    """The whole point, wired: a mapped file whose tags carry a Spotify id ends
    the scan with that id on the catalogue row."""
    import web_server

    audio = tmp_path / "xtal.flac"
    audio.write_bytes(b"not really audio")
    track_id = _imported_track(db, path=str(audio))
    assert db.insert_or_update_media_track(
        _server_track(path=str(audio)), "al-1", "ar-1", server_source="plex") == "inserted"

    w = _bare_worker()
    w._new_track_ids = {'srv-1'}

    monkeypatch.setattr(web_server, "get_database", lambda: db)
    monkeypatch.setattr(web_server, "_resolve_library_file_path", lambda p: p)
    monkeypatch.setattr(
        "core.library.file_tags.read_embedded_tags",
        lambda path: {'available': True, 'tags': {'spotify_track_id': 'sp-xtal'}},
    )

    web_server._reconcile_after_scan(w)

    with db._get_connection() as conn:
        stored = conn.execute(
            "SELECT spotify_id FROM lib2_tracks WHERE id=?", (track_id,)).fetchone()[0]
    assert stored == 'sp-xtal'


# ── wiring: the hook has to be ATTACHED, not merely defined ─────────────────


class TestItIsActuallyWired:
    """Three separate places had to be right for this feature to exist, and
    each of them failed quietly on its own. Defining the function proves
    nothing — pin that a real scan gets it."""

    def test_both_scan_entry_points_attach_the_hook(self):
        import inspect

        from api import database_admin

        source = inspect.getsource(database_admin)
        starts = source.count("db_update_worker.run(")
        starts += source.count("db_update_worker.run_deep_scan(")
        attached = source.count("db_update_worker.post_scan_hook = _reconcile_after_scan")
        assert starts == attached == 2, (
            f"{starts} scan start(s), {attached} attaching the reconcile — a scan "
            "without the hook silently skips the gap-fill")

    def test_the_web_layer_hands_over_the_real_function(self):
        import web_server
        from api import database_admin

        assert database_admin._reconcile_after_scan is web_server._reconcile_after_scan, (
            "configure() was handed None or a stub, so every scan's tail is a no-op")

    def test_the_worker_calls_whatever_was_attached(self):
        """The link the rewrite dropped: `post_scan_hook` existed as an
        attribute and nothing ever read it."""
        import inspect

        from core import database_update_worker

        source = inspect.getsource(database_update_worker.DatabaseUpdateWorker._emit_finished)
        assert "self.post_scan_hook(self)" in source
