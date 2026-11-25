"""youtube follows download only for owners who may download (S9, nov 17).

the channel and playlist scans run once per follower profile, and the drain
grabs the whole household's youtube wishlist as the system. what a follow
wished on its own (auto_wish) is skipped while its owner has downloads off,
asked at grab time. a video the person wished themselves, or one an admin
approved for them, still downloads.
"""

from __future__ import annotations

import pytest

from database.video_database import VideoDatabase

CAN, CANNOT = 3, 4


@pytest.fixture
def db(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.permissions as perms
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    real = perms.owner_may_download
    # the real check, with the music db's profile table stood in for
    monkeypatch.setattr(perms, "owner_may_download", lambda pid, get_profile=None: real(
        pid, lambda p: {CAN: {"id": CAN, "can_download": True},
                        CANNOT: {"id": CANNOT, "can_download": False}}.get(p)))
    # the scans ask the owner's permissions (#2166): movies and tv right away
    # for CAN, ask first for CANNOT
    import core.owner_gate as og
    from tests.permissions_fixtures import member_ctx
    monkeypatch.setattr(og, "_default_context", lambda pid: {
        CAN: member_ctx(profile_id=CAN),
        CANNOT: member_ctx(profile_id=CANNOT, **{"movies.get": "ask", "tv.get": "ask"})}[pid])
    try:
        yield videoapi._video_db
    finally:
        videoapi._video_db = None


def _wish(db, pid, vid, auto):
    db.add_videos_to_wishlist({"youtube_id": "UC1", "title": "Chan"},
                              [{"youtube_id": vid, "title": vid, "published_at": "2026-11-01"}],
                              profile_id=pid, auto=auto)


def test_the_drain_skips_auto_wishes_of_an_owner_who_cannot_download(db):
    from core.automation.handlers.video_process_youtube_wishlist import _default_fetch_wanted
    _wish(db, CANNOT, "followed", auto=True)
    _wish(db, CANNOT, "approved", auto=False)    # an approved request lands like this
    _wish(db, CAN, "theirs", auto=True)
    _wish(db, CANNOT, "shared", auto=True)
    _wish(db, CAN, "shared", auto=True)           # someone who may download wants it too
    got = sorted(v["video_id"] for v in _default_fetch_wanted())
    assert got == ["approved", "shared", "theirs"]
    # without the owner check, everything (the old behaviour)
    assert len(db.youtube_wishlist_to_download()) == 4


@pytest.mark.parametrize("module,fetch,add", [
    ("core.automation.handlers.video_scan_watchlist_channels", "_default_fetch_channels",
     lambda db, pid: db.add_channel_to_watchlist({"youtube_id": "UC1", "title": "Chan"}, profile_id=pid)),
    ("core.automation.handlers.video_scan_watchlist_playlists", "_default_fetch_playlists",
     lambda db, pid: db.add_playlist_to_watchlist({"playlist_id": "PL1", "title": "List"}, profile_id=pid)),
])
def test_the_scans_find_nothing_to_wish_for_an_owner_who_cannot_download(db, monkeypatch,
                                                                       module, fetch, add):
    import importlib
    mod = importlib.import_module(module)
    for pid in (CAN, CANNOT):
        add(db, pid)
    monkeypatch.setattr(mod, "_profile", lambda: CANNOT)
    assert getattr(mod, fetch)() == []
    monkeypatch.setattr(mod, "_profile", lambda: CAN)
    assert len(getattr(mod, fetch)()) == 1
