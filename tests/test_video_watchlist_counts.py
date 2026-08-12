"""watchlist_counts must agree with the effective watchlist — cheaply.

Perf sweep (Aug 2026): the counts used to call list_watchlist() three times
just for len(), and the show pass runs two correlated episode-count
subqueries PER AIRING SHOW (~3s a pass on a real library) — so the sidebar
badge cost ~9s of database time per hit. The rewrite counts in SQL; this
test pins that its arithmetic matches the materialized list exactly across
the tricky cases: explicit follows, muted airing shows, auto-included airing
library shows, ended shows, tmdb-less shows, and duplicate library rows
sharing one tmdb_id.
"""

from __future__ import annotations

from database.video_database import VideoDatabase


def _db(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "video.db"))
    with db._get_connection() as conn:
        c = conn.cursor()
        # Airing library shows (status != ended → auto-watchlisted).
        c.execute("INSERT INTO shows (id, title, tmdb_id, status) VALUES (1, 'Airing A', 100, 'Continuing')")
        c.execute("INSERT INTO shows (id, title, tmdb_id, status) VALUES (2, 'Airing B', 200, 'Returning Series')")
        # Duplicate library row for the SAME tmdb id — must count once.
        c.execute("INSERT INTO shows (id, title, tmdb_id, status) VALUES (3, 'Airing B copy', 200, 'Continuing')")
        # Ended → never auto-included.
        c.execute("INSERT INTO shows (id, title, tmdb_id, status) VALUES (4, 'Done', 300, 'Ended')")
        # Airing but muted → excluded.
        c.execute("INSERT INTO shows (id, title, tmdb_id, status) VALUES (5, 'Muted', 400, 'Continuing')")
        # Airing but no tmdb id → excluded (nothing to key on).
        c.execute("INSERT INTO shows (id, title, tmdb_id, status) VALUES (6, 'No Id', NULL, 'Continuing')")
        # Explicit follows: one show that ALSO airs (must not double-count),
        # one show not in the library at all, a person and a studio.
        c.execute("""INSERT INTO video_watchlist (kind, tmdb_id, title, state)
                     VALUES ('show', 100, 'Airing A', 'follow')""")
        c.execute("""INSERT INTO video_watchlist (kind, tmdb_id, title, state)
                     VALUES ('show', 999, 'Followed Only', 'follow')""")
        c.execute("""INSERT INTO video_watchlist (kind, tmdb_id, title, state)
                     VALUES ('show', 400, 'Muted', 'mute')""")
        c.execute("""INSERT INTO video_watchlist (kind, tmdb_id, title, state)
                     VALUES ('person', 7000, 'Director X', 'follow')""")
        c.execute("""INSERT INTO video_watchlist (kind, tmdb_id, title, state)
                     VALUES ('studio', 8000, 'A24', 'follow')""")
        conn.commit()
    return db


def test_counts_match_the_materialized_effective_list(tmp_path):
    db = _db(tmp_path)
    counts = db.watchlist_counts()
    # Ground truth stays the (expensive) materialized list.
    truth = {k: len(db.list_watchlist(k)) for k in ("show", "person", "studio")}
    assert counts["show"] == truth["show"]
    assert counts["person"] == truth["person"]
    assert counts["studio"] == truth["studio"]
    assert counts["total"] == sum(truth.values())
    # And the arithmetic itself: follows(100, 999) + auto(200 once) = 3 shows.
    assert counts["show"] == 3
    assert counts["person"] == 1
    assert counts["studio"] == 1
