"""A re-match must not leave the previous title's episodes behind.

Reported: show "Lucky (2026)" re-matched from an older series of the same
name. The show row updated correctly, but the episode list stayed a graft of
two shows — episodes 1-2 from 2026 and 3-8 still airing 2022-12-27.

The cause is that backfill_episodes gap-fills
(COALESCE(NULLIF(col, ''), ?)) and UPSERTs by (show, season, episode). The new
title only has two episodes, so nothing ever touched 3-8 and nothing could
correct them — the same reasoning rematch_item already applies to credits,
which were dropped for exactly this reason. Episodes were simply missed.

The split matters and is the point of these tests: an episode with a FILE is
a fact about the library and must survive (the Silo E03 rule — a scan demotes,
never deletes), while an episode the old match invented as "missing" is not a
fact about this show at all.

Hermetic: a tmp VideoDatabase, no server, no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def vdb(tmp_path):
    from database.video_database import VideoDatabase
    return VideoDatabase(database_path=str(tmp_path / "video.db"))


def _show(vdb, **over):
    conn = vdb._get_connection()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(shows)").fetchall()}
        data = {"title": "Lucky (2026)", "year": 2026, "tmdb_id": 111,
                "tmdb_match_status": "matched"}
        data.update(over)
        data = {k: v for k, v in data.items() if k in cols}
        keys = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        cur = conn.execute(f"INSERT INTO shows ({keys}) VALUES ({marks})", tuple(data.values()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _episode(vdb, show_id, season, number, title, has_file, air_date="2022-12-27"):
    conn = vdb._get_connection()
    try:
        conn.execute("INSERT OR IGNORE INTO seasons (show_id, season_number) VALUES (?, ?)",
                     (show_id, season))
        sid = conn.execute("SELECT id FROM seasons WHERE show_id=? AND season_number=?",
                           (show_id, season)).fetchone()["id"]
        cols = {r[1] for r in conn.execute("PRAGMA table_info(episodes)").fetchall()}
        data = {"show_id": show_id, "season_id": sid, "season_number": season,
                "episode_number": number, "title": title, "air_date": air_date,
                "has_file": 1 if has_file else 0}
        data = {k: v for k, v in data.items() if k in cols}
        keys = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        conn.execute(f"INSERT INTO episodes ({keys}) VALUES ({marks})", tuple(data.values()))
        conn.commit()
    finally:
        conn.close()


def _episodes(vdb, show_id):
    conn = vdb._get_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT episode_number, title, air_date, has_file FROM episodes "
            "WHERE show_id=? ORDER BY episode_number", (show_id,)).fetchall()]
    finally:
        conn.close()


class TestRematchClearsThePreviousTitle:
    def test_invented_missing_episodes_are_removed(self, vdb):
        show = _show(vdb)
        _episode(vdb, show, 1, 1, "No Shortcuts", has_file=True, air_date="2026-07-14")
        _episode(vdb, show, 1, 2, "Make 'em Dance", has_file=True, air_date="2026-07-14")
        for n, t in [(3, "The Talented Mr Ferrari"), (4, "Enemy. Friend. Comrade."),
                     (5, "The Show"), (6, "Four Kings"), (7, "Ringmaster"),
                     (8, "And Then I Was Gone")]:
            _episode(vdb, show, 1, n, t, has_file=False)

        assert len(_episodes(vdb, show)) == 8
        assert vdb.rematch_item("show", show, "tmdb", 278624) is True

        after = _episodes(vdb, show)
        assert [e["episode_number"] for e in after] == [1, 2], \
            "the previous title's phantom episodes are gone"

    def test_owned_episodes_survive_with_their_file(self, vdb):
        # The Silo E03 rule: a row backed by a real file is a fact about the
        # library. Losing it would lose ownership, not just metadata.
        show = _show(vdb)
        _episode(vdb, show, 1, 1, "Wrong Title", has_file=True)
        vdb.rematch_item("show", show, "tmdb", 278624)
        after = _episodes(vdb, show)
        assert len(after) == 1
        assert after[0]["has_file"] == 1

    def test_owned_episode_text_is_cleared_so_it_can_refill(self, vdb):
        # backfill_episodes only fills gaps, so leaving the wrong show's title
        # in place would freeze it there forever.
        show = _show(vdb)
        _episode(vdb, show, 1, 1, "The Talented Mr Ferrari", has_file=True)
        vdb.rematch_item("show", show, "tmdb", 278624)
        after = _episodes(vdb, show)[0]
        assert after["title"] is None
        assert after["air_date"] is None

    def test_the_backfill_then_refills_the_right_title(self, vdb):
        show = _show(vdb)
        _episode(vdb, show, 1, 1, "The Talented Mr Ferrari", has_file=True)
        vdb.rematch_item("show", show, "tmdb", 278624)
        vdb.backfill_episodes(show, 1, [
            {"episode_number": 1, "title": "No Shortcuts", "air_date": "2026-07-14"},
            {"episode_number": 2, "title": "Make 'em Dance", "air_date": "2026-07-14"},
        ])
        after = _episodes(vdb, show)
        assert [e["title"] for e in after] == ["No Shortcuts", "Make 'em Dance"]
        assert after[0]["has_file"] == 1, "the owned one kept its file"
        assert after[1]["has_file"] == 0, "the new one is correctly missing"

    def test_emptied_seasons_are_pruned(self, vdb):
        show = _show(vdb)
        _episode(vdb, show, 2, 1, "Old S2E1", has_file=False)
        vdb.rematch_item("show", show, "tmdb", 278624)
        conn = vdb._get_connection()
        try:
            left = conn.execute("SELECT COUNT(*) c FROM seasons WHERE show_id=?",
                                (show,)).fetchone()["c"]
        finally:
            conn.close()
        assert left == 0


class TestScopedNarrowly:
    def test_an_imdb_repoint_leaves_episodes_alone(self, vdb):
        # imdb only feeds ratings/backfills — it does not re-source the
        # episode list, so nothing should be thrown away.
        show = _show(vdb)
        _episode(vdb, show, 1, 1, "Keep Me", has_file=False)
        vdb.rematch_item("show", show, "imdb", "tt34866681")
        assert len(_episodes(vdb, show)) == 1

    def test_movies_are_untouched(self, vdb):
        # There is no episode table involvement for a movie re-match; this
        # simply must not raise.
        conn = vdb._get_connection()
        try:
            cur = conn.execute("INSERT INTO movies (title, year) VALUES ('Lucky', 2026)")
            conn.commit()
            movie_id = cur.lastrowid
        finally:
            conn.close()
        assert vdb.rematch_item("movie", movie_id, "tmdb", 999) is True
