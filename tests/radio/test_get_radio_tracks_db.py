"""End-to-end behavioral pin for MusicDatabase.get_radio_tracks.

Phase 0a extracted the radio SELECTION logic into core.radio.selection but the
DB method still owns the SQL. These tests drive the REAL get_radio_tracks
against a real catalogue database to prove the refactor preserved behavior —
the 4-tier fallback (same-artist cap → genre → mood/style → random), dedup, and
exclude handling all still work through the extracted helpers.

Rows are seeded the way the media-server scan leaves them (see
tests/support/catalogue_seed.py); the helpers hand back the catalogue id, which
is what the assertions compare against.
"""

import pytest

from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_album, seed_artist, seed_track


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _add_artist(db, aid, name, genres="", mood="", style=""):
    conn = db._get_connection()
    artist_id = seed_artist(conn, server_id=aid, name=name)
    conn.execute("UPDATE lib2_artists SET genres=?, mood=?, style=? WHERE id=?",
                 (genres or '[]', mood, style, artist_id))
    conn.commit()
    return artist_id


def _add_album(db, alid, aid, title, genres="", mood="", style=""):
    conn = db._get_connection()
    album_id = seed_album(conn, server_id=alid, title=title, artist_id=aid)
    conn.execute("UPDATE lib2_albums SET genres=?, mood=?, style=? WHERE id=?",
                 (genres or '[]', mood, style, album_id))
    conn.commit()
    return album_id


def _add_track(db, tid, alid, aid, title, file_path="/m/x.flac", play_count=0):
    conn = db._get_connection()
    track_id = seed_track(conn, server_id=tid, title=title, album_id=alid,
                          artist_id=aid, track_number=1, duration=200,
                          file_path=file_path or None, bitrate=1000)
    conn.execute("UPDATE lib2_tracks SET play_count=? WHERE id=?",
                 (play_count, track_id))
    conn.commit()
    return track_id


def test_missing_seed_track_returns_failure(db):
    res = db.get_radio_tracks("nope", limit=10)
    assert res["success"] is False


def test_tier1_same_artist_other_albums(db):
    ar1 = _add_artist(db, "ar1", "Artist One")
    al1 = _add_album(db, "al1", ar1, "Album A")
    al2 = _add_album(db, "al2", ar1, "Album B")
    seed = _add_track(db, "seed", al1, ar1, "Seed")
    t2 = _add_track(db, "t2", al2, ar1, "Other Album Track")

    res = db.get_radio_tracks(seed, limit=10)
    assert res["success"] is True
    ids = [t["id"] for t in res["tracks"]]
    assert t2 in ids
    assert seed not in ids          # seed always excluded


def test_excludes_caller_supplied_ids(db):
    ar1 = _add_artist(db, "ar1", "Artist One")
    al1 = _add_album(db, "al1", ar1, "Album A")
    al2 = _add_album(db, "al2", ar1, "Album B")
    seed = _add_track(db, "seed", al1, ar1, "Seed")
    t2 = _add_track(db, "t2", al2, ar1, "T2")
    t3 = _add_track(db, "t3", al2, ar1, "T3")

    res = db.get_radio_tracks(seed, limit=10, exclude_ids=[t2])
    ids = [t["id"] for t in res["tracks"]]
    assert t2 not in ids
    assert t3 in ids


def test_tier2_genre_match_other_artists(db):
    # No same-artist alternatives; falls to genre tier.
    ar1 = _add_artist(db, "ar1", "Seed Artist", genres='["shoegaze"]')
    ar2 = _add_artist(db, "ar2", "Other Artist", genres='["shoegaze"]')
    al1 = _add_album(db, "al1", ar1, "Seed Album", genres='["shoegaze"]')
    al2 = _add_album(db, "al2", ar2, "Other Album", genres='["shoegaze"]')
    seed = _add_track(db, "seed", al1, ar1, "Seed")
    g1 = _add_track(db, "g1", al2, ar2, "Genre Match")

    res = db.get_radio_tracks(seed, limit=10)
    ids = [t["id"] for t in res["tracks"]]
    assert g1 in ids


