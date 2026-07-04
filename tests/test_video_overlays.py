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
