"""Continue Watching: the answer to "where was I".

The data has been in the schema since the scanner shipped - view_offset_ms is
literally commented "resume position (Continue Watching)" - and nothing ever
read it. This is the read.

The rules that decide whether the row is any good:

* A title watched past the tail credits is FINISHED. Without a done threshold
  the row fills with things you completed and never clears itself, which is how
  a resume row stops being trusted.
* A few seconds in is an accident. A wrong click should not follow you around.
* ONE ROW PER SHOW. A binge would otherwise push everything else off the rail
  with eight episodes of the same programme.
* Finishing an episode should hand you the NEXT one, not empty the row at
  exactly the moment you want to keep going. Plex calls this On Deck.
* Only what you OWN. You cannot resume a file you do not have.
"""

from __future__ import annotations

import pytest

from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "v.db"))


def _movie(db, title, *, runtime=100, offset=None, viewed="2026-09-01", has_file=1):
    mid = db.upsert_movie("plex", {
        "server_id": "m-" + title, "title": title, "year": 2020,
        "runtime_minutes": runtime, "poster_url": "/p.jpg", "backdrop_url": "/b.jpg",
        "file": {"relative_path": title + ".mkv", "size_bytes": 1},
    }) if has_file else db.upsert_movie("plex", {
        "server_id": "m-" + title, "title": title, "year": 2020,
        "runtime_minutes": runtime})
    conn = db._get_connection()
    # backdrop_url is written by ENRICHMENT, not by the scan upsert, so it has
    # to be set directly here. Worth knowing in itself: a freshly scanned,
    # un-enriched movie has only a poster, and the rail falls back to it.
    conn.execute("UPDATE movies SET view_offset_ms=?, last_viewed_at=?, has_file=?,"
                 " backdrop_url='/b.jpg' WHERE id=?",
                 (offset, viewed, has_file, mid))
    conn.commit()
    conn.close()
    return mid


def _show(db, title, episodes):
    """episodes: [(season, ep, has_file, play_count, offset, viewed, runtime)]"""
    sid = db.upsert_show_tree("plex", {
        "server_id": "s-" + title, "title": title, "poster_url": "/sp.jpg",
        "backdrop_url": "/sb.jpg",
        "seasons": [{"season_number": s, "episodes": [
            {"episode_number": e, "title": "E%d" % e, "runtime_minutes": rt,
             "still_url": "/still.jpg",
             "file": ({"relative_path": "e.mkv", "size_bytes": 1} if hf else None)}
            for (s2, e, hf, pc, off, vw, rt) in episodes if s2 == s]}
            for s in sorted({x[0] for x in episodes})],
    })
    conn = db._get_connection()
    for (s, e, hf, pc, off, vw, rt) in episodes:
        conn.execute("""UPDATE episodes SET has_file=?, play_count=?, view_offset_ms=?,
                        last_viewed_at=? WHERE show_id=? AND season_number=? AND episode_number=?""",
                     (hf, pc, off, vw, sid, s, e))
    conn.commit()
    conn.close()
    return sid


# ── what counts as still watching ────────────────────────────────────────────
def test_a_movie_halfway_through_is_offered(db):
    _movie(db, "Arrival", runtime=100, offset=50 * 60_000)
    rows = db.continue_watching()
    assert [r["title"] for r in rows] == ["Arrival"]
    assert rows[0]["reason"] == "in_progress"


def test_a_movie_watched_to_the_credits_is_finished_not_continuing(db):
    """Past the done threshold it is over. A resume row that keeps offering
    things you completed is a row you stop reading."""
    _movie(db, "Arrival", runtime=100, offset=95 * 60_000)
    assert db.continue_watching() == []


def test_thirty_seconds_in_is_a_misclick_not_a_resume_point(db):
    _movie(db, "Arrival", runtime=100, offset=8_000)
    assert db.continue_watching() == []


def test_an_unknown_runtime_still_trusts_a_real_offset(db):
    """A thin scan leaves runtime null. Refusing to resume those would hide
    genuinely half-watched films."""
    _movie(db, "Arrival", runtime=None, offset=20 * 60_000)
    assert len(db.continue_watching()) == 1


def test_an_unknown_runtime_still_discards_the_accidental_start(db):
    _movie(db, "Arrival", runtime=None, offset=5_000)
    assert db.continue_watching() == []


def test_you_cannot_resume_what_you_do_not_own(db):
    _movie(db, "Arrival", runtime=100, offset=50 * 60_000, has_file=0)
    assert db.continue_watching() == []


# ── shows ────────────────────────────────────────────────────────────────────
def test_a_part_watched_episode_names_itself(db):
    _show(db, "Silo", [(1, 1, 1, 0, 12 * 60_000, "2026-09-02", 50)])
    row = db.continue_watching()[0]
    assert row["kind"] == "show" and row["reason"] == "in_progress"
    assert row["title"] == "Silo"
    assert "S1 E1" in row["subtitle"]


