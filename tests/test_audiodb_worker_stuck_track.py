"""Pin AudioDB worker doesn't infinite-loop on direct-ID-lookup
failures.

Issue #553: when an entity already has `audiodb_id` populated
(from manual match or earlier scan) but `audiodb_match_status` is
NULL, the worker tries a direct ID lookup. If that lookup fails
(returns None on timeout — AudioDB's `track.php` endpoint is slow
and 10s timeouts are common), the prior code returned WITHOUT
marking status. Result: row stayed in NULL state, queue picked it
up next tick, retried, timed out, returned again — infinite loop.
User saw constant requests with no progress.

The fix:
  - Mark status='error' so the queue's NULL-status filter stops
    picking the row on every tick
  - Add 'error' to the retry-after-cutoff queries (priorities 4-6)
    so transient AudioDB outages still recover automatically after
    `retry_days`
  - Preserve the existing `audiodb_id` (don't overwrite it via
    name-search fallback — original "preserve manual match" intent)

These tests pin:
  - Direct-lookup-returns-None marks status='error' (no infinite loop)
  - Direct-lookup-raises-exception marks status='error'
  - Direct-lookup-success preserves existing match-success path
  - 'error' status is included in retry-cutoff queue so eventual
    recovery happens
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.audiodb_worker import AudioDBWorker


def _make_real_db_with_audiodb_columns(tmp_path):
    """Build a minimal SQLite DB with the artist/album/track schema
    the worker needs. Real SQLite (not mocks) so the SQL queries
    actually exercise the column names + retry-cutoff logic."""
    db_path = tmp_path / "audiodb_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE artists (
            id INTEGER PRIMARY KEY,
            name TEXT,
            audiodb_id TEXT,
            audiodb_match_status TEXT,
            audiodb_last_attempted DATETIME,
            updated_at DATETIME
        );
        CREATE TABLE albums (
            id INTEGER PRIMARY KEY,
            title TEXT,
            artist_id INTEGER,
            audiodb_id TEXT,
            audiodb_match_status TEXT,
            audiodb_last_attempted DATETIME,
            updated_at DATETIME
        );
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY,
            title TEXT,
            artist_id INTEGER,
            audiodb_id TEXT,
            audiodb_match_status TEXT,
            audiodb_last_attempted DATETIME,
            updated_at DATETIME
        );
    """)
    conn.commit()
    conn.close()

    class _RealDB:
        def _get_connection(self):
            return sqlite3.connect(str(db_path))

    return _RealDB(), db_path


def _make_worker(db, fake_client):
    """Build a worker with a real DB + mocked AudioDB client.
    Skip __init__ side effects (config load, thread start)."""
    worker = AudioDBWorker.__new__(AudioDBWorker)
    worker.db = db
    worker.client = fake_client
    worker.retry_days = 30
    worker.stats = {'matched': 0, 'not_found': 0, 'errors': 0, 'pending': 0}
    worker.current_item = None
    worker.running = False
    worker.paused = False
    worker.thread = None
    return worker


# ---------------------------------------------------------------------------
# Issue #553 — direct-ID lookup failure no longer infinite-loops
# ---------------------------------------------------------------------------


