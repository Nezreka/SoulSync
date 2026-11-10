"""#889 Phase 2: the import seam — a hint short-circuits identification, and the
old library row is replaced only after the re-import succeeds.

Two layers:
  * pure helpers (build_identification_from_hint, delete_replaced_track) — exact
    mapping + safe replacement against an in-memory DB, injectable unlink.
  * the worker seam (_resolve_rematch_hint / _finalize_rematch_hint) — proves the
    NO-HINT path is untouched, the hint path returns a ready identification, the
    lookup is fail-safe, and finalize consumes + replaces.
"""

from __future__ import annotations

import sqlite3
import types

import pytest

from core.auto_import_worker import AutoImportWorker, FolderCandidate
from core.imports.rematch_hints import (
    RematchHint,
    build_identification_from_hint,
    consume_hint,
    create_hint,
    delete_replaced_track,
    find_hint_for_file,
)

_SCHEMA = """
CREATE TABLE rematch_hints (
    id INTEGER PRIMARY KEY AUTOINCREMENT, staged_path TEXT NOT NULL, content_hash TEXT,
    source TEXT NOT NULL, isrc TEXT, track_id TEXT, album_id TEXT, artist_id TEXT,
    track_title TEXT, album_name TEXT, artist_name TEXT, album_type TEXT,
    track_number INTEGER, disc_number INTEGER, replace_track_id INTEGER,
    exempt_dedup INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, consumed_at TIMESTAMP
);
CREATE TABLE lib2_tracks (id INTEGER PRIMARY KEY, title TEXT);
CREATE TABLE lib2_track_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT, track_id INTEGER, path TEXT,
    file_state TEXT DEFAULT 'active', is_primary INTEGER DEFAULT 1,
    primary_manual INTEGER DEFAULT 0, profile_rank INTEGER,
    format TEXT, bit_depth INTEGER, sample_rate INTEGER, bitrate INTEGER,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
"""


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    yield c
    c.close()


def _hint(**kw):
    base = dict(staged_path="/staging/Song.flac", source="spotify", album_id="alb_album1",
                artist_id="art_1", track_id="trk_1", track_title="Song", album_name="Album1",
                artist_name="Artist", album_type="album", track_number=5, disc_number=1)
    base.update(kw)
    return RematchHint(**base)


def _file(cur, track_id, path):
    cur.execute("INSERT OR IGNORE INTO lib2_tracks(id,title) VALUES(?,'Song')", (track_id,))
    cur.execute("INSERT INTO lib2_track_files(track_id,path) VALUES(?,?)", (track_id, path))


# ── pure: identification mapping ──────────────────────────────────────────────
def test_build_identification_maps_hint_fields():
    ident = build_identification_from_hint(_hint())
    assert ident["album_id"] == "alb_album1"
    assert ident["source"] == "spotify"
    assert ident["album_name"] == "Album1"
    assert ident["artist_id"] == "art_1"
    assert ident["track_number"] == 5
    assert ident["method"] == "rematch_hint"
    assert ident["identification_confidence"] == 1.0
    # album_type 'album' → not a single, and force_album_match makes the matcher
    # fetch the real album (year/track#/art) instead of the singles stub.
    assert ident["is_single"] is False
    assert ident["force_album_match"] is True


def test_build_identification_single_release_still_forces_album_fetch():
    # Even a chosen SINGLE release is fetched (it has a year too); is_single flags
    # the type, force_album_match drives the album path regardless.
    ident = build_identification_from_hint(_hint(album_type="single"))
    assert ident["is_single"] is True
    assert ident["force_album_match"] is True


# ── pure: safe replacement ────────────────────────────────────────────────────
def test_delete_replaced_track_removes_row_and_file(conn):
    cur = conn.cursor()
    _file(cur, 7, '/lib/EP1/05 - Song.flac')
    removed = []
    out = delete_replaced_track(cur, 7, unlink=lambda p: removed.append(p))
    assert out == "/lib/EP1/05 - Song.flac"
    assert removed == ["/lib/EP1/05 - Song.flac"]   # file removed (we faked existence below)
    assert cur.execute("SELECT file_state FROM lib2_track_files WHERE track_id=7").fetchone()[0] == 'deleted'
    assert cur.execute("SELECT 1 FROM lib2_tracks WHERE id=7").fetchone()  # catalogue survives


