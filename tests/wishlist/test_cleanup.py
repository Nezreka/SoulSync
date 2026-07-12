import sqlite3

from core.wishlist import processing


class _FakeLogger:
    def __init__(self):
        self.info_messages = []
        self.warning_messages = []
        self.error_messages = []

    def info(self, msg, *args):
        self.info_messages.append(msg % args if args else msg)

    def warning(self, msg, *args):
        self.warning_messages.append(msg % args if args else msg)

    def error(self, msg, *args):
        self.error_messages.append(msg % args if args else msg)

    def debug(self, msg, *args):
        pass


class _FakeWishlistService:
    def __init__(self, tracks):
        self._tracks = list(tracks)
        self.removed_ids = set()

    def get_wishlist_tracks_for_download(self, profile_id=1):
        return [
            track
            for track in self._tracks
            if (track.get("spotify_track_id") or track.get("id")) not in self.removed_ids
        ]

    def mark_track_download_result(self, spotify_track_id, success, error_message=None, profile_id=1):
        self.removed_ids.add(spotify_track_id)
        return True

    def remove_track_from_wishlist(self, spotify_track_id, profile_id=1):
        self.removed_ids.add(spotify_track_id)
        return True


class _FakeMusicDatabase:
    def __init__(self, owned_matches=None, database_path=None):
        self.owned_matches = set(owned_matches or [])
        self.database_path = database_path
        self.track_checks = []
        self.ignored = []

    def check_track_exists(self, track_name, artist_name, confidence_threshold=0.7, server_source=None, album=None):
        self.track_checks.append((track_name, artist_name, server_source, album))
        if (track_name, artist_name) in self.owned_matches:
            return {"id": "db-track"}, 0.9
        return None, 0.0

    def add_to_wishlist_ignore(self, track_id, track_name="", artist_name="", reason="removed", profile_id=1):
        self.ignored.append((profile_id, track_id, track_name, artist_name, reason))
        return True


def test_cleanup_wishlist_against_library_removes_owned_tracks():
    service = _FakeWishlistService(
        [
            {
                "id": "track-1",
                "name": "Song A",
                "artists": [{"name": "Artist A"}],
                "album": {"name": "Album A", "album_type": "album"},
            },
            {
                "id": "track-2",
                "name": "Song B",
                "artists": [{"name": "Artist B"}],
                "album": {"name": "Album B", "album_type": "album"},
            },
        ]
    )
    db = _FakeMusicDatabase(owned_matches={("Song A", "Artist A")})
    logger = _FakeLogger()

    payload, status = processing.cleanup_wishlist_against_library(
        service,
        db,
        1,
        "navidrome",
        logger=logger,
    )

    assert status == 200
    assert payload["success"] is True
    assert payload["removed_count"] == 1
    assert payload["processed_count"] == 2
    assert service.removed_ids == {"track-1"}
    assert any("Completed cleanup: 1 tracks removed" in msg for msg in logger.info_messages)


def test_cleanup_wishlist_against_library_handles_empty_wishlist():
    service = _FakeWishlistService([])
    db = _FakeMusicDatabase()
    logger = _FakeLogger()

    payload, status = processing.cleanup_wishlist_against_library(
        service,
        db,
        1,
        "navidrome",
        logger=logger,
    )

    assert status == 200
    assert payload == {"success": True, "message": "No tracks in wishlist to clean up", "removed_count": 0}


def test_remove_deleted_downloads_removes_wishlist_and_blocks_album(tmp_path):
    db_path = tmp_path / "music.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE track_downloads (
                id INTEGER PRIMARY KEY,
                track_id TEXT,
                spotify_track_id TEXT,
                soul_id TEXT,
                itunes_track_id TEXT,
                deezer_track_id TEXT,
                tidal_track_id TEXT,
                qobuz_track_id TEXT,
                musicbrainz_recording_id TEXT,
                file_path TEXT,
                status TEXT
            );
            CREATE TABLE blocklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL DEFAULT 1,
                entity_type TEXT NOT NULL,
                name TEXT NOT NULL COLLATE NOCASE,
                spotify_id TEXT,
                itunes_id TEXT,
                deezer_id TEXT,
                musicbrainz_id TEXT,
                parent_name TEXT,
                match_status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            "INSERT INTO track_downloads (track_id, spotify_track_id, file_path, status) VALUES (?, ?, ?, ?)",
            ("track-1", "track-1", str(tmp_path / "deleted.flac"), "completed"),
        )

    service = _FakeWishlistService(
        [
            {
                "id": "track-1",
                "name": "Deleted Song",
                "artists": [{"name": "Deleted Artist"}],
                "album": {"id": "album-1", "name": "Deleted Album", "album_type": "album"},
            }
        ]
    )
    db = _FakeMusicDatabase(database_path=db_path)
    logger = _FakeLogger()

    removed = processing.remove_deleted_downloads_from_wishlist(
        service,
        type("Profiles", (), {"get_all_profiles": lambda self: [{"id": 1}]})(),
        db,
        logger=logger,
    )

    assert removed == 1
    assert service.removed_ids == {"track-1"}
    assert db.ignored == [(1, "track-1", "Deleted Song", "Deleted Artist", "deleted_from_library")]
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT entity_type, name, parent_name, itunes_id FROM blocklist"
        ).fetchone()
    assert row == ("album", "Deleted Album", "Deleted Artist", "album-1")


def test_remove_deleted_downloads_keeps_wishlist_when_file_still_exists(tmp_path):
    audio_path = tmp_path / "existing.flac"
    audio_path.write_text("audio")
    db_path = tmp_path / "music.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE track_downloads (
                id INTEGER PRIMARY KEY,
                track_id TEXT,
                spotify_track_id TEXT,
                soul_id TEXT,
                itunes_track_id TEXT,
                deezer_track_id TEXT,
                tidal_track_id TEXT,
                qobuz_track_id TEXT,
                musicbrainz_recording_id TEXT,
                file_path TEXT,
                status TEXT
            );
            CREATE TABLE blocklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL DEFAULT 1,
                entity_type TEXT NOT NULL,
                name TEXT NOT NULL COLLATE NOCASE,
                spotify_id TEXT,
                itunes_id TEXT,
                deezer_id TEXT,
                musicbrainz_id TEXT,
                parent_name TEXT,
                match_status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            "INSERT INTO track_downloads (track_id, spotify_track_id, file_path, status) VALUES (?, ?, ?, ?)",
            ("track-1", "track-1", str(audio_path), "completed"),
        )

    service = _FakeWishlistService(
        [
            {
                "id": "track-1",
                "name": "Still There",
                "artists": [{"name": "Artist"}],
                "album": {"id": "album-1", "name": "Album"},
            }
        ]
    )
    db = _FakeMusicDatabase(database_path=db_path)

    removed = processing.remove_deleted_downloads_from_wishlist(
        service,
        type("Profiles", (), {"get_all_profiles": lambda self: [{"id": 1}]})(),
        db,
    )

    assert removed == 0
    assert service.removed_ids == set()
    assert db.ignored == []