class TestDirectLookupFailureMarksError:
    def test_lookup_returns_none_marks_status_error(self, tmp_path):
        """Reporter's exact scenario: track has audiodb_id set,
        match_status is NULL. AudioDB times out → lookup returns None.
        Pre-fix: return without marking → infinite loop next tick.
        Post-fix: mark status='error' → queue stops re-picking."""
        db, db_path = _make_real_db_with_audiodb_columns(tmp_path)

        # Seed a track with audiodb_id populated, status NULL
        with sqlite3.connect(str(db_path)) as seed_conn:
            seed_conn.execute(
                "INSERT INTO artists (id, name) VALUES (?, ?)",
                (1, 'Test Artist'),
            )
            seed_conn.execute(
                "INSERT INTO tracks (id, title, artist_id, audiodb_id, audiodb_match_status) "
                "VALUES (?, ?, ?, ?, ?)",
                (32743988, 'Sweet Talk', 1, '12345', None),
            )
            seed_conn.commit()

        # AudioDB client returns None on timeout (matches lookup_track_by_id behavior)
        fake_client = SimpleNamespace(
            lookup_artist_by_id=MagicMock(return_value=None),
            lookup_album_by_id=MagicMock(return_value=None),
            lookup_track_by_id=MagicMock(return_value=None),
        )

        worker = _make_worker(db, fake_client)
        item = {
            'type': 'track',
            'id': 32743988,
            'name': 'Sweet Talk',
            'artist': 'Test Artist',
            'artist_audiodb_id': None,
        }

        worker._process_item(item)

        # Verify status was marked (no longer NULL → queue won't re-pick)
        with sqlite3.connect(str(db_path)) as verify:
            row = verify.execute(
                "SELECT audiodb_match_status, audiodb_id, audiodb_last_attempted "
                "FROM tracks WHERE id = ?",
                (32743988,),
            ).fetchone()

        assert row[0] == 'error', f"Expected status='error' to break loop; got {row[0]!r}"
        # audiodb_id preserved (manual match not overwritten)
        assert row[1] == '12345', f"audiodb_id must NOT be cleared; got {row[1]!r}"
        # last_attempted set so retry-cutoff logic can re-pick later
        assert row[2] is not None, "audiodb_last_attempted must be set for retry logic"
        # Stats updated
        assert worker.stats['errors'] == 1

    def test_lookup_raises_exception_marks_status_error(self, tmp_path):
        """Defensive: if the AudioDB client itself raises (not just
        returns None) the same loop-protection must apply. Some
        client paths re-raise on certain error classes."""
        db, db_path = _make_real_db_with_audiodb_columns(tmp_path)

        with sqlite3.connect(str(db_path)) as seed_conn:
            seed_conn.execute(
                "INSERT INTO artists (id, name) VALUES (?, ?)",
                (1, 'X'),
            )
            seed_conn.execute(
                "INSERT INTO tracks (id, title, artist_id, audiodb_id, audiodb_match_status) "
                "VALUES (?, ?, ?, ?, ?)",
                (99, 'Y', 1, '67890', None),
            )
            seed_conn.commit()

        fake_client = SimpleNamespace(
            lookup_artist_by_id=MagicMock(side_effect=RuntimeError("boom")),
            lookup_album_by_id=MagicMock(side_effect=RuntimeError("boom")),
            lookup_track_by_id=MagicMock(side_effect=RuntimeError("read timeout")),
        )

        worker = _make_worker(db, fake_client)
        item = {'type': 'track', 'id': 99, 'name': 'Y', 'artist': 'X',
                'artist_audiodb_id': None}

        worker._process_item(item)

        with sqlite3.connect(str(db_path)) as verify:
            row = verify.execute(
                "SELECT audiodb_match_status FROM tracks WHERE id = ?",
                (99,),
            ).fetchone()

        assert row[0] == 'error'

    def test_lookup_success_preserves_existing_path(self, tmp_path):
        """Sanity: when direct lookup SUCCEEDS, the existing match-
        success path runs (update + stats['matched'] += 1). Don't
        regress the happy path."""
        db, db_path = _make_real_db_with_audiodb_columns(tmp_path)

        with sqlite3.connect(str(db_path)) as seed_conn:
            seed_conn.execute("INSERT INTO artists (id, name) VALUES (?, ?)", (1, 'A'))
            seed_conn.execute(
                "INSERT INTO tracks (id, title, artist_id, audiodb_id) "
                "VALUES (?, ?, ?, ?)",
                (50, 'T', 1, '111'),
            )
            seed_conn.commit()

        fake_client = SimpleNamespace(
            lookup_artist_by_id=MagicMock(),
            lookup_album_by_id=MagicMock(),
            lookup_track_by_id=MagicMock(return_value={
                'idTrack': '111',
                'strTrack': 'T',
                'idArtist': '999',
            }),
        )

        worker = _make_worker(db, fake_client)
        # Stub the per-entity update method so we don't need every column
        worker._update_track = MagicMock()
        worker._verify_artist_id = MagicMock(return_value=True)

        item = {'type': 'track', 'id': 50, 'name': 'T', 'artist': 'A',
                'artist_audiodb_id': None}
        worker._process_item(item)

        worker._update_track.assert_called_once()
        assert worker.stats['matched'] == 1
        assert worker.stats['errors'] == 0


# ---------------------------------------------------------------------------
# Retry queue includes 'error' status — transient outages eventually recover
# ---------------------------------------------------------------------------


class TestErrorRetryAfterCutoff:
    def test_error_track_picked_up_after_cutoff(self, tmp_path):
        """After fix #553, rows marked 'error' get a 30-day retry
        cutoff — same treatment as 'not_found'. Without this they'd
        stay errored forever after a transient AudioDB outage."""
        db, db_path = _make_real_db_with_audiodb_columns(tmp_path)

        # Seed a track marked 'error' with last_attempted older than retry_days.
        # Artist must be marked 'matched' too — otherwise priority 1 (NULL-status
        # artists) wins over priority 6 (error/not_found track retry).
        old_attempt = datetime.now() - timedelta(days=31)
        with sqlite3.connect(str(db_path)) as seed_conn:
            seed_conn.execute(
                "INSERT INTO artists (id, name, audiodb_match_status) VALUES (?, ?, ?)",
                (1, 'A', 'matched'),
            )
            seed_conn.execute(
                "INSERT INTO tracks (id, title, artist_id, audiodb_match_status, audiodb_last_attempted) "
                "VALUES (?, ?, ?, ?, ?)",
                (10, 'OldErrored', 1, 'error', old_attempt),
            )
            seed_conn.commit()

        fake_client = SimpleNamespace()  # not called for queue check
        worker = _make_worker(db, fake_client)

        item = worker._get_next_item()
        assert item is not None, "Expected error-status track past retry cutoff to be picked up"
        assert item['type'] == 'track'
        assert item['id'] == 10

    def test_error_track_NOT_picked_within_cutoff(self, tmp_path):
        """Sanity: rows marked 'error' but recently-attempted should
        NOT be picked. Otherwise the retry-cutoff doesn't actually
        rate-limit retries and we're back to the loop."""
        db, db_path = _make_real_db_with_audiodb_columns(tmp_path)

        # Just-attempted (within cutoff). Artist marked matched
        # so priority 1 doesn't intercept the queue check.
        recent_attempt = datetime.now() - timedelta(days=1)
        with sqlite3.connect(str(db_path)) as seed_conn:
            seed_conn.execute(
                "INSERT INTO artists (id, name, audiodb_match_status) VALUES (?, ?, ?)",
                (1, 'A', 'matched'),
            )
            seed_conn.execute(
                "INSERT INTO tracks (id, title, artist_id, audiodb_match_status, audiodb_last_attempted) "
                "VALUES (?, ?, ?, ?, ?)",
                (20, 'RecentErrored', 1, 'error', recent_attempt),
            )
            seed_conn.commit()

        worker = _make_worker(db, SimpleNamespace())
        item = worker._get_next_item()
        assert item is None, (
            "Recently-attempted error rows must NOT be picked up — that's "
            "the loop-prevention mechanism"
        )
