"""Overlay templates can leave this install, and come back.

Collection Studio has had Export/Import since it shipped. Overlay Studio never
did, which is backwards: a template is the artifact people actually share -
Kometa's whole ecosystem is shared configs - and until now an hour of design
work could not be backed up or handed to anybody.

The judgements this pins:

* Portable means the DESIGN and nothing else. No id, no thumbnail (a
  machine-local data-URL the import re-renders anyway), no timestamps, and
  crucially no assignment: which scope a template is bound to is a property of
  YOUR library, and importing someone else's binding would silently repaint your
  posters.
* An existing NAME is skipped, never overwritten. Re-importing a pack has to be
  safe, and losing a design you have since edited is not a recoverable mistake.
* A template with no layers is not a design. Importing one adds a gallery card
  that paints nothing.
"""

from __future__ import annotations

import json

import pytest
from flask import Flask


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import api.video as videoapi
    from database.video_database import VideoDatabase

    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    # The gallery thumb is a rendered preview; rendering one per import would
    # make these tests about Pillow rather than about portability.
    import api.video.overlays as ov
    monkeypatch.setattr(ov, "_prerender_thumb", lambda *a, **kw: None)

    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client(), videoapi._video_db
    finally:
        videoapi._video_db = None


DESIGN = {"version": 1, "canvas": {"w": 1000, "h": 1500},
          "layers": [{"kind": "text", "text": "4K", "x": 0.1, "y": 0.1}]}


def _make(db, name, definition=None):
    return db.create_overlay_template(name, definition=definition or DESIGN)


# ── export ───────────────────────────────────────────────────────────────────
def test_export_carries_the_design(client):
    c, db = client
    _make(db, "Corner 4K badge")

    d = c.get("/api/video/overlays/templates/export").get_json()
    assert d["soulsync_overlay_templates"] == 1
    assert [t["name"] for t in d["templates"]] == ["Corner 4K badge"]
    assert d["templates"][0]["definition"]["layers"][0]["text"] == "4K"


def test_export_carries_NOTHING_machine_local(client):
    """An id or a thumbnail means nothing on someone else's install, and an
    assignment would repaint their library the moment they imported."""
    c, db = client
    _make(db, "Corner 4K badge")

    row = c.get("/api/video/overlays/templates/export").get_json()["templates"][0]
    assert set(row) == {"name", "definition"}
    for leaked in ("id", "thumbnail", "created_at", "updated_at", "scope", "enabled"):
        assert leaked not in row


def test_an_empty_install_exports_an_empty_list_not_an_error(client):
    c, _ = client
    assert c.get("/api/video/overlays/templates/export").get_json()["templates"] == []


# ── import ───────────────────────────────────────────────────────────────────
def test_import_creates_the_template(client):
    c, db = client
    r = c.post("/api/video/overlays/templates/import",
               json={"templates": [{"name": "Shared badge", "definition": DESIGN}]})
    assert r.get_json()["imported"] == ["Shared badge"]
    assert [t["name"] for t in db.list_overlay_templates()] == ["Shared badge"]


def test_an_existing_name_is_skipped_never_overwritten(client):
    """Re-importing a pack must be safe. Losing a design you have since edited
    is not a mistake anyone recovers from."""
    c, db = client
    tid = _make(db, "Shared badge", {"version": 1, "layers": [{"kind": "text", "text": "MINE"}]})

    r = c.post("/api/video/overlays/templates/import",
               json={"templates": [{"name": "Shared badge", "definition": DESIGN}]})
    body = r.get_json()
    assert body["imported"] == [] and body["skipped"] == ["Shared badge"]
    # the original survives, untouched
    assert db.get_overlay_template(tid)["definition"]["layers"][0]["text"] == "MINE"


def test_a_name_matches_regardless_of_case_and_spacing(client):
    c, db = client
    _make(db, "Shared  Badge")
    body = c.post("/api/video/overlays/templates/import",
                  json={"templates": [{"name": "shared badge", "definition": DESIGN}]}).get_json()
    assert body["skipped"] == ["shared badge"]


def test_a_template_with_no_layers_is_not_a_design(client):
    """It would add a gallery card that paints nothing."""
    c, db = client
    body = c.post("/api/video/overlays/templates/import", json={"templates": [
        {"name": "Empty", "definition": {"version": 1, "layers": []}},
        {"name": "Nothing", "definition": {}},
        {"name": "Real", "definition": DESIGN},
    ]}).get_json()
    assert body["imported"] == ["Real"]
    assert [t["name"] for t in db.list_overlay_templates()] == ["Real"]


def test_a_definition_that_arrives_as_a_json_string_still_imports(client):
    """Some exports round-trip the definition as text; refusing those would
    reject a file SoulSync itself could have produced."""
    c, db = client
    body = c.post("/api/video/overlays/templates/import",
                  json={"templates": [{"name": "Stringy", "definition": json.dumps(DESIGN)}]}).get_json()
    assert body["imported"] == ["Stringy"]


def test_junk_rows_are_stepped_over_not_fatal(client):
    c, db = client
    body = c.post("/api/video/overlays/templates/import", json={"templates": [
        "not a dict", {"definition": DESIGN}, {"name": "   "},
        {"name": "Broken", "definition": "{not json"},
        {"name": "Good", "definition": DESIGN},
    ]}).get_json()
    assert body["ok"] is True
    assert body["imported"] == ["Good"]


def test_two_copies_of_one_name_in_a_single_file_import_once(client):
    """Otherwise a duplicate inside the file defeats the skip, since the
    existing-name set is read before the loop."""
    c, db = client
    body = c.post("/api/video/overlays/templates/import", json={"templates": [
        {"name": "Twice", "definition": DESIGN},
        {"name": "Twice", "definition": DESIGN},
    ]}).get_json()
    assert body["imported"] == ["Twice"]
    assert body["skipped"] == ["Twice"]
    assert len(db.list_overlay_templates()) == 1


def test_an_empty_payload_is_a_clear_400(client):
    c, _ = client
    for payload in ({}, {"templates": []}, {"templates": "nope"}):
        r = c.post("/api/video/overlays/templates/import", json=payload)
        assert r.status_code == 400
        assert "required" in r.get_json()["error"]


# ── the round trip ───────────────────────────────────────────────────────────
def test_export_then_import_on_a_fresh_install_reproduces_the_design(client, tmp_path):
    c, db = client
    _make(db, "Corner 4K badge")
    exported = c.get("/api/video/overlays/templates/export").get_json()

    import api.video as videoapi
    from database.video_database import VideoDatabase
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "other.db"))

    body = c.post("/api/video/overlays/templates/import", json=exported).get_json()
    assert body["imported"] == ["Corner 4K badge"]
    fresh = videoapi._video_db.list_overlay_templates()
    got = videoapi._video_db.get_overlay_template(fresh[0]["id"])
    assert got["definition"]["layers"] == DESIGN["layers"]
