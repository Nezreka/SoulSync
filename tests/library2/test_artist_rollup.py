"""The artist list's cached ordering key.

`lib2_artist_rollup` exists because SQLite will not build an automatic index on
the right-hand side of a `LEFT JOIN` against a CTE, so the only way to order
12,000 artists by an aggregate without re-running it per row is to put the
aggregate in a real table with a real primary key (11,469 ms -> 46 ms measured;
see core/library2/artist_rollup.py).

The hazard a cache introduces is DISAGREEMENT: the roll-up decides the order,
while the numbers rendered next to each artist come from `list_artists`' exact
per-page CTEs. If the two ever compute different things, the list is sorted by
one number and labelled with another. That is what most of this file pins.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2 import queries as Q
from core.library2.artist_rollup import (
    ensure_fresh_artist_rollup,
    refresh_artist_rollup,
)
from core.library2.schema import ensure_library_v2_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    yield c
    c.close()


def _artist(conn, name, canonical=None):
    return conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, canonical_artist_id) VALUES(?,?,?)",
        (name, name, canonical),
    ).lastrowid


def _album(conn, artist_id, title, tracks=1, *, owned=True, album_type="album"):
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type,origin,monitored) "
        "VALUES(?,?,?,'library',1)",
        (artist_id, title, album_type),
    ).lastrowid
    conn.execute("INSERT INTO lib2_album_artists(album_id,artist_id) VALUES(?,?)",
                 (album_id, artist_id))
    for number in range(tracks):
        track_id = conn.execute(
            "INSERT INTO lib2_tracks(album_id,title,track_number,monitored) VALUES(?,?,?,1)",
            (album_id, f"{title} {number}", number + 1),
        ).lastrowid
        conn.execute("INSERT INTO lib2_track_artists(track_id,artist_id) VALUES(?,?)",
                     (track_id, artist_id))
        if owned:
            conn.execute(
                "INSERT INTO lib2_track_files(track_id,path,is_primary,file_state,size) "
                "VALUES(?,?,1,'active',1000)", (track_id, f"/music/{track_id}.flac"))
    return album_id


def _seed(conn):
    """Two canonical artists, one of them with an ALIAS that owns a release."""
    aphex = _artist(conn, "Aphex Twin")
    boards = _artist(conn, "Boards of Canada")
    afx = _artist(conn, "AFX", canonical=aphex)
    _album(conn, aphex, "SAW 85-92", tracks=5)
    _album(conn, aphex, "Drukqs", tracks=3)
    _album(conn, afx, "Analogue Bubblebath", tracks=2)
    _album(conn, boards, "Music Has the Right", tracks=4)
    conn.commit()
    return aphex, boards, afx


def test_the_rollup_matches_the_numbers_the_page_renders(conn):
    """The sort key and the rendered count must be the same number.

    Including the alias fold: AFX's release belongs to Aphex Twin in both.
    """
    aphex, boards, _afx = _seed(conn)
    rows, _total = Q.list_artists(conn, sort="albums")
    rendered = {row["name"]: (row["album_count"], row["track_count"]) for row in rows}

    cached = {
        int(row["artist_id"]): (int(row["album_count"]), int(row["track_count"]))
        for row in conn.execute("SELECT * FROM lib2_artist_rollup")
    }

    assert rendered["Aphex Twin"] == (3, 10)
    assert rendered["Boards of Canada"] == (1, 4)
    assert cached[aphex] == rendered["Aphex Twin"]
    assert cached[boards] == rendered["Boards of Canada"]
    # The alias is not listed on its own and gets no row of its own.
    assert set(cached) == {aphex, boards}


@pytest.mark.parametrize("sort", ["name", "albums", "tracks"])
def test_every_sort_returns_the_same_counts(conn, sort):
    _seed(conn)
    rows, total = Q.list_artists(conn, sort=sort)
    assert total == 2
    assert {r["name"]: r["album_count"] for r in rows} == {
        "Aphex Twin": 3, "Boards of Canada": 1,
    }


def test_ordering_actually_follows_the_counts(conn):
    _seed(conn)
    small = _artist(conn, "Zero Albums")
    conn.commit()

    by_albums = [r["name"] for r in Q.list_artists(conn, sort="albums")[0]]
    assert by_albums[0] == "Aphex Twin"
    assert by_albums[-1] == "Zero Albums", "an artist with no albums must sort last"
    assert small  # silence the unused warning; the id is not needed


def test_a_new_artist_invalidates_the_rollup_immediately(conn):
    """Age alone is not enough.

    Adding or removing an artist is the one drift a user notices at once --
    they sort by album count right after an import and the new artist is
    missing from the ordering -- and the row count is already in hand, so it
    costs nothing to check.
    """
    _seed(conn)
    Q.list_artists(conn, sort="albums")            # builds the roll-up
    assert ensure_fresh_artist_rollup(conn) is False, "should be fresh"

    newcomer = _artist(conn, "Autechre")
    _album(conn, newcomer, "Amber", tracks=9)
    conn.commit()

    assert ensure_fresh_artist_rollup(conn) is True, "an added artist must invalidate"
    row = conn.execute(
        "SELECT album_count, track_count FROM lib2_artist_rollup WHERE artist_id=?",
        (newcomer,)).fetchone()
    assert (row["album_count"], row["track_count"]) == (1, 9)


def test_singles_and_unowned_releases_follow_the_same_rule_as_the_page(conn):
    """`album_count` counts non-single releases the user owns or monitors --
    the roll-up has to apply that predicate identically or the order disagrees
    with the label."""
    artist = _artist(conn, "Mix")
    _album(conn, artist, "A Real Album", tracks=2)
    _album(conn, artist, "A Single", tracks=1, album_type="single")
    conn.commit()

    rows, _ = Q.list_artists(conn, sort="albums")
    rendered = rows[0]
    cached = conn.execute(
        "SELECT album_count FROM lib2_artist_rollup WHERE artist_id=?",
        (artist,)).fetchone()

    assert rendered["album_count"] == 1
    assert rendered["single_count"] == 1
    assert cached["album_count"] == rendered["album_count"]


def test_refresh_is_idempotent_and_replaces_rather_than_accumulates(conn):
    _seed(conn)
    first = refresh_artist_rollup(conn)
    second = refresh_artist_rollup(conn)
    assert first == second == 2
    assert conn.execute("SELECT COUNT(*) FROM lib2_artist_rollup").fetchone()[0] == 2


def test_an_empty_library_does_not_thrash(conn):
    """Nothing to roll up must not mean "rebuild on every request"."""
    assert ensure_fresh_artist_rollup(conn) is False
    assert ensure_fresh_artist_rollup(conn) is False


def test_concurrent_requests_rebuild_once(tmp_path):
    """Two count-sorted requests arriving on a stale roll-up must not both
    write. The loser would sit on `busy_timeout` behind the winner for no
    benefit — on a READ path.

    A file-backed DB with one connection PER THREAD, because sqlite3 refuses a
    connection used from a thread other than the one that made it — which is
    also how the production code works (every request opens its own).
    """
    import threading

    import core.library2.artist_rollup as mod

    db_path = str(tmp_path / "rollup.db")
    seed = sqlite3.connect(db_path)
    seed.row_factory = sqlite3.Row
    ensure_library_v2_schema(seed)
    _seed(seed)
    seed.close()

    calls = []
    real = mod.refresh_artist_rollup

    def counting(c):
        calls.append(1)
        return real(c)

    mod.refresh_artist_rollup = counting
    errors = []
    try:
        barrier = threading.Barrier(2)

        def worker():
            try:
                own = sqlite3.connect(db_path, timeout=30)
                own.row_factory = sqlite3.Row
                try:
                    barrier.wait(timeout=10)
                    mod.ensure_fresh_artist_rollup(own)
                finally:
                    own.close()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
    finally:
        mod.refresh_artist_rollup = real

    assert errors == []
    assert len(calls) == 1, f"rebuilt {len(calls)} times, expected 1"

    check = sqlite3.connect(db_path)
    try:
        assert check.execute(
            "SELECT COUNT(*) FROM lib2_artist_rollup").fetchone()[0] == 2
    finally:
        check.close()
