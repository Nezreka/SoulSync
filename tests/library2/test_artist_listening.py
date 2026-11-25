"""you and an artist on the artist page: your own plays, from your pile.

real MusicDatabase on a tmp file. plays are matched to the artist by name, by
an alias's name, or by their library track link; never by another artist's
plays, never from another profile's pile, never the taste-only import."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.library2.artist_listening import artist_listening, clear_rank_cache
from core.listening_import import spotify_export
from core.listening_import.dedup import insert_import_events
from core.listening_scope import SHARED_OWNER
from database.music_database import MusicDatabase
from tests import lib2_seed


@pytest.fixture()
def db(tmp_path):
    clear_rank_cache()
    return MusicDatabase(str(tmp_path / "music.db"))


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _plays(db, artist, title, n, *, owner=SHARED_OWNER, days=1, lib2=None, source="plex",
           album="Album"):
    events = [{"track_id": f"{source}:{artist}:{title}:{i}:{days}", "title": title, "artist": artist,
               "album": album, "played_at": _ago(days + i), "lib2_track_id": lib2,
               "scrobble_eligible": 1} for i in range(n)]
    insert_import_events(db, events, source, profile_id=owner)


def _seed(db):
    conn = db._get_connection()
    try:
        song = lib2_seed.track(conn, "Band", "Album", "Song")
        lib2_seed.track(conn, "Band", "Album", "Other")
        artist_id = lib2_seed.artist(conn, "Band")
        conn.commit()
        return artist_id, song
    finally:
        conn.close()


def _read(db, artist_id, profile_id=SHARED_OWNER):
    conn = db._get_connection()
    try:
        return artist_listening(db, conn, artist_id, profile_id)
    finally:
        conn.close()


def test_nothing_played_is_nothing_to_show(db):
    artist_id, _song = _seed(db)
    _plays(db, "Someone Else", "Song", 3)
    assert _read(db, artist_id) is None


def test_plays_counts_dates_and_top_songs(db):
    artist_id, song = _seed(db)
    _plays(db, "Band", "Song", 5, days=1)
    _plays(db, "Band", "Other", 2, days=40)
    _plays(db, "Band", "Old Hit", 1, days=400)

    out = _read(db, artist_id)

    assert out["play_count"] == 8
    assert out["plays_30d"] == 5
    assert out["first_played"] < out["last_played"]
    assert [t["title"] for t in out["top_tracks"]] == ["Song", "Other", "Old Hit"]
    top = out["top_tracks"][0]
    assert top["play_count"] == 5 and top["track_id"] == song
    assert top["thumb_url"].startswith("/api/library/v2/artwork/album/")
    # the song not in the library has no link
    assert out["top_tracks"][2]["track_id"] is None
    assert len(out["months"]) == 12 and sum(m["plays"] for m in out["months"]) == 7


def test_a_play_under_another_credit_still_counts_by_its_track_link(db):
    """a play filed as "Band feat. Guest", or linked from another server's
    copy, belongs to Band through its library track"""
    artist_id, song = _seed(db)
    _plays(db, "Band feat. Guest", "Song", 2, lib2=song)
    _plays(db, "Band feat. Guest", "Unlinked", 4)

    out = _read(db, artist_id)

    assert out["play_count"] == 2


def test_an_aliases_name_counts_as_the_artist(db):
    artist_id, _song = _seed(db)
    conn = db._get_connection()
    try:
        alias = lib2_seed.artist(conn, "The Band")
        conn.execute("UPDATE lib2_artists SET canonical_artist_id = ? WHERE id = ?",
                     (artist_id, alias))
        conn.commit()
    finally:
        conn.close()
    _plays(db, "Band", "Song", 1)
    _plays(db, "The Band", "Song", 2)

    assert _read(db, artist_id)["play_count"] == 3
    # opened from the alias row, it is the same artist
    assert _read(db, alias)["play_count"] == 3


def test_rank_among_your_artists(db):
    artist_id, _song = _seed(db)
    _plays(db, "Top Act", "A", 9)
    _plays(db, "Band", "Song", 4)
    _plays(db, "Small Act", "B", 1)

    out = _read(db, artist_id)

    assert out["rank"] == 2 and out["artists_played"] == 3


def test_only_your_own_pile(db):
    artist_id, _song = _seed(db)
    kim = db.create_profile(name="kim")
    db.set_profile_listenbrainz(kim, "token", "", "kim_lb")
    _plays(db, "Band", "Song", 3, owner=kim, source="listenbrainz")
    _plays(db, "Band", "Song", 1, owner=SHARED_OWNER)

    assert _read(db, artist_id, kim)["play_count"] == 3
    assert _read(db, artist_id, SHARED_OWNER)["play_count"] == 1


def test_a_taste_only_import_stays_off_the_page(db):
    artist_id, _song = _seed(db)
    _plays(db, "Band", "Song", 1)
    ended = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    spotify_export.import_batch(db, [{"ended_at": ended, "ms_played": 200000, "track": "Other",
                                      "artist": "Band"}], SHARED_OWNER, include_in_stats=False)

    assert _read(db, artist_id)["play_count"] == 1
    spotify_export.set_include_in_stats(db, SHARED_OWNER, True)
    clear_rank_cache()
    assert _read(db, artist_id)["play_count"] == 2


def test_the_query_walks_the_indexes(db):
    """both halves of the match use an index, never a scan of the history"""
    artist_id, _song = _seed(db)
    _plays(db, "Band", "Song", 2)
    conn = db._get_connection()
    try:
        plan = conn.execute("""
            EXPLAIN QUERY PLAN
            SELECT id FROM listening_history WHERE artist IN (?)
            UNION
            SELECT id FROM listening_history WHERE lib2_track_id IN (
                SELECT t.id FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id
                WHERE al.primary_artist_id IN (?))
        """, ("Band", artist_id)).fetchall()
    finally:
        conn.close()
    details = " | ".join(str(r[-1]) for r in plan)
    assert "idx_listening_artist" in details
    assert "idx_listening_lib2_track" in details
    assert "SCAN listening_history" not in details
