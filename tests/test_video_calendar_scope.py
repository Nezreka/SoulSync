"""Calendar source scope: watchlist (default) vs all-library.

watchlist_only restricts the feed to the EFFECTIVE watchlist — explicit show
follows ∪ airing library shows (not muted) — so the calendar tracks what you
follow, with an 'All library' escape hatch.
"""

from __future__ import annotations

import pytest

from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _seed_show(db, tmdb_id, title, server_id, air_date="2026-06-20"):
    return db.upsert_show_tree("plex", {
        "server_id": server_id, "title": title, "tmdb_id": tmdb_id,
        "seasons": [{"season_number": 1, "episodes": [
            {"episode_number": 1, "title": "E1", "air_date": air_date}]}]})


def _set_status(db, show_id, status):
    conn = db._get_connection()
    conn.execute("UPDATE shows SET status=? WHERE id=?", (status, show_id))
    conn.commit()
    conn.close()


def _tmdbs(rows):
    return {r["show_tmdb_id"] for r in rows}


def test_watchlist_scope_only_returns_followed_shows(db):
    _seed_show(db, 11, "A", "sA")
    b = _seed_show(db, 22, "B", "sB")
    _set_status(db, b, "Ended")              # KNOWN-terminal → excluded unless followed
    _seed_show(db, 44, "C", "sC")            # NULL status (enrichment gap)
    db.add_to_watchlist("show", 11, "A")

    allp = db.calendar_upcoming("2026-06-01", "2026-06-30", server_source="plex", watchlist_only=False)
    wl = db.calendar_upcoming("2026-06-01", "2026-06-30", server_source="plex", watchlist_only=True)
    assert _tmdbs(allp) == {11, 22, 44}      # all-library sees everything
    # watchlist = followed ∪ airing-not-muted. A show with an episode in the
    # window is airing BY DEFINITION, so C's missing status must not hide it
    # (the enrichment-gap rule); only known-terminal B drops out.
    assert _tmdbs(wl) == {11, 44}


def test_watchlist_scope_includes_airing_default_and_respects_mute(db):
    a = _seed_show(db, 33, "Airing", "sA")
    _set_status(db, a, "Returning Series")

    # airing show, not explicitly followed → still in the watchlist scope by default
    wl = db.calendar_upcoming("2026-06-01", "2026-06-30", server_source="plex", watchlist_only=True)
    assert _tmdbs(wl) == {33}

    # mute it → drops out of the watchlist scope (but still in all-library)
    db.remove_from_watchlist("show", 33)     # stores a 'mute' tombstone
    wl2 = db.calendar_upcoming("2026-06-01", "2026-06-30", server_source="plex", watchlist_only=True)
    assert wl2 == []
    allp = db.calendar_upcoming("2026-06-01", "2026-06-30", server_source="plex", watchlist_only=False)
    assert _tmdbs(allp) == {33}

# The scope toggle and the episode modal's wishlist action are React now
# (webui/src/routes/calendar), pinned in calendar-page.test.tsx.
