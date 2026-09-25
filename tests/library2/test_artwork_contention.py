"""Regression tests for the §27 artwork-contention findings (dd28-01/03/04/24).

The user-visible symptom these all feed was "changing an artist photo usually
works, but sometimes throws an API error".  Each test here pins one of the
contributing causes rather than the symptom:

* dd28-03 — the override store re-ran its schema DDL (and so took SQLite's
  write lock) on every single override write;
* dd28-04 — the apply path opened its write transaction *before* waiting on the
  per-entity artwork build lock, so the whole app's DB write lock could end up
  queued behind a network-bound provider walk;
* dd28-24 — concurrent thumbnail writers shared one fixed staging filename;
* the provider walk itself had no budget, so the lock's hold time was unbounded.
"""

from __future__ import annotations

import threading
import time
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from core.library2 import artwork
from core.library2 import metadata_overrides
from core.library2.metadata_overrides import (
    ensure_metadata_overrides_schema,
    set_field_override,
)


def _image_bytes(color=(255, 0, 0)) -> bytes:
    image = Image.new("RGB", (4, 3), color)
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _art_db(legacy_db):
    return SimpleNamespace(database_path=legacy_db.path)


@pytest.fixture
def artist_id(imported_conn):
    return imported_conn.execute("SELECT id FROM lib2_artists LIMIT 1").fetchone()[0]


class _RecordingCursor:
    """Wraps a real cursor and records every statement it is asked to run."""

    def __init__(self, cursor, statements):
        self._cursor = cursor
        self._statements = statements

    def execute(self, sql, *args, **kwargs):
        self._statements.append(" ".join(str(sql).split()))
        return self._cursor.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


def test_schema_ensure_is_read_only_once_the_schema_exists(imported_conn):
    """dd28-03: the steady state must not touch the write lock at all."""
    ensure_metadata_overrides_schema(imported_conn.cursor())
    imported_conn.commit()

    statements: list[str] = []
    ensure_metadata_overrides_schema(
        _RecordingCursor(imported_conn.cursor(), statements)
    )

    assert statements, "the fast path still has to read sqlite_master"
    for sql in statements:
        upper = sql.upper()
        assert upper.startswith("SELECT"), f"schema ensure issued a write: {sql}"


def test_schema_ensure_still_creates_missing_objects(imported_conn):
    """The fast path must not skip a genuinely incomplete schema."""
    imported_conn.execute(
        "DROP TRIGGER IF EXISTS trg_lib2_artists_metadata_overrides_delete"
    )
    imported_conn.commit()

    ensure_metadata_overrides_schema(imported_conn.cursor())
    imported_conn.commit()

    assert imported_conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=?",
        ("trg_lib2_artists_metadata_overrides_delete",),
    ).fetchone() is not None


def test_schema_ensure_recreates_a_dropped_table(imported_conn):
    imported_conn.execute("DROP TABLE IF EXISTS lib2_metadata_overrides")
    imported_conn.commit()

    ensure_metadata_overrides_schema(imported_conn.cursor())
    imported_conn.commit()

    assert imported_conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lib2_metadata_overrides'"
    ).fetchone() is not None


def test_apply_takes_the_build_lock_before_opening_the_write_transaction(
    imported_conn, legacy_db, monkeypatch, artist_id,
):
    """dd28-04: no DB write may be issued while still queued on the build lock."""
    order: list[str] = []

    real_lock = artwork._build_lock

    from contextlib import contextmanager

    @contextmanager
    def _tracking_lock(database, kind, entity_id, **kwargs):
        order.append("lock-acquired")
        with real_lock(database, kind, entity_id, **kwargs) as acquired:
            yield acquired

    real_set_override = metadata_overrides.set_field_override

    def _tracking_set_override(*args, **kwargs):
        order.append("override-written")
        return real_set_override(*args, **kwargs)

    monkeypatch.setattr(artwork, "_build_lock", _tracking_lock)
    monkeypatch.setattr(
        metadata_overrides, "set_field_override", _tracking_set_override
    )
    monkeypatch.setattr(
        artwork, "_download_remote_artwork", lambda _url: _image_bytes((1, 2, 3))
    )

    applied = artwork.apply_manual_artwork(
        _art_db(legacy_db), imported_conn, "artist", artist_id,
        "https://example.com/photo.jpg",
    )

    assert applied is True
    assert order == ["lock-acquired", "override-written"]