def test_finishing_an_episode_hands_you_the_next_one(db):
    """The moment the row would otherwise empty is exactly when you want it to
    say what is next."""
    _show(db, "Silo", [
        (1, 1, 1, 1, 49 * 60_000, "2026-09-02", 50),   # finished
        (1, 2, 1, 0, None, None, 50),                  # owned, unwatched
    ])
    row = db.continue_watching()[0]
    assert row["reason"] == "up_next"
    assert "S1 E2" in row["subtitle"]
    assert row["view_offset_ms"] == 0


def test_up_next_crosses_into_the_following_season(db):
    _show(db, "Silo", [
        (1, 10, 1, 1, 49 * 60_000, "2026-09-02", 50),
        (2, 1, 1, 0, None, None, 50),
    ])
    assert "S2 E1" in db.continue_watching()[0]["subtitle"]


def test_up_next_skips_an_episode_you_do_not_own(db):
    _show(db, "Silo", [
        (1, 1, 1, 1, 49 * 60_000, "2026-09-02", 50),
        (1, 2, 0, 0, None, None, 50),      # not owned
        (1, 3, 1, 0, None, None, 50),
    ])
    assert "S1 E3" in db.continue_watching()[0]["subtitle"]


def test_caught_up_on_a_show_shows_no_card_at_all(db):
    """Nothing to offer beats an empty card saying you are done."""
    _show(db, "Silo", [(1, 1, 1, 1, 49 * 60_000, "2026-09-02", 50)])
    assert db.continue_watching() == []


def test_a_binge_gets_ONE_card_not_eight(db):
    """Per-episode rows would push everything else off the rail, and the answer
    to "where was I" is a single card."""
    _show(db, "Silo", [
        (1, 1, 1, 1, 49 * 60_000, "2026-09-01", 50),
        (1, 2, 1, 1, 49 * 60_000, "2026-09-02", 50),
        (1, 3, 1, 0, 10 * 60_000, "2026-09-03", 50),
        (1, 4, 1, 0, None, None, 50),
    ])
    rows = db.continue_watching()
    assert len(rows) == 1
    # the newest activity wins, so it is the part-watched E3
    assert "S1 E3" in rows[0]["subtitle"]


# ── ordering and shape ───────────────────────────────────────────────────────
def test_most_recently_watched_leads(db):
    _movie(db, "Older", runtime=100, offset=50 * 60_000, viewed="2026-08-01")
    _movie(db, "Newer", runtime=100, offset=50 * 60_000, viewed="2026-09-03")
    assert [r["title"] for r in db.continue_watching()] == ["Newer", "Older"]


def test_a_card_carries_what_the_ui_needs_without_a_second_lookup(db):
    _movie(db, "Arrival", runtime=100, offset=50 * 60_000)
    row = db.continue_watching()[0]
    for key in ("kind", "reason", "id", "title", "subtitle", "image_url",
                "runtime_minutes", "view_offset_ms", "last_viewed_at"):
        assert key in row, f"card is missing {key}"


def test_an_episode_prefers_its_own_still_over_the_show_art(db):
    """A landscape still of the actual episode is the picture people recognise;
    falling back to the show backdrop is a worse but acceptable answer."""
    _show(db, "Silo", [(1, 1, 1, 0, 12 * 60_000, "2026-09-02", 50)])
    url = db.continue_watching()[0]["image_url"]
    assert "/episode/" in url


# ── artwork must go through the proxy ────────────────────────────────────────
def test_art_is_a_proxy_path_not_the_stored_url(db):
    """TMDB writes absolute links that load anywhere; a Plex or Jellyfin scan
    writes a path relative to a server the browser has no route to. Emitting the
    stored url meant art appeared only for the items that happened to have been
    enriched - half the rail came up blank."""
    _movie(db, "Arrival", runtime=100, offset=50 * 60_000)
    url = db.continue_watching()[0]["image_url"]
    assert url.startswith("/api/video/"), url
    assert "/b.jpg" not in url and "/p.jpg" not in url


def test_a_movie_prefers_its_backdrop_and_falls_back_to_the_poster(db):
    _movie(db, "Arrival", runtime=100, offset=50 * 60_000)
    assert "/backdrop/movie/" in db.continue_watching()[0]["image_url"]

    conn = db._get_connection()
    conn.execute("UPDATE movies SET backdrop_url = NULL")
    conn.commit()
    conn.close()
    assert "/poster/movie/" in db.continue_watching()[0]["image_url"]


def test_an_item_with_no_art_at_all_gets_an_empty_string(db):
    """Which is the UI's cue to draw a letter tile rather than a broken image."""
    _movie(db, "Arrival", runtime=100, offset=50 * 60_000)
    conn = db._get_connection()
    conn.execute("UPDATE movies SET backdrop_url = NULL, poster_url = NULL")
    conn.commit()
    conn.close()
    assert db.continue_watching()[0]["image_url"] == ""


def test_the_row_is_bounded(db):
    for i in range(30):
        _movie(db, "Film%02d" % i, runtime=100, offset=50 * 60_000,
               viewed="2026-09-%02d" % (i + 1))
    assert len(db.continue_watching(limit=8)) == 8
