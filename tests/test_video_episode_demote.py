"""Vanished episodes demote to missing instead of being erased (the Silo-E03 hole).

An enrichment-created missing episode that gets downloaded acquires a
server_id via the scan upsert. If its file then disappears from the server,
the old prune hard-DELETEd the row — the episode vanished from the page
entirely (1, 2, 4, 5…), and nothing re-created it because the full episode
sync is one-time. Episodes are facts; only files are server-owned:

  • a vanished episode WITH enrichment identity (air_date or tvdb_id) is
    demoted — files cleared, server_id NULL, row (title/air date/overview)
    kept, so the page still shows it as missing and it stays wishlistable
  • identity-less server junk is still deleted
  • enrichment rows (server_id NULL) remain untouched, as before
"""

from __future__ import annotations

import pytest

from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video.db"))


def _ep(sid, e, *, air_date=None, tvdb=None, with_file=True, title=None):
    d = {"server_id": sid, "season_number": 1, "episode_number": e,
         "title": title or ("E%d" % e), "air_date": air_date, "tvdb_id": tvdb}
    if with_file:
        d["file"] = {"relative_path": "/tv/silo/s1e%d.mkv" % e,
                     "size_bytes": 1000, "resolution": "1080p"}
    return d


def _tree(eps):
    return {"server_id": "silo", "title": "Silo",
            "seasons": [{"season_number": 1, "episodes": eps}]}


def _rows(db, show_id):
    conn = db._get_connection()
    try:
        return {r["episode_number"]: dict(r) for r in conn.execute(
            "SELECT episode_number, server_id, has_file, title, air_date, "
            "(SELECT COUNT(*) FROM media_files f WHERE f.episode_id=episodes.id) AS files "
            "FROM episodes WHERE show_id=?", (show_id,))}
    finally:
        conn.close()


def test_silo_e03_scenario_demotes_instead_of_erasing(db):
    # e1..e3 on the server (e3 was downloaded after airing), then e3's file
    # disappears — the next scan tree only carries e1, e2
    show_id = db.upsert_show_tree("plex", _tree([
        _ep("p1", 1, air_date="2026-07-02"),
        _ep("p2", 2, air_date="2026-07-09"),
        _ep("p3", 3, air_date="2026-07-16"),
    ]))
    db.upsert_show_tree("plex", _tree([
        _ep("p1", 1, air_date="2026-07-02"),
        _ep("p2", 2, air_date="2026-07-09"),
    ]))
    rows = _rows(db, show_id)
    assert 3 in rows, "an aired episode must never vanish from the page"
    assert rows[3]["has_file"] == 0 and rows[3]["server_id"] is None
    assert rows[3]["files"] == 0
    assert rows[3]["air_date"] == "2026-07-16"          # identity kept
    # and the detail tree shows it as missing, not absent
    eps = db.show_detail(show_id)["seasons"][0]["episodes"]
    assert [e["episode_number"] for e in eps] == [1, 2, 3]
    assert eps[2]["owned"] is False


def test_identityless_junk_is_still_deleted(db):
    show_id = db.upsert_show_tree("plex", _tree([
        _ep("p1", 1, air_date="2026-07-02"),
        _ep("p9", 9),                                    # no air_date, no tvdb id
    ]))
    db.upsert_show_tree("plex", _tree([_ep("p1", 1, air_date="2026-07-02")]))
    rows = _rows(db, show_id)
    assert 9 not in rows


def test_tvdb_id_also_counts_as_identity(db):
    show_id = db.upsert_show_tree("plex", _tree([
        _ep("p1", 1, air_date="2026-07-02"),
        _ep("p3", 3, tvdb=987654),
    ]))
    db.upsert_show_tree("plex", _tree([_ep("p1", 1, air_date="2026-07-02")]))
    assert 3 in _rows(db, show_id)


def test_backfill_restores_a_deleted_episode_row_silo_shape(db):
    # the EXACT live shape from the report: E1/E2 server-owned, E3's row
    # deleted (pre-demote-fix prune), E4+ enrichment rows — a TMDB season
    # backfill must re-INSERT the hole, not just gap-fill existing rows
    show_id = db.upsert_show_tree("plex", _tree([
        _ep("p1", 1, air_date="2026-07-02"),
        _ep("p2", 2, air_date="2026-07-09"),
    ]))
    db.backfill_episodes(show_id, 1, [
        {"episode_number": n, "title": "E%d" % n, "air_date": "2026-07-%02d" % (2 + 7 * (n - 1))}
        for n in range(1, 11)
    ])
    conn = db._get_connection()
    conn.execute("DELETE FROM episodes WHERE show_id=? AND season_number=1 AND episode_number=3",
                 (show_id,))
    conn.commit()
    conn.close()
    assert 3 not in _rows(db, show_id)          # the hole, as on the live box

    touched = db.backfill_episodes(show_id, 1, [
        {"episode_number": n, "title": "E%d" % n, "air_date": "2026-07-%02d" % (2 + 7 * (n - 1))}
        for n in range(1, 11)
    ])
    rows = _rows(db, show_id)
    assert 3 in rows, "backfill must restore the deleted episode row"
    assert rows[3]["has_file"] == 0
    assert touched >= 1
    # owned rows untouched
    assert rows[1]["has_file"] == 1 and rows[2]["has_file"] == 1


def test_demoted_episode_can_be_repromoted(db):
    # re-download: the file comes back on the server → owned again, same row
    show_id = db.upsert_show_tree("plex", _tree([
        _ep("p3", 3, air_date="2026-07-16"),
    ]))
    db.upsert_show_tree("plex", _tree([]))               # gone → demoted
    assert _rows(db, show_id)[3]["has_file"] == 0
    db.upsert_show_tree("plex", _tree([
        _ep("p3b", 3, air_date="2026-07-16"),
    ]))
    rows = _rows(db, show_id)
    assert rows[3]["has_file"] == 1 and rows[3]["server_id"] == "p3b"
    assert rows[3]["files"] == 1
