"""Tests for core/audiobook_search_job.py — background release searches.

Hermetic: no Prowlarr, no slskd, no network. The search legs are patched and
the job thread is joined rather than slept on.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from core.audiobook_release_search import AudiobookRelease
from core.audiobook_search_job import forget, poll, start

BOOK = {
    "asin": "B08G9PRS1K",
    "title": "Project Hail Mary",
    "author_names": ["Andy Weir"],
    "narrator_names": ["Ray Porter"],
    "runtime_minutes": 970,
    "series": [],
}


def _release(title="Project Hail Mary M4B", source="prowlarr", protocol="torrent"):
    """A release that will actually survive ranking.

    The job re-ranks everything it collects, so a double titled "a" scores no
    relevance against Project Hail Mary and is filtered out — leaving a test
    that asserts on an empty list and proves nothing.
    """
    return AudiobookRelease(source=source, protocol=protocol, title=title,
                            indexer="x", size_bytes=800_000_000)


def _chain(order=("torrent", "usenet", "soulseek")):
    manager = MagicMock()
    manager.get.side_effect = lambda key, default=None: (
        "hybrid" if key.endswith(".mode") else list(order)
    )
    return patch("core.settings.config_manager", manager)


def _wait(job_id, timeout=5.0):
    """Poll until the job says it is finished."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = poll(job_id)
        if state is None or state["complete"]:
            return state
        time.sleep(0.02)
    raise AssertionError("job never completed")


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------

def test_start_returns_immediately_with_an_id():
    # The whole point: the caller must not block on the search.
    def slow(*a, **kw):
        time.sleep(0.3)
        return iter(())

    with _chain(), patch("core.audiobook_release_search.iter_prowlarr_releases", slow), \
         patch("core.audiobook_soulseek.is_available", return_value=False):
        began = time.time()
        job_id = start(BOOK)
        assert time.time() - began < 0.2
        assert job_id
        _wait(job_id)


def test_results_appear_before_the_search_finishes():
    # The bug this exists to fix: nothing rendered until every source settled.
    gate = {"released": False}

    def two_steps(*a, **kw):
        yield {"query": "andy weir project hail mary", "releases": [_release("Project Hail Mary M4B")],
               "complete": False}
        while not gate["released"]:
            time.sleep(0.01)
        yield {"query": "project hail mary", "releases": [_release("Project Hail Mary M4B"),
                            _release("Project Hail Mary MP3 64k")],
               "complete": True}

    with _chain(), \
         patch("core.audiobook_release_search.iter_prowlarr_releases", two_steps), \
         patch("core.audiobook_soulseek.is_available", return_value=False):
        job_id = start(BOOK)

        deadline = time.time() + 5
        partial = None
        while time.time() < deadline:
            state = poll(job_id)
            if state and state["releases"] and not state["complete"]:
                partial = state
                break
            time.sleep(0.02)

        assert partial is not None, "no results were visible mid-search"
        assert partial["stage"], "the stage should say what is being searched"

        gate["released"] = True
        final = _wait(job_id)

    assert len(final["releases"]) > len(partial["releases"])


def test_a_poll_always_returns_the_whole_ranked_pool():
    # Never a delta: ranking is global, so a late arrival has to be able to
    # sort above an early one.
    def steps(*a, **kw):
        yield {"query": "q1", "releases": [_release("Project Hail Mary M4B")],
               "complete": False}
        yield {"query": "q2", "releases": [_release("Project Hail Mary M4B"),
                                  _release("Project Hail Mary MP3 64k")],
               "complete": True}

    with _chain(), patch("core.audiobook_release_search.iter_prowlarr_releases", steps), \
         patch("core.audiobook_soulseek.is_available", return_value=False):
        state = _wait(start(BOOK))
    titles = [r["title"] for r in state["releases"]]
    assert "Project Hail Mary M4B" in titles
    assert "Project Hail Mary MP3 64k" in titles


def test_soulseek_results_join_the_same_pool():
    peer = _release("Project Hail Mary read by Ray Porter", source="soulseek",
                    protocol="soulseek")

    def steps(*a, **kw):
        yield {"query": "q1", "releases": [_release()], "complete": True}

    with _chain(), patch("core.audiobook_release_search.iter_prowlarr_releases", steps), \
         patch("core.audiobook_soulseek.is_available", return_value=True), \
         patch("core.audiobook_soulseek.search", return_value=[peer]):
        state = _wait(start(BOOK))

    assert {r["source"] for r in state["releases"]} == {"prowlarr", "soulseek"}


def test_the_job_says_what_it_is_doing_while_it_works():
    """The stage line is the difference between "working" and "hung".

    A blank modal for forty seconds reads as broken however fast the search
    actually is.
    """
    seen = set()
    gate = {"released": False}

    def steps(*a, **kw):
        yield {"query": "andy weir project hail mary",
               "releases": [_release()], "complete": False}
        while not gate["released"]:
            time.sleep(0.01)
        yield {"query": "project hail mary", "releases": [_release()], "complete": True}

    with _chain(), patch("core.audiobook_release_search.iter_prowlarr_releases", steps), \
         patch("core.audiobook_soulseek.is_available", return_value=False):
        job_id = start(BOOK)
        # Collect every stage the job publishes rather than asserting on
        # whichever one the first poll happens to catch. The job is set up
        # before the search starts, so "Starting the search" is a legitimate
        # first answer and racing it made this flaky.
        deadline = time.time() + 5
        while time.time() < deadline:
            state = poll(job_id)
            if state and state["stage"]:
                seen.add(state["stage"])
            if any("andy weir" in stage for stage in seen):
                break
            time.sleep(0.02)
        gate["released"] = True
        _wait(job_id)

    assert seen, "the job never reported a stage"
    assert any("andy weir project hail mary" in stage for stage in seen), seen


