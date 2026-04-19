import sqlite3
import sys
import types
from pathlib import Path
from types import SimpleNamespace


if "spotipy" not in sys.modules:
    spotipy = types.ModuleType("spotipy")

    class _DummySpotify:
        def __init__(self, *args, **kwargs):
            pass

    oauth2 = types.ModuleType("spotipy.oauth2")

    class _DummyOAuth:
        def __init__(self, *args, **kwargs):
            pass

    spotipy.Spotify = _DummySpotify
    oauth2.SpotifyOAuth = _DummyOAuth
    oauth2.SpotifyClientCredentials = _DummyOAuth
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
            return "plex"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod

from core.repair_worker import RepairWorker


def test_perform_album_fill_copy_branch_generates_track_id(tmp_path, monkeypatch):
    src_path = tmp_path / "source.flac"
    src_path.write_bytes(b"fake-audio")
    album_folder = tmp_path / "album"
    db_path = tmp_path / "tracks.db"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE tracks (
                id TEXT PRIMARY KEY,
                album_id TEXT NOT NULL,
                artist_id TEXT NOT NULL,
                title TEXT NOT NULL,
                track_number INTEGER,
                duration INTEGER,
                file_path TEXT,
                bitrate INTEGER,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration, file_path, bitrate, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            ("target-track-1", "target-album", "target-artist", "Existing Track", 1, 180000, str(src_path), 320),
        )
        conn.commit()

    class _FakeDB:
        def _get_connection(self):
            return sqlite3.connect(db_path)

        def get_tracks_by_album(self, album_id):
            return [
                SimpleNamespace(
                    id="source-track-1",
                    album_id="source-album",
                    artist_id="source-artist",
                    duration=180000,
                    file_path=str(src_path),
                    bitrate=320,
                ),
                SimpleNamespace(
                    id="source-track-2",
                    album_id="source-album",
                    artist_id="source-artist",
                    duration=181000,
                    file_path=str(src_path),
                    bitrate=320,
                ),
            ]

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = _FakeDB()
    worker.transfer_folder = str(tmp_path)
    worker._config_manager = None
    worker._enhance_file_metadata = None
    worker._enhance_placed_track = lambda *args, **kwargs: None

    monkeypatch.setattr(
        "core.repair_worker.uuid.uuid4",
        lambda: SimpleNamespace(hex="deadbeefcafebabe"),
    )

    result = worker._perform_album_fill(
        candidate=SimpleNamespace(
            id="source-track-1",
            album_id="source-album",
            artist_id="source-artist",
            duration=180000,
            file_path=str(src_path),
            bitrate=320,
        ),
        album_id="target-album",
        album_title="Target Album",
        artist_name="Target Artist",
        track_name="New Track",
        track_number=2,
        disc_number=1,
        album_folder=str(album_folder),
        filename_pattern="{num:02d} - {title}",
        download_folder=None,
    )

    assert result["success"] is True
    assert result["action"] == "copied"

    with sqlite3.connect(db_path) as verify_conn:
        row = verify_conn.execute(
            "SELECT id, title, file_path FROM tracks WHERE title = ?",
            ("New Track",),
        ).fetchone()
        assert row is not None
        assert row[0] is not None
        assert row[0].startswith("album_fill_source-track-1_deadbeef")
        assert row[1] == "New Track"
        assert Path(row[2]).exists()
        assert verify_conn.execute("SELECT COUNT(*) FROM tracks WHERE id IS NULL").fetchone()[0] == 0
