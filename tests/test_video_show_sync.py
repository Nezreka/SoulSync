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
    gate = (_ROOT / "api" / "video" / "__init__.py").read_text(encoding="utf-8", errors="replace")
    assert '"/detail/show/<int:show_id>/sync"' in api
    assert '"/sync"' in gate


def test_ui_button_and_handler():
    js = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(
        encoding="utf-8", errors="replace")
    assert 'data-vd-act="sync-show"' in js
    assert "function syncShowNow" in js
    assert "/sync'" in js
