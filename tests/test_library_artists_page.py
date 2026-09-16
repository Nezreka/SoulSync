"""the library page's artist list (get_library_artists).

every click ran two statements that scanned every artist with a
correlated MIN(id) subquery per row (~750 ms each on a 5k-artist install)
and a counts join over every track of every artist on the page (~470 ms).
a covering index on (server_source, name, id) makes the first two index-only
and the counts come off the album and track indexes per artist row. same
rows, same numbers.
"""

import sqlite3

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    d = MusicDatabase(database_path=str(tmp_path / "music.db"))
    c = sqlite3.connect(str(d.database_path))
    # 'Dupe' exists twice on plex (a split artist row) and once on jellyfin
    artists = [(1, "Alpha", "plex"), (2, "beta", "plex"), (3, "Dupe", "plex"), (4, "Dupe", "plex"), (5, "Dupe", "jellyfin"), (6, "Gamma", "plex")]
    c.executemany("INSERT INTO artists (id, name, server_source) VALUES (?, ?, ?)", artists)
    albums = [(10, 1, "A1", "plex"), (11, 3, "D1", "plex"), (12, 4, "D2", "plex"), (13, 5, "D3", "jellyfin"), (14, 6, "G1", "plex")]
    c.executemany("INSERT INTO albums (id, artist_id, title, server_source) VALUES (?, ?, ?, ?)", albums)
    n = 0
    for alb, art, count in ((10, 1, 3), (11, 3, 2), (12, 4, 5), (13, 5, 1), (14, 6, 4)):
        for i in range(count):
            n += 1
            c.execute("INSERT INTO tracks (id, album_id, artist_id, title, server_source, file_path) VALUES (?, ?, ?, ?, ?, ?)",
                      (n, alb, art, f"t{n}", "plex" if art != 5 else "jellyfin", f"/{n}.flac"))
    c.commit()
    c.close()
    return d


def test_covering_index_exists(db):
    c = sqlite3.connect(str(db.database_path))
    assert c.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_artists_source_name_id'").fetchone()
    plan = c.execute(
        "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM artists a WHERE a.server_source = 'plex' "
        "AND a.id = (SELECT MIN(a2.id) FROM artists a2 WHERE a2.name = a.name AND a2.server_source = a.server_source)").fetchall()
    assert any("idx_artists_source_name_id" in str(row) for row in plan), plan


def test_page_dedups_split_artists_and_counts_across_them(db):
    r = db.get_library_artists(search_query="", letter="", page=1, limit=50, watchlist_filter="all", profile_id=1, source_filter="plex")
    names = [a["name"] for a in r["artists"]]
    assert names == ["Alpha", "beta", "Dupe", "Gamma"]          # case-insensitive order, one Dupe
    assert r["pagination"]["total_count"] == 4
    dupe = next(a for a in r["artists"] if a["name"] == "Dupe")
    # both plex rows merged: 2 albums, 2 + 5 tracks; the jellyfin row is not counted
    assert (dupe["album_count"], dupe["track_count"]) == (2, 7)
    alpha = next(a for a in r["artists"] if a["name"] == "Alpha")
    assert (alpha["album_count"], alpha["track_count"]) == (1, 3)


def test_pagination_and_letter_filter(db):
    p1 = db.get_library_artists(search_query="", letter="", page=1, limit=2, watchlist_filter="all", profile_id=1, source_filter="plex")
    p2 = db.get_library_artists(search_query="", letter="", page=2, limit=2, watchlist_filter="all", profile_id=1, source_filter="plex")
    assert [a["name"] for a in p1["artists"]] == ["Alpha", "beta"]
    assert [a["name"] for a in p2["artists"]] == ["Dupe", "Gamma"]
    assert p1["pagination"]["total_count"] == p2["pagination"]["total_count"] == 4
    d = db.get_library_artists(search_query="", letter="D", page=1, limit=50, watchlist_filter="all", profile_id=1, source_filter="plex")
    assert [a["name"] for a in d["artists"]] == ["Dupe"]
