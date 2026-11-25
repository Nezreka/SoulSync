"""The popular mark on an album's tracks (#2144).

Popular comes from the last.fm playcount the worker already stores on each
track, ranked inside the album: a few tracks clearly above the album's middle
one. Missing or flat data marks nothing.
"""

from __future__ import annotations

import json

from core.library2 import queries as Q
from core.library2.track_popularity import lastfm_playcount, popular_track_ids

# back in black's real last.fm playcounts: plexamp flames 1, 6 and 7.
BACK_IN_BLACK = {
    1: 7428363, 2: 6221684, 3: 2089356, 4: 839552, 5: 2055463,
    6: 16928651, 7: 12787971, 8: 2435718, 9: 1572122, 10: 1879551,
}


def test_the_top_few_clearly_above_the_middle_are_popular():
    """#2144: same three plexamp marks on back in black."""
    assert popular_track_ids(BACK_IN_BLACK) == {1, 6, 7}


def test_a_flat_album_marks_nothing():
    flat = {i: 100_000 + i * 5_000 for i in range(1, 11)}
    assert popular_track_ids(flat) == set()


def test_too_little_data_marks_nothing():
    """one known track would always look like the hit."""
    sparse = {i: None for i in range(1, 14)}
    sparse[11] = 2_805_077
    assert popular_track_ids(sparse) == set()
    assert popular_track_ids({1: 9_000_000, 2: 10, 3: 12}) == set()


def test_half_the_album_unknown_marks_nothing():
    counts = dict(BACK_IN_BLACK)
    counts.update({i: None for i in range(11, 22)})
    assert popular_track_ids(counts) == set()


def test_tiny_playcounts_are_noise():
    assert popular_track_ids({1: 400, 2: 30, 3: 25, 4: 20, 5: 22}) == set()


def test_never_more_than_three_marks():
    counts = {i: 100_000 for i in range(1, 13)}
    counts.update({1: 5_000_000, 2: 6_000_000, 3: 7_000_000, 4: 8_000_000, 5: 9_000_000})
    assert popular_track_ids(counts) == {3, 4, 5}


def test_playcount_reads_only_real_numbers():
    assert lastfm_playcount('{"lastfm":{"listeners":2941,"playcount":7834}}') == 7834
    assert lastfm_playcount('{"lastfm":{"playcount":"12"}}') == 12
    assert lastfm_playcount('{"lastfm":{"playcount":0}}') is None
    assert lastfm_playcount('{"spotify":{}}') is None
    assert lastfm_playcount("not json") is None
    assert lastfm_playcount(None) is None


def test_album_detail_carries_the_popular_flag(imported_conn):
    """#2144: get_album marks the hits from the stored playcounts, no fetch."""
    conn = imported_conn
    album_id = conn.execute(
        "SELECT album_id FROM lib2_tracks GROUP BY album_id ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()["album_id"]
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM lib2_tracks WHERE album_id=? ORDER BY id", (album_id,))]
    # make sure there are enough tracks to rank.
    while len(ids) < 5:
        cur = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number) "
            "VALUES(?,?,?,1)", (album_id, f"filler {len(ids)}", 90 + len(ids)))
        ids.append(cur.lastrowid)
    hit = ids[0]
    for tid in ids:
        plays = 5_000_000 if tid == hit else 100_000
        conn.execute("UPDATE lib2_tracks SET enrichment=? WHERE id=?",
                     (json.dumps({"lastfm": {"playcount": plays}}), tid))
    conn.commit()

    tracks = Q.get_album(conn, album_id)["tracks"]

    marks = {t["id"]: t["popular"] for t in tracks if t.get("id")}
    assert marks[hit] is True
    assert not any(v for tid, v in marks.items() if tid != hit)
    assert all(t["popular"] is False for t in tracks if not t.get("id"))


# ── listenbrainz when last.fm knows too little (last.fm keys need pro) ──────

class _Row(dict):
    def keys(self):  # sqlite3.Row's interface
        return list(super().keys())


def _rows(counts, service):
    key = "playcount" if service == "lastfm" else "listen_count"
    return [_Row(id=tid, enrichment=json.dumps({service: {key: n}} if n else {}))
            for tid, n in counts.items()]


LB_ALBUM = {1: 4200, 2: 900, 3: 800, 4: 750, 5: 700, 6: 3100}


def test_listenbrainz_ranks_the_album_when_lastfm_has_nothing():
    from core.library2.track_popularity import popular_for_rows
    assert popular_for_rows(_rows(LB_ALBUM, "listenbrainz")) == ({1, 6}, "listenbrainz")


def test_listenbrainz_counts_have_their_own_floor():
    """listenbrainz has far fewer listeners: a few hundred listens can be a hit
    there, but a handful is still noise"""
    from core.library2.track_popularity import popular_for_rows
    tiny = {1: 40, 2: 5, 3: 4, 4: 3, 5: 2}
    assert popular_for_rows(_rows(tiny, "listenbrainz")) == (set(), "listenbrainz")


def test_lastfm_wins_when_it_knows_enough_and_the_two_never_mix():
    from core.library2.track_popularity import popular_for_rows
    rows = []
    for tid, n in BACK_IN_BLACK.items():
        # listenbrainz says track 2 is the hit; last.fm knows enough to decide
        rows.append(_Row(id=tid, enrichment=json.dumps({
            "lastfm": {"playcount": n},
            "listenbrainz": {"listen_count": 99_999 if tid == 2 else 10}})))
    assert popular_for_rows(rows) == ({1, 6, 7}, "lastfm")


def test_a_few_lastfm_counts_hand_the_album_to_listenbrainz():
    from core.library2.track_popularity import popular_for_rows
    rows = []
    for tid, n in LB_ALBUM.items():
        data = {"listenbrainz": {"listen_count": n}}
        if tid == 2:
            data["lastfm"] = {"playcount": 9_000_000}  # one stray last.fm number
        rows.append(_Row(id=tid, enrichment=json.dumps(data)))
    assert popular_for_rows(rows) == ({1, 6}, "listenbrainz")


def test_nothing_known_says_no_source():
    from core.library2.track_popularity import popular_for_rows
    assert popular_for_rows(_rows({1: None, 2: None}, "lastfm")) == (set(), None)
