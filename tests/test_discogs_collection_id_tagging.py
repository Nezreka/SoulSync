"""#848 follow-up: the Your Albums Discogs collection sync stores album IDs
TAGGED ('r<id>') like search/discography, so every stored Discogs album ID is
uniform and re-fetches route to the correct endpoint (no master/release collision).

The divergence didn't cause a live bug (the pool dedups by normalized name, and
discogs_release_id is only ever re-fetched — which handles bare too); this locks
in the consistency so a future ID comparison can't be tripped by mixed forms.
"""

from __future__ import annotations

import pytest

from core.discogs_client import _tag_discogs_album_id, _discogs_album_endpoints
from database.music_database import MusicDatabase


def test_collection_release_id_is_tagged_and_routes_to_releases_only(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    # Exactly what the Your Albums Discogs sync now passes (collection = releases).
    ok = db.upsert_liked_album(
        album_name="Some Album", artist_name="Some Artist",
        source_service="discogs",
        source_id=_tag_discogs_album_id(7361634, "release"), source_id_type="discogs",
        profile_id=1,
    )
    assert ok
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT discogs_release_id FROM liked_albums_pool WHERE profile_id = 1"
        ).fetchone()
    assert row is not None
    assert row["discogs_release_id"] == "r7361634"            # stored tagged, not bare
    # A tagged collection ID routes ONLY to /releases — never /masters — so it
    # can't hit the master/release collision #848 fixed.
    assert _discogs_album_endpoints("r7361634") == ["/releases/7361634"]


def test_tagged_collection_id_never_hits_masters():
    assert "/masters/" not in " ".join(_discogs_album_endpoints(_tag_discogs_album_id(123, "release")))
