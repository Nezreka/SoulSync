"""Minimal persisted acquisition database."""
import sqlite3

class Database:
    def __init__(self, path):
        self.path = str(path)
        with self._get_connection() as conn:
            conn.execute('CREATE TABLE wishlist_tracks(profile_id INTEGER, spotify_track_id TEXT, spotify_data TEXT)')

    def _get_connection(self):
        return sqlite3.connect(self.path)

