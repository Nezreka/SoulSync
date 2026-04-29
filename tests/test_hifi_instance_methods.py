"""Tests for the HiFi instance CRUD helpers on ``MusicDatabase``:

- ``get_hifi_instances()`` — returns enabled instances ordered by priority
- ``get_all_hifi_instances()`` — returns all instances (enabled + disabled)
- ``add_hifi_instance(url, priority)`` — inserts a new instance
- ``remove_hifi_instance(url)`` — deletes an instance by URL
- ``toggle_hifi_instance(url, enabled)`` — enables/disables an instance
- ``reorder_hifi_instances(urls)`` — updates priority ordering
- ``seed_hifi_instances(default_urls)`` — seeds defaults when table is empty

These are isolated DB-method tests so the SQL itself is verified
without spinning up Flask or any HiFi client.
"""

import sqlite3
import sys
import types

import pytest


# ── stubs (same shape used elsewhere in the test suite) ───────────────────
if "spotipy" not in sys.modules:
    spotipy = types.ModuleType("spotipy")
    spotipy.Spotify = object
    oauth2 = types.ModuleType("spotipy.oauth2")
    oauth2.SpotifyOAuth = object
    oauth2.SpotifyClientCredentials = object
    spotipy.oauth2 = oauth2
    sys.modules["spotipy"] = spotipy
    sys.modules["spotipy.oauth2"] = oauth2

if "config.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("config.settings")

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "primary"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod


from database.music_database import MusicDatabase  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────

class _InMemoryDB(MusicDatabase):
    """MusicDatabase that uses an in-memory sqlite that survives across
    `_get_connection()` calls."""

    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE hifi_instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                priority INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._conn.commit()

    def _get_connection(self):
        return _NonClosingConn(self._conn)


class _NonClosingConn:
    """Wraps the shared sqlite connection so `with db._get_connection()
    as conn:` doesn't close the underlying handle between calls."""
    def __init__(self, real):
        self._real = real

    def cursor(self):
        return self._real.cursor()

    def commit(self):
        return self._real.commit()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.fixture
def db():
    return _InMemoryDB()


def _seed(db, *, instances=()):
    """Seed hifi_instances rows. Each tuple: (url, priority, enabled)."""
    cur = db._conn.cursor()
    for url, priority, enabled in instances:
        cur.execute(
            "INSERT INTO hifi_instances (url, priority, enabled) VALUES (?, ?, ?)",
            (url, priority, enabled),
        )
    db._conn.commit()


# ── get_hifi_instances ────────────────────────────────────────────────────


def test_get_hifi_instances_returns_enabled_ordered_by_priority(db):
    _seed(db, instances=[
        ("http://b.com", 10, 1),
        ("http://a.com", 5, 1),
        ("http://c.com", 1, 1),
    ])
    rows = db.get_hifi_instances()
    assert [r["url"] for r in rows] == ["http://c.com", "http://a.com", "http://b.com"]
    assert [r["priority"] for r in rows] == [1, 5, 10]


def test_get_hifi_instances_excludes_disabled(db):
    _seed(db, instances=[
        ("http://a.com", 0, 1),
        ("http://b.com", 1, 0),
        ("http://c.com", 2, 1),
    ])
    rows = db.get_hifi_instances()
    assert {r["url"] for r in rows} == {"http://a.com", "http://c.com"}


def test_get_hifi_instances_returns_empty_when_no_rows(db):
    assert db.get_hifi_instances() == []


def test_get_hifi_instances_tiebreaks_on_id(db):
    """Same priority → ordered by insertion order (autoincrement id)."""
    _seed(db, instances=[
        ("http://first.com", 0, 1),
        ("http://second.com", 0, 1),
        ("http://third.com", 0, 1),
    ])
    rows = db.get_hifi_instances()
    assert [r["url"] for r in rows] == ["http://first.com", "http://second.com", "http://third.com"]


# ── get_all_hifi_instances ────────────────────────────────────────────────


def test_get_all_hifi_instances_returns_all_including_disabled(db):
    _seed(db, instances=[
        ("http://a.com", 0, 1),
        ("http://b.com", 1, 0),
    ])
    rows = db.get_all_hifi_instances()
    assert {r["url"] for r in rows} == {"http://a.com", "http://b.com"}


def test_get_all_hifi_instances_ordered_by_priority(db):
    _seed(db, instances=[
        ("http://c.com", 20, 0),
        ("http://a.com", 0, 1),
        ("http://b.com", 10, 1),
    ])
    rows = db.get_all_hifi_instances()
    assert [r["url"] for r in rows] == ["http://a.com", "http://b.com", "http://c.com"]


def test_get_all_hifi_instances_returns_empty_when_no_rows(db):
    assert db.get_all_hifi_instances() == []


# ── add_hifi_instance ─────────────────────────────────────────────────────


def test_add_hifi_instance_returns_true_on_insert(db):
    assert db.add_hifi_instance("http://new.com", priority=3) is True
    rows = db.get_all_hifi_instances()
    assert len(rows) == 1
    assert rows[0]["url"] == "http://new.com"
    assert rows[0]["priority"] == 3
    assert rows[0]["enabled"] == 1


def test_add_hifi_instance_returns_false_on_duplicate(db):
    _seed(db, instances=[("http://dup.com", 0, 1)])
    # INSERT OR IGNORE — should not raise, but return False (rowcount == 0)
    assert db.add_hifi_instance("http://dup.com", priority=5) is False
    rows = db.get_all_hifi_instances()
    assert len(rows) == 1


def test_add_hifi_instance_default_priority(db):
    db.add_hifi_instance("http://x.com")
    row = db.get_all_hifi_instances()[0]
    assert row["priority"] == 0


# ── remove_hifi_instance ──────────────────────────────────────────────────