def test_tier4_random_fallback_fills_when_no_metadata_match(db):
    # Seed has no genre/mood/style and no same-artist alts → random tier.
    ar1 = _add_artist(db, "ar1", "Seed Artist")
    ar2 = _add_artist(db, "ar2", "Unrelated")
    al1 = _add_album(db, "al1", ar1, "Seed Album")
    al2 = _add_album(db, "al2", ar2, "Unrelated Album")
    seed = _add_track(db, "seed", al1, ar1, "Seed")
    r1 = _add_track(db, "r1", al2, ar2, "Random One")

    res = db.get_radio_tracks(seed, limit=10)
    ids = [t["id"] for t in res["tracks"]]
    assert r1 in ids                # filled from random tier


def test_only_returns_tracks_with_files(db):
    ar1 = _add_artist(db, "ar1", "Artist One")
    al1 = _add_album(db, "al1", ar1, "Album A")
    al2 = _add_album(db, "al2", ar1, "Album B")
    seed = _add_track(db, "seed", al1, ar1, "Seed")
    nofile = _add_track(db, "nofile", al2, ar1, "No File", file_path="")

    res = db.get_radio_tracks(seed, limit=10)
    ids = [t["id"] for t in res["tracks"]]
    assert nofile not in ids        # a track without a live file never plays


def test_a_deleted_file_does_not_qualify_a_track(db):
    """A deleted file row is history (ADR-03), not something to queue up."""
    ar1 = _add_artist(db, "ar1", "Artist One")
    al1 = _add_album(db, "al1", ar1, "Album A")
    al2 = _add_album(db, "al2", ar1, "Album B")
    seed = _add_track(db, "seed", al1, ar1, "Seed")
    gone = _add_track(db, "gone", al2, ar1, "Deleted File")
    conn = db._get_connection()
    conn.execute("UPDATE lib2_track_files SET file_state='deleted' WHERE track_id=?",
                 (gone,))
    conn.commit()

    res = db.get_radio_tracks(seed, limit=10)
    assert gone not in [t["id"] for t in res["tracks"]]


def test_discography_tracks_are_not_queued(db):
    """A tracked artist's provider discography lives in the same tables. It has
    no files, so it must never reach the queue."""
    ar1 = _add_artist(db, "ar1", "Artist One")
    al1 = _add_album(db, "al1", ar1, "Album A")
    seed = _add_track(db, "seed", al1, ar1, "Seed")
    conn = db._get_connection()
    wish_album = seed_album(conn, server_id="al-wish", title="Not Owned",
                            artist_id=ar1, origin='discography')
    wish = seed_track(conn, server_id="t-wish", title="Wanted",
                      album_id=wish_album, artist_id=ar1)
    conn.commit()

    res = db.get_radio_tracks(seed, limit=10)
    assert wish not in [t["id"] for t in res["tracks"]]


def test_no_duplicate_ids_across_tiers(db):
    # A track that qualifies for both same-artist AND genre must appear once.
    ar1 = _add_artist(db, "ar1", "Artist One", genres='["pop"]')
    al1 = _add_album(db, "al1", ar1, "Album A", genres='["pop"]')
    al2 = _add_album(db, "al2", ar1, "Album B", genres='["pop"]')
    seed = _add_track(db, "seed", al1, ar1, "Seed")
    dup = _add_track(db, "dup", al2, ar1, "Could Match Twice")

    res = db.get_radio_tracks(seed, limit=10)
    ids = [t["id"] for t in res["tracks"]]
    assert ids.count(dup) == 1


