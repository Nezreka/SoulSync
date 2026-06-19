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
CREATE TABLE tracks (
    id INTEGER PRIMARY KEY, album_id INTEGER, artist_id INTEGER, title TEXT,
    track_number INTEGER, file_path TEXT
);
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
    cur.execute("INSERT INTO tracks (id, file_path) VALUES (7, '/lib/EP1/05 - Song.flac')")
    removed = []
    out = delete_replaced_track(cur, 7, unlink=lambda p: removed.append(p))
    assert out == "/lib/EP1/05 - Song.flac"
    assert removed == ["/lib/EP1/05 - Song.flac"]   # file removed (we faked existence below)
    cur.execute("SELECT 1 FROM tracks WHERE id = 7")
    assert cur.fetchone() is None                    # row gone


def test_delete_replaced_track_keeps_file_if_another_row_points_at_it(conn):
    cur = conn.cursor()
    cur.execute("INSERT INTO tracks (id, file_path) VALUES (7, '/lib/shared.flac')")
    cur.execute("INSERT INTO tracks (id, file_path) VALUES (8, '/lib/shared.flac')")
    removed = []
    out = delete_replaced_track(cur, 7, unlink=lambda p: removed.append(p))
    assert out is None and removed == []             # row 8 still references it → no unlink
    cur.execute("SELECT 1 FROM tracks WHERE id = 7")
    assert cur.fetchone() is None                     # but row 7 still deleted


def test_delete_replaced_track_noops_on_missing_id(conn):
    cur = conn.cursor()
    assert delete_replaced_track(cur, None) is None
    assert delete_replaced_track(cur, 999) is None    # no such row


def test_delete_replaced_track_same_home_is_noop(conn):
    # THE data-loss bug: re-identify to the release it's already in → the import
    # reuses the same file/row, so deleting it would orphan the file. Guard: no-op.
    cur = conn.cursor()
    cur.execute("INSERT INTO tracks (id, file_path) VALUES (7, '/lib/Album1/05 - Song.flac')")
    removed = []
    out = delete_replaced_track(cur, 7, unlink=lambda p: removed.append(p),
                                new_paths=['/lib/Album1/05 - Song.flac'])
    assert out is None and removed == []           # NOTHING unlinked
    cur.execute("SELECT 1 FROM tracks WHERE id = 7")
    assert cur.fetchone() is not None              # row PRESERVED (it's the re-imported track)


def test_delete_replaced_track_different_home_still_deletes(conn):
    # Genuinely re-homed (new path differs) → old row + file removed as intended.
    cur = conn.cursor()
    cur.execute("INSERT INTO tracks (id, file_path) VALUES (7, '/lib/EP1/05 - Song.flac')")
    removed = []
    out = delete_replaced_track(cur, 7, unlink=lambda p: removed.append(p),
                                new_paths=['/lib/Album1/05 - Song.flac'])
    assert out == '/lib/EP1/05 - Song.flac'
    assert removed == ['/lib/EP1/05 - Song.flac']
    cur.execute("SELECT 1 FROM tracks WHERE id = 7")
    assert cur.fetchone() is None


def test_delete_replaced_track_resolves_path_before_unlink(conn):
    # The stored path is a server/Docker view this process can't read literally;
    # resolve_fn maps it to the real file so we unlink the RIGHT path (not orphan it).
    cur = conn.cursor()
    cur.execute("INSERT INTO tracks (id, file_path) VALUES (7, '/mnt/serverview/Song.flac')")
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
    cur.execute("INSERT INTO tracks (id, file_path) VALUES (42, '/lib/EP1/05 - Song.flac')")
    hid = create_hint(cur, _hint(replace_track_id=42))
    conn.commit()
    w = _worker(conn)
    hint = find_hint_for_file(conn.cursor(), "/staging/Song.flac")
    w._finalize_rematch_hint(hint)
    # old row deleted, hint consumed
    cur.execute("SELECT 1 FROM tracks WHERE id = 42")
    assert cur.fetchone() is None
    assert find_hint_for_file(conn.cursor(), "/staging/Song.flac") is None   # consumed