def test_remove_hifi_instance_returns_true_on_delete(db):
    _seed(db, instances=[("http://go.com", 0, 1)])
    assert db.remove_hifi_instance("http://go.com") is True
    assert db.get_all_hifi_instances() == []


def test_remove_hifi_instance_returns_false_when_not_found(db):
    assert db.remove_hifi_instance("http://missing.com") is False


def test_remove_hifi_instance_only_removes_matching_url(db):
    _seed(db, instances=[
        ("http://keep.com", 0, 1),
        ("http://delete.com", 1, 1),
    ])
    db.remove_hifi_instance("http://delete.com")
    rows = db.get_all_hifi_instances()
    assert len(rows) == 1
    assert rows[0]["url"] == "http://keep.com"


# ── toggle_hifi_instance ──────────────────────────────────────────────────


def test_toggle_hifi_instance_disable(db):
    _seed(db, instances=[("http://x.com", 0, 1)])
    assert db.toggle_hifi_instance("http://x.com", enabled=False) is True
    row = db.get_all_hifi_instances()[0]
    assert row["enabled"] == 0


def test_toggle_hifi_instance_enable(db):
    _seed(db, instances=[("http://x.com", 0, 0)])
    assert db.toggle_hifi_instance("http://x.com", enabled=True) is True
    row = db.get_all_hifi_instances()[0]
    assert row["enabled"] == 1


def test_toggle_hifi_instance_returns_false_when_not_found(db):
    assert db.toggle_hifi_instance("http://missing.com", enabled=True) is False


def test_toggle_hifi_instance_noop_when_already_set(db):
    """Toggling to the same value should still return True (row matched)."""
    _seed(db, instances=[("http://x.com", 0, 1)])
    # SQLite rowcount for UPDATE is 1 even if value didn't change
    assert db.toggle_hifi_instance("http://x.com", enabled=True) is True


# ── reorder_hifi_instances ────────────────────────────────────────────────


def test_reorder_hifi_instances_updates_priorities(db):
    _seed(db, instances=[
        ("http://a.com", 0, 1),
        ("http://b.com", 1, 1),
        ("http://c.com", 2, 1),
    ])
    db.reorder_hifi_instances(["http://c.com", "http://a.com", "http://b.com"])
    rows = db.get_all_hifi_instances()
    by_url = {r["url"]: r["priority"] for r in rows}
    assert by_url == {"http://c.com": 0, "http://a.com": 1, "http://b.com": 2}


def test_reorder_hifi_instances_returns_true_on_empty_list(db):
    assert db.reorder_hifi_instances([]) is True


def test_reorder_hifi_instances_returns_false_with_unknown_urls(db):
    """Reorder should fail when any URL doesn't exist."""
    _seed(db, instances=[("http://a.com", 0, 1)])
    assert db.reorder_hifi_instances(["http://a.com", "http://phantom.com"]) is False


# ── seed_hifi_instances ───────────────────────────────────────────────────


def test_seed_hifi_instances_inserts_when_empty(db):
    db.seed_hifi_instances(["http://a.com", "http://b.com"])
    rows = db.get_all_hifi_instances()
    assert len(rows) == 2
    by_url = {r["url"]: r["priority"] for r in rows}
    assert by_url == {"http://a.com": 0, "http://b.com": 1}


def test_seed_hifi_instances_does_nothing_when_table_has_rows(db):
    _seed(db, instances=[("http://existing.com", 0, 1)])
    db.seed_hifi_instances(["http://new.com"])
    rows = db.get_all_hifi_instances()
    assert len(rows) == 1
    assert rows[0]["url"] == "http://existing.com"


def test_seed_hifi_instances_does_not_duplicate_on_reseed(db):
    db.seed_hifi_instances(["http://a.com"])
    db.seed_hifi_instances(["http://a.com"])
    rows = db.get_all_hifi_instances()
    assert len(rows) == 1


# ── error propagation ────────────────────────────────────────────────────
# These methods now let DB errors bubble up so the route layer turns them
# into a 500 — the user sees a real failure instead of a phantom empty state.


def _db_without_hifi_table():
    """Returns a MusicDatabase with NO hifi_instances table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    class _NoTableDB(MusicDatabase):
        def __init__(self):
            self._conn = conn

        def _get_connection(self):
            return _NonClosingConn(self._conn)

    return _NoTableDB()


def test_get_hifi_instances_propagates_db_errors():
    db = _db_without_hifi_table()
    with pytest.raises(sqlite3.OperationalError):
        db.get_hifi_instances()


def test_get_all_hifi_instances_propagates_db_errors():
    db = _db_without_hifi_table()
    with pytest.raises(sqlite3.OperationalError):
        db.get_all_hifi_instances()


def test_add_hifi_instance_propagates_db_errors():
    db = _db_without_hifi_table()
    with pytest.raises(sqlite3.OperationalError):
        db.add_hifi_instance("http://x.com")


def test_remove_hifi_instance_propagates_db_errors():
    db = _db_without_hifi_table()
    with pytest.raises(sqlite3.OperationalError):
        db.remove_hifi_instance("http://x.com")


def test_toggle_hifi_instance_propagates_db_errors():
    db = _db_without_hifi_table()
    with pytest.raises(sqlite3.OperationalError):
        db.toggle_hifi_instance("http://x.com", enabled=True)


def test_reorder_hifi_instances_propagates_db_errors():
    db = _db_without_hifi_table()
    with pytest.raises(sqlite3.OperationalError):
        db.reorder_hifi_instances(["http://x.com"])


def test_seed_hifi_instances_propagates_db_errors():
    db = _db_without_hifi_table()
    with pytest.raises(sqlite3.OperationalError):
        db.seed_hifi_instances(["http://x.com"])
