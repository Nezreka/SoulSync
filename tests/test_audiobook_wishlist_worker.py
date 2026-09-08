"""Tests for core/audiobook_wishlist_worker.py.

Hermetic: the release search and the grab are always patched, the database is
always a temp file, and no test starts a real thread that outlives it.

The behaviour under test is mostly about restraint. A wishlist worker that
searches too eagerly is worse than one that does not exist — it spends the
shared indexer budget the music side also needs.
"""

from unittest.mock import patch

import pytest

from core.audiobook_database import (
    STATUS_FAILED,
    STATUS_GRABBED,
    STATUS_WANTED,
    AudiobookDatabase,
)
from core.audiobook_release_search import AudiobookRelease
from core.audiobook_wishlist_worker import (
    AUTOMATION_ACTION,
    _book_from_row,
    batch_size,
    process_one,
    retry_after_seconds,
    run_pass,
    schedule_status,
)


@pytest.fixture
def db(tmp_path):
    return AudiobookDatabase(str(tmp_path / "audiobooks.db"))


def _book(asin="B1", title="The Final Empire"):
    return {
        "asin": asin,
        "title": title,
        "author_names": ["Brandon Sanderson"],
        "narrator_names": ["Michael Kramer"],
        "runtime_minutes": 1479,
        "release_date": "2006-07-17",
        "series": [{"title": "The Mistborn Saga", "sequence": "1"}],
    }


def _release(title="The Final Empire M4B"):
    return AudiobookRelease(
        source="prowlarr", protocol="torrent", title=title, indexer="X",
        size_bytes=700 * 1024 * 1024, guid="g1",
        download_url="https://example.invalid/a.torrent", seeders=30,
    )


# ---------------------------------------------------------------------------
# Row round-trip
# ---------------------------------------------------------------------------

def test_a_stored_row_rebuilds_into_a_searchable_book(db):
    db.add_to_wishlist(_book())
    row = db.get_wishlist()[0]
    book = _book_from_row(row)
    assert book["title"] == "The Final Empire"
    assert book["author_names"] == ["Brandon Sanderson"]
    assert book["series"] == [{"title": "The Mistborn Saga", "sequence": "1"}]


def test_a_standalone_row_rebuilds_with_no_series(db):
    db.add_to_wishlist(dict(_book(), series=[]))
    assert _book_from_row(db.get_wishlist()[0])["series"] == []


# ---------------------------------------------------------------------------
# One book
# ---------------------------------------------------------------------------

def test_a_found_release_is_grabbed_and_the_row_moves_on(db):
    db.add_to_wishlist(_book())
    row = db.get_wishlist()[0]
    with patch("core.audiobook_release_search.search_releases", return_value=[_release()]), \
         patch("core.audiobook_grab.grab_release", return_value={"ok": True, "ref": "h1"}):
        result = process_one(row, db=db)
    assert result["grabbed"] is True
    assert db.get_wishlist()[0]["status"] == STATUS_GRABBED


def test_nothing_found_still_records_an_attempt(db):
    # A pass that failed silently leaves the book due again immediately and
    # turns the wishlist into a spin loop.
    db.add_to_wishlist(_book())
    row = db.get_wishlist()[0]
    with patch("core.audiobook_release_search.search_releases", return_value=[]):
        result = process_one(row, db=db)
    stored = db.get_wishlist()[0]
    assert result["grabbed"] is False
    assert stored["status"] == STATUS_FAILED
    assert stored["attempt_count"] == 1
    assert stored["last_error"]


def test_a_failing_search_does_not_raise(db):
    db.add_to_wishlist(_book())
    row = db.get_wishlist()[0]
    with patch("core.audiobook_release_search.search_releases",
               side_effect=RuntimeError("indexer down")):
        result = process_one(row, db=db)
    assert "indexer down" in result["error"]
    assert db.get_wishlist()[0]["status"] == STATUS_FAILED


def test_a_refused_grab_is_recorded_as_failed(db):
    db.add_to_wishlist(_book())
    row = db.get_wishlist()[0]
    with patch("core.audiobook_release_search.search_releases", return_value=[_release()]), \
         patch("core.audiobook_grab.grab_release",
               return_value={"ok": False, "error": "No torrent client configured"}):
        result = process_one(row, db=db)
    stored = db.get_wishlist()[0]
    assert result["grabbed"] is False
    assert stored["status"] == STATUS_FAILED
    assert "No torrent client" in stored["last_error"]


