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


def test_incomplete_album_auto_fill_skips_source_artist_mismatch(tmp_path):
    """Album Completeness must never fill a target album with another artist."""
    existing_path = tmp_path / "Gut" / "Light Years" / "01 - Wound Fuck.flac"
    existing_path.parent.mkdir(parents=True)
    existing_path.write_bytes(b"existing")

    candidate_path = tmp_path / "Jamiroquai" / "Light Years.flac"
    candidate_path.parent.mkdir(parents=True)
    candidate_path.write_bytes(b"candidate")

    class _FakeDB:
        def __init__(self):
            self.search_calls = []
            self.wishlist = []

        def get_tracks_by_album(self, album_id):
            if album_id == "target-album":
                return [
                    SimpleNamespace(
                        id="target-existing-1",
                        album_id="target-album",
                        artist_id="gut",
                        title="Wound Fuck",
                        track_number=1,
                        file_path=str(existing_path),
                        bitrate=9999,
                    ),
                ]
            return []

        def search_tracks(self, title="", artist="", limit=50, server_source=None):
            self.search_calls.append((title, artist, limit, server_source))
            return [
                SimpleNamespace(
                    id="candidate-1",
                    album_id="single-album",
                    artist_id="jamiroquai",
                    artist_name="Jamiroquai",
                    title="Light Years",
                    track_number=1,
                    file_path=str(candidate_path),
                    bitrate=9999,
                ),
            ]

        def add_to_wishlist(self, *args, **kwargs):
            self.wishlist.append((args, kwargs))

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = _FakeDB()
    worker.transfer_folder = str(tmp_path)
    worker._config_manager = None

    result = worker._fix_incomplete_album(
        "album",
        "target-album",
        None,
        {
            "album_id": "target-album",
            "album_title": "Light Years",
            "artist": "Gut",
            "missing_tracks": [
                {
                    "name": "Light Years",
                    "track_number": 2,
                    "disc_number": 1,
                    "source": "spotify",
                    "source_track_id": "sp-light-years",
                    "artists": ["Jamiroquai"],
                },
            ],
        },
    )

    assert result["success"] is False
    assert result["fixed"] == 0
    assert result["skipped"] == 1
    assert result["details"][0]["reason"] == "source artist does not match target album artist"
    assert worker.db.search_calls == []
    assert worker.db.wishlist == []


def test_incomplete_album_auto_fill_rejects_wrong_artist_candidate(tmp_path):
    """Exact title is not enough when the candidate artist differs."""
    existing_path = tmp_path / "album" / "01 - Existing.flac"
    existing_path.parent.mkdir(parents=True)
    existing_path.write_bytes(b"existing")
    candidate_path = tmp_path / "wrong" / "02 - Light Years.flac"
    candidate_path.parent.mkdir(parents=True)
    candidate_path.write_bytes(b"wrong")

    class _FakeDB:
        def __init__(self):
            self.wishlist = []

        def get_tracks_by_album(self, album_id):
            if album_id == "target-album":
                return [
                    SimpleNamespace(
                        id="target-existing-1",
                        album_id="target-album",
                        artist_id="jamiroquai",
                        title="Existing",
                        track_number=1,
                        file_path=str(existing_path),
                        bitrate=9999,
                    ),
                ]
            return []

        def search_tracks(self, title="", artist="", limit=50, server_source=None):
            return [
                SimpleNamespace(
                    id="candidate-1",
                    album_id="wrong-album",
                    artist_id="gut",
                    artist_name="Gut",
                    title="Light Years",
                    track_number=1,
                    file_path=str(candidate_path),
                    bitrate=9999,
                ),
            ]

        def add_to_wishlist(self, *args, **kwargs):
            self.wishlist.append((args, kwargs))

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = _FakeDB()
    worker.transfer_folder = str(tmp_path)
    worker._config_manager = None

    result = worker._fix_incomplete_album(
        "album",
        "target-album",
        None,
        {
            "album_id": "target-album",
            "album_title": "Light Years",
            "artist": "Jamiroquai",
            "spotify_album_id": "sp-album",
            "expected_tracks": 2,
            "missing_tracks": [
                {
                    "name": "Light Years",
                    "track_number": 2,
                    "disc_number": 1,
                    "source": "spotify",
                    "source_track_id": "sp-light-years",
                    "artists": ["Jamiroquai"],
                },
            ],
        },
    )

    assert result["success"] is True
    assert result["fixed"] == 0
    assert result["wishlisted"] == 1
    assert worker.db.wishlist


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
                server_source TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration, file_path, bitrate, server_source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            ("target-track-1", "target-album", "target-artist", "Existing Track", 1, 180000, str(src_path), 320, "navidrome"),
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
                    server_source="soulsync",
                ),
                SimpleNamespace(
                    id="source-track-2",
                    album_id="source-album",
                    artist_id="source-artist",
                    duration=181000,
                    file_path=str(src_path),
                    bitrate=320,
                    server_source="soulsync",
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
            server_source="soulsync",
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
            "SELECT id, title, file_path, server_source FROM tracks WHERE title = ?",
            ("New Track",),
        ).fetchone()
        assert row is not None
        assert row[0] is not None
        assert row[0].startswith("album_fill_source-track-1_deadbeef")
        assert row[1] == "New Track"
        assert Path(row[2]).exists()
        assert row[3] == "navidrome"
        assert verify_conn.execute("SELECT COUNT(*) FROM tracks WHERE id IS NULL").fetchone()[0] == 0