def test_apply_does_not_block_a_concurrent_writer_while_queued(
    imported_conn, legacy_db, monkeypatch, artist_id,
):
    """dd28-04, the consequence: a queued apply leaves the DB writable.

    A second connection must be able to commit a write while the apply is still
    waiting for a build that holds the artwork lock.  Before the reorder the
    apply had already opened its write transaction, so this second write hit
    ``database is locked``.
    """
    database = _art_db(legacy_db)
    monkeypatch.setattr(
        artwork, "_download_remote_artwork", lambda _url: _image_bytes((4, 5, 6))
    )

    holder_has_lock = threading.Event()
    holder_may_release = threading.Event()

    def _hold_the_build_lock():
        with artwork._build_lock(database, "artist", artist_id):
            holder_has_lock.set()
            holder_may_release.wait(10)

    holder = threading.Thread(target=_hold_the_build_lock, daemon=True)
    holder.start()
    assert holder_has_lock.wait(10)

    apply_done = threading.Event()

    def _apply():
        import sqlite3 as _sqlite3

        # A route runs on its own worker thread with its own connection.
        conn = _sqlite3.connect(legacy_db.path, timeout=10)
        conn.row_factory = _sqlite3.Row
        try:
            artwork.apply_manual_artwork(
                database, conn, "artist", artist_id,
                "https://example.com/photo.jpg",
            )
            conn.commit()
        finally:
            conn.close()
            apply_done.set()

    applier = threading.Thread(target=_apply, daemon=True)
    applier.start()
    # Give the applier time to reach (and block on) the build lock.
    time.sleep(0.2)
    assert not apply_done.is_set(), "apply should still be queued on the build lock"

    import sqlite3

    other = sqlite3.connect(legacy_db.path, timeout=2)
    try:
        other.execute(
            "UPDATE lib2_artists SET sort_name = 'contention probe' WHERE id=?",
            (artist_id,),
        )
        other.commit()
    finally:
        other.close()

    holder_may_release.set()
    assert apply_done.wait(10)
    holder.join(10)
    applier.join(10)


def test_unique_tmp_paths_do_not_collide_between_writers():
    """dd28-24: two writers for the same destination need distinct staging files."""
    from pathlib import Path

    dst = Path("/tmp/lib2-artwork-probe/artist_1_t.jpg")
    first = artwork._unique_tmp_path(dst)
    second = artwork._unique_tmp_path(dst)

    assert first != second
    assert first.parent == dst.parent
    assert first.name.endswith(".tmp")


def test_provider_walk_stops_at_its_deadline(monkeypatch):
    """An expired budget must end the walk instead of querying more providers."""
    from core.library2 import provider_adapters

    queried: list[str] = []

    def _fake_get_artist_image_url(provider_id, source_override=None, artist_name=""):
        queried.append(source_override)
        return None

    import core.metadata.artist_image as artist_image

    monkeypatch.setattr(
        artist_image, "get_artist_image_url", _fake_get_artist_image_url
    )
    monkeypatch.setattr(
        provider_adapters, "_configured_source_order",
        lambda: ("spotify", "deezer", "itunes"),
    )

    result = provider_adapters.fetch_artwork_url(
        "artist",
        artist_name="Any",
        source_ids={"spotify": "a", "deezer": "b", "itunes": "c"},
        deadline=time.monotonic() - 1,
    )

    assert result is None
    assert queried == [], "no provider may be queried once the budget is gone"
