"""Failed-track wishlist re-add: log what actually happened to each track.

``add_failed_track_from_modal`` used to return the bare ``add_to_wishlist``
bool, which is True only for a NEW row. A track that was already on the
wishlist (refreshed or a plain duplicate), blocklisted or ignore-listed came
back False and was logged at ERROR as "Failed to add", next to progress lines
that were also hard-coded at ERROR. The loop now asks for the detailed outcome
and logs each status at a level that matches it, without changing the batch
summary counts or the ``wishlist_item_added`` event.
"""
import logging

import pytest

import core.downloads.wishlist_failed as wf_mod

LOGGER = wf_mod.logger.name  # "soulsync.downloads.wishlist_failed"


def _outcome(status, reason=None):
    return {"status": status,
            "created": status in ("created", "satisfied"),
            "applied": status in ("created", "updated", "satisfied"),
            "track_id": "sp-1", "reason": reason}


class _Service:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def add_failed_track_from_modal(self, **kwargs):
        self.calls.append(kwargs)
        return self.outcome

    def mark_track_download_result(self, *args, **kwargs):
        return True


class _Engine:
    def __init__(self):
        self.events = []

    def emit(self, name, payload):
        self.events.append(name)


def _run(monkeypatch, caplog, outcome):
    engine = _Engine()
    monkeypatch.setattr(wf_mod, "automation_engine", engine)
    service = _Service(outcome)
    track = {"track_name": "Your Arms", "failure_reason": "no candidates",
             "track_data": {"id": "sp-1", "name": "Your Arms",
                            "artists": [{"name": "Some Artist"}]}}
    batch = {"profile_id": 1, "playlist_id": "wishlist", "playlist_name": "Wishlist"}
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        summary = wf_mod.add_failed_tracks_to_wishlist(batch, [track], service)
    records = [r for r in caplog.records if r.name == LOGGER]
    return service, engine, summary, records


def _errors(records):
    return [r.getMessage() for r in records if r.levelno >= logging.ERROR]


def test_asks_the_service_for_the_detailed_outcome(monkeypatch, caplog):
    service, *_ = _run(monkeypatch, caplog, _outcome("created"))
    assert service.calls[0]["detailed"] is True


@pytest.mark.parametrize("status", ["created", "satisfied"])
def test_new_or_covered_track_counts_as_added(monkeypatch, caplog, status):
    _, engine, summary, records = _run(monkeypatch, caplog, _outcome(status))
    assert summary["tracks_added"] == 1
    assert engine.events == ["wishlist_item_added"]
    assert not _errors(records)
    assert any("Added Your Arms to wishlist" in r.getMessage() for r in records)


@pytest.mark.parametrize("status,reason", [("updated", None), ("skipped", "duplicate")])
def test_track_already_on_the_wishlist_is_not_an_error(monkeypatch, caplog, status, reason):
    _, engine, summary, records = _run(monkeypatch, caplog, _outcome(status, reason))
    assert summary["tracks_added"] == 0
    assert summary["errors"] == 0
    assert engine.events == []
    assert not _errors(records)
    assert not any("Failed to add" in r.getMessage() for r in records)
    assert any("already on the wishlist" in r.getMessage() and r.levelno == logging.INFO
               for r in records)
    assert any("(1 already on it" in r.getMessage() for r in records)


@pytest.mark.parametrize("reason", ["blocklisted", "ignore-list"])
def test_blocked_track_is_logged_as_not_re_added(monkeypatch, caplog, reason):
    _, engine, summary, records = _run(monkeypatch, caplog, _outcome("skipped", reason))
    assert summary["tracks_added"] == 0
    assert engine.events == []
    assert not _errors(records)
    assert any(f"not re-added to wishlist ({reason})" in r.getMessage()
               and r.levelno == logging.INFO for r in records)


@pytest.mark.parametrize("status,reason", [("rejected", "request_limit"), ("error", "db locked")])
def test_real_failure_is_a_warning_with_its_reason(monkeypatch, caplog, status, reason):
    _, engine, summary, records = _run(monkeypatch, caplog, _outcome(status, reason))
    assert summary["tracks_added"] == 0
    assert summary["errors"] == 0  # still counts exceptions only
    assert engine.events == []
    warn = [r for r in records if "Failed to add Your Arms" in r.getMessage()]
    assert warn and warn[0].levelno == logging.WARNING
    assert reason in warn[0].getMessage()


def test_progress_lines_are_not_errors(monkeypatch, caplog):
    _, _, _, records = _run(monkeypatch, caplog, _outcome("created"))
    progress = [r for r in records
                if "Adding track 1/1" in r.getMessage()
                or "Added 1/1 failed tracks" in r.getMessage()]
    assert len(progress) == 2
    assert all(r.levelno == logging.INFO for r in progress)