def test_smart_ranking_prefers_more_played_in_same_tier(db):
    """Phase 2: within a tier, the ranker surfaces the heavily-played track
    first out of the fetched pool.

    Robustness note: this proves the ranking is WIRED IN end-to-end. The pool
    factor (4x, floored) means with these few candidates the whole set is
    fetched, so ranking is deterministic here. The deterministic guarantee of
    the ranking *math* lives in TestRankCandidates / TestScoreCandidate (unit
    level) — those can't pass against pre-Phase-2 code at all. We seed many
    unplayed decoys so a pre-Phase-2 ``ORDER BY RANDOM()`` would only return
    'hit' first by a ~1-in-N fluke, making the wiring claim meaningful."""
    ar1 = _add_artist(db, "ar1", "Artist One")
    al1 = _add_album(db, "al1", ar1, "Seed Album")
    al2 = _add_album(db, "al2", ar1, "Other Album")
    seed = _add_track(db, "seed", al1, ar1, "Seed")
    for i in range(15):
        _add_track(db, f"rare{i}", al2, ar1, f"Rarely Played {i}", play_count=0)
    hit = _add_track(db, "hit", al2, ar1, "Big Hit", play_count=5000)

    res = db.get_radio_tracks(seed, limit=5)
    assert res["success"] is True
    ids = [t["id"] for t in res["tracks"]]
    # The heavily-played track is ranked first out of the same-artist pool.
    assert ids[0] == hit


def test_global_popularity_ranks_when_nothing_was_played_locally(db):
    """Global popularity lives in the Library-v2 enrichment payload."""
    ar1 = _add_artist(db, "ar1", "Artist One")
    al1 = _add_album(db, "al1", ar1, "Seed Album")
    al2 = _add_album(db, "al2", ar1, "Other Album")
    seed = _add_track(db, "seed", al1, ar1, "Seed")
    for i in range(15):
        _add_track(db, f"rare{i}", al2, ar1, f"Unknown {i}")
    famous = _add_track(db, "famous", al2, ar1, "Famous Elsewhere")
    conn = db._get_connection()
    conn.execute(
        "UPDATE lib2_tracks SET enrichment='{\"lastfm\": {\"playcount\": 9000000}}'"
        " WHERE id=?", (famous,))
    conn.commit()

    res = db.get_radio_tracks(seed, limit=5)
    assert [t["id"] for t in res["tracks"]][0] == famous


# ── Library Radio (seedless mode) ─────────────────────────────────────────


def test_library_radio_returns_tracks_with_no_seed_and_no_excludes(db):
    """The seedless path with an EMPTY exclude set must not emit `NOT IN ()`
    (a sqlite syntax error) — the clause is conditional."""
    ar1 = _add_artist(db, "ar1", "A One")
    ar2 = _add_artist(db, "ar2", "A Two")
    al1 = _add_album(db, "al1", ar1, "Album One")
    al2 = _add_album(db, "al2", ar2, "Album Two")
    t1 = _add_track(db, "t1", al1, ar1, "T1")
    t2 = _add_track(db, "t2", al2, ar2, "T2")

    res = db.get_library_radio_tracks(limit=10)
    assert res["success"] is True
    assert {t["id"] for t in res["tracks"]} == {t1, t2}


def test_library_radio_honors_excludes_and_file_filter(db):
    ar1 = _add_artist(db, "ar1", "A One")
    al1 = _add_album(db, "al1", ar1, "Album One")
    keep = _add_track(db, "keep", al1, ar1, "Keep")
    skip = _add_track(db, "skip", al1, ar1, "Skip Me")
    _add_track(db, "nofile", al1, ar1, "No File", file_path="")

    res = db.get_library_radio_tracks(limit=10, exclude_ids=[skip])
    ids = [t["id"] for t in res["tracks"]]
    assert ids == [keep]


def test_library_radio_ranking_is_wired(db):
    """Same wiring claim as the seeded test: the heavily-played track ranks
    first out of the pooled random fetch."""
    ar1 = _add_artist(db, "ar1", "A One")
    al1 = _add_album(db, "al1", ar1, "Album One")
    for i in range(15):
        _add_track(db, f"rare{i}", al1, ar1, f"Rare {i}", play_count=0)
    hit = _add_track(db, "hit", al1, ar1, "Big Hit", play_count=5000)

    res = db.get_library_radio_tracks(limit=5)
    assert res["success"] is True
    assert res["tracks"][0]["id"] == hit


def test_library_radio_respects_limit(db):
    ar1 = _add_artist(db, "ar1", "A One")
    al1 = _add_album(db, "al1", ar1, "Album One")
    for i in range(30):
        _add_track(db, f"t{i}", al1, ar1, f"T {i}")

    res = db.get_library_radio_tracks(limit=5)
    assert len(res["tracks"]) == 5