def test_the_best_release_is_the_one_grabbed(db):
    # search_releases already returns them ranked; taking anything but the first
    # would quietly ignore that ranking.
    db.add_to_wishlist(_book())
    row = db.get_wishlist()[0]
    ranked = [_release("BEST M4B"), _release("worse mp3")]
    with patch("core.audiobook_release_search.search_releases", return_value=ranked), \
         patch("core.audiobook_grab.grab_release", return_value={"ok": True}) as grab:
        process_one(row, db=db)
    assert grab.call_args[0][0].title == "BEST M4B"


def test_auto_grab_can_be_turned_off(db):
    db.add_to_wishlist(_book())
    row = db.get_wishlist()[0]
    with patch("core.audiobook_release_search.search_releases", return_value=[_release()]), \
         patch("core.audiobook_grab.grab_release") as grab:
        result = process_one(row, db=db, auto_grab=False)
    grab.assert_not_called()
    assert result["found"] == 1


def test_a_row_without_an_asin_is_skipped(db):
    with patch("core.audiobook_release_search.search_releases") as search:
        result = process_one({"title": "No asin"}, db=db)
    search.assert_not_called()
    assert result["grabbed"] is False


# ---------------------------------------------------------------------------
# A pass
# ---------------------------------------------------------------------------

def test_a_pass_walks_the_due_books(db):
    for index in range(3):
        db.add_to_wishlist(_book(f"B{index}", f"Book {index}"))
    with patch("core.audiobook_release_search.search_releases", return_value=[]):
        summary = run_pass(db=db, limit=10)
    assert summary["checked"] == 3


def test_a_pass_takes_only_a_handful(db):
    # Searching the whole wishlist at once puts a hundred searches on the
    # indexers in one burst.
    for index in range(20):
        db.add_to_wishlist(_book(f"B{index}", f"Book {index}"))
    with patch("core.audiobook_release_search.search_releases", return_value=[]):
        summary = run_pass(db=db, limit=4)
    assert summary["checked"] == 4


def test_a_pass_skips_books_still_inside_their_backoff(db):
    # One unfindable book must not be searched every half hour forever while
    # newer entries wait behind it.
    db.add_to_wishlist(_book())
    with patch("core.audiobook_release_search.search_releases", return_value=[]):
        run_pass(db=db, limit=10)
        second = run_pass(db=db, limit=10)
    assert second["checked"] == 0


def test_a_pass_never_re_grabs_something_already_grabbed(db):
    db.add_to_wishlist(_book())
    with patch("core.audiobook_release_search.search_releases", return_value=[_release()]), \
         patch("core.audiobook_grab.grab_release", return_value={"ok": True}):
        run_pass(db=db, limit=10)
    with patch("core.audiobook_release_search.search_releases", return_value=[_release()]), \
         patch("core.audiobook_grab.grab_release") as grab:
        summary = run_pass(db=db, limit=10)
    grab.assert_not_called()
    assert summary["checked"] == 0


def test_an_empty_wishlist_is_a_no_op(db):
    with patch("core.audiobook_release_search.search_releases") as search:
        assert run_pass(db=db)["checked"] == 0
    search.assert_not_called()


def test_a_broken_database_does_not_raise(db):
    class Broken:
        def get_wishlist_due(self, **kwargs):
            raise RuntimeError("db gone")

    assert run_pass(db=Broken())["checked"] == 0


# ---------------------------------------------------------------------------
# Pacing knobs
# ---------------------------------------------------------------------------

def test_the_defaults_are_deliberately_quiet():
    assert retry_after_seconds() >= 3600
    assert batch_size() <= 25


@pytest.mark.parametrize("value", [1, 0, -5])
def test_the_backoff_cannot_be_set_to_a_hammer(value):
    with patch("core.settings.config_manager.get", return_value=value):
        assert retry_after_seconds() >= 60.0


def test_junk_settings_fall_back_to_the_defaults():
    with patch("core.settings.config_manager.get", return_value="not a number"):
        assert retry_after_seconds() > 0
        assert batch_size() > 0


def test_the_batch_size_is_capped():
    with patch("core.settings.config_manager.get", return_value=10_000):
        assert batch_size() == 25





# ---------------------------------------------------------------------------
# Scheduling belongs to the automation engine
# ---------------------------------------------------------------------------

def test_this_module_owns_no_timer():
    """The pass is driven by the shared automation engine, not a private thread.

    An automation can be paused, rescheduled or run by hand from the Automations
    page and obeys the master switch; a daemon thread is none of those, and a
    user who wanted it to stop had nowhere to say so.
    """
    import inspect

    import core.audiobook_wishlist_worker as module

    source = inspect.getsource(module)
    assert "threading.Thread" not in source
    assert "class AudiobookWishlistWorker" not in source
    assert not hasattr(module, "ensure_started")


