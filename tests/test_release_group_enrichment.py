"""MusicBrainz release groups for albums and wishlist rows.

MetaSync's SIN signals key albums by MusicBrainz release GROUP. The pinned
contract:

* a matched album stores the release group its release belongs to;
* an album that already has a release id (tags, a manual match) gets its
  group from one background lookup, paced by
  ``musicbrainz_release_group_attempted``;
* changing an album's release id clears a group the same UPDATE did not
  also set, so a retag can never keep the old edition's group;
* wishlist rows are matched in the background by their album + artist,
  and the group is served on the wishlist API.

Ported to Library v2: an album is a ``lib2_albums`` row whose release id is
``musicbrainz_id``; the group keeps upstream's column name there.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import core.musicbrainz_worker as mbw
from api.serializers import serialize_album, serialize_wishlist_track
from database.music_database import MusicDatabase

REL = "11111111-1111-4111-8111-111111111111"
REL_OTHER = "33333333-3333-4333-8333-333333333333"
RG = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "rg.db"))


@pytest.fixture
def worker(db):
    w = mbw.MusicBrainzWorker(db)
    w.mb_service.mb_client = MagicMock()
    return w


def _album(db, release_id=None, status=None):
    # lib2 port: an owned album whose artist and track are already enriched,
    # so the album is the only thing in the queue
    from core.library2.provider_attempts import record_attempt
    from tests.lib2_seed import track

    with db._get_connection() as conn:
        track_id = track(conn, "The Testers", "Album A", "Song", album_cols={"musicbrainz_id": release_id})
        album_id, artist_id = conn.execute(
            "SELECT id, primary_artist_id FROM lib2_albums WHERE title = 'Album A'").fetchone()
        done = [("artist", artist_id, "matched"), ("track", track_id, "not_found")]
        for entity_type, entity_id, outcome in done + ([("album", album_id, status)] if status else []):
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service="musicbrainz", status=outcome)
        conn.commit()
        return album_id


def _album_row(db):
    with db._get_connection() as conn:
        return dict(conn.execute(
            "SELECT musicbrainz_id AS musicbrainz_release_id, musicbrainz_release_group_id, "
            "musicbrainz_release_group_attempted FROM lib2_albums WHERE title = 'Album A'").fetchone())


def _wishlist(db, track_data):
    import json
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO wishlist_tracks (spotify_track_id, spotify_data, profile_id) VALUES (?, ?, 1)",
            ("sp1", json.dumps(track_data)))
        conn.commit()
        return conn.execute("SELECT id FROM wishlist_tracks WHERE spotify_track_id = 'sp1'").fetchone()[0]


def test_a_matched_album_stores_its_release_group(db, worker):
    _album(db)
    worker.mb_service.match_release = MagicMock(return_value={
        "mbid": REL, "release_group_mbid": RG, "confidence": 95})
    item = worker._get_next_item()
    assert item["type"] == "album"
    worker._process_item(item)
    row = _album_row(db)
    assert row["musicbrainz_release_id"] == REL
    assert row["musicbrainz_release_group_id"] == RG


def test_match_release_reads_the_group_off_the_search_hit(db, worker):
    worker.mb_service.mb_client.search_release.return_value = [{
        "id": REL, "title": "Album A", "score": 100,
        "artist-credit": [{"name": "The Testers", "artist": {"name": "The Testers"}}],
        "release-group": {"id": RG.upper(), "primary-type": "Album"},
    }]
    result = worker.mb_service.match_release("Album A", "The Testers")
    assert result["mbid"] == REL
    assert result["release_group_mbid"] == RG
    # The cache keeps the hit, group included.
    cached = worker.mb_service.match_release("Album A", "The Testers")
    assert cached["cached"] is True
    assert cached["release_group_mbid"] == RG


def test_an_album_with_a_release_id_gets_its_group_backfilled(db, worker):
    album_id = _album(db, release_id=REL, status="matched")
    worker.mb_service.mb_client.get_release.return_value = {"id": REL, "release-group": {"id": RG}}
    item = worker._get_next_item()
    assert (item["type"], item["id"], item["release_id"]) == ("album_group", album_id, REL)
    worker._process_item(item)
    worker.mb_service.mb_client.get_release.assert_called_once_with(REL, includes=["release-groups"])
    assert _album_row(db)["musicbrainz_release_group_id"] == RG
    assert worker._get_next_item() is None


def test_a_failed_backfill_waits_instead_of_looping(db, worker):
    _album(db, release_id=REL, status="matched")
    worker.mb_service.mb_client.get_release.return_value = None
    worker._process_item(worker._get_next_item())
    row = _album_row(db)
    assert row["musicbrainz_release_group_id"] is None
    assert row["musicbrainz_release_group_attempted"] is not None
    assert worker._get_next_item() is None  # retried after retry_days, not now


def test_a_new_release_id_clears_the_old_group(db, worker):
    album_id = _album(db, release_id=REL, status="matched")
    worker.mb_service.update_album_release_group(album_id, RG)
    with db._get_connection() as conn:
        conn.execute("UPDATE lib2_albums SET musicbrainz_id = ? WHERE id = ?", (REL_OTHER, album_id))
        conn.commit()
    row = _album_row(db)
    assert row["musicbrainz_release_group_id"] is None
    assert row["musicbrainz_release_group_attempted"] is None  # back in the backfill queue
    # ...but an UPDATE that sets both keeps the group it set.
    worker.mb_service.update_album_mbid(album_id, REL, "matched", release_group_mbid=RG)
    assert _album_row(db)["musicbrainz_release_group_id"] == RG


def test_re_marking_an_existing_id_keeps_the_group(db, worker):
    album_id = _album(db, release_id=REL, status="matched")
    worker.mb_service.update_album_release_group(album_id, RG)
    worker.mb_service.update_album_mbid(album_id, REL, "matched")
    assert _album_row(db)["musicbrainz_release_group_id"] == RG


def test_wishlist_rows_are_matched_by_album_in_the_background(db, worker):
    wid = _wishlist(db, {
        "id": "sp1", "name": "Song", "artists": [{"name": "Featured Guest"}],
        "album": {"name": "Album A", "artists": [{"name": "The Testers"}]},
    })
    worker.mb_service.match_release = MagicMock(return_value={"mbid": REL, "release_group_mbid": RG})
    item = worker._get_next_item()
    assert item["type"] == "wishlist" and item["id"] == wid
    assert (item["album"], item["artist"]) == ("Album A", "The Testers")
    worker._process_item(item)
    worker.mb_service.match_release.assert_called_once_with("Album A", "The Testers")
    stored = db.get_wishlist_tracks(profile_id=1)[0]
    assert stored["musicbrainz_release_group_id"] == RG
    assert serialize_wishlist_track(stored)["musicbrainz_release_group_id"] == RG
    assert worker._get_next_item() is None


def test_a_wishlist_row_without_a_match_is_marked_and_left_alone(db, worker):
    _wishlist(db, {"id": "sp1", "name": "Song", "artists": ["Nobody"], "album": {"name": "Unknown"}})
    worker.mb_service.match_release = MagicMock(return_value=None)
    worker._process_item(worker._get_next_item())
    stored = db.get_wishlist_tracks(profile_id=1)[0]
    assert stored["musicbrainz_release_group_id"] is None
    assert worker._get_next_item() is None


def test_a_wishlist_row_that_already_carries_a_group_skips_the_search(db, worker):
    _wishlist(db, {"id": "sp1", "name": "Song", "musicbrainz_release_group_id": RG, "album": {"name": "A"}})
    worker.mb_service.match_release = MagicMock()
    worker._process_item(worker._get_next_item())
    worker.mb_service.match_release.assert_not_called()
    assert db.get_wishlist_tracks(profile_id=1)[0]["musicbrainz_release_group_id"] == RG


def test_wishlist_album_query_shapes():
    assert mbw.wishlist_album_query('{"album": "Plain", "artists": ["Solo"]}') == {
        "album": "Plain", "artist": "Solo", "release_group": ""}
    assert mbw.wishlist_album_query("not json") == {"album": "", "artist": "", "release_group": ""}


def test_the_album_api_serves_the_group(db, worker):
    album_id = _album(db, release_id=REL, status="matched")
    worker.mb_service.update_album_release_group(album_id, RG)
    assert serialize_album(db.api_get_album(album_id))["musicbrainz_release_group_id"] == RG
