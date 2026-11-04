"""Saved manual-search filters (SeadogsBooty): per profile, and only fields and
operators the browser's filter script knows."""

import re
from pathlib import Path

import pytest

from core.video import search_filters as sf
from database.video_database import VideoDatabase

MEGUSTA = {"name": "1080p HEVC MeGusta", "match": "all", "conditions": [
    {"field": "resolution", "op": "equals", "value": "1080"},
    {"field": "codec", "op": "equals", "value": "hevc"},
    {"field": "group", "op": "contains", "value": "megusta"},
    {"field": "size_gb", "op": "lt", "value": "1"}]}


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def test_a_filter_round_trips_per_profile(db):
    assert sf.save(db, 1, [MEGUSTA]) == [MEGUSTA]
    assert sf.load(db, 1) == [MEGUSTA]
    assert sf.load(db, 2) == []


def test_bad_conditions_and_nameless_filters_are_dropped():
    f = sf.normalize_filter({"name": "  odd   one ", "match": "whatever", "conditions": [
        {"field": "size_gb", "op": "contains", "value": "1"},       # wrong op for a number
        {"field": "password", "op": "equals", "value": "x"},        # unknown field
        {"field": "title", "op": "contains", "value": "PROPER"}]})
    assert f == {"name": "odd one", "match": "all",
                 "conditions": [{"field": "title", "op": "contains", "value": "PROPER"}]}
    assert sf.normalize_filter({"name": "", "conditions": MEGUSTA["conditions"]}) is None
    assert sf.normalize_filter({"name": "empty", "conditions": [{"field": "nope", "op": "gt"}]}) is None


def test_a_same_named_filter_replaces_the_old_one():
    newer = dict(MEGUSTA, match="any")
    assert sf.normalize_filters([MEGUSTA, {**newer, "name": "1080P hevc megusta"}]) == \
        [{**newer, "name": "1080P hevc megusta"}]


def test_the_api_saves_and_reads_them(db, monkeypatch):
    from flask import Flask

    import api.video as videoapi
    import core.profile_context as pc
    monkeypatch.setattr(videoapi, "_video_db", db)
    monkeypatch.setattr(pc, "get_current_profile_id", lambda: 3)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()
    r = client.put("/api/video/search-filters", json={"filters": [MEGUSTA, {"name": "x"}]}).get_json()
    assert r["filters"] == [MEGUSTA]
    assert client.get("/api/video/search-filters").get_json()["filters"] == [MEGUSTA]
    assert sf.load(db, 1) == []


def test_python_and_the_browser_script_agree_on_fields_and_operators():
    js = (Path(__file__).resolve().parents[2] / "webui" / "static" / "video"
          / "search-filters.js").read_text(encoding="utf-8")
    fields = dict(re.findall(r"^\s{8}(\w+): \{ label: '[^']*', kind: '(\w+)'", js, re.M))
    assert fields == sf.FIELDS
    ops = {op: tuple(kinds.replace("'", "").replace(" ", "").split(","))
           for op, kinds in re.findall(r"^\s{8}(\w+): \{ label: .*?, kinds: \[([^\]]*)\] \}", js, re.M)}
    assert ops == sf.OPS
