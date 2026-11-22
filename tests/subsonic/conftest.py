"""a small Library v2 catalogue in a throwaway database, for the music apps
api tests."""

from __future__ import annotations

import pytest

from core.subsonic import library as L


class Seed:
    """inserts artists, albums, tracks and files straight into lib2."""

    def __init__(self, db):
        self.db = db

    def _x(self, sql, params=()):
        with self.db._get_connection() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid

    def artist(self, name, **kw):
        return self._x("INSERT INTO lib2_artists (name, sort_name, image_url, musicbrainz_id, enrichment, spotify_id) "
                       "VALUES (?, ?, ?, ?, ?, ?)",
                       (name, kw.get("sort_name"), kw.get("image_url"), kw.get("mbid"), kw.get("enrichment", "{}"),
                        kw.get("spotify_id")))

    def album(self, artist_id, title, *, year=2020, genres='[]', explicit=None, added_at="2024-01-01 00:00:00",
              album_type="album", label=None):
        aid = self._x("INSERT INTO lib2_albums (primary_artist_id, title, year, genres, explicit, added_at, "
                      "album_type, label) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (artist_id, title, year, genres, explicit, added_at, album_type, label))
        return aid

    def track(self, album_id, title, *, n=1, disc=1, duration=180000, explicit=None, path=None, owner=None,
              file_state="active", bitrate=320, size=1000, server_source=None, track_artist=None,
              play_count=0):
        tid = self._x("INSERT INTO lib2_tracks (album_id, title, track_number, disc_number, duration, explicit, "
                      "track_artist, play_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (album_id, title, n, disc, duration, explicit, track_artist, play_count))
        if path is not False:
            self._x("INSERT INTO lib2_track_files (track_id, path, size, bitrate, format, is_primary, file_state, "
                    "owner_profile_id, server_source) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
                    (tid, path or "/music/%s/%s.mp3" % (album_id, tid), size, bitrate, "mp3", file_state, owner,
                     server_source))
        return tid


@pytest.fixture
def mdb(tmp_path, monkeypatch):
    from core.library_scope import invalidate_library_scope_cache
    from database.music_database import MusicDatabase
    d = MusicDatabase(str(tmp_path / "library.db"))
    monkeypatch.setattr("database.music_database.get_database", lambda *a, **k: d)
    invalidate_library_scope_cache()
    L.forget_snapshots()
    yield d
    L.forget_snapshots()
    invalidate_library_scope_cache()


@pytest.fixture
def seed(mdb):
    return Seed(mdb)
