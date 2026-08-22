"""Tests for ``core.enrichment.manual_match_honoring.honor_stored_match``.

The helper is the shared "fast-path enrichment via stored source ID"
used by every per-source enrichment worker (Spotify / iTunes / Deezer
/ Discogs / MusicBrainz / AudioDB / Tidal / Qobuz). It reads the
stored ID from a configurable column, fetches via a caller-supplied
client method, and invokes a caller-supplied update callback. Pin
the contract so per-worker wiring can rely on uniform semantics.

Issue #501: enrichment workers were running fuzzy name search and
overwriting manually-set source IDs. This helper is the lift point —
all 8 workers will plug in the same way.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from core.enrichment.manual_match_honoring import (
    MATCHED, NO_STORED_ID, UNAVAILABLE, honor_stored_match,
)


# ---------------------------------------------------------------------------
# Fake DB fixture (just enough to exercise _read_id_column)
# ---------------------------------------------------------------------------


class _FakeConn:
    """Minimal sqlite3.Connection-like that the helper can use."""

    def __init__(self, real_conn):
        self._real = real_conn

    def cursor(self):
        return self._real.cursor()

    def close(self):
        # Don't actually close — tests share the connection.
        pass


class _FakeDB:
    """Stand-in MusicDatabase. Supports ``_get_connection()`` returning
    a wrapper that doesn't close, so per-test in-memory state survives
    across helper invocations."""

    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.cursor()
        cur.execute("""
            CREATE TABLE albums (
                id INTEGER PRIMARY KEY,
                spotify_album_id TEXT,
                deezer_id TEXT,
                itunes_album_id TEXT,
                spotify_match_status TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY,
                spotify_track_id TEXT,
                deezer_id TEXT
            )
        """)
        self._conn.commit()

    def insert_album(self, album_id, **id_columns):
        cols = ['id'] + list(id_columns.keys())
        placeholders = ','.join('?' for _ in cols)
        values = [album_id] + list(id_columns.values())
        self._conn.execute(
            f"INSERT INTO albums ({','.join(cols)}) VALUES ({placeholders})",
            values,
        )
        self._conn.commit()

    def insert_track(self, track_id, **id_columns):
        cols = ['id'] + list(id_columns.keys())
        placeholders = ','.join('?' for _ in cols)
        values = [track_id] + list(id_columns.values())
        self._conn.execute(
            f"INSERT INTO tracks ({','.join(cols)}) VALUES ({placeholders})",
            values,
        )
        self._conn.commit()

    def _get_connection(self):
        return _FakeConn(self._conn)


@pytest.fixture
def db():
    return _FakeDB()


# ---------------------------------------------------------------------------
# Stored-ID fast path (the new behavior)
# ---------------------------------------------------------------------------


def test_honors_stored_id_when_present(db):
    """Pin: stored ID found → fetch → on_match called → returns True.
    Caller skips its search-by-name flow."""
    db.insert_album(42, spotify_album_id='SP-ABC')
    api_payload = {'id': 'SP-ABC', 'name': 'Real Album'}
    fetch = MagicMock(return_value=api_payload)
    on_match = MagicMock()

    result = honor_stored_match(
        db=db, entity_table='albums', entity_id=42,
        id_column='spotify_album_id',
        client_fetch_fn=fetch, on_match_fn=on_match,
        log_prefix='Spotify',
    )

    assert result == MATCHED
    fetch.assert_called_once_with('SP-ABC')
    on_match.assert_called_once_with(42, 'SP-ABC', api_payload)


def test_returns_no_stored_id_when_no_stored_id(db):
    """Pin: no stored ID → returns False, no fetch attempted, no
    callback. Caller proceeds with its search-by-name fallback."""
    db.insert_album(42, spotify_album_id=None)
    fetch = MagicMock()
    on_match = MagicMock()

    result = honor_stored_match(
        db=db, entity_table='albums', entity_id=42,
        id_column='spotify_album_id',
        client_fetch_fn=fetch, on_match_fn=on_match,
        log_prefix='Spotify',
    )

    assert result == NO_STORED_ID
    fetch.assert_not_called()
    on_match.assert_not_called()


def test_returns_no_stored_id_when_stored_id_empty_string(db):
    """Pin: empty string treated same as NULL — no fetch, return False."""
    db.insert_album(42, spotify_album_id='')
    fetch = MagicMock()
    on_match = MagicMock()

    result = honor_stored_match(
        db=db, entity_table='albums', entity_id=42,
        id_column='spotify_album_id',
        client_fetch_fn=fetch, on_match_fn=on_match,
    )

    assert result == NO_STORED_ID
    fetch.assert_not_called()


def test_returns_no_stored_id_when_entity_not_in_db(db):
    """Pin: missing row → returns False, no fetch, no callback."""
    fetch = MagicMock()
    on_match = MagicMock()

    result = honor_stored_match(
        db=db, entity_table='albums', entity_id=999,
        id_column='spotify_album_id',
        client_fetch_fn=fetch, on_match_fn=on_match,
    )

    assert result == NO_STORED_ID
    fetch.assert_not_called()


# ---------------------------------------------------------------------------
# Failure paths — fall through to search instead of crashing the worker
# ---------------------------------------------------------------------------


def test_a_fetch_failure_keeps_the_stored_id_instead_of_searching(db):
    """L2-005: a transient API failure is not evidence that the stored ID is
    wrong. Returning "no stored id" sent the worker into a fuzzy name search,
    which then overwrote a deliberately chosen match with whatever came back.

    The failure is recorded as ``error`` (with its retry timestamp) so the
    entity is not re-picked on the very next cycle with no backoff."""
    db.insert_album(42, spotify_album_id='SP-ABC')
    fetch = MagicMock(side_effect=RuntimeError("API down"))
    on_match = MagicMock()
    marked = []

    result = honor_stored_match(
        db=db, entity_table='albums', entity_id=42,
        id_column='spotify_album_id',
        client_fetch_fn=fetch, on_match_fn=on_match,
        mark_status_fn=lambda kind, eid, status: marked.append((kind, eid, status)),
    )

    assert result == UNAVAILABLE
    assert result, "must be truthy so the caller skips search-by-name"
    assert marked == [('album', 42, 'error')]
    on_match.assert_not_called()


def test_a_broken_status_writer_does_not_break_the_refresh(db):
    db.insert_album(42, spotify_album_id='SP-ABC')

    def _boom(*_a):
        raise RuntimeError("db locked")

    result = honor_stored_match(
        db=db, entity_table='albums', entity_id=42,
        id_column='spotify_album_id',
        client_fetch_fn=MagicMock(side_effect=RuntimeError("API down")),
        on_match_fn=MagicMock(), mark_status_fn=_boom,
    )

    assert result == UNAVAILABLE


def test_an_empty_fetch_also_keeps_the_stored_id(db):
    """A source that answers "I have nothing for this id" is indistinguishable
    from one that is having a bad day, and both cases used to release the id to
    a name search. Releasing it takes an explicit re-match."""
    db.insert_album(42, spotify_album_id='SP-DEAD')
    fetch = MagicMock(return_value=None)
    on_match = MagicMock()

    result = honor_stored_match(
        db=db, entity_table='albums', entity_id=42,
        id_column='spotify_album_id',
        client_fetch_fn=fetch, on_match_fn=on_match,
    )

    assert result == UNAVAILABLE
    on_match.assert_not_called()


def test_an_empty_dict_response_also_keeps_the_stored_id(db):
    """Pin: empty dict treated same as None — falsy result skips
    callback."""
    db.insert_album(42, spotify_album_id='SP-EMPTY')
    fetch = MagicMock(return_value={})
    on_match = MagicMock()

    result = honor_stored_match(
        db=db, entity_table='albums', entity_id=42,
        id_column='spotify_album_id',
        client_fetch_fn=fetch, on_match_fn=on_match,
    )

    assert result == UNAVAILABLE
    on_match.assert_not_called()


def test_on_match_exceptions_propagate(db):
    """Pin: exceptions inside on_match (DB write errors) propagate to
    the worker — they're real errors the worker should surface, not
    swallowed silently."""
    db.insert_album(42, spotify_album_id='SP-ABC')
    fetch = MagicMock(return_value={'id': 'SP-ABC'})
    on_match = MagicMock(side_effect=ValueError("bad write"))

    with pytest.raises(ValueError, match="bad write"):
        honor_stored_match(
            db=db, entity_table='albums', entity_id=42,
            id_column='spotify_album_id',
            client_fetch_fn=fetch, on_match_fn=on_match,
        )


# ---------------------------------------------------------------------------
# Per-table / per-column wiring
# ---------------------------------------------------------------------------


def test_works_with_tracks_table(db):
    """Pin: ``entity_table='tracks'`` works the same as 'albums' — the
    helper is generic across both."""
    db.insert_track(7, spotify_track_id='SP-T-1')
    fetch = MagicMock(return_value={'id': 'SP-T-1'})
    on_match = MagicMock()

    result = honor_stored_match(
        db=db, entity_table='tracks', entity_id=7,
        id_column='spotify_track_id',
        client_fetch_fn=fetch, on_match_fn=on_match,
    )

    assert result == MATCHED
    fetch.assert_called_once_with('SP-T-1')


def test_works_with_alternate_columns(db):
    """Pin: ``id_column`` is configurable so each worker reads its own
    column (deezer_id, itunes_album_id, etc)."""
    db.insert_album(42, deezer_id='12345', itunes_album_id='IT-99')
    fetch = MagicMock(return_value={'id': '12345'})
    on_match = MagicMock()

    # Read the deezer_id column even though spotify_album_id exists too.
    result = honor_stored_match(
        db=db, entity_table='albums', entity_id=42,
        id_column='deezer_id',
        client_fetch_fn=fetch, on_match_fn=on_match,
    )

    assert result == MATCHED
    fetch.assert_called_once_with('12345')


def test_rejects_invalid_table_name(db):
    """Pin: defensive — only known tables (albums/tracks/artists)
    accepted. Avoids SQL injection via crafted table name even though
    every caller is hard-coded."""
    fetch = MagicMock()
    on_match = MagicMock()

    result = honor_stored_match(
        db=db, entity_table='not_a_real_table; DROP TABLE albums',
        entity_id=1, id_column='spotify_album_id',
        client_fetch_fn=fetch, on_match_fn=on_match,
    )

    assert result == NO_STORED_ID
    fetch.assert_not_called()


def test_an_already_matched_entity_is_not_downgraded_by_a_hiccup(db):
    """Backoff must not cost the user their chip: an album that reads
    ``matched`` is not the row a retry loop keeps re-picking, and turning it
    red for a provider outage misreports a match that is still good."""
    db.insert_album(42, spotify_album_id='SP-ABC',
                    spotify_match_status='matched')
    marked = []

    result = honor_stored_match(
        db=db, entity_table='albums', entity_id=42,
        id_column='spotify_album_id',
        client_fetch_fn=MagicMock(side_effect=RuntimeError("API down")),
        on_match_fn=MagicMock(),
        mark_status_fn=lambda kind, eid, status: marked.append((kind, eid, status)),
        status_column='spotify_match_status',
    )

    assert result == UNAVAILABLE
    assert marked == []


def test_an_unsettled_entity_still_gets_its_backoff(db):
    """The row a retry loop WOULD keep re-picking: an id is stored but no
    status was ever written, so nothing stops the next cycle choosing it again
    immediately."""
    db.insert_album(43, spotify_album_id='SP-XYZ')
    marked = []

    result = honor_stored_match(
        db=db, entity_table='albums', entity_id=43,
        id_column='spotify_album_id',
        client_fetch_fn=MagicMock(side_effect=RuntimeError("API down")),
        on_match_fn=MagicMock(),
        mark_status_fn=lambda kind, eid, status: marked.append((kind, eid, status)),
        status_column='spotify_match_status',
    )

    assert result == UNAVAILABLE
    assert marked == [('album', 43, 'error')]