def test_delete_replaced_track_keeps_file_if_another_row_points_at_it(conn):
    cur = conn.cursor()
    _file(cur, 7, '/lib/shared.flac')
    _file(cur, 8, '/lib/shared.flac')
    removed = []
    out = delete_replaced_track(cur, 7, unlink=lambda p: removed.append(p))
    assert out is None and removed == []             # row 8 still references it → no unlink
    assert cur.execute("SELECT file_state FROM lib2_track_files WHERE track_id=7").fetchone()[0] == 'deleted'


def test_delete_replaced_track_noops_on_missing_id(conn):
    cur = conn.cursor()
    assert delete_replaced_track(cur, None) is None
    assert delete_replaced_track(cur, 999) is None    # no such row


def test_delete_replaced_track_same_home_is_noop(conn):
    # THE data-loss bug: re-identify to the release it's already in → the import
    # reuses the same file/row, so deleting it would orphan the file. Guard: no-op.
    cur = conn.cursor()
    _file(cur, 7, '/lib/Album1/05 - Song.flac')
    removed = []
    out = delete_replaced_track(cur, 7, unlink=lambda p: removed.append(p),
                                new_paths=['/lib/Album1/05 - Song.flac'])
    assert out is None and removed == []           # NOTHING unlinked
    assert cur.execute("SELECT file_state FROM lib2_track_files WHERE track_id=7").fetchone()[0] == 'active'


def test_delete_replaced_track_different_home_still_deletes(conn):
    # Genuinely re-homed (new path differs) → old row + file removed as intended.
    cur = conn.cursor()
    _file(cur, 7, '/lib/EP1/05 - Song.flac')
    removed = []
    out = delete_replaced_track(cur, 7, unlink=lambda p: removed.append(p),
                                new_paths=['/lib/Album1/05 - Song.flac'])
    assert out == '/lib/EP1/05 - Song.flac'
    assert removed == ['/lib/EP1/05 - Song.flac']
    assert cur.execute("SELECT file_state FROM lib2_track_files WHERE track_id=7").fetchone()[0] == 'deleted'


def test_delete_replaced_track_resolves_path_before_unlink(conn):
    # The stored path is a server/Docker view this process can't read literally;
    # resolve_fn maps it to the real file so we unlink the RIGHT path (not orphan it).
    cur = conn.cursor()
    _file(cur, 7, '/mnt/serverview/Song.flac')
    removed = []
    out = delete_replaced_track(cur, 7, unlink=lambda p: removed.append(p),
                                resolve_fn=lambda stored: '/real/local/Song.flac')
    assert out == '/real/local/Song.flac'
    assert removed == ['/real/local/Song.flac']      # unlinked the RESOLVED path


# patch os.path.exists so the unlink branch is reachable without real files
@pytest.fixture(autouse=True)
def _exists(monkeypatch):
    monkeypatch.setattr("core.imports.rematch_hints.os.path.exists", lambda p: True)


# ── worker seam ───────────────────────────────────────────────────────────────
def _worker(conn):
    # Production hands out a FRESH connection per call (the worker closes it);
    # here we share one in-memory DB, so proxy close() to a no-op.
    w = AutoImportWorker.__new__(AutoImportWorker)
    proxy = types.SimpleNamespace(cursor=conn.cursor, commit=conn.commit, close=lambda: None)
    w.database = types.SimpleNamespace(_get_connection=lambda: proxy)
    return w


def test_resolve_returns_none_when_no_hint(conn):
    w = _worker(conn)
    cand = FolderCandidate(path="/staging", name="Song", audio_files=["/staging/Song.flac"])
    assert w._resolve_rematch_hint(cand) == (None, None)   # untouched → normal identify


def test_resolve_returns_identification_when_hinted(conn, monkeypatch):
    # don't hash a real file
    monkeypatch.setattr("core.imports.rematch_hints.quick_file_signature", lambda p: None)
    create_hint(conn.cursor(), _hint(staged_path="/staging/Song.flac"))
    conn.commit()
    w = _worker(conn)
    cand = FolderCandidate(path="/staging", name="Song", audio_files=["/staging/Song.flac"])
    hint, ident = w._resolve_rematch_hint(cand)
    assert hint is not None and hint.album_id == "alb_album1"
    assert ident["album_id"] == "alb_album1" and ident["method"] == "rematch_hint"


