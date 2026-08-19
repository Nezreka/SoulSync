"""Library bitrate: estimate when the container has no header field.

Ogg Opus (YouTube remux) stores 0 because mutagen.oggopus only exposes
length + channels. The enhanced album table then rendered a red dash
while completed-download chips already showed the size/duration average.
"""

from __future__ import annotations

import os

from database.music_database import MusicDatabase
from core.soulsync_client import _read_tags


def test_read_tags_estimates_opus_bitrate_without_header(tmp_path, monkeypatch):
    path = tmp_path / "Autumnal Embrace.opus"
    path.write_bytes(b"x" * 40_000)

    class _Info:
        length = 2.0
        channels = 2

    class _Audio:
        tags = None
        info = _Info()

    monkeypatch.setattr("mutagen.File", lambda *_a, **_k: _Audio())
    assert _read_tags(str(path))["bitrate"] == 160


def test_artist_full_detail_fills_opus_bitrate_from_size_and_duration(tmp_path):
    """Already-imported Opus rows stay at bitrate=0 until a rescan. The
    enhanced payload must still show the average so the table is not a dash."""
    db = MusicDatabase(os.path.join(tmp_path, "t.db"))
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO artists (id, name, server_source) VALUES ('1', 'Skyforest', 'soulsync')"
    )
    cur.execute("INSERT INTO albums (id, artist_id, title) VALUES ('a1', '1', 'Autumn')")
    cur.execute(
        """
        INSERT INTO tracks (id, album_id, artist_id, title, duration, file_path, bitrate, file_size)
        VALUES ('t1', 'a1', '1', 'Autumnal Embrace', 2000, 'Autumnal Embrace.opus', 0, 40000)
        """
    )
    conn.commit()
    conn.close()

    result = db.get_artist_full_detail("1")
    assert result["success"] is True
    track = result["albums"][0]["tracks"][0]
    assert track["bitrate"] == 160
    assert track["bitrate_vbr"] == 1
