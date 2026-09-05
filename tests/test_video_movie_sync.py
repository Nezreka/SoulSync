"""Per-movie Synchronize - a deep scan scoped to ONE movie."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.video.movie_sync import MovieSyncError, sync_movie
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent


def _movie(server_id="m1", title="The Movie", files=1):
    d = {"server_id": server_id, "title": title, "year": 2026, "tmdb_id": 123}
    if files:
        d["files"] = [
            {"relative_path": "/movies/%s-%d.mkv" % (title, i), "size_bytes": 1000 + i}
            for i in range(files)
        ]
        d["file"] = d["files"][0]
    return d


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video.db"))


@pytest.fixture()
def seeded(db):
    mid = db.upsert_movie("plex", _movie(files=1))
    return db, mid


class _Source:
    server_name = "plex"

    def __init__(self, item="unset", raises=None):
        self.item = item
        self.raises = raises
        self.seen = {}

    def movie_item(self, server_id, title=None, tmdb_id=None):
        self.seen.update(server_id=server_id, title=title, tmdb_id=tmdb_id)
        if self.raises:
            raise self.raises
        return self.item


@pytest.fixture()
def _quiet_scanner(monkeypatch):
    import core.video.scanner as scanner
    monkeypatch.setattr(scanner, "get_video_scanner",
                        lambda db: SimpleNamespace(get_status=lambda: {"state": "idle"}))


def _use_source(monkeypatch, src):
    import core.video.sources as sources
    monkeypatch.setattr(sources, "get_active_video_source", lambda: src)


def _file_count(db, movie_id):
    with db.connect() as c:
        return c.execute("SELECT COUNT(*) FROM media_files WHERE movie_id=?", (movie_id,)).fetchone()[0]


def test_new_movie_file_on_server_is_added(seeded, monkeypatch, _quiet_scanner):
    db, mid = seeded
    _use_source(monkeypatch, _Source(item=_movie(files=2)))
    res = sync_movie(db, mid)
    assert res["files_added"] == 1 and res["files_removed"] == 0
    assert res["movie_removed"] is False
    assert _file_count(db, res["movie_id"]) == 2


def test_removed_movie_file_on_server_is_pruned(seeded, monkeypatch, _quiet_scanner):
    db, mid = seeded
    _use_source(monkeypatch, _Source(item=_movie(files=0)))
    res = sync_movie(db, mid)
    assert res["files_removed"] == 1 and res["files_added"] == 0
    assert _file_count(db, res["movie_id"]) == 0


def test_movie_verifiably_gone_is_removed(seeded, monkeypatch, _quiet_scanner):
    db, mid = seeded
    _use_source(monkeypatch, _Source(item=None))
    res = sync_movie(db, mid)
    assert res["movie_removed"] is True
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM movies WHERE id=?", (mid,)).fetchone()[0] == 0


def test_server_error_aborts_and_deletes_nothing(seeded, monkeypatch, _quiet_scanner):
    db, mid = seeded
    _use_source(monkeypatch, _Source(raises=RuntimeError("server down")))
    with pytest.raises(RuntimeError):
        sync_movie(db, mid)
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM movies WHERE id=?", (mid,)).fetchone()[0] == 1


def test_wrong_active_server_is_refused(seeded, monkeypatch, _quiet_scanner):
    db, mid = seeded
    src = _Source(item=_movie())
    src.server_name = "jellyfin"
    _use_source(monkeypatch, src)
    with pytest.raises(MovieSyncError, match="active server"):
        sync_movie(db, mid)


def test_running_scan_is_refused(seeded, monkeypatch):
    db, mid = seeded
    import core.video.scanner as scanner
    monkeypatch.setattr(scanner, "get_video_scanner",
                        lambda db: SimpleNamespace(get_status=lambda: {"state": "running"}))
    _use_source(monkeypatch, _Source(item=_movie()))
    with pytest.raises(MovieSyncError, match="already running"):
        sync_movie(db, mid)


def test_plex_rekey_heals_the_row_instead_of_deleting(seeded, monkeypatch, _quiet_scanner):
    db, mid = seeded
    _use_source(monkeypatch, _Source(item=_movie(server_id="m1-NEW", files=2)))
    res = sync_movie(db, mid)
    assert res["movie_removed"] is False
    assert res["rekeyed"] is True
    assert res["movie_id"] != mid
    with db.connect() as c:
        rows = c.execute("SELECT id, server_id FROM movies").fetchall()
        assert len(rows) == 1 and rows[0]["server_id"] == "m1-NEW"


def test_sync_triggers_metadata_refresh(seeded, monkeypatch, _quiet_scanner):
    db, mid = seeded
    calls = {}

    class _Engine:
        def refresh_movie_art(self, movie_id):
            calls["movie_id"] = movie_id
            return {"ok": True}

    import core.video.enrichment.engine as engine_mod
    monkeypatch.setattr(engine_mod, "get_video_enrichment_engine", lambda: _Engine())
    _use_source(monkeypatch, _Source(item=_movie()))
    res = sync_movie(db, mid)
    assert res["metadata_refresh"] == "ok"
    assert calls["movie_id"] == mid


def test_source_movie_item_receives_title_and_tmdb(seeded, monkeypatch, _quiet_scanner):
    db, mid = seeded
    src = _Source(item=_movie())
    _use_source(monkeypatch, src)
    sync_movie(db, mid)
    assert src.seen == {"server_id": "m1", "title": "The Movie", "tmdb_id": 123}


def test_endpoint_and_ui_wiring_pins():
    api = (_ROOT / "api" / "video" / "detail.py").read_text(encoding="utf-8", errors="replace")
    js = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(encoding="utf-8", errors="replace")
    assert '"/detail/movie/<int:movie_id>/sync"' in api
    assert 'data-vd-act="sync-movie"' in js
    assert "function syncMovieNow" in js
    assert "/detail/movie/" in js and "/sync'" in js


def test_source_adapters_expose_movie_item():
    src = (_ROOT / "core" / "video" / "sources.py").read_text(encoding="utf-8", errors="replace")
    assert "def movie_item(self, server_id, title=None, tmdb_id=None):" in src
    assert "Plex: movie rekey-check search failed" in src
    assert "Jellyfin unreachable - cannot verify the movie's state" in src
