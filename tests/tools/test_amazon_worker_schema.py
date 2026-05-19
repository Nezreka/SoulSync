import sqlite3

from core.amazon_worker import AmazonWorker


class _LegacyDatabase:
    def __init__(self, path):
        self.path = str(path)
        with sqlite3.connect(self.path) as conn:
            conn.executescript(
                """
                CREATE TABLE artists (
                    id INTEGER PRIMARY KEY,
                    name TEXT
                );
                CREATE TABLE albums (
                    id INTEGER PRIMARY KEY,
                    title TEXT,
                    artist_id INTEGER
                );
                CREATE TABLE tracks (
                    id INTEGER PRIMARY KEY,
                    title TEXT,
                    artist_id INTEGER
                );
                INSERT INTO artists (id, name) VALUES (1, 'Artist A');
                INSERT INTO albums (id, title, artist_id) VALUES (10, 'Album A', 1);
                INSERT INTO tracks (id, title, artist_id) VALUES (100, 'Track A', 1);
                """
            )

    def _get_connection(self):
        return sqlite3.connect(self.path)


def _columns(db_path, table):
    with sqlite3.connect(str(db_path)) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_amazon_worker_self_heals_legacy_schema_before_selecting_next_item(tmp_path):
    db_path = tmp_path / "legacy.db"
    db = _LegacyDatabase(db_path)
    worker = AmazonWorker(db)

    item = worker._get_next_item()

    assert item == {"type": "artist", "id": 1, "name": "Artist A"}
    for table in ("artists", "albums", "tracks"):
        assert {"amazon_id", "amazon_match_status", "amazon_last_attempted"} <= _columns(db_path, table)


def test_amazon_worker_stats_self_heal_legacy_schema(tmp_path):
    db_path = tmp_path / "legacy.db"
    db = _LegacyDatabase(db_path)
    worker = AmazonWorker(db)

    assert worker._count_pending_items() == 3
    assert worker._get_progress_breakdown() == {
        "artists": {"matched": 0, "total": 1, "percent": 0},
        "albums": {"matched": 0, "total": 1, "percent": 0},
        "tracks": {"matched": 0, "total": 1, "percent": 0},
    }
