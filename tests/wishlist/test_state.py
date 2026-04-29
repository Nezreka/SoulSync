from contextlib import contextmanager

from core.wishlist import state


class _FakeLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, msg):
        self.warnings.append(msg)


class _FakeCursor:
    def __init__(self, row=None):
        self.row = row
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.row


class _FakeConnection:
    def __init__(self, row=None):
        self.cursor_obj = _FakeCursor(row=row)
        self.committed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True


class _FakeDB:
    def __init__(self, row=None):
        self.connection = _FakeConnection(row=row)

    @contextmanager
    def _get_connection(self):
        yield self.connection


def test_flag_age_seconds_returns_zero_for_missing_timestamp():
    assert state.flag_age_seconds(None, now=100.0) == 0.0


def test_flag_age_seconds_uses_now_minus_started_at():
    assert state.flag_age_seconds(25.0, now=100.0) == 75.0


def test_is_flag_recent_requires_active_and_recent_timestamp():
    assert state.is_flag_recent(True, 100.0, timeout_seconds=10, now=105.0) is True
    assert state.is_flag_recent(True, 100.0, timeout_seconds=10, now=111.0) is False
    assert state.is_flag_recent(False, 100.0, timeout_seconds=10, now=105.0) is False


def test_is_flag_stuck_requires_active_and_expired_timestamp():
    assert state.is_flag_stuck(True, 100.0, timeout_seconds=10, now=111.0) is True
    assert state.is_flag_stuck(True, 100.0, timeout_seconds=10, now=105.0) is False
    assert state.is_flag_stuck(False, 100.0, timeout_seconds=10, now=111.0) is False


def test_is_wishlist_actually_processing_warns_and_recovers_when_stuck():
    logger = _FakeLogger()
    calls = []

    result = state.is_wishlist_actually_processing(
        True,
        100.0,
        timeout_seconds=10,
        now=700.0,
        on_stuck=lambda: calls.append(True),
        logger=logger,
    )

    assert result is False
    assert calls == [True]
    assert logger.warnings == [
        "[Stuck Detection] Wishlist flag stuck for 10.0 minutes - auto-recovering"
    ]


def test_get_wishlist_cycle_creates_default_entry_when_missing():
    db = _FakeDB(row=None)

    cycle = state.get_wishlist_cycle(lambda: db)

    assert cycle == "albums"
    assert db.connection.committed is True
    assert db.connection.cursor_obj.calls[0][0] == "SELECT value FROM metadata WHERE key = 'wishlist_cycle'"
    assert db.connection.cursor_obj.calls[1][1] == ("albums",)


def test_get_wishlist_cycle_returns_stored_value_when_present():
    db = _FakeDB(row={"value": "singles"})

    cycle = state.get_wishlist_cycle(lambda: db)

    assert cycle == "singles"
    assert db.connection.committed is False


def test_set_wishlist_cycle_persists_value():
    db = _FakeDB()

    state.set_wishlist_cycle(lambda: db, "singles")

    assert db.connection.committed is True
    assert db.connection.cursor_obj.calls[-1][1] == ("singles",)
