"""Pin /api/auto-import/clear-completed behavior.

Reported case: Clear History button on the Import page left zombie
rows behind — every survivor showed "⧗ Processing" from 2-9 days ago.
Trace: `_record_in_progress` inserts a `status='processing'` row up-front
so the UI can render the in-flight import; `_finalize_result` updates
it to `completed`/`failed` when the import finishes. If the worker is
killed mid-import (server restart, crash), the row never gets finalized
and stays at `processing` forever. The endpoint's SQL delete-list
omitted `processing`, so zombies survived every click.

Fix added `processing` to the delete list, BUT guards against nuking
genuinely-live imports by intersecting against the worker's
`_snapshot_active()` map — any folder hash currently registered there
is excluded from the delete.

These tests pin:
- `processing` rows ARE swept (no longer zombies)
- Live `processing` rows (folder hash currently in `_active_imports`) survive
- `pending_review` survives (user still must approve/reject)
- `completed` / `approved` / `failed` / `needs_identification` /
  `rejected` rows still get swept (unchanged contract)
- Count returned in the JSON response matches the actual delete count
- Empty active set falls through to the unparameterized DELETE
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app_test_client():
    import web_server
    web_server.app.config['TESTING'] = True
    with web_server.app.test_client() as client:
        yield client


@pytest.fixture
def seeded_db(tmp_path):
    """Real sqlite DB with the auto_import_history table populated by
    a mix of statuses + folder hashes. Returns a (connection_factory,
    rows_seeded) tuple. The factory is a `_get_connection` lookalike
    that returns the same connection so the endpoint sees the same data."""
    db_path = str(tmp_path / 'test.db')
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE auto_import_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            folder_name TEXT NOT NULL,
            folder_path TEXT NOT NULL,
            folder_hash TEXT,
            status TEXT NOT NULL DEFAULT 'scanning',
            confidence REAL DEFAULT 0.0,
            album_id TEXT,
            album_name TEXT,
            artist_name TEXT,
            image_url TEXT,
            total_files INTEGER DEFAULT 0,
            matched_files INTEGER DEFAULT 0,
            match_data TEXT,
            identification_method TEXT,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP
        )
    """)
    seeds = [
        # (folder_name, folder_hash, status)
        ('Album A',           'hash-completed-1',  'completed'),
        ('Album B',           'hash-approved-1',   'approved'),
        ('Album C',           'hash-failed-1',     'failed'),
        ('Album D',           'hash-needsid-1',    'needs_identification'),
        ('Album E',           'hash-rejected-1',   'rejected'),
        ('Zombie F',          'hash-zombie-1',     'processing'),  # stale
        ('Zombie G',          'hash-zombie-2',     'processing'),  # stale
        ('Live Import H',     'hash-LIVE',         'processing'),  # active — must survive
        ('Awaiting Review I', 'hash-review-1',     'pending_review'),  # must survive
    ]
    for name, fh, status in seeds:
        cursor.execute(
            "INSERT INTO auto_import_history (folder_name, folder_path, folder_hash, status) "
            "VALUES (?, ?, ?, ?)",
            (name, f"/staging/{name}", fh, status),
        )
    conn.commit()

    # Build a fake DB object whose `_get_connection` returns a context
    # manager wrapping the live connection (matches the endpoint's
    # `with db._get_connection() as conn` usage).
    @contextmanager
    def _conn_cm():
        yield conn

    fake_db = MagicMock()
    fake_db._get_connection = _conn_cm

    return fake_db, conn


