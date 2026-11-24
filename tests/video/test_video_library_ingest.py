"""a download is in the library the moment it lands (core/video/library_ingest.py).

the importer writes the title and file rows itself, with an ``ss:`` id, so a
landed movie or episode is owned before plex or jellyfin scans it in. the
server's scan then takes those rows over instead of adding a second one, and
no scan prunes them while their file is still on disk.
"""

from __future__ import annotations

import json
import os

import pytest

from core.video import library_ingest as li
from core.video import owned_ids
from database.video_database import VideoDatabase


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    monkeypatch.setattr(li, "library_label", lambda _db: "plex")
    monkeypatch.setattr("core.video.library_roots.library_roots", lambda _db, kind=None: [])
    monkeypatch.setattr("core.video.library_manager.expire", lambda *a, **k: None)
    return db, tmp_path


def _file(tmp_path, rel, size=1000):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    return str(p)


def _q(db, sql, *args):
    conn = db._get_connection()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _movie_dl(tmdb=603, title="The Matrix", year=1999, **kw):
    return {"id": 1, "kind": "movie", "media_source": "tmdb", "media_id": str(tmdb),
            "title": title, "year": year, "quality_label": "1080p",
            "release_title": "The.Matrix.1999.1080p.BluRay.x264.DV.Atmos-GRP",
            "search_ctx": json.dumps({"scope": "movie", "title": title, "year": year}), **kw}


def _ep_dl(tmdb=1399, s=1, e=2, title="Silo"):
    return {"id": 2, "kind": "show", "media_source": "tmdb", "media_id": str(tmdb),
            "title": title, "quality_label": "1080p", "release_title": f"Silo.S0{s}E0{e}.1080p.WEB-DL",
            "search_ctx": json.dumps({"scope": "episode", "season": s, "episode": e,
                                      "title": title, "episode_title": "Holston"})}


def _plex_movie(sid="501", tmdb=603, path="/plex/movies/The Matrix (1999)/m.mkv"):
    return {"server_id": sid, "title": "The Matrix", "year": 1999, "tmdb_id": tmdb,
            "files": [{"relative_path": path, "size_bytes": 1000, "resolution": "1080"}]}


# ── movies ───────────────────────────────────────────────────────────────────

def test_a_landed_movie_is_owned_at_once(env):
    db, tmp = env
    path = _file(tmp, "Movies/The Matrix (1999)/The Matrix (1999).mkv")
    out = li.ingest_landed(db, _movie_dl(), path)
    rows = _q(db, "SELECT * FROM movies")
    assert len(rows) == 1 and rows[0]["id"] == out["id"]
    m = rows[0]
    assert m["server_source"] == "plex" and owned_ids.is_owned(m["server_id"])
    assert m["tmdb_id"] == 603 and m["has_file"] == 1 and m["title"] == "The Matrix"
    # enrichment fills the rest in by the tmdb id
    assert m["tmdb_match_status"] is None
    f = _q(db, "SELECT * FROM media_files WHERE movie_id=?", m["id"])
    assert [x["relative_path"] for x in f] == [path]
    assert f[0]["resolution"] == "1080p" and f[0]["size_bytes"] == 1000
    assert f[0]["dynamic_range"] == "DV" and f[0]["atmos"] == 1


def test_the_servers_scan_takes_the_row_over(env):
    db, tmp = env
    path = _file(tmp, "Movies/The Matrix (1999)/m.mkv")
    mid = li.ingest_landed(db, _movie_dl(), path)["id"]
    db.upsert_movie("plex", _plex_movie())
    rows = _q(db, "SELECT id, server_source, server_id FROM movies")
    assert rows == [{"id": mid, "server_source": "plex", "server_id": "501"}]
    # the server's file view from here on
    assert [f["relative_path"] for f in _q(db, "SELECT * FROM media_files")] == \
        ["/plex/movies/The Matrix (1999)/m.mkv"]


def test_another_servers_rows_and_server_rows_are_never_claimed(env):
    db, tmp = env
    db.upsert_movie("jellyfin", {"server_id": "j1", "title": "The Matrix", "tmdb_id": 603})
    db.upsert_movie("plex", _plex_movie(sid="501"))
    db.upsert_movie("plex", _plex_movie(sid="502"))   # a second plex copy stays a second row
    assert len(_q(db, "SELECT id FROM movies")) == 3


def test_a_soulsync_library_is_taken_over_by_a_new_server(env, monkeypatch):
    db, tmp = env
    monkeypatch.setattr(li, "library_label", lambda _db: "soulsync")
    mid = li.ingest_landed(db, _movie_dl(), _file(tmp, "Movies/The Matrix (1999)/m.mkv"))["id"]
    db.upsert_movie("plex", _plex_movie())
    assert _q(db, "SELECT id, server_source FROM movies") == [{"id": mid, "server_source": "plex"}]


