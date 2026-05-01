import json

from core.wishlist import presence


class _FakeCursor:
    def __init__(self, profile_rows=None, legacy_rows=None, fail_profile_query=False):
        self.profile_rows = list(profile_rows or [])
        self.legacy_rows = list(legacy_rows or [])
        self.fail_profile_query = fail_profile_query
        self.calls = []
        self._last_sql = ""

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self._last_sql = sql
        if self.fail_profile_query and "WHERE profile_id = ?" in sql:
            raise RuntimeError("profile_id column missing")

    def fetchall(self):
        if "WHERE profile_id = ?" in self._last_sql and not self.fail_profile_query:
            return list(self.profile_rows)
        return list(self.legacy_rows)


def test_load_wishlist_keys_uses_profile_specific_query():
    cursor = _FakeCursor(
        profile_rows=[
            (json.dumps({"name": "Song One", "artists": [{"name": "Artist One"}]}),),
            ("not-json",),
        ]
    )

    keys = presence.load_wishlist_keys(cursor, profile_id=7)

    assert keys == {"song one|||artist one"}
    assert cursor.calls == [
        ("SELECT spotify_data FROM wishlist_tracks WHERE profile_id = ?", (7,)),
    ]


def test_load_wishlist_keys_falls_back_to_legacy_schema():
    cursor = _FakeCursor(
        fail_profile_query=True,
        legacy_rows=[
            (json.dumps({"name": "Song Two", "artists": [{"name": "Artist Two"}]}),),
        ],
    )

    keys = presence.load_wishlist_keys(cursor, profile_id=3)

    assert keys == {"song two|||artist two"}
    assert cursor.calls == [
        ("SELECT spotify_data FROM wishlist_tracks WHERE profile_id = ?", (3,)),
        ("SELECT spotify_data FROM wishlist_tracks", None),
    ]
