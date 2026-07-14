"""Plex set_poster's overlay-cleanup guard: on a re-apply it deletes the PREVIOUS overlay
upload before uploading the new one — but ONLY when the currently-selected poster is the one
we uploaded (so a poster the user picked by hand is never clobbered)."""

from __future__ import annotations

from core.video.sources import PlexVideoSource


class _FakePoster:
    def __init__(self, rating_key, selected):
        self.ratingKey = rating_key
        self.selected = selected


class _FakeItem:
    def __init__(self, selected_key="upload://old"):
        self.deleted = 0
        self.uploaded = []
        self._posters = [_FakePoster(selected_key, True)]

    def posters(self):
        return self._posters

    def deletePoster(self):
        self.deleted += 1

    def uploadPoster(self, url=None, filepath=None):
        self.uploaded.append(url or filepath)
        self._posters = [_FakePoster("upload://new", True)]   # the new upload becomes selected


class _FakeServer:
    def __init__(self, item):
        self._item = item

    def fetchItem(self, rating_key):
        return self._item


def test_deletes_previous_overlay_when_key_matches_then_returns_new_key():
    item = _FakeItem(selected_key="upload://old")
    src = PlexVideoSource(_FakeServer(item))
    res = src.set_poster(1, image_bytes=b"x", delete_key="upload://old")   # the selected IS ours
    assert res["ok"] and res["poster_key"] == "upload://new"
    assert item.deleted == 1 and len(item.uploaded) == 1                   # deleted prev, uploaded new


def test_leaves_a_manually_picked_poster_alone():
    item = _FakeItem(selected_key="upload://user-choice")                  # user picked a different one
    src = PlexVideoSource(_FakeServer(item))
    res = src.set_poster(1, image_bytes=b"x", delete_key="upload://ours")  # doesn't match selected
    assert res["ok"]
    assert item.deleted == 0 and len(item.uploaded) == 1                   # uploaded, never deleted theirs


def test_first_touch_no_delete_key_keeps_the_original():
    item = _FakeItem()
    src = PlexVideoSource(_FakeServer(item))
    res = src.set_poster(1, image_bytes=b"x")                              # no delete_key = first apply
    assert res["ok"] and res["poster_key"] == "upload://new"
    assert item.deleted == 0
