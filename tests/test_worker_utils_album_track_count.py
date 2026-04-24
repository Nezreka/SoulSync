"""Tests for `worker_utils.set_album_api_track_count` — the shared helper
enrichment workers call to cache authoritative track counts."""

from core.worker_utils import set_album_api_track_count


class _RecordingCursor:
    """Minimal cursor stand-in that captures execute() calls."""

    def __init__(self):
        self.calls = []

    def execute(self, query, params=None):
        self.calls.append((query, params))


# ---------------------------------------------------------------------------
# Happy-path writes
# ---------------------------------------------------------------------------

def test_writes_positive_int_count():
    cursor = _RecordingCursor()
    set_album_api_track_count(cursor, "album-1", 12)
    assert len(cursor.calls) == 1
    query, params = cursor.calls[0]
    assert "UPDATE albums SET api_track_count = ?" in query
    assert "WHERE id = ?" in query
    assert params == (12, "album-1")


def test_coerces_numeric_string_to_int():
    """Deezer / raw API dicts often have track counts as strings."""
    cursor = _RecordingCursor()
    set_album_api_track_count(cursor, "album-2", "14")
    assert cursor.calls[0][1] == (14, "album-2")


def test_writes_one_for_single_track_album():
    cursor = _RecordingCursor()
    set_album_api_track_count(cursor, "album-single", 1)
    assert cursor.calls[0][1] == (1, "album-single")


# ---------------------------------------------------------------------------
# Skip-write cases (don't overwrite good values with bad ones)
# ---------------------------------------------------------------------------

def test_skips_write_when_count_is_zero():
    """A source that doesn't report track counts must not clobber a value
    written by another source."""
    cursor = _RecordingCursor()
    set_album_api_track_count(cursor, "album-x", 0)
    assert cursor.calls == []


def test_skips_write_when_count_is_none():
    cursor = _RecordingCursor()
    set_album_api_track_count(cursor, "album-x", None)
    assert cursor.calls == []


def test_skips_write_when_count_is_negative():
    cursor = _RecordingCursor()
    set_album_api_track_count(cursor, "album-x", -1)
    assert cursor.calls == []


def test_skips_write_on_non_numeric_string():
    cursor = _RecordingCursor()
    set_album_api_track_count(cursor, "album-x", "not a number")
    assert cursor.calls == []


def test_skips_write_on_non_numeric_object():
    cursor = _RecordingCursor()
    set_album_api_track_count(cursor, "album-x", object())
    assert cursor.calls == []


# ---------------------------------------------------------------------------
# Does not commit — caller owns the transaction
# ---------------------------------------------------------------------------

def test_helper_does_not_commit():
    """Workers batch multiple UPDATEs into one transaction. The helper
    must not call commit() or it would break that batching."""

    class _StrictCursor(_RecordingCursor):
        commits = 0

        def commit(self):  # pragma: no cover — asserts it's never called
            _StrictCursor.commits += 1

    cursor = _StrictCursor()
    set_album_api_track_count(cursor, "album-y", 5)
    assert _StrictCursor.commits == 0


# ---------------------------------------------------------------------------
# Error isolation — a cursor.execute failure must not poison the worker's
# other UPDATEs in the same transaction
# ---------------------------------------------------------------------------

def test_swallows_cursor_execute_errors():
    """If the column doesn't exist yet (e.g., migration hasn't run) or
    the DB is otherwise unhappy, the helper must not propagate the error.
    Otherwise the worker's other UPDATEs (spotify_album_id, thumb_url,
    etc.) batched in the same transaction would roll back."""

    class _BrokenCursor:
        def execute(self, query, params=None):
            raise RuntimeError("no such column: api_track_count")

    cursor = _BrokenCursor()
    # Should not raise.
    set_album_api_track_count(cursor, "album-z", 10)
