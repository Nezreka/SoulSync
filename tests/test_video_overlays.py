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
    # Full comma-joined genre list (sorted by name) so a "genre includes X"
    # condition can match ANY of them; the Genre badge shows just the first.
    assert s["genre"] == "Adventure, Science Fiction"
    from core.video.overlays.fields import format_field
    assert format_field("genre", s["genre"]) == "Adventure"   # badge = primary only


def test_overlay_sample_data_versions_and_subtitles(db):
    import json
    mid = db.upsert_movie("plex", {"server_id": "v1", "title": "Dune"})
    with db.connect() as c:
        c.execute("INSERT INTO media_files(movie_id, relative_path) VALUES (?, 'a.mkv')", (mid,))
        c.execute("INSERT INTO media_files(movie_id, relative_path) VALUES (?, 'b.mkv')", (mid,))
        c.execute("UPDATE movies SET subtitle_langs=? WHERE id=?", (json.dumps(["en", "es", "fr"]), mid))
        c.commit()
    s = db.overlay_sample_data("movie", mid)
    assert s["versions"] == 2      # two owned copies → "2 Versions" badge
    assert s["subtitles"] == 3     # three subtitle languages

    mid2 = db.upsert_movie("plex", {"server_id": "v2", "title": "Solo"})
    with db.connect() as c:
        c.execute("INSERT INTO media_files(movie_id, relative_path) VALUES (?, 'x.mkv')", (mid2,))
        c.commit()
    s2 = db.overlay_sample_data("movie", mid2)
    assert s2["versions"] == 1      # single copy (formatter hides the badge)
    assert s2["subtitles"] is None  # no subs → None


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


# ── season/episode overlay scopes (sub-item overlays) ─────────────────────────
def _show_with_sub(db):
    """A show whose season + episodes carry server_ids (poster-push targets)."""
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "Show", "network": "HBO",
                                       "content_rating": "TV-MA", "year": 2019, "seasons": [
        {"season_number": 1, "episodes": [
            {"server_id": "e1", "episode_number": 1, "title": "Pilot", "air_date": "2019-04-14",
             "runtime_minutes": 58, "rating": 8.2,
             "file": {"relative_path": "a.mkv", "size_bytes": 9, "resolution": "2160p",
                      "video_codec": "hevc", "audio_codec": "atmos", "release_source": "bluray"}},
            {"server_id": "e2", "episode_number": 2, "title": "Next", "air_date": "2019-04-21"}]}]})
    with db.connect() as c:
        c.execute("UPDATE seasons SET server_id='se1' WHERE show_id=? AND season_number=1", (sid,))
        c.commit()
    return sid


def test_overlay_scope_items_season_and_episode(db):
    _show_with_sub(db)
    seasons = db.overlay_scope_items("season")
    assert len(seasons) == 1 and "Show" in seasons[0]["title"] and "Season 1" in seasons[0]["title"]
    eps = db.overlay_scope_items("episode")
    assert len(eps) == 2                                   # both episodes carry a server_id
    assert "S1E1" in eps[0]["title"] and "Pilot" in eps[0]["title"]
    assert db.overlay_scope_items("bogus") == []


def test_overlay_sample_data_season(db):
    _show_with_sub(db)
    sea = db.overlay_scope_items("season")[0]
    s = db.overlay_sample_data("season", sea["id"])
    assert s["title"] == "Show" and s["network"] == "HBO" and s["content_rating"] == "TV-MA"
    assert s["season_number"] == 1 and s["episode_count"] == 2
    from core.video.overlays.fields import format_field
    assert format_field("season_number", s["season_number"]) == "Season 1"


def test_overlay_sample_data_episode(db):
    _show_with_sub(db)
    e1 = [e for e in db.overlay_scope_items("episode") if "S1E1" in e["title"]][0]
    s = db.overlay_sample_data("episode", e1["id"])
    assert s["title"] == "Pilot" and s["season_number"] == 1 and s["episode_number"] == 1
    assert s["episode_code"] == "S1E1" and s["year"] == 2019 and s["runtime"] == 58
    assert s["tmdb"] == 8.2                                # episode rating drives the rating badge
    assert s["resolution"] == "2160p" and s["audio_codec"] == "atmos"   # from the owned file
    from core.video.overlays.fields import format_field
    assert format_field("episode_code", s["episode_code"]) == "S1E1"
    assert format_field("episode_number", s["episode_number"]) == "Episode 1"


def test_preview_random_and_search_for_sub_types(db):
    _show_with_sub(db)
    # season poster exists (set in the fixture); episode still set below
    with db.connect() as c:
        c.execute("UPDATE seasons SET poster_url='http://x/season.jpg' WHERE show_id IN (SELECT id FROM shows WHERE title='Show')")
        c.execute("UPDATE episodes SET still_url='http://x/still.jpg' WHERE show_id IN (SELECT id FROM shows WHERE title='Show')")
        c.commit()
    ss = db.random_overlay_preview_item("season")
    assert ss and ss["kind"] == "season" and "Show" in ss["title"]
    es = db.random_overlay_preview_item("episode")
    assert es and es["kind"] == "episode" and "Show — S1E" in es["title"]   # E1 or E2, random
    # search finds them by show title
    assert any("Season 1" in r["title"] for r in db.search_overlay_preview("season", "Show"))
    eps = db.search_overlay_preview("episode", "Show")
    assert eps and all(r["has_poster"] for r in eps) and any("Pilot" in r["title"] for r in eps)
    assert db.search_overlay_preview("season", "Nonexistent") == []


def test_preview_skips_items_without_art(db):
    _show_with_sub(db)
    # no poster/still set on the fixture's season/episode -> nothing to preview
    with db.connect() as c:
        c.execute("UPDATE seasons SET poster_url=NULL")
        c.execute("UPDATE episodes SET still_url=NULL")
        c.commit()
    assert db.random_overlay_preview_item("season") is None
    assert db.random_overlay_preview_item("episode") is None
    assert db.search_overlay_preview("episode", "Show") == []


def test_poster_set_target_and_assignment_for_sub_scopes(db):
    _show_with_sub(db)
    sea = db.overlay_scope_items("season")[0]
    ep = db.overlay_scope_items("episode")[0]
    st = db.poster_set_target("season", sea["id"])
    assert st["server_source"] == "plex" and st["server_id"] == "se1"   # inherits show's source
    et = db.poster_set_target("episode", ep["id"])
    assert et["server_source"] == "plex" and et["server_id"] in ("e1", "e2")
    # assignment now accepts the sub scopes (and still rejects junk)
    tid = db.create_overlay_template("Ep badge", definition=_scene("S1E1"))
    assert db.set_overlay_assignment("season", tid, True) is True
    assert db.set_overlay_assignment("episode", tid, True) is True
    assert db.set_overlay_assignment("bogus", tid, True) is False
    a = db.get_overlay_assignments()
    assert a["episode"]["template_id"] == tid and a["episode"]["enabled"] is True
