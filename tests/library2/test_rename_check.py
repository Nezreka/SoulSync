"""#2141: the artist page's "would be renamed" count, read off the preview plan."""

from __future__ import annotations

import pytest

from core.library2 import rename_check
from core.library2.reorganize_bridge import ReorganizeBridgeError
from core.library2.rename_check import check_artist_renames, files_to_rename, naming_problem


class Config:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


def _track(**over):
    return {"track_id": 1, "matched": True, "file_exists": True, "new_path": "A/B/01 - x.flac",
            "unchanged": False, **over}


def test_counts_only_files_that_would_move():
    """#2141: the same files the preview lists as moving: found on disk, given
    a path, and not already there."""
    plan = {"tracks": [
        _track(),
        _track(unchanged=True),
        _track(matched=False, reason="File not found on disk"),
        _track(new_path=""),
        _track(file_exists=False),
        _track(collision=True),
    ]}
    assert len(files_to_rename(plan)) == 2
    assert files_to_rename({}) == []


@pytest.fixture
def albums(monkeypatch):
    ids = [11, 12, 13]
    monkeypatch.setattr("core.library2.reorganize_bridge.artist_reorganize_albums",
                        lambda _db, _artist: [{"album_id": i} for i in ids])
    monkeypatch.setattr(rename_check, "naming_problem", lambda _config: None)
    return ids


def test_each_album_gets_its_count_and_a_refused_one_is_left_out(albums):
    """#2141: per album, from the plan; placeholder metadata is no count."""
    plans = {11: {"tracks": [_track(track_id=101), _track(track_id=102)]},
             12: {"tracks": [_track(track_id=103, unchanged=True)]}}

    def preview(album_id):
        if album_id == 13:
            raise ReorganizeBridgeError("placeholder", status=409)
        return plans[album_id]

    result = check_artist_renames(None, Config(), 7, preview_fn=preview)
    assert result == {"enabled": True, "albums": {11: 2, 12: 0}, "tracks": [101, 102],
                      "next": None, "total": 3}


def test_a_big_artist_is_checked_in_time_bounded_slices(albums):
    """#2141: one slice stops once its budget is spent and says where to go on."""
    now = [0.0]
    seen = []

    def preview(album_id):
        seen.append(album_id)
        now[0] += 3.0
        return {"tracks": [_track()]}

    first = check_artist_renames(None, Config(), 7, preview_fn=preview,
                                 clock=lambda: now[0], budget=5.0)
    assert first["albums"] == {11: 1, 12: 1} and first["next"] == 2
    second = check_artist_renames(None, Config(), 7, start=first["next"], preview_fn=preview,
                                  clock=lambda: now[0], budget=5.0)
    assert second["albums"] == {13: 1} and second["next"] is None
    assert seen == [11, 12, 13]


def test_nothing_is_planned_when_organizing_is_off(monkeypatch):
    """#2141: no hint while files aren't organized at all."""
    called = []
    result = check_artist_renames(
        None, Config({"file_organization.enabled": False}), 7,
        preview_fn=lambda album_id: called.append(album_id) or {})
    assert result["enabled"] is False and result["reason"] == "off"
    assert called == []


def test_a_template_that_does_not_render_turns_the_hint_off():
    """#2141: a hand-edited template with a variable the renderer doesn't know
    would put a literal $ in every path, so every file would read as wrong."""
    good = {"album_path": "$albumartist/$album/$track - $title"}
    assert naming_problem(Config({"file_organization.templates": good})) is None
    assert naming_problem(Config()) is None  # the defaults
    for bad in ("$albumartist/$albmu/$track - $title", "/$artist/$album/$title",
                "$artist - $title"):
        assert naming_problem(Config({"file_organization.templates": {"album_path": bad}})) \
            == "template", bad
