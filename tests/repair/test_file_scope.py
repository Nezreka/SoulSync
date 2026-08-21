"""Exact file scoping for destructive/moving repair jobs."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.repair_jobs.base import (
    build_artist_file_scope,
    file_path_in_scope,
    get_scope_file_paths,
    JobContext,
)


class _DB:
    def __init__(self, conn):
        self.conn = conn

    def _get_connection(self):
        return self.conn


class _NonClosingConnection:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, *args, **kwargs):
        return self.conn.execute(*args, **kwargs)

    def close(self):
        pass


_SCOPE_DDL = """
    CREATE TABLE lib2_artists(id INTEGER PRIMARY KEY, name TEXT,
                              canonical_artist_id INTEGER);
    CREATE TABLE lib2_album_artists(album_id INTEGER, artist_id INTEGER);
    CREATE TABLE lib2_track_artists(track_id INTEGER, artist_id INTEGER);
    CREATE TABLE lib2_tracks(id INTEGER PRIMARY KEY, album_id INTEGER);
    CREATE TABLE lib2_track_files(track_id INTEGER, path TEXT, file_state TEXT);
"""


def _scope_db(script: str):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCOPE_DDL + script)
    return _DB(_NonClosingConnection(conn))


def test_build_artist_file_scope_uses_lib2_links_and_keeps_empty_scope_explicit():
    db = _scope_db("""
        INSERT INTO lib2_artists(id,name) VALUES(1, 'Artist'), (2, 'Empty');
        INSERT INTO lib2_album_artists VALUES(10, 1);
        INSERT INTO lib2_tracks VALUES(100, 10), (101, 10);
        INSERT INTO lib2_track_files VALUES
            (100, '/music/Artist/Album/a.flac', 'active'),
            (101, '/music/Artist/Album/b.flac', 'active');
    """)

    scope = build_artist_file_scope(db, 1)
    assert scope == {
        "artist_id": 1,
        "artist_name": "Artist",
        "file_paths": [
            "/music/Artist/Album/a.flac",
            "/music/Artist/Album/b.flac",
        ],
    }
    assert build_artist_file_scope(db, 2)["file_paths"] == []


def test_file_scope_is_exact_and_empty_never_means_library_wide():
    context = SimpleNamespace(scope={"file_paths": [r"C:\\Music\\Artist\\one.flac"]})
    allowed = get_scope_file_paths(context)
    assert file_path_in_scope("C:/Music/Artist/one.flac", allowed)
    assert not file_path_in_scope("C:/Music/Artist/two.flac", allowed)
    assert not file_path_in_scope("/any/file.flac", frozenset())
    assert file_path_in_scope("/any/file.flac", None)


def _scan_actionable_singles_against_global_album_candidates(context, rows):
    """Minimal stand-in for a scoped dedup-style job's scan(): the same
    contract ``SingleAlbumDedupJob`` (retired, see docs §7 P3 checklist)
    used to exercise — only a "single" whose file is IN scope is actionable,
    while "album" candidates it matches against stay library-wide/global and
    are never filtered by scope. Exists purely to keep this file's coverage
    of ``get_scope_file_paths``/``file_path_in_scope`` working together
    end-to-end, independent of any one concrete job's business logic."""
    scope_paths = get_scope_file_paths(context)
    singles = [r for r in rows if r["kind"] == "single" and file_path_in_scope(r["file_path"], scope_paths)]
    album_tracks = [r for r in rows if r["kind"] == "album"]
    created = 0
    for single in singles:
        for album_track in album_tracks:
            if album_track["norm_title"] == single["norm_title"]:
                context.create_finding(
                    entity_id=str(single["id"]),
                    details={"album_track": {"id": album_track["id"]}},
                )
                created += 1
                break
    return created


def test_dedup_scopes_actionable_single_path_but_keeps_album_candidates_global():
    rows = [
        {"id": 1, "kind": "single", "norm_title": "song",
         "file_path": "/music/Artist/Song/single.flac"},
        {"id": 2, "kind": "single", "norm_title": "song",
         "file_path": "/music/Other/Song/single.flac"},
        {"id": 3, "kind": "album", "norm_title": "song",
         "file_path": "/music/Other/Album/song.flac"},
    ]
    findings = []
    context = JobContext(
        db=MagicMock(),
        transfer_folder="/music",
        config_manager=None,
        scope={"file_paths": ["/music/Artist/Song/single.flac"]},
        create_finding=lambda **finding: findings.append(finding) or True,
    )

    created = _scan_actionable_singles_against_global_album_candidates(context, rows)

    assert created == 1
    assert findings[0]["entity_id"] == "1"
    assert findings[0]["details"]["album_track"]["id"] == 3


# ── the scope must be READ, not just built ─────────────────────────────────
#
# These primitives had zero production callers: the endpoint resolved an
# artist's files, `run_job_now` stored the scope, the log line reported it —
# and every job then ran library-wide. "Reorganize this artist" moved the whole
# library while the API answered `scope_files: 180` (bug-audit BUG-13).