def test_a_deep_scan_keeps_it_while_the_file_is_there(env):
    db, tmp = env
    path = _file(tmp, "Movies/The Matrix (1999)/m.mkv")
    li.ingest_landed(db, _movie_dl(), path)
    db.upsert_movie("plex", _plex_movie(sid="9", tmdb=11))
    assert db.prune_missing("movies", "plex", {"9"}) == 0
    assert len(_q(db, "SELECT id FROM movies")) == 2
    os.remove(path)
    assert db.prune_missing("movies", "plex", {"9"}) == 1
    assert [r["tmdb_id"] for r in _q(db, "SELECT tmdb_id FROM movies")] == [11]


def test_an_upgrade_replaces_the_old_file_and_keeps_other_versions(env):
    db, tmp = env
    old = _file(tmp, "Movies/The Matrix (1999)/m.720p.mkv")
    other = _file(tmp, "Movies/The Matrix (1999)/m.Directors.Cut.mkv")
    mid = li.ingest_landed(db, _movie_dl(), old)["id"]
    li.ingest_landed(db, _movie_dl(), other)
    new = _file(tmp, "Movies/The Matrix (1999)/m.2160p.mkv")
    assert li.ingest_landed(db, _movie_dl(), new, replaced=old)["id"] == mid
    paths = sorted(f["relative_path"] for f in _q(db, "SELECT * FROM media_files"))
    assert paths == sorted([other, new])


def test_a_download_for_a_library_row_lands_on_that_row(env):
    db, tmp = env
    mid = db.upsert_movie("plex", _plex_movie())
    dl = _movie_dl(media_source="library", media_id=str(mid))
    path = _file(tmp, "Movies/The Matrix (1999)/better.mkv")
    assert li.ingest_landed(db, dl, path)["id"] == mid
    assert len(_q(db, "SELECT id FROM movies")) == 1
    assert path in [f["relative_path"] for f in _q(db, "SELECT * FROM media_files")]


def test_nothing_is_written_without_a_file_or_an_identity(env):
    db, tmp = env
    assert li.ingest_landed(db, _movie_dl(), str(tmp / "nope.mkv")) is None
    path = _file(tmp, "x.mkv")
    assert li.ingest_landed(db, _movie_dl(media_id=None), path) is None
    assert li.ingest_landed(db, {"kind": "youtube", "media_id": "abc"}, path) is None
    assert _q(db, "SELECT id FROM movies") == []


# ── episodes ─────────────────────────────────────────────────────────────────

def _plex_show(eps=((1, 1),), sid="s1", tmdb=1399):
    seasons = {}
    for s, e in eps:
        seasons.setdefault(s, []).append({
            "server_id": f"e{s}{e}", "episode_number": e, "air_date": "2023-05-05",
            "files": [{"relative_path": f"/plex/tv/Silo/S{s}E{e}.mkv"}]})
    return {"server_id": sid, "title": "Silo", "tmdb_id": tmdb,
            "seasons": [{"season_number": s, "episodes": v} for s, v in seasons.items()]}


def test_an_episode_lands_in_the_show_the_server_has(env):
    db, tmp = env
    show_id = db.upsert_show_tree("plex", _plex_show())
    path = _file(tmp, "TV/Silo/Season 01/Silo - S01E02.mkv")
    out = li.ingest_landed(db, _ep_dl(), path)
    assert out["show_id"] == show_id
    ep = _q(db, "SELECT * FROM episodes WHERE id=?", out["id"])[0]
    assert (ep["season_number"], ep["episode_number"], ep["has_file"]) == (1, 2, 1)
    assert owned_ids.is_owned(ep["server_id"]) and ep["title"] == "Holston"
    # the next scan of the show, before plex has seen the file, keeps it
    db.upsert_show_tree("plex", _plex_show())
    ep = _q(db, "SELECT has_file FROM episodes WHERE id=?", out["id"])[0]
    assert ep["has_file"] == 1
    # plex scans it in: one episode, plex's
    db.upsert_show_tree("plex", _plex_show(eps=((1, 1), (1, 2))))
    eps = _q(db, "SELECT id, server_id FROM episodes WHERE season_number=1 AND episode_number=2")
    assert eps == [{"id": out["id"], "server_id": "e12"}]