def test_the_action_type_matches_the_seeded_automation():
    # The module and the engine must not disagree about which action owns this.
    from core.automation_engine import SYSTEM_AUTOMATIONS

    actions = {spec["action_type"] for spec in SYSTEM_AUTOMATIONS}
    assert AUTOMATION_ACTION in actions


def test_the_automation_is_registered_with_a_handler():
    # A seeded automation with no handler fires and does nothing, forever.
    from core.automation.handlers import auto_process_audiobook_wishlist

    assert callable(auto_process_audiobook_wishlist)


def test_the_status_only_claims_what_this_module_owns():
    # The interval belongs to the automation. Reporting one here would let the
    # page show a cadence that had drifted from the one actually running.
    status = schedule_status()
    assert set(status) == {
        "retry_after_seconds", "batch_size", "action_type", "automation_name",
    }
    assert status["action_type"] == AUTOMATION_ACTION
    assert "interval_seconds" not in status


def test_the_handler_skips_an_install_that_never_used_audiobooks():
    # Seeding an automation must not be what creates the audiobook database.
    from core.automation.handlers.audiobook_process_wishlist import (
        auto_process_audiobook_wishlist,
    )

    with patch("core.audiobook_database.subsystem_in_use", return_value=False), \
         patch("core.audiobook_wishlist_worker.run_pass") as pass_fn:
        result = auto_process_audiobook_wishlist({}, deps=None)
    pass_fn.assert_not_called()
    assert result["status"] == "completed"


def test_the_handler_reports_what_the_pass_did():
    from core.automation.handlers.audiobook_process_wishlist import (
        auto_process_audiobook_wishlist,
    )

    with patch("core.audiobook_database.subsystem_in_use", return_value=True), \
         patch("core.audiobook_wishlist_worker.run_pass",
               return_value={"checked": 3, "found": 2, "grabbed": 1, "errors": 0}):
        result = auto_process_audiobook_wishlist({}, deps=None)
    assert result["status"] == "completed"
    assert result["checked"] == 3
    assert result["grabbed"] == 1


def test_the_handler_never_raises_into_the_engine():
    # A handler that throws would mark the automation failed and, worse, could
    # take the engine's run loop with it.
    from core.automation.handlers.audiobook_process_wishlist import (
        auto_process_audiobook_wishlist,
    )

    with patch("core.audiobook_database.subsystem_in_use", return_value=True), \
         patch("core.audiobook_wishlist_worker.run_pass", side_effect=RuntimeError("boom")):
        result = auto_process_audiobook_wishlist({}, deps=None)
    assert result["status"] == "error"
    assert "boom" in result["error"]


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------

def test_the_worker_never_touches_music_download_state():
    import ast
    import inspect

    import core.audiobook_wishlist_worker as module

    tree = ast.parse(inspect.getsource(module))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    for name in imported:
        for forbidden in ("core.downloads", "core.runtime_state", "core.wishlist",
                          "core.download_engine", "core.download_orchestrator",
                          "database", "core.video", "web_server"):
            assert not name.startswith(forbidden), f"the worker imports {name}"


# ---------------------------------------------------------------------------
# Narrator choice
# ---------------------------------------------------------------------------

def test_the_pass_honours_the_stored_narrator_choice(db):
    # The automatic path must hold to the listener's choice exactly as the
    # manual one does — a book is always one narrator, never a mixture.
    db.add_to_wishlist(_book(), narrator_mode="any")
    row = db.get_wishlist()[0]
    with patch("core.audiobook_release_search.search_releases", return_value=[]) as search:
        process_one(row, db=db)
    assert search.call_args.kwargs["narrator_mode"] == "any"


def test_the_pass_defaults_to_the_chosen_reading(db):
    db.add_to_wishlist(_book())
    row = db.get_wishlist()[0]
    with patch("core.audiobook_release_search.search_releases", return_value=[]) as search:
        process_one(row, db=db)
    assert search.call_args.kwargs["narrator_mode"] == "exact"


def test_a_row_predating_the_choice_is_treated_as_exact(db):
    # Older wishlist rows have no narrator_mode; holding to the chosen reading
    # is the safe reading of a book wished for before the choice existed.
    db.add_to_wishlist(_book())
    row = dict(db.get_wishlist()[0])
    row.pop("narrator_mode", None)
    with patch("core.audiobook_release_search.search_releases", return_value=[]) as search:
        process_one(row, db=db)
    assert search.call_args.kwargs["narrator_mode"] == "exact"
