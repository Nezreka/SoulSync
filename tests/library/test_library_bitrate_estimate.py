"""Library bitrate: estimate when the container has no header field.

Ogg Opus (YouTube remux) stores 0 because mutagen.oggopus only exposes
length + channels. The enhanced album table then rendered a red dash
while completed-download chips already showed the size/duration average.
"""

from __future__ import annotations

import os

from database.music_database import MusicDatabase
from core.soulsync_client import _read_tags
from tests.support.catalogue_seed import seed_album, seed_artist, seed_track


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
    artist_id = seed_artist(conn, server_id="ar-1", name="Skyforest", server_source="soulsync")
    album_id = seed_album(conn, server_id="al-1", title="Autumn", artist_id=artist_id,
                          server_source="soulsync")
    seed_track(conn, server_id="tr-1", title="Autumnal Embrace", album_id=album_id,
              artist_id=artist_id, server_source="soulsync", duration=2000,
              file_path="Autumnal Embrace.opus", bitrate=0, file_size=40000)
    conn.commit()
    conn.close()

    result = db.get_artist_full_detail(artist_id)
    assert result["success"] is True
    track = result["albums"][0]["tracks"][0]
    assert track["bitrate"] == 160
    assert track["bitrate_vbr"] == 1