# ---------------------------------------------------------------------------
# Failure
# ---------------------------------------------------------------------------

def test_a_broken_source_with_no_results_reports_the_error():
    # Same rule as the blocking search: a source that errored did not answer
    # "no", so an empty pool plus a failure is an error, not an empty shelf.
    def boom(*a, **kw):
        raise RuntimeError("prowlarr down")

    with _chain(), patch("core.audiobook_release_search.iter_prowlarr_releases", boom), \
         patch("core.audiobook_soulseek.is_available", return_value=False):
        state = _wait(start(BOOK))

    assert state["complete"] is True
    assert "prowlarr down" in state["error"]


def test_results_beat_a_broken_source():
    def boom(*a, **kw):
        raise RuntimeError("prowlarr down")

    peer = _release("Project Hail Mary read by Ray Porter", source="soulseek",
                    protocol="soulseek")
    with _chain(), patch("core.audiobook_release_search.iter_prowlarr_releases", boom), \
         patch("core.audiobook_soulseek.is_available", return_value=True), \
         patch("core.audiobook_soulseek.search", return_value=[peer]):
        state = _wait(start(BOOK))

    assert state["error"] == ""
    assert len(state["releases"]) == 1


def test_a_search_that_finds_nothing_is_not_an_error():
    def nothing(*a, **kw):
        return iter(())

    with _chain(), patch("core.audiobook_release_search.iter_prowlarr_releases", nothing), \
         patch("core.audiobook_soulseek.is_available", return_value=False):
        state = _wait(start(BOOK))

    assert state["complete"] is True and state["error"] == "" and state["releases"] == []


def test_a_dying_job_still_completes():
    # A job that never sets complete would leave the modal spinning forever.
    with _chain(), \
         patch("core.audiobook_release_search.iter_prowlarr_releases",
               side_effect=BaseException("catastrophic")), \
         patch("core.audiobook_soulseek.is_available", return_value=False):
        job_id = start(BOOK)
        state = _wait(job_id)
    assert state["complete"] is True


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------

def test_polling_an_unknown_job_reports_nothing():
    assert poll("not-a-job") is None


def test_polling_an_empty_id_reports_nothing():
    assert poll("") is None


def test_a_job_can_be_dropped():
    def nothing(*a, **kw):
        return iter(())

    with _chain(), patch("core.audiobook_release_search.iter_prowlarr_releases", nothing), \
         patch("core.audiobook_soulseek.is_available", return_value=False):
        job_id = start(BOOK)
        _wait(job_id)

    assert forget(job_id) is True
    assert poll(job_id) is None
    assert forget(job_id) is False


def test_two_searches_do_not_share_a_job():
    def nothing(*a, **kw):
        return iter(())

    with _chain(), patch("core.audiobook_release_search.iter_prowlarr_releases", nothing), \
         patch("core.audiobook_soulseek.is_available", return_value=False):
        first = start(BOOK)
        second = start({**BOOK, "asin": "B2"})
        _wait(first)
        _wait(second)

    assert first != second
    assert poll(first)["id"] != poll(second)["id"]


def test_finished_jobs_are_pruned_rather_than_accumulating():
    import core.audiobook_search_job as module

    def nothing(*a, **kw):
        return iter(())

    with _chain(), patch("core.audiobook_release_search.iter_prowlarr_releases", nothing), \
         patch("core.audiobook_soulseek.is_available", return_value=False):
        old = start(BOOK)
        _wait(old)
        # Age it past the TTL, then start another — the prune runs on start.
        with module._lock:
            module._jobs[old]["updated_at"] = time.time() - module.JOB_TTL_SECONDS - 1
        fresh = start(BOOK)
        _wait(fresh)

    assert poll(old) is None
    assert poll(fresh) is not None


def test_the_chain_decides_which_sources_a_job_asks():
    def steps(*a, **kw):
        yield {"query": "q", "releases": [], "complete": True}

    with _chain(order=("torrent",)), \
         patch("core.audiobook_release_search.iter_prowlarr_releases", steps), \
         patch("core.audiobook_soulseek.search") as slsk:
        _wait(start(BOOK))
    slsk.assert_not_called()


@pytest.mark.parametrize("mode", ["exact", "any"])
def test_the_narrator_choice_reaches_the_search(mode):
    seen = {}

    def steps(*a, **kw):
        seen["narrator_mode"] = kw.get("narrator_mode")
        yield {"query": "q", "releases": [], "complete": True}

    with _chain(), patch("core.audiobook_release_search.iter_prowlarr_releases", steps), \
         patch("core.audiobook_soulseek.is_available", return_value=False):
        _wait(start(BOOK, narrator_mode=mode))

    assert seen["narrator_mode"] == mode