def _statuses_remaining(conn):
    cur = conn.cursor()
    cur.execute("SELECT folder_name, status FROM auto_import_history ORDER BY id")
    return [(row['folder_name'], row['status']) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClearCompletedEndpoint:
    def test_sweeps_zombie_processing_rows_but_keeps_live(self, app_test_client, seeded_db, monkeypatch):
        """The bug: a `processing` row that's been there for days is a
        zombie (server restart killed `_finalize_result`). Endpoint must
        sweep it. But a `processing` row whose folder_hash is currently
        registered in `_active_imports` is a LIVE import — must survive
        or the UI loses its in-flight row mid-run."""
        fake_db, conn = seeded_db
        # Worker reports one live import — folder_hash hash-LIVE
        fake_worker = MagicMock()
        fake_worker._snapshot_active.return_value = [{'folder_hash': 'hash-LIVE'}]
        monkeypatch.setattr('web_server.auto_import_worker', fake_worker)
        monkeypatch.setattr('web_server.get_database', lambda: fake_db)

        resp = app_test_client.post('/api/auto-import/clear-completed')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True

        survivors = _statuses_remaining(conn)
        names = {n for n, _ in survivors}
        # Live import survives
        assert 'Live Import H' in names, (
            f"Live in-flight processing row was deleted; survivors={names}"
        )
        # Pending review survives — user still must approve/reject
        assert 'Awaiting Review I' in names
        # Both zombies swept
        assert 'Zombie F' not in names
        assert 'Zombie G' not in names
        # Standard terminal-status rows swept
        for completed_name in ('Album A', 'Album B', 'Album C', 'Album D', 'Album E'):
            assert completed_name not in names, f"{completed_name} should have been deleted"

    def test_count_in_response_matches_actual_deletes(self, app_test_client, seeded_db, monkeypatch):
        """JSON response carries the rowcount so the UI toast can show
        accurate `Cleared N items`."""
        fake_db, conn = seeded_db
        fake_worker = MagicMock()
        fake_worker._snapshot_active.return_value = [{'folder_hash': 'hash-LIVE'}]
        monkeypatch.setattr('web_server.auto_import_worker', fake_worker)
        monkeypatch.setattr('web_server.get_database', lambda: fake_db)

        resp = app_test_client.post('/api/auto-import/clear-completed')
        body = resp.get_json()
        # 9 rows seeded; 7 deletable (5 terminal + 2 zombie processing);
        # 2 survive (1 live, 1 pending_review)
        assert body['count'] == 7, f"Expected 7 deletes; got {body['count']}"
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM auto_import_history")
        assert cur.fetchone()[0] == 2

    def test_empty_active_set_takes_unparameterized_path(self, app_test_client, seeded_db, monkeypatch):
        """When no live imports are running, the SQL skips the `AND
        folder_hash NOT IN (...)` clause. Pinned because an empty
        `IN ()` is a SQL syntax error in sqlite — the branch matters."""
        fake_db, conn = seeded_db
        # Remove the live row so all `processing` rows are zombies
        cur = conn.cursor()
        cur.execute("DELETE FROM auto_import_history WHERE folder_hash = ?", ('hash-LIVE',))
        conn.commit()

        fake_worker = MagicMock()
        fake_worker._snapshot_active.return_value = []  # nothing active
        monkeypatch.setattr('web_server.auto_import_worker', fake_worker)
        monkeypatch.setattr('web_server.get_database', lambda: fake_db)

        resp = app_test_client.post('/api/auto-import/clear-completed')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        # 5 terminal + 2 zombie processing = 7. Pending_review (1) survives.
        assert body['count'] == 7

        survivors = _statuses_remaining(conn)
        assert len(survivors) == 1
        assert survivors[0][1] == 'pending_review'

    def test_worker_unavailable_returns_500(self, app_test_client, monkeypatch):
        """If the auto-import worker isn't initialised, the endpoint
        bails early — no DB access, clear error. Pre-fix this branch
        was already in place; pinning ensures the active-hash refactor
        didn't accidentally start touching the worker before the guard."""
        monkeypatch.setattr('web_server.auto_import_worker', None)
        resp = app_test_client.post('/api/auto-import/clear-completed')
        assert resp.status_code == 500
        body = resp.get_json()
        assert body['success'] is False
        assert 'not available' in body['error'].lower()

    def test_pending_review_always_survives(self, app_test_client, seeded_db, monkeypatch):
        """Specific pin for the deliberate `pending_review` exclusion.
        Even when no imports are active and every other status is being
        swept, `pending_review` rows must be left alone — user-action
        required, not automatic cleanup."""
        fake_db, conn = seeded_db
        fake_worker = MagicMock()
        fake_worker._snapshot_active.return_value = []
        monkeypatch.setattr('web_server.auto_import_worker', fake_worker)
        monkeypatch.setattr('web_server.get_database', lambda: fake_db)

        app_test_client.post('/api/auto-import/clear-completed')

        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM auto_import_history WHERE status = 'pending_review'")
        assert cur.fetchone()[0] == 1, (
            "pending_review rows must never be swept by clear-completed"
        )