def test_resolve_ignores_multi_file_candidates(conn):
    create_hint(conn.cursor(), _hint(staged_path="/staging/Song.flac"))
    conn.commit()
    w = _worker(conn)
    cand = FolderCandidate(path="/staging", name="Album",
                           audio_files=["/staging/Song.flac", "/staging/Other.flac"])
    assert w._resolve_rematch_hint(cand) == (None, None)   # re-identify is single-track only


def test_resolve_is_failsafe_on_db_error():
    w = AutoImportWorker.__new__(AutoImportWorker)
    def _boom():
        raise RuntimeError("db down")
    w.database = types.SimpleNamespace(_get_connection=_boom)
    cand = FolderCandidate(path="/staging", name="Song", audio_files=["/staging/Song.flac"])
    assert w._resolve_rematch_hint(cand) == (None, None)   # error never breaks auto-import


def test_finalize_consumes_and_replaces(conn, monkeypatch):
    monkeypatch.setattr("core.imports.rematch_hints.quick_file_signature", lambda p: None)
    cur = conn.cursor()
    _file(cur, 42, '/lib/EP1/05 - Song.flac')
    hid = create_hint(cur, _hint(replace_track_id=42))
    conn.commit()
    w = _worker(conn)
    hint = find_hint_for_file(conn.cursor(), "/staging/Song.flac")
    w._finalize_rematch_hint(hint)
    # old file retired, catalogue identity retained, hint consumed
    assert cur.execute("SELECT file_state FROM lib2_track_files WHERE track_id=42").fetchone()[0] == 'deleted'
    assert find_hint_for_file(conn.cursor(), "/staging/Song.flac") is None   # consumed


# ── a hint from 3.6.1 (third #1062 review, G06-1) ──────────────────────────────
def test_a_delete_refuses_an_id_from_another_space(conn):
    """a pending 3.6.1 hint names a Plex ratingKey; read as a lib2 id it was an
    unrelated track whose file got deleted"""
    cur = conn.cursor()
    _file(cur, 7, '/lib/unrelated.flac')
    removed = []
    assert delete_replaced_track(cur, 7, unlink=lambda p: removed.append(p), id_space=None) is None
    assert removed == []
    assert cur.execute("SELECT file_state FROM lib2_track_files WHERE track_id=7").fetchone()[0] == 'active'


def test_a_new_hint_records_that_it_names_a_lib2_track(conn):
    cur = conn.cursor()
    create_hint(cur, _hint(replace_track_id=7))
    assert cur.execute("SELECT replace_id_space FROM rematch_hints").fetchone()[0] == 'lib2'


def test_a_pending_3_6_1_hint_is_mapped_or_loses_only_its_replace(conn):
    from core.imports.rematch_hints import carry_legacy_rematch_hints, ensure_replace_id_space_column
    cur = conn.cursor()
    ensure_replace_id_space_column(cur)
    cur.execute("ALTER TABLE lib2_tracks ADD COLUMN legacy_track_id INTEGER")
    cur.execute("ALTER TABLE lib2_track_files ADD COLUMN legacy_track_id INTEGER")
    cur.execute("INSERT INTO lib2_tracks(id, title, legacy_track_id) VALUES(31, 'Song', 48213)")
    for staged, legacy in (("/staging/a.flac", 48213), ("/staging/b.flac", 99999)):
        cur.execute("INSERT INTO rematch_hints(staged_path, source, replace_track_id) VALUES(?, 'spotify', ?)",
                    (staged, legacy))
    conn.commit()

    assert carry_legacy_rematch_hints(conn) == 1

    rows = dict(cur.execute("SELECT staged_path, replace_track_id || ':' || COALESCE(replace_id_space,'-') "
                            "FROM rematch_hints").fetchall())
    assert rows == {"/staging/a.flac": "31:lib2", "/staging/b.flac": None}