def _reorg_db(tmp_path):
    db_path = str(tmp_path / "scope.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    from core.library2.schema import ensure_library_v2_schema
    ensure_library_v2_schema(conn)
    conn.execute("INSERT INTO lib2_artists(id, name) VALUES(1, 'Wanted'), (2, 'Other')")
    conn.execute("INSERT INTO lib2_albums(id, primary_artist_id, title) "
                 "VALUES(10, 1, 'Mine'), (20, 2, 'Theirs')")
    conn.execute("INSERT INTO lib2_tracks(id, album_id, title, track_number) "
                 "VALUES(100, 10, 'A', 1), (200, 20, 'B', 1)")
    conn.execute("INSERT INTO lib2_track_files(id, track_id, path) "
                 "VALUES(1000, 100, '/music/Wanted/Mine/a.flac'), "
                 "      (2000, 200, '/music/Other/Theirs/b.flac')")
    conn.commit()
    conn.close()

    class _Db:
        database_path = db_path

        def _get_connection(self):
            opened = sqlite3.connect(db_path)
            opened.row_factory = sqlite3.Row
            return opened

    return _Db()


def test_library_reorganize_albums_honour_the_file_allowlist(tmp_path):
    from core.repair_jobs.library_reorganize import LibraryReorganizeJob

    db = _reorg_db(tmp_path)
    ctx = SimpleNamespace(db=db, scope={
        "artist_id": 1, "artist_name": "Wanted",
        "file_paths": ["/music/Wanted/Mine/a.flac"],
    })

    assert [a["id"] for a in LibraryReorganizeJob._albums(ctx)] == [10]


def test_library_reorganize_is_library_wide_without_a_scope(tmp_path):
    from core.repair_jobs.library_reorganize import LibraryReorganizeJob

    db = _reorg_db(tmp_path)
    ctx = SimpleNamespace(db=db, scope=None)

    assert [a["id"] for a in LibraryReorganizeJob._albums(ctx)] == [10, 20]


def test_an_empty_allowlist_scans_nothing_rather_than_everything(tmp_path):
    from core.repair_jobs.library_reorganize import LibraryReorganizeJob

    db = _reorg_db(tmp_path)
    ctx = SimpleNamespace(db=db, scope={"artist_name": "Empty", "file_paths": []})

    assert LibraryReorganizeJob._albums(ctx) == []


def test_a_job_that_cannot_honour_a_file_scope_refuses_it(tmp_path):
    """Fail closed: the alternative is a library-wide run the user did not ask
    for, reported back to them as scoped."""
    import pytest

    from core.repair_worker import RepairWorker

    worker = RepairWorker.__new__(RepairWorker)
    worker._jobs = {
        "scoped": SimpleNamespace(supports_file_scope=True, display_name="Scoped"),
        "unscoped": SimpleNamespace(supports_file_scope=False, display_name="Unscoped"),
    }
    worker._ensure_jobs_loaded = lambda: None
    worker._force_run_lock = __import__("threading").Lock()
    worker._force_run_scopes = {}
    worker._force_run_queue = []

    scope = {"artist_name": "A", "file_paths": ["/music/a.flac"]}
    assert worker.run_job_now("scoped", scope=scope) is True

    with pytest.raises(ValueError, match="cannot be scoped"):
        worker.run_job_now("unscoped", scope=scope)

    # An artist-NAME-only scope is not a file scope and stays allowed.
    assert worker.run_job_now("unscoped", scope={"artist_name": "A"}) is True


# ---------------------------------------------------------------------------
# L2-015: the scope is the artist the user sees, and the files that exist
# ---------------------------------------------------------------------------


def test_the_scope_covers_the_whole_alias_group():
    """The detail page merges canonical + aliases. Scoping to the canonical row
    alone ran AcoustID/corruption/ReplayGain/reorganize over part of what the
    user was looking at."""
    db = _scope_db("""
        INSERT INTO lib2_artists(id,name,canonical_artist_id)
            VALUES(1,'Röyksopp',NULL), (2,'Royksopp',1);
        INSERT INTO lib2_album_artists VALUES(10, 1), (11, 2);
        INSERT INTO lib2_tracks VALUES(100, 10), (110, 11);
        INSERT INTO lib2_track_files VALUES
            (100, '/music/canonical.flac', 'active'),
            (110, '/music/alias.flac', 'active');
    """)

    for entry_point in (1, 2):  # canonical deep link and alias deep link alike
        assert build_artist_file_scope(db, entry_point)["file_paths"] == [
            "/music/alias.flac", "/music/canonical.flac",
        ]


def test_inactive_files_are_left_out():
    """A tombstoned path is nothing a scoped run can act on; including it only
    produced irrelevant findings and errors — and in an alias group it could be
    the ONLY path in scope while the real file was skipped."""
    db = _scope_db("""
        INSERT INTO lib2_artists(id,name,canonical_artist_id)
            VALUES(1,'Röyksopp',NULL), (2,'Royksopp',1);
        INSERT INTO lib2_album_artists VALUES(10, 1), (11, 2);
        INSERT INTO lib2_tracks VALUES(100, 10), (110, 11);
        INSERT INTO lib2_track_files VALUES
            (100, '/music/tombstoned.flac', 'deleted'),
            (110, '/music/alias.flac', 'active');
    """)

    assert build_artist_file_scope(db, 1)["file_paths"] == ["/music/alias.flac"]


def test_a_track_only_credit_is_in_scope():
    """A featured credit puts the release on the artist's page, so a scoped run
    started from that page has to reach its file too."""
    db = _scope_db("""
        INSERT INTO lib2_artists(id,name) VALUES(1,'Guest');
        INSERT INTO lib2_album_artists VALUES(10, 99);
        INSERT INTO lib2_tracks VALUES(100, 10);
        INSERT INTO lib2_track_artists VALUES(100, 1);
        INSERT INTO lib2_track_files VALUES(100, '/music/feature.flac', 'active');
    """)

    assert build_artist_file_scope(db, 1)["file_paths"] == ["/music/feature.flac"]