def test_a_missing_episode_row_becomes_owned(env):
    db, tmp = env
    show_id = db.upsert_show_tree("plex", _plex_show())
    conn = db._get_connection()
    season_id = conn.execute("SELECT id FROM seasons WHERE show_id=?", (show_id,)).fetchone()[0]
    # what enrichment adds for an episode nobody has: no server id, no file
    conn.execute("INSERT INTO episodes (show_id, season_id, season_number, episode_number, air_date) "
                 "VALUES (?, ?, 1, 2, '2023-05-12')", (show_id, season_id))
    conn.commit()
    conn.close()
    path = _file(tmp, "TV/Silo/Season 01/e2.mkv")
    out = li.ingest_landed(db, _ep_dl(), path)
    ep = _q(db, "SELECT * FROM episodes WHERE id=?", out["id"])[0]
    assert ep["has_file"] == 1 and ep["air_date"] == "2023-05-12"
    assert ep["server_source"] == "plex" and owned_ids.is_owned(ep["server_id"])
    # its file goes away before plex ever saw it: the scan demotes it as usual
    os.remove(path)
    db.upsert_show_tree("plex", _plex_show())
    ep = _q(db, "SELECT has_file, server_id FROM episodes WHERE id=?", out["id"])[0]
    assert ep == {"has_file": 0, "server_id": None}


def test_a_show_the_library_never_had_is_made_and_then_taken_over(env):
    db, tmp = env
    path = _file(tmp, "TV/Silo/Season 01/e2.mkv")
    out = li.ingest_landed(db, _ep_dl(), path)
    show = _q(db, "SELECT * FROM shows")[0]
    assert show["id"] == out["show_id"] and show["tmdb_id"] == 1399 and show["title"] == "Silo"
    assert show["server_id"] == owned_ids.of_path(str(tmp / "TV" / "Silo"))
    # a deep scan before plex has it keeps it; plex reporting it takes it over
    assert db.prune_missing("shows", "plex", {"other"}) == 0
    assert _q(db, "SELECT id FROM episodes") == [{"id": out["id"]}]
    db.upsert_show_tree("plex", _plex_show(eps=((1, 2),)))
    assert _q(db, "SELECT id, server_id FROM shows") == [{"id": out["show_id"], "server_id": "s1"}]
    assert len(_q(db, "SELECT id FROM episodes")) == 1


def test_a_season_pack_row_itself_writes_nothing(env):
    db, tmp = env
    dl = _ep_dl()
    dl["search_ctx"] = json.dumps({"scope": "season", "season": 1})
    assert li.ingest_landed(db, dl, _file(tmp, "TV/Silo/Season 01/e1.mkv")) is None


# ── the import paths call it ─────────────────────────────────────────────────

def test_a_completed_download_is_written_in(env, monkeypatch):
    db, tmp = env
    from core.video import download_monitor as mon
    path = _file(tmp, "Movies/The Matrix (1999)/m.mkv")
    seen = []
    monkeypatch.setattr(li, "ingest_landed", lambda _db, dl, p, replaced=None: seen.append((p, replaced)))
    mon._own_landed(db, _movie_dl(), {"status": "completed", "dest_path": path, "_replaced": "/old.mkv"})
    assert seen == [(path, "/old.mkv")]


def test_the_upgrade_patch_names_the_replaced_copy(tmp_path):
    from core.video.importer import run_import

    class FS:
        def makedirs(self, d):
            pass

        def copy(self, a, b):
            pass

        def list_dir(self, d):
            return []

        def remove(self, p):
            pass

    plan = {"action": "upgrade", "dest": {"dir": "/lib/M", "path": "/lib/M/new.mkv"},
            "replace_path": "/lib/M/old.mkv"}
    patch = run_import({"source": "slskd"}, "/dl/new.mkv", fs=FS(), plan=plan)
    assert patch["_replaced"] == "/lib/M/old.mkv"
    plan["action"] = "import"
    assert run_import({"source": "slskd"}, "/dl/new.mkv", fs=FS(), plan=plan)["_replaced"] is None


# ── the ids ──────────────────────────────────────────────────────────────────

def test_ids_are_the_place_on_disk():
    assert owned_ids.movie_id("/m/A (1999)/a.mkv") == owned_ids.movie_id("/m/A (1999)/b.mkv")
    # a loose movie in the root is its own title
    assert owned_ids.movie_id("/m/a.mkv", ["/m"]) != owned_ids.movie_id("/m/b.mkv", ["/m"])
    # (compared as ids: the folder's spelling is the platform's own)
    silo = owned_ids.of_path("/tv/Silo")
    assert owned_ids.show_id("/tv/Silo/Season 01/e.mkv") == silo
    assert owned_ids.show_id("/tv/Silo/Specials/e.mkv") == silo
    assert owned_ids.show_id("/tv/Silo/Season 01/e.mkv", ["/tv"]) == silo
    assert owned_ids.show_id("/tv/Silo/e.mkv", ["/tv"]) == silo
    # a show whose name starts like a season folder is still the show
    assert owned_ids.show_id("/x/Series of Unfortunate Events/e.mkv") == \
        owned_ids.of_path("/x/Series of Unfortunate Events")
    assert owned_ids.of_path("/a/b/") == owned_ids.of_path("/a/b")


