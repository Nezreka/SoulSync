"""Overlay-template CRUD — the storage behind the Artwork Studio editor.

Templates are saved designs (a JSON scene of positioned layers). These cover the
DB layer: create/list/get/update/delete/duplicate, and that the definition JSON
round-trips intact (it's the thing the editor loads back to keep editing).
"""

from __future__ import annotations

import pytest

from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _scene(*labels):
    return {"version": 1, "canvas": {"aspect": "2:3"},
            "layers": [{"id": str(i), "type": "text", "text": t, "anchor": "top-left",
                        "x": 0.1, "y": 0.1} for i, t in enumerate(labels)]}


def test_create_and_get_roundtrips_definition(db):
    scene = _scene("4K", "HDR")
    tid = db.create_overlay_template("My overlay", definition=scene)
    assert isinstance(tid, int)
    got = db.get_overlay_template(tid)
    assert got["name"] == "My overlay"
    assert got["definition"] == scene              # JSON survived intact
    assert got["created_at"] and got["updated_at"]


def test_get_missing_is_none(db):
    assert db.get_overlay_template(99999) is None


def test_list_is_light_and_newest_first(db):
    a = db.create_overlay_template("A", definition=_scene("x"))
    b = db.create_overlay_template("B", definition=_scene("y", "z"))
    rows = db.list_overlay_templates()
    assert [r["id"] for r in rows] == [b, a]        # newest updated first
    top = rows[0]
    assert top["name"] == "B" and top["layer_count"] == 2
    assert "definition" not in top                  # list stays light


def test_update_patches_only_given_fields(db):
    tid = db.create_overlay_template("Orig", definition=_scene("a"))
    assert db.update_overlay_template(tid, name="Renamed") is True
    got = db.get_overlay_template(tid)
    assert got["name"] == "Renamed"
    assert got["definition"] == _scene("a")         # untouched by a name-only patch

    new_scene = _scene("b", "c", "d")
    assert db.update_overlay_template(tid, definition=new_scene) is True
    assert db.get_overlay_template(tid)["definition"] == new_scene

    assert db.update_overlay_template(tid) is False  # nothing to patch
    assert db.update_overlay_template(99999, name="x") is False


def test_empty_name_falls_back(db):
    tid = db.create_overlay_template("   ")
    assert db.get_overlay_template(tid)["name"] == "Untitled template"


def test_delete(db):
    tid = db.create_overlay_template("Bye")
    assert db.delete_overlay_template(tid) is True
    assert db.get_overlay_template(tid) is None
    assert db.delete_overlay_template(tid) is False


def test_duplicate_copies_definition(db):
    scene = _scene("k")
    tid = db.create_overlay_template("Base", definition=scene)
    cid = db.duplicate_overlay_template(tid)
    assert cid != tid
    copy = db.get_overlay_template(cid)
    assert copy["name"] == "Base (copy)" and copy["definition"] == scene
    assert db.duplicate_overlay_template(99999) is None


def test_bad_definition_string_parses_to_empty(db):
    tid = db.create_overlay_template("Broken", definition="{not valid json")
    assert db.get_overlay_template(tid)["definition"] == {}   # tolerated, not crashed


# ── sample data (dynamic-badge preview) ───────────────────────────────────────
def test_overlay_sample_data_movie(db):
    mid = db.upsert_movie("plex", {
        "server_id": "p1", "title": "Dune", "year": 2021, "runtime_minutes": 155,
        "status": "released", "studio": "Legendary", "content_rating": "PG-13",
        "file": {"relative_path": "Dune.mkv", "size_bytes": 9000, "resolution": "2160p",
                 "video_codec": "hevc", "audio_codec": "truehd", "release_source": "bluray"}})
    with db.connect() as c:
        c.execute("UPDATE movies SET imdb_rating=8.0, rt_rating=83, metacritic=74, rating=7.9 WHERE id=?", (mid,))
        c.commit()
    s = db.overlay_sample_data("movie", mid)
    assert s["title"] == "Dune" and s["year"] == 2021 and s["runtime"] == 155
    assert s["resolution"] == "2160p" and s["video_codec"] == "hevc" and s["source"] == "bluray"
    assert s["imdb"] == 8.0 and s["rt"] == 83 and s["metacritic"] == 74 and s["studio"] == "Legendary"
    assert db.overlay_sample_data("movie", 99999) is None
    assert db.overlay_sample_data("bogus", mid) is None


def test_overlay_sample_data_includes_genre(db):
    mid = db.upsert_movie("plex", {"server_id": "g1", "title": "Dune",
                                   "genres": ["Science Fiction", "Adventure"]})
    s = db.overlay_sample_data("movie", mid)
    assert s["genre"] == "Adventure"          # first genre by name


def test_random_overlay_preview_items(db):
    assert db.random_overlay_preview_items(4) == []          # empty library → nothing to preview
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Dune", "year": 2021})
    with db.connect() as c:
        c.execute("UPDATE movies SET tmdb_id=438631, poster_url='http://x/p.jpg' WHERE id=?", (mid,))
        c.commit()
    items = db.random_overlay_preview_items(4)
    assert len(items) == 1 and items[0]["kind"] == "movie" and items[0]["tmdb_id"] == 438631


def test_preview_filmstrip_assembles_and_skips_failures(monkeypatch):
    from core.video.overlays import service

    class FakeDB:
        def random_overlay_preview_items(self, n):
            return [{"kind": "movie", "id": 1, "tmdb_id": 10, "title": "A"},
                    {"kind": "show", "id": 2, "tmdb_id": 20, "title": "B"},
                    {"kind": "movie", "id": 3, "tmdb_id": 30, "title": "C"}]

    def fake_render(dbx, definition, pick):
        return None if pick["title"] == "B" else b"\xff\xd8jpeg"   # B fails to render

    monkeypatch.setattr(service, "_render_for_item", fake_render)
    frames = service.preview_filmstrip(FakeDB(), {"layers": []}, 3)
    assert [f["title"] for f in frames] == ["A", "C"]        # the failed one is skipped, no hole
    assert all(f["data_uri"].startswith("data:image/jpeg;base64,") for f in frames)


def test_overlay_sample_data_show_counts_and_best_res(db):
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "Show", "network": "HBO", "seasons": [
        {"season_number": 1, "episodes": [
            {"server_id": "e1", "episode_number": 1, "title": "A",
             "file": {"relative_path": "a.mkv", "size_bytes": 5, "resolution": "1080p"}},
            {"server_id": "e2", "episode_number": 2, "title": "B",
             "file": {"relative_path": "b.mkv", "size_bytes": 5, "resolution": "2160p"}}]}]})
    s = db.overlay_sample_data("show", sid)
    assert s["title"] == "Show" and s["network"] == "HBO"
    assert s["season_count"] == 1 and s["episode_count"] == 2
    assert s["resolution"] == "2160p"          # best across the show's episode files
