"""Automation events from the audiobook subsystem (core/audiobook_events.py).

Every music trigger an audiobook has a counterpart for must actually fire, and
fire with the variables its block advertises. Hermetic: events are captured on
the app-event bus, nothing reaches a real engine.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core import app_events
from core.audiobook_database import AudiobookDatabase
from core.automation.blocks import TRIGGERS

_ROOT = Path(__file__).resolve().parents[1]
_BLOCKS = {block["type"]: block for block in TRIGGERS}


@pytest.fixture
def db(tmp_path):
    database = AudiobookDatabase(str(tmp_path / "audiobooks.db"))
    yield database
    database.close()


@pytest.fixture
def events():
    """Every published event, checked against its trigger block as it arrives."""
    captured = []

    def forwarder(event_type, data):
        block = _BLOCKS.get(event_type)
        assert block is not None, f"{event_type} has no trigger in blocks.py"
        undeclared = set(data) - set(block.get("variables", []))
        assert not undeclared, f"{event_type} sends {sorted(undeclared)} which its block does not list"
        captured.append((event_type, data))

    app_events._reset_for_tests()
    app_events.register_forwarder(forwarder)
    yield captured
    app_events._reset_for_tests()


def _types(captured):
    return [event_type for event_type, _ in captured]


BOOK = {"asin": "B1", "title": "The Final Empire", "author_names": ["Brandon Sanderson"],
        "series": [{"title": "Mistborn", "sequence": "1"}]}


# ---------------------------------------------------------------------------
# The triggers exist for everything the code announces
# ---------------------------------------------------------------------------

def test_every_announced_event_type_is_a_trigger():
    announced = set()
    for path in list((_ROOT / "core").glob("audiobook*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "announce"
                    and node.args and isinstance(node.args[0], ast.Constant)):
                announced.add(node.args[0].value)
    assert announced, "found no announce() calls to check"
    assert announced <= set(_BLOCKS), sorted(announced - set(_BLOCKS))


def test_announce_without_a_forwarder_is_a_no_op():
    from core.audiobook_events import announce

    app_events._reset_for_tests()
    announce("audiobook_new_release", artist="x")


def test_announce_survives_a_broken_forwarder():
    from core.audiobook_events import announce

    app_events._reset_for_tests()
    app_events.register_forwarder(lambda *_: 1 / 0)
    try:
        announce("audiobook_new_release", artist="x")
    finally:
        app_events._reset_for_tests()


# ---------------------------------------------------------------------------
# Wishlist and following
# ---------------------------------------------------------------------------

def test_a_new_wishlist_item_is_announced_once(db, events):
    assert db.add_to_wishlist(BOOK) is True
    assert db.add_to_wishlist(BOOK) is False
    assert events == [("audiobook_wishlist_item_added", {
        "title": "The Final Empire", "author": "Brandon Sanderson",
        "series": "Mistborn", "asin": "B1"})]


def test_following_and_unfollowing_are_announced(db, events):
    db.follow_author("Brandon Sanderson")
    db.follow_author("Brandon Sanderson")          # already followed: silent
    db.unfollow_author("Brandon Sanderson")
    db.unfollow_author("Brandon Sanderson")        # already gone: silent
    assert events == [
        ("audiobook_author_followed", {"name": "Brandon Sanderson", "role": "author"}),
        ("audiobook_author_unfollowed", {"name": "Brandon Sanderson", "role": "author"}),
    ]


class _Catalogue:
    def __init__(self, books):
        self.books = books

    def get_by_author(self, name, **kwargs):
        return self.books


def _release(asin="NEW", title="Wind and Truth"):
    from core.audiobook_client import product_to_item

    return product_to_item({"asin": asin, "title": title, "authors": [{"name": "Brandon Sanderson"}],
                            "release_date": "2026-06-01", "runtime_length_min": 100})


def test_a_new_release_is_announced_once_per_author(db, events):
    from core.audiobook_watchlist import scan_author

    db.follow_author("Brandon Sanderson", since_date="2026-01-01")
    events.clear()
    scan_author(db.get_watchlist()[0], db=db,
                client=_Catalogue([_release("N1", "Wind and Truth"), _release("N2", "Dawnshard")]))
    release = [data for event_type, data in events if event_type == "audiobook_new_release"]
    assert len(release) == 1
    assert release[0]["artist"] == "Brandon Sanderson"
    assert release[0]["count"] == "2" and release[0]["wishlisted"] == "2"
    assert "Wind and Truth" in release[0]["titles"] and "Dawnshard" in release[0]["titles"]


def test_a_release_is_announced_even_when_it_is_not_wishlisted(db, events):
    from core.audiobook_watchlist import scan_author

    db.follow_author("Brandon Sanderson", since_date="2026-01-01")
    db.update_watchlist_author("Brandon Sanderson", auto_wishlist=False)
    events.clear()
    scan_author(db.get_watchlist()[0], db=db, client=_Catalogue([_release()]))
    assert _types(events) == ["audiobook_new_release"]
    assert events[0][1]["wishlisted"] == "0"


def test_nothing_new_announces_no_release(db, events):
    from core.audiobook_watchlist import scan_author

    db.follow_author("Brandon Sanderson", since_date="2026-01-01")
    events.clear()
    scan_author(db.get_watchlist()[0], db=db, client=_Catalogue([]))
    assert events == []


def test_a_watchlist_scan_announces_its_summary(db, events):
    from core.audiobook_watchlist import run_scan

    db.follow_author("Brandon Sanderson", since_date="2026-01-01")
    db._connect().execute("UPDATE audiobook_watchlist SET last_scanned_at = 0")
    events.clear()
    with patch("core.audiobook_client.get_audiobook_client", return_value=_Catalogue([])):
        run_scan(db=db)
    assert _types(events) == ["audiobook_watchlist_scan_completed"]
    assert events[0][1]["authors"] == "1"


def test_a_watchlist_scan_with_nobody_due_is_silent(db, events):
    from core.audiobook_watchlist import run_scan

    run_scan(db=db)
    assert events == []


# ---------------------------------------------------------------------------
# Wishlist pass and grab
# ---------------------------------------------------------------------------

def test_a_wishlist_pass_announces_when_it_searched(db, events):
    from core.audiobook_wishlist_worker import run_pass

    db.add_to_wishlist(BOOK)
    events.clear()
    with patch("core.audiobook_release_search.search_releases", return_value=[]):
        run_pass(db=db)
    assert _types(events) == ["audiobook_wishlist_processing_completed"]
    assert events[0][1]["checked"] == "1"


def test_an_idle_wishlist_pass_is_silent(db, events):
    from core.audiobook_wishlist_worker import run_pass

    run_pass(db=db)
    assert events == []


def test_a_grab_is_announced(db, events):
    from core.audiobook_grab import record_grab

    with patch("core.audiobook_download_monitor.ensure_started"):
        record_grab(db, asin="B1", ref="r1", client_ref="c1", book=BOOK,
                    release={"title": "Mistborn M4B", "protocol": "usenet"})
    assert ("audiobook_grab_started", {
        "title": "The Final Empire", "author": "Brandon Sanderson", "series": "Mistborn",
        "protocol": "usenet", "release_title": "Mistborn M4B"}) in events


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

def _status(state):
    return SimpleNamespace(state=state, progress=1.0, downloaded=100, size=100, save_path="/d/book")


@pytest.fixture(autouse=True)
def no_fallback_search():
    from core import audiobook_download_monitor as monitor

    with patch.object(monitor, "_spawn"):
        yield


def _download(db):
    db.add_to_wishlist(BOOK)
    db.record_download("d1", "B1", "The Final Empire", "torrent", client_id="h1",
                       author="Brandon Sanderson", book=BOOK)


def test_a_failed_download_is_announced(db, events):
    from core.audiobook_download_monitor import tick

    _download(db)
    events.clear()
    with patch("core.audiobook_download_monitor._get_status", return_value=_status("error")), \
         patch("core.audiobook_download_monitor._resolve_path", side_effect=lambda r: r):
        tick(db=db)
    failed = [data for event_type, data in events if event_type == "audiobook_download_failed"]
    assert failed == [{"title": "The Final Empire", "author": "Brandon Sanderson", "error": failed[0]["error"]}]


def test_a_held_import_is_announced_once_not_every_poll(db, events):
    from core.audiobook_download_monitor import tick

    _download(db)
    events.clear()
    short = {"complete": False, "reason": "about 180 minutes short"}
    with patch("core.audiobook_download_monitor._get_status", return_value=_status("completed")), \
         patch("core.audiobook_download_monitor._resolve_path", side_effect=lambda r: r), \
         patch("core.audiobook_download_monitor._check_complete", return_value=short):
        tick(db=db)
        tick(db=db)
        tick(db=db)
    assert events == [("audiobook_import_needs_attention", {
        "title": "The Final Empire", "author": "Brandon Sanderson",
        "reason": "about 180 minutes short"})]


def test_a_finished_import_announces_import_completed(db, events):
    from core.audiobook_download_monitor import tick

    _download(db)
    events.clear()
    with patch("core.audiobook_download_monitor._get_status", return_value=_status("completed")), \
         patch("core.audiobook_download_monitor._resolve_path", side_effect=lambda r: r), \
         patch("core.audiobook_download_monitor._check_complete",
               return_value={"complete": True, "reason": "ok"}), \
         patch("core.audiobook_download_monitor._organize", return_value={"ok": True, "path": "/lib/b"}):
        tick(db=db)
    assert ("import_completed", {"track_count": "1", "album_name": "The Final Empire",
                                 "artist": "Brandon Sanderson"}) in events


# ---------------------------------------------------------------------------
# Library scan
# ---------------------------------------------------------------------------

def test_a_library_scan_announces_its_result(db, events, tmp_path):
    from core.audiobook_library_scan import scan

    scan(root=str(tmp_path), db=db)
    assert _types(events) == ["audiobook_library_scan_completed"]
    assert events[0][1]["status"] == "completed"
