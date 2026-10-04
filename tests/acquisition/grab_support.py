"""What the manual and scheduled grab tests both stand on: an imported legacy
library with the acquisition schema, the track it grabs and the hit it picks."""

from __future__ import annotations

from core.acquisition import ensure_acquisition_schema
from core.library2.importer import import_legacy_library


CONFIG_GET = lambda key, default=None: default


def prepared_conn(legacy_db):
    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    ensure_acquisition_schema(conn)
    conn.commit()
    return conn


def track_context(conn, title="One Dance"):
    row = conn.execute(
        """SELECT t.id AS track_id, t.album_id, t.quality_profile_id
             FROM lib2_tracks t
             JOIN lib2_release_tracks rt ON rt.track_id=t.id
             JOIN lib2_albums al ON al.id=t.album_id
            WHERE t.title=? AND al.title='Views'
            ORDER BY t.id LIMIT 1""",
        (title,),
    ).fetchone()
    assert row is not None
    return {
        "track_id": row["track_id"],
        "album_id": row["album_id"],
        "quality_profile_id": row["quality_profile_id"],
    }


def search_result(**overrides):
    result = {
        "username": "peer1",
        "filename": "Music\\Drake\\01 - One Dance.flac",
        "size": 12345678,
        "title": "One Dance",
        "artist": "Drake",
        "album": "Views",
        "quality": "flac",
        "bitrate": 1000,
    }
    result.update(overrides)
    return result
