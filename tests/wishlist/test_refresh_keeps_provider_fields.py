"""a wishlist refresh fills gaps, it doesn't throw away what it doesn't know.

the library v2 mirror refreshes a row a provider add (search, playlist sync,
watchlist scan) already wrote. it replaced the whole payload, so the
provider's album date, links and artists were gone before the download read
them, and the track landed in a yearless twin of its album's folder.
"""

from __future__ import annotations

import json

import pytest

from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _row(db):
    with db._get_connection() as conn:
        return json.loads(conn.execute("SELECT spotify_data FROM wishlist_tracks").fetchone()[0])


def test_a_mirror_refresh_keeps_what_only_the_provider_knew(db):
    provider = {
        "id": "sp-track", "name": "Thick Thighs",
        "artists": [{"name": "Priscilla Block"}],
        "album": {"id": "sp-album", "name": "Different Route", "release_date": "2017-08-25",
                  "artists": [{"name": "Priscilla Block"}],
                  "external_urls": {"spotify": "https://open.spotify.com/album/x"}},
        "duration_ms": 200000,
    }
    db.add_to_wishlist(provider, source_type="playlist")
    mirror = {
        "id": "sp-track", "name": "Thick Thighs",
        "artists": [{"name": "Priscilla Block"}],
        "album": {"id": "sp-album", "name": "Different Route", "release_date": "",
                  "total_tracks": 6, "artists": []},
        "quality_profile_id": 1,
    }
    db.add_to_wishlist(mirror, source_type="album", quality_profile_id=1)

    album = _row(db)["album"]
    assert album["release_date"] == "2017-08-25"
    assert album["artists"] == [{"name": "Priscilla Block"}]
    assert album["external_urls"]["spotify"].endswith("/album/x")
    # what the refresh does know still wins
    assert album["total_tracks"] == 6
    assert _row(db)["duration_ms"] == 200000