# ── a page's Sync before the server has scanned the file in ─────────────────

class _Server:
    """a plex or jellyfin that doesn't list the title yet"""
    server_name = "plex"

    def __init__(self, item=None):
        self.item, self.asked = item, []

    def movie_item(self, server_id, title=None, tmdb_id=None):
        self.asked.append(server_id)
        return self.item

    def show_tree(self, server_id, title=None, tmdb_id=None):
        self.asked.append(server_id)
        return self.item


def _idle(monkeypatch, source):
    class _S:
        def get_status(self):
            return {"state": "idle"}
    monkeypatch.setattr("core.video.scanner.get_video_scanner", lambda _db: _S())
    monkeypatch.setattr("core.video.sources.get_active_video_source", lambda **k: source)


def test_sync_keeps_a_landed_movie_the_server_hasnt_scanned(env, monkeypatch):
    db, tmp = env
    from core.video.movie_sync import sync_movie
    path = _file(tmp, "Movies/The Matrix (1999)/m.mkv")
    mid = li.ingest_landed(db, _movie_dl(), path)["id"]
    _idle(monkeypatch, _Server())
    out = sync_movie(db, mid, refresh_metadata=False)
    assert out["waiting"] is True and out["movie_removed"] is False and out["gone"] is False
    assert len(_q(db, "SELECT id FROM movies")) == 1
    # the file really is gone: Sync removes the title as it always did
    os.remove(path)
    assert sync_movie(db, mid, refresh_metadata=False)["movie_removed"] is True


def test_sync_hands_a_landed_movie_to_the_server_once_it_has_it(env, monkeypatch):
    db, tmp = env
    from core.video.movie_sync import sync_movie
    mid = li.ingest_landed(db, _movie_dl(), _file(tmp, "Movies/The Matrix (1999)/m.mkv"))["id"]
    _idle(monkeypatch, _Server(_plex_movie()))
    out = sync_movie(db, mid, refresh_metadata=False)
    assert out["movie_removed"] is False and not out.get("waiting")
    assert _q(db, "SELECT id, server_id FROM movies") == [{"id": mid, "server_id": "501"}]


def test_sync_keeps_a_landed_show_the_server_hasnt_scanned(env, monkeypatch):
    db, tmp = env
    from core.video.show_sync import sync_show
    out = li.ingest_landed(db, _ep_dl(), _file(tmp, "TV/Silo/Season 01/e2.mkv"))
    _idle(monkeypatch, _Server())
    res = sync_show(db, out["show_id"], refresh_schedule=False)
    assert res["waiting"] is True and res["show_removed"] is False
    assert len(_q(db, "SELECT id FROM episodes")) == 1


def test_plex_never_asks_for_an_owned_id_as_a_key():
    from core.video.sources import PlexVideoSource

    class _Plex:
        def fetchItem(self, key):
            raise AssertionError("asked plex for %r" % (key,))

    src = PlexVideoSource(_Plex(), movies_lib="M", tv_lib="T")
    src._scan_sections = lambda kind, name: []
    assert src.movie_item("ss:abc", title="The Matrix", tmdb_id=603) is None
    assert src.show_tree("ss:abc", title="Silo", tmdb_id=1399) is None


def test_nothing_is_pushed_to_a_server_that_doesnt_have_the_title_yet(env):
    db, tmp = env
    mid = li.ingest_landed(db, _movie_dl(), _file(tmp, "Movies/The Matrix (1999)/m.mkv"))["id"]
    assert db.poster_set_target("movie", mid)["server_id"] is None

    class _Plex:
        server_name = "plex"

        def set_watched(self, *a, **k):
            raise AssertionError("pushed a watched tick for a title plex doesn't have")

        def edit_item_metadata(self, *a, **k):
            raise AssertionError("pushed an edit for a title plex doesn't have")

    from core.video import metadata as med
    assert med.set_watched(db, "movie", mid, True, source=_Plex()) == \
        {"ok": True, "watched": True, "pushed": False}
    assert med.edit_item(db, "movie", mid, {"tagline": "Free your mind"}, source=_Plex())["pushed"] is False
    # once plex has it, pushes go to plex's id
    db.upsert_movie("plex", _plex_movie())
    assert db.poster_set_target("movie", mid)["server_id"] == "501"