@pytest.mark.parametrize("v2_ran", [True, False])
def test_a_hint_written_by_earlier_v2_code_is_never_carried_as_legacy(conn, v2_ran):
    """review 4 R4-D-1: a hint pending from library v2 code that predates the
    id-space column holds a lib2 id. read as a 3.6.1 legacy id, it mapped onto
    whichever unrelated track had that legacy id, and the import deleted it"""
    from core.imports.rematch_hints import carry_legacy_rematch_hints, ensure_replace_id_space_column
    cur = conn.cursor()
    cur.execute("ALTER TABLE lib2_tracks ADD COLUMN legacy_track_id INTEGER")
    cur.execute("ALTER TABLE lib2_track_files ADD COLUMN legacy_track_id INTEGER")
    cur.execute("INSERT INTO lib2_tracks(id, title, legacy_track_id) VALUES(31, 'Song', NULL)")
    cur.execute("INSERT INTO lib2_tracks(id, title, legacy_track_id) VALUES(77, 'Other', 31)")
    if v2_ran:
        cur.execute("CREATE TABLE lib2_bootstrap_state (id INTEGER PRIMARY KEY, status TEXT, started_at TEXT)")
        cur.execute("INSERT INTO lib2_bootstrap_state VALUES(1, 'done', '2026-10-29T00:00:00')")
    cur.execute("INSERT INTO rematch_hints(staged_path, source, replace_track_id) VALUES('/s/a.flac', 'spotify', 31)")
    conn.commit()

    ensure_replace_id_space_column(cur)
    carry_legacy_rematch_hints(conn)

    row = cur.execute("SELECT replace_track_id, replace_id_space FROM rematch_hints").fetchone()
    if v2_ran:
        # ambiguous: the import stays, the replace (and any delete) goes
        assert tuple(row) == (None, "lib2")
    else:
        # a real 3.6.1 database: the legacy id still maps
        assert tuple(row) == (77, "lib2")


# ── review 5 W2-WD-3: the replace retires the staged library's copy only ──────
@pytest.fixture
def two_library_conn():
    from core.library2.schema import ensure_library_v2_schema
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    c.execute("INSERT INTO lib2_artists(id, name) VALUES(1, 'Band')")
    c.execute("INSERT INTO lib2_albums(id, title, primary_artist_id) VALUES(1, 'Record', 1)")
    c.execute("INSERT INTO lib2_tracks(id, album_id, title) VALUES(7, 1, 'Song')")
    # profile 3's own library imported it first: its copy holds the one primary flag
    c.execute("INSERT INTO lib2_track_files(id, track_id, path, is_primary, file_state, owner_profile_id)"
              " VALUES(10, 7, '/profiles/3/Band/Record/01 Song.flac', 1, 'active', 3)")
    c.execute("INSERT INTO lib2_track_files(id, track_id, path, is_primary, file_state, owner_profile_id)"
              " VALUES(11, 7, '/music/Band/Record/01 Song.flac', 0, 'active', NULL)")
    c.commit()
    yield c
    c.close()


@pytest.mark.parametrize("staged,kept", [(11, 10), (10, 11)])
def test_replace_retires_exactly_the_staged_copy(two_library_conn, staged, kept):
    removed = []
    delete_replaced_track(two_library_conn.cursor(), 7, unlink=removed.append,
                          new_paths=["/elsewhere/01 Song.flac"], file_id=staged)
    states = dict(two_library_conn.execute("SELECT id, file_state FROM lib2_track_files").fetchall())
    assert states == {staged: 'deleted', kept: 'active'}
    path = two_library_conn.execute("SELECT path FROM lib2_track_files WHERE id=?", (staged,)).fetchone()[0]
    assert removed == [path]


def test_a_hint_without_its_file_keeps_copies_in_two_libraries(two_library_conn):
    removed = []
    out = delete_replaced_track(two_library_conn.cursor(), 7, unlink=removed.append,
                                new_paths=["/elsewhere/01 Song.flac"])
    assert out is None and removed == []
    assert {r[0] for r in two_library_conn.execute("SELECT file_state FROM lib2_track_files")} == {'active'}


def test_the_hint_keeps_the_file_it_replaces(conn):
    cur = conn.cursor()
    hint_id = create_hint(cur, _hint(replace_track_id=7, replace_file_id=11))
    assert find_hint_for_file(cur, "/staging/Song.flac").replace_file_id == 11
    assert hint_id
