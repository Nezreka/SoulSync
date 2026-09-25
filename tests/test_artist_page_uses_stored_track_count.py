"""the artist page reads albums.api_track_count before fetching a tracklist.

the enrichment workers write the provider's track count onto almost every
album (66,949 of 69,654 on boulder's install), and the artist page's
completion check never read it: for a discography whose cards carry no
count (deezer's artist list doesn't) it fetched the tracklist of every
owned album, one network call per item, on every visit.

now: when the matched library album is provably the card's release (its
stored id for the card's source equals the card's id) and it has a stored
count, that count is used and nothing is fetched. an album without a count
is fetched exactly as before, and the answer is written back so it is
never fetched again. a match that is NOT provably the same release keeps
fetching: another edition can have a different tracklist.
"""

import sqlite3

import pytest

import core.metadata.completion as completion
from database.music_database import MusicDatabase


# Upstream stores the provider's total in ``albums.api_track_count``. The
# catalogue's own name for that number -- the true total from metadata, as
# opposed to ``track_count``, which is what the server reported -- is
# ``lib2_albums.expected_track_count``, and that is the column the ported
# get/set pair reads. Seeded through the shared catalogue helper, so the album
# ids the assertions use are the ones the fixture actually created.
ALBUMS = {}


@pytest.fixture()
def db(tmp_path):
    from tests.support.catalogue_seed import seed_album, seed_artist, seed_track

    d = MusicDatabase(database_path=str(tmp_path / "music.db"))
    ALBUMS.clear()
    conn = d._get_connection()
    try:
        artist = seed_artist(conn, server_id='ar-1', name='Oasis')
        # 10: proven deezer release with a stored count; 11: proven, no count yet;
        # 12: same title, different deezer id (another edition), stored count
        spec = ((10, 'Definitely Maybe', 'dz-10', 11, 11),
                (11, 'Be Here Now', 'dz-11', None, 12),
                (12, 'Morning Glory', 'dz-12-deluxe', 20, 12))
        for key, title, deezer_id, expected, files in spec:
            album = seed_album(conn, server_id=f'al-{key}', title=title, artist_id=artist)
            ALBUMS[key] = album
            conn.execute(
                "UPDATE lib2_albums SET expected_track_count=?,"
                " external_ids=json_set(COALESCE(external_ids,'{}'), '$.deezer', ?)"
                " WHERE id=?", (expected, deezer_id, album))
            for i in range(1, files + 1):
                seed_track(conn, server_id=f'tr-{key}-{i}', title=f'Track {key}-{i}',
                           album_id=album, artist_id=artist, track_number=i,
                           file_path=f'/m/{key}/{i}.flac')
        conn.commit()
    finally:
        conn.close()
    return d


def _counts(db, ids):
    return {int(k): v for k, v in db.get_album_api_track_counts(ids).items()}


def _check(db, card, calls, fetch_result=14):
    """run check_album_completion the way the stream does: pooled candidates,
    with the network call counted."""
    import core.metadata.completion as mod
    albums = db.get_candidate_albums_for_artist("Oasis", server_source="plex")
    tracks = db.get_candidate_tracks_for_albums([a.id for a in albums])
    ids = db.get_album_source_ids([a.id for a in albums])
    counts = db.get_album_api_track_counts([a.id for a in albums])
    original = mod.get_album_tracks_for_source
    mod.get_album_tracks_for_source = lambda src, aid: calls.append((src, aid)) or {"items": [{}] * fetch_result}
    try:
        return completion.check_album_completion(
            db, card, "Oasis", source_chain=["deezer"],
            candidate_albums=albums, candidate_tracks=tracks,
            completeness_cache=db.build_candidate_completeness_cache(albums, tracks),
            album_source_ids_cache=ids, canonical_cache={}, track_cache={}, pin_tracks_cache={},
            api_counts_cache=counts,
        )
    finally:
        mod.get_album_tracks_for_source = original


def test_stored_count_is_used_and_nothing_is_fetched(db):
    calls = []
    result = _check(db, {"id": "dz-10", "name": "Definitely Maybe", "total_tracks": 0}, calls)
    assert calls == []
    assert result["expected_tracks"] == 11
    assert result["status"] == "completed"


def test_album_without_a_count_is_fetched_once_and_remembered(db):
    calls = []
    first = _check(db, {"id": "dz-11", "name": "Be Here Now", "total_tracks": 0}, calls, fetch_result=14)
    assert calls == [("deezer", "dz-11")]
    assert first["expected_tracks"] == 14
    assert first["status"] == "partial"           # 12 of 14
    assert _counts(db, [ALBUMS[11]]) == {ALBUMS[11]: 14}
    calls = []
    second = _check(db, {"id": "dz-11", "name": "Be Here Now", "total_tracks": 0}, calls)
    assert calls == []
    assert second["expected_tracks"] == first["expected_tracks"]
    assert second["status"] == first["status"]


def test_a_different_edition_still_fetches(db):
    """the card is the standard edition; the library holds the deluxe with a
    stored count of 20. not the same release, so the stored count must not be
    used for this card."""
    calls = []
    result = _check(db, {"id": "dz-12-standard", "name": "Morning Glory", "total_tracks": 0}, calls, fetch_result=12)
    assert calls == [("deezer", "dz-12-standard")]
    assert result["expected_tracks"] == 12
    # and the deluxe row's own count is untouched
    assert _counts(db, [ALBUMS[12]]) == {ALBUMS[12]: 20}


def test_a_card_that_carries_its_count_needs_neither(db):
    calls = []
    result = _check(db, {"id": "dz-10", "name": "Definitely Maybe", "total_tracks": 11}, calls)
    assert calls == []
    assert result["expected_tracks"] == 11


def test_remember_never_overwrites_an_existing_count(db):
    assert db.set_album_api_track_count(ALBUMS[10], 99) is False
    assert _counts(db, [ALBUMS[10]]) == {ALBUMS[10]: 11}
    assert db.set_album_api_track_count(ALBUMS[11], 0) is False
    assert db.set_album_api_track_count(ALBUMS[11], "x") is False
    assert db.set_album_api_track_count(ALBUMS[11], 12) is True
    assert _counts(db, [ALBUMS[10], ALBUMS[11], ALBUMS[12]]) == {ALBUMS[10]: 11, ALBUMS[11]: 12, ALBUMS[12]: 20}
