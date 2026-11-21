"""Per-show Synchronize — a deep scan scoped to ONE show.

sync_show fetches the show's tree from the active server and reconciles the
local rows through the scanner's own ingest (upsert_show_tree prunes vanished
episodes). Deletion is paranoid by design:
  • a server ERROR aborts — it never reads as "show gone"
  • "gone" needs the source's positive not-found signal
  • an EMPTY tree against local episodes is refused (Plex's tree builder
    swallows a mid-fetch failure into an empty seasons list — upserting that
    would prune the entire show)
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import core.video.show_sync as show_sync
from core.video.show_sync import ShowSyncError, sync_show
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent


def _ep(sid, s, e, with_file=True):
    d = {"server_id": str(sid), "season_number": s, "episode_number": e,
         "title": "E%d" % e}
    if with_file:
        d["file"] = {"relative_path": "/tv/show/s%de%d.mkv" % (s, e),
                     "size_bytes": 1000, "resolution": "1080p"}
    return d


def _tree(server_id="sh1", eps=((1, 1), (1, 2))):
    return {"server_id": server_id, "title": "The Show",
            "seasons": [{"season_number": 1, "episodes":
                         [_ep("e%d%d" % (s, e), s, e) for (s, e) in eps]}]}


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video.db"))


@pytest.fixture()
def seeded(db):
    show_id = db.upsert_show_tree("plex", _tree(eps=((1, 1), (1, 2))))
    return db, show_id


class _Source:
    server_name = "plex"

    def __init__(self, tree="unset", raises=None):
        self.tree = tree
        self.raises = raises

    def show_tree(self, server_id, title=None, tmdb_id=None):
        if self.raises:
            raise self.raises
        return self.tree


@pytest.fixture()
def _quiet_scanner(monkeypatch):
    import core.video.scanner as scanner
    monkeypatch.setattr(scanner, "get_video_scanner",
                        lambda db: SimpleNamespace(get_status=lambda: {"state": "idle"}))


def _use_source(monkeypatch, src):
    import core.video.sources as sources
    monkeypatch.setattr(sources, "get_active_video_source", lambda: src)


def test_new_episode_on_server_is_added(seeded, monkeypatch, _quiet_scanner):
    db, show_id = seeded
    _use_source(monkeypatch, _Source(tree=_tree(eps=((1, 1), (1, 2), (1, 3)))))
    res = sync_show(db, show_id)
    assert res["episodes_added"] == 1 and res["episodes_removed"] == 0
    assert res["show_removed"] is False


def test_removed_episode_on_server_is_pruned(seeded, monkeypatch, _quiet_scanner):
    db, show_id = seeded
    _use_source(monkeypatch, _Source(tree=_tree(eps=((1, 1),))))
    res = sync_show(db, show_id)
    assert res["episodes_removed"] == 1 and res["episodes_added"] == 0


def test_show_verifiably_gone_is_removed(seeded, monkeypatch, _quiet_scanner):
    db, show_id = seeded
    _use_source(monkeypatch, _Source(tree=None))
    res = sync_show(db, show_id)
    assert res["show_removed"] is True
    conn = db._get_connection()
    assert conn.execute("SELECT COUNT(*) c FROM shows WHERE id=?",
                        (show_id,)).fetchone()["c"] == 0
    conn.close()


def test_server_error_aborts_and_deletes_nothing(seeded, monkeypatch, _quiet_scanner):
    db, show_id = seeded
    _use_source(monkeypatch, _Source(raises=RuntimeError("server down")))
    with pytest.raises(RuntimeError):
        sync_show(db, show_id)
    conn = db._get_connection()
    assert conn.execute("SELECT COUNT(*) c FROM shows WHERE id=?",
                        (show_id,)).fetchone()["c"] == 1
    conn.close()


def test_empty_tree_against_local_episodes_is_refused(seeded, monkeypatch, _quiet_scanner):
    db, show_id = seeded
    _use_source(monkeypatch, _Source(tree=_tree(eps=())))
    with pytest.raises(ShowSyncError, match="no episodes"):
        sync_show(db, show_id)


def test_wrong_active_server_is_refused(seeded, monkeypatch, _quiet_scanner):
    db, show_id = seeded
    src = _Source(tree=_tree())
    src.server_name = "jellyfin"
    _use_source(monkeypatch, src)
    with pytest.raises(ShowSyncError, match="active server"):
        sync_show(db, show_id)


def test_running_scan_is_refused(seeded, monkeypatch):
    db, show_id = seeded
    import core.video.scanner as scanner
    monkeypatch.setattr(scanner, "get_video_scanner",
                        lambda db: SimpleNamespace(get_status=lambda: {"state": "running"}))
    _use_source(monkeypatch, _Source(tree=_tree()))
    with pytest.raises(ShowSyncError, match="already running"):
        sync_show(db, show_id)


def test_unknown_show_is_refused(db, monkeypatch, _quiet_scanner):
    with pytest.raises(ShowSyncError, match="not found"):
        sync_show(db, 999)


def test_plex_rekey_heals_the_row_instead_of_deleting(seeded, monkeypatch, _quiet_scanner):
    # plex re-keys items on metadata refresh — the old id 404s while the show
    # still exists under a new key. sync must migrate to the new row, never
    # delete the show.
    db, show_id = seeded
    new_tree = _tree(server_id="sh1-NEW", eps=((1, 1), (1, 2), (1, 3)))
    _use_source(monkeypatch, _Source(tree=new_tree))
    res = sync_show(db, show_id)
    assert res["show_removed"] is False
    assert res["rekeyed"] is True
    assert res["show_id"] != show_id
    conn = db._get_connection()
    try:
        # exactly one row remains, under the NEW key, with all episodes
        rows = conn.execute("SELECT id, server_id FROM shows").fetchall()
        assert len(rows) == 1 and rows[0]["server_id"] == "sh1-NEW"
        eps = conn.execute("SELECT COUNT(*) c FROM episodes WHERE show_id=?",
                           (res["show_id"],)).fetchone()["c"]
        assert eps == 3
    finally:
        conn.close()


def test_rekey_keeps_the_ids_and_year_the_new_read_lacks(seeded, monkeypatch, _quiet_scanner):
    # plex's one-show read can come back without the imdb id or year the old
    # row had: the healed row keeps them (and an anime choice made by hand)
    db, show_id = seeded
    conn = db._get_connection()
    conn.execute("UPDATE shows SET imdb_id='tt0105559', tvdb_id=80009, year=1992, "
                 "series_type='anime' WHERE id=?", (show_id,))
    conn.commit()
    conn.close()
    new_tree = dict(_tree(server_id="sh1-NEW"), tmdb_id=43004, tvdb_id=99999)
    _use_source(monkeypatch, _Source(tree=new_tree))
    res = sync_show(db, show_id)
    assert res["rekeyed"] is True
    conn = db._get_connection()
    try:
        row = dict(conn.execute("SELECT tmdb_id, tvdb_id, imdb_id, year, series_type FROM shows "
                                "WHERE id=?", (res["show_id"],)).fetchone())
    finally:
        conn.close()
    # what the server sent wins; what it left out comes from the old row
    assert row == {"tmdb_id": 43004, "tvdb_id": 99999, "imdb_id": "tt0105559",
                   "year": 1992, "series_type": "anime"}


def test_rekey_keeps_what_the_user_locked_and_chose(seeded, monkeypatch, _quiet_scanner):
    # a fix-match locks the ids and title; a padlock, an unfollow and a
    # quality profile are the user's too. the new row is only the server's
    # read, so all of it comes over, and a locked id beats the server's
    import json
    db, show_id = seeded
    conn = db._get_connection()
    conn.execute("INSERT INTO genres (name) VALUES ('Anime')")
    gid = conn.execute("SELECT id FROM genres WHERE name='Anime'").fetchone()[0]
    conn.execute("INSERT INTO show_genres (show_id, genre_id) VALUES (?, ?)", (show_id, gid))
    profile = (conn.execute("INSERT INTO quality_profiles (name) VALUES ('Anime 1080p')").lastrowid,)
    conn.execute("UPDATE shows SET title='Tenchi Muyo!', tmdb_id=43004, imdb_id='tt0105559', "
                 "hands_off=1, monitored=0, quality_profile_id=?, manual_aliases='[\"Tenchi\"]', "
                 "locked_fields=? WHERE id=?",
                 (profile[0],
                  json.dumps(["genres", "imdb_id", "title", "tmdb_id"]), show_id))
    conn.commit()
    conn.close()
    new_tree = dict(_tree(server_id="sh1-NEW"), tmdb_id=99999, imdb_id="tt9999999")
    _use_source(monkeypatch, _Source(tree=new_tree))
    res = sync_show(db, show_id)
    assert res["rekeyed"] is True
    conn = db._get_connection()
    try:
        row = dict(conn.execute("SELECT * FROM shows WHERE id=?", (res["show_id"],)).fetchone())
        genres = [r[0] for r in conn.execute("SELECT genre_id FROM show_genres WHERE show_id=?",
                                             (res["show_id"],))]
    finally:
        conn.close()
    assert (row["title"], row["tmdb_id"], row["imdb_id"]) == ("Tenchi Muyo!", 43004, "tt0105559")
    assert set(json.loads(row["locked_fields"])) >= {"genres", "imdb_id", "title", "tmdb_id"}
    assert (row["hands_off"], row["monitored"]) == (1, 0)
    assert row["manual_aliases"] == '["Tenchi"]'
    assert row["quality_profile_id"] == profile[0]
    assert genres == [gid]


def test_sync_triggers_a_scoped_episode_list_refresh(seeded, monkeypatch, _quiet_scanner):
    # the server only knows about FILES; the aired-episode schedule comes from
    # enrichment — sync asks TMDB for the recent seasons so schedule holes
    # (like a pre-demote-fix deleted episode) heal on the spot
    db, show_id = seeded
    calls = {}

    class _Engine:
        def refresh_show_art(self, sid, **kw):
            calls.update(sid=sid, **kw)
            return {"ok": True}
    import core.video.enrichment.engine as engine_mod
    monkeypatch.setattr(engine_mod, "get_video_enrichment_engine", lambda: _Engine())
    _use_source(monkeypatch, _Source(tree=_tree()))
    sync_show(db, show_id)
    assert calls["sid"] == show_id
    assert calls["recent_seasons_only"] is True and calls["with_ratings"] is False


def test_episode_refresh_failure_never_fails_the_sync(seeded, monkeypatch, _quiet_scanner):
    db, show_id = seeded
    import core.video.enrichment.engine as engine_mod
    monkeypatch.setattr(engine_mod, "get_video_enrichment_engine",
                        lambda: (_ for _ in ()).throw(RuntimeError("tmdb down")))
    _use_source(monkeypatch, _Source(tree=_tree()))
    res = sync_show(db, show_id)
    assert res["status"] == "ok"


def test_source_show_tree_receives_title_and_tmdb(seeded, monkeypatch, _quiet_scanner):
    # the identity hints are what let plex disambiguate a stale key from a
    # genuinely-removed show — sync must pass them
    db, show_id = seeded
    seen = {}

    class _Spy(_Source):
        def show_tree(self, server_id, title=None, tmdb_id=None):
            seen.update(server_id=server_id, title=title, tmdb_id=tmdb_id)
            return _tree()
    _use_source(monkeypatch, _Spy())
    sync_show(db, show_id)
    assert seen["title"] == "The Show"
    assert seen["server_id"] == "sh1"


# ── wiring pins ───────────────────────────────────────────────────────────────

def test_plex_gone_is_notfound_only():
    src = (_ROOT / "core" / "video" / "sources.py").read_text(encoding="utf-8", errors="replace")
    assert "from plexapi.exceptions import NotFound" in src
    assert "except NotFound:" in src


def test_jellyfin_gone_requires_a_live_server():
    src = (_ROOT / "core" / "video" / "sources.py").read_text(encoding="utf-8", errors="replace")
    assert "Jellyfin unreachable" in src


def test_endpoint_and_admin_gate():
    api = (_ROOT / "api" / "video" / "detail.py").read_text(encoding="utf-8", errors="replace")
    assert '"/detail/show/<int:show_id>/sync"' in api
    assert '"/detail/movie/<int:movie_id>/sync"' in api
    # the gate's admin rules moved to api/video/access.py with the
    # permissions overhaul; ask the table itself, not a file's text
    from api.video.access import route_access
    for rule in ("/api/video/detail/show/<int:show_id>/sync",
                 "/api/video/detail/movie/<int:movie_id>/sync"):
        assert route_access(rule, "POST") == ("admin", None), rule


def test_ui_button_and_handler():
    js = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(
        encoding="utf-8", errors="replace")
    assert "moreItem('sync-show'" in js
    assert "function syncShowNow" in js
    assert "/sync'" in js
