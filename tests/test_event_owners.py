"""events belong to whoever they're about (#2166 follow-up 25).

each profile only cares about its own events; admins hear everything. a
member's own automation runs only on an event stamped with that member
(_event_owner, one id or a list); install-wide events never carry anyone.

the engine half (stamp, gate) is driven through the real emit and
event_refusal, with the real emit sites feeding it, so a site that stops
naming its owner fails here.
"""

from __future__ import annotations

import threading

import pytest

from core.automation.owners import INSTALL_EVENTS, OWNER_KEY, owned, owner_value, owners_of

ADMIN, MAYA, JONAH, KIM, ADMIN2 = 1, 2, 3, 5, 4


class _Profiles:
    """the engine reads owners' profiles through db.get_profile."""

    rows = {ADMIN: {"id": ADMIN, "is_admin": 1}, ADMIN2: {"id": ADMIN2, "is_admin": 1},
            MAYA: {"id": MAYA, "is_admin": 0}, JONAH: {"id": JONAH, "is_admin": 0},
            KIM: {"id": KIM, "is_admin": 0}}

    def get_profile(self, pid):
        return self.rows.get(int(pid))


class _Engine:
    """the real AutomationEngine, its event thread run inline and recorded."""

    def __init__(self):
        from core.automation_engine import AutomationEngine
        self.eng = AutomationEngine.__new__(AutomationEngine)
        self.eng.db = _Profiles()
        self.eng._running = True
        self.seen = []
        self.eng._process_event = lambda etype, data: self.seen.append((etype, data))

    def emit(self, event_type, data):
        orig = threading.Thread

        class _Inline:
            def __init__(self, target, args, **_k):
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)
        threading.Thread = _Inline
        try:
            self.eng.emit(event_type, data)
        finally:
            threading.Thread = orig

    def last(self, event_type):
        return [d for t, d in self.seen if t == event_type][-1]

    def hears(self, owner, event_type):
        """would an automation owned by ``owner`` run on the last such event?"""
        row = {"id": 9, "profile_id": owner, "is_system": 0, "trigger_type": event_type}
        return self.eng.event_refusal(row, self.last(event_type)) is None


@pytest.fixture
def engine():
    return _Engine()


# ── the owner stamp ──────────────────────────────────────────────────────

def test_owner_values():
    assert owner_value(3) == 3 and owner_value("3") == 3
    assert owner_value([5, 3, 3, None]) == [3, 5]
    assert owner_value(None) is None and owner_value([]) is None and owner_value(True) is None
    assert owned({"a": 1}, None) == {"a": 1}
    assert owned({"a": 1}, {2}) == {"a": 1, OWNER_KEY: 2}
    assert owners_of({OWNER_KEY: [2, 3]}) == [2, 3] and owners_of({}) == []
    # the video side keeps its own copy (core/video stays off the automation
    # package): it must stamp the same key the same way
    from core.video import download_events as ve
    assert ve.OWNER_KEY == OWNER_KEY
    for v in (3, "3", [5, 3, 3], None, [], [None]):
        assert ve.owner_value(v) == owner_value(v), v


# ── download batches: the motivating case ────────────────────────────────

def _finish_batch(engine, owner, *, succeed=True):
    from core.downloads import lifecycle as lc
    from core.runtime_state import download_batches, download_tasks
    from tests.downloads.test_downloads_lifecycle import _build_deps, _FakeMonitor
    download_tasks.clear()
    download_batches.clear()
    download_tasks["t1"] = {"status": "completed" if succeed else "failed", "track_info": {"name": "X"},
                            "track_index": 0}
    download_batches["b1"] = {"queue": ["t1"], "queue_index": 1, "active_count": 1, "max_concurrent": 1,
                              "permanently_failed_tracks": [], "cancelled_tracks": set(),
                              "playlist_name": "PL", "profile_id": owner}
    deps, _ = _build_deps(automation=engine.eng, monitor=_FakeMonitor())
    orig = threading.Thread

    class _Inline:
        def __init__(self, target=None, args=(), **_k):
            self.target, self.args = target, args

        def start(self):
            if self.target:
                self.target(*self.args)
    threading.Thread = _Inline
    try:
        lc.on_download_completed("b1", "t1", succeed, deps)
    finally:
        threading.Thread = orig
        download_tasks.clear()
        download_batches.clear()


def test_a_members_automation_hears_their_own_batch_not_another_members(engine, monkeypatch):
    from core.downloads import lifecycle as lc
    monkeypatch.setattr(lc, "_scan_owed", False)
    _finish_batch(engine, MAYA)
    assert engine.last("batch_complete")[OWNER_KEY] == MAYA
    assert engine.hears(MAYA, "batch_complete")          # "when my downloads finish"
    assert not engine.hears(JONAH, "batch_complete")     # someone else's
    assert engine.hears(ADMIN, "batch_complete")         # admins hear everything
    assert engine.hears(ADMIN2, "batch_complete")
    _finish_batch(engine, JONAH)
    assert engine.hears(JONAH, "batch_complete") and not engine.hears(MAYA, "batch_complete")


def test_a_batch_that_downloaded_nothing_only_pays_the_installs_scan(engine, monkeypatch):
    from core.downloads import lifecycle as lc
    monkeypatch.setattr(lc, "_scan_owed", True)
    _finish_batch(engine, MAYA, succeed=False)
    data = engine.last("batch_complete")
    assert OWNER_KEY not in data
    assert not engine.hears(MAYA, "batch_complete") and engine.hears(ADMIN, "batch_complete")


def test_a_failed_track_is_its_downloaders(engine):
    from core.downloads import lifecycle as lc
    from core.runtime_state import download_batches, download_tasks
    from tests.downloads.test_downloads_lifecycle import _build_deps
    download_tasks.clear()
    download_batches.clear()
    download_tasks["t1"] = {"status": "not_found", "track_info": {"name": "X"}, "track_index": 0,
                            "retry_count": 0, "profile_id": KIM}
    download_batches["b1"] = {"queue": ["t1"], "queue_index": 1, "active_count": 1, "max_concurrent": 1,
                              "permanently_failed_tracks": [], "cancelled_tracks": set(), "profile_id": KIM}
    deps, _ = _build_deps(automation=engine.eng)
    try:
        lc.on_download_completed("b1", "t1", False, deps)
    finally:
        download_tasks.clear()
        download_batches.clear()
    assert engine.hears(KIM, "download_failed") and not engine.hears(MAYA, "download_failed")


# ── install-wide events ──────────────────────────────────────────────────

@pytest.mark.parametrize("event_type", sorted(INSTALL_EVENTS))
def test_an_install_wide_event_never_reaches_a_member(engine, event_type):
    from core.profile_context import reset_background_profile, set_background_profile
    # even set off inside a member's job, and even if a site named them
    token = set_background_profile(MAYA)
    try:
        engine.emit(event_type, {OWNER_KEY: MAYA})
    finally:
        reset_background_profile(token)
    assert OWNER_KEY not in engine.last(event_type)
    assert not engine.hears(MAYA, event_type)
    assert engine.hears(ADMIN, event_type)


def test_install_events_are_real_triggers():
    from core.automation.blocks import TRIGGERS
    names = {t["type"] for t in TRIGGERS}
    assert INSTALL_EVENTS <= names, INSTALL_EVENTS - names


# ── someone else acting on your things ───────────────────────────────────

def test_an_approval_is_the_requesters_not_the_approvers(engine, monkeypatch):
    import core.app_events as app_events
    from flask import Flask, g
    monkeypatch.setattr(app_events, "_forwarders", [engine.emit])
    from api.music_requests import _event, _video_group
    app = Flask(__name__)
    with app.test_request_context():
        g.profile_id = ADMIN                           # the admin approves, in their request
        _event("music_request_approved", {"kind": "album", "title": "T", "profile_id": MAYA})
        assert engine.hears(MAYA, "music_request_approved")
        assert not engine.hears(JONAH, "music_request_approved")
        _event("music_request_declined", {"kind": "album", "title": "T"}, owner=JONAH)
        assert engine.hears(JONAH, "music_request_declined")
        _event("music_request_available", _video_group({"title": "V", "profile_id": KIM}))
        assert engine.hears(KIM, "music_request_available")


def test_one_video_answer_reaches_everyone_who_asked(engine, monkeypatch):
    import core.video.download_events as ve
    monkeypatch.setattr(ve, "_forwarders", [engine.emit])
    from core.requests.video import sweep_arrivals

    class _Db:
        rows = [{"id": 1, "kind": "movie", "tmdb_id": 7, "title": "M", "profile_id": MAYA},
                {"id": 2, "kind": "movie", "tmdb_id": 7, "title": "M", "profile_id": JONAH}]

        def approved_requests_awaiting_arrival(self):
            return [dict(r) for r in self.rows]

        def annotate_requests_in_library(self, rows):
            for r in rows:
                r["in_library"] = True

        def mark_video_requests_available(self, ids):
            return len(ids)
    sweep_arrivals(_Db(), lambda *a: None)
    data = engine.last("video_request_available")
    assert data[OWNER_KEY] == [MAYA, JONAH]
    assert engine.hears(MAYA, "video_request_available") and engine.hears(JONAH, "video_request_available")
    assert not engine.hears(KIM, "video_request_available")


def test_an_issue_answer_is_its_reporters_and_followers(engine, monkeypatch):
    import core.app_events as app_events
    monkeypatch.setattr(app_events, "_forwarders", [engine.emit])
    from core.issues import service

    class _Store:
        def follower_ids(self, _iid):
            return [JONAH]

        def add_comment(self, *a, **k):
            return 1

        def set_unread(self, *a):
            pass

    issue = {"id": 4, "profile_id": MAYA, "title": "Bad art", "status": "open", "category": "wrong_poster"}
    side = service.IssueSide(get=lambda _i: dict(issue), create=None, update=lambda *_a: True,
                             store=_Store(), notify=lambda *a: None, categories=("wrong_poster",))
    service.comment(side, actor=ADMIN, actor_name="Sam", is_admin=True, issue_id=4, body={"body": "on it"})
    assert engine.hears(MAYA, "issue_commented") and engine.hears(JONAH, "issue_commented")
    assert not engine.hears(KIM, "issue_commented")


def test_an_audiobook_owner_rides_as_an_owner_not_a_string(engine, monkeypatch):
    import core.app_events as app_events
    monkeypatch.setattr(app_events, "_forwarders", [engine.emit])
    from core.audiobook_events import announce
    announce("audiobook_new_release", owner=KIM, artist="A", count=2)
    data = engine.last("audiobook_new_release")
    assert data[OWNER_KEY] == KIM and data["count"] == "2"
    assert engine.hears(KIM, "audiobook_new_release") and not engine.hears(MAYA, "audiobook_new_release")
    announce("audiobook_download_failed", owner=[MAYA, KIM], title="B")
    assert engine.hears(MAYA, "audiobook_download_failed") and engine.hears(KIM, "audiobook_download_failed")


def test_a_book_download_belongs_to_whoever_wished_for_it():
    from core.audiobook_download_monitor import _asin_owners

    class _Db:
        def profiles_with_rows(self):
            return [MAYA, JONAH, KIM]

        def get_wishlist_entry(self, asin, profile_id):
            return {"asin": asin} if profile_id in (MAYA, KIM) else None
    assert _asin_owners(_Db(), "B01") == [MAYA, KIM]
    assert _asin_owners(_Db(), "") == []


# ── video downloads ──────────────────────────────────────────────────────

class _VideoDb:
    def __init__(self, wishers):
        self.wishers = wishers
        self.asked = []

    def wishing_profile_ids(self, **kw):
        self.asked.append(kw)
        return list(self.wishers)


def test_a_video_download_is_its_grabbers_else_its_wishers():
    from core.video.download_events import download_owners
    db = _VideoDb([MAYA, JONAH])
    assert download_owners(db, {"profile_id": KIM, "kind": "movie"}) == [KIM]       # grabbed by hand
    assert download_owners(db, {"kind": "movie", "media_id": "603", "media_source": "tmdb"}) == [MAYA, JONAH]
    assert db.asked[-1] == {"kind": "movie", "tmdb_id": "603"}
    download_owners(db, {"kind": "show", "media_id": "9", "media_source": "tmdb",
                         "search_ctx": '{"season": 1, "episode": 4}'})
    assert db.asked[-1] == {"kind": "episode", "tmdb_id": "9", "season_number": 1, "episode_number": 4}
    download_owners(db, {"kind": "youtube", "media_id": "dQw4w9WgXcQ"})
    assert db.asked[-1] == {"kind": "youtube", "youtube_id": "dQw4w9WgXcQ"}
    assert download_owners(None, {"kind": "movie"}) == []                          # nobody known


def test_the_monitor_names_a_finished_downloads_owners(engine, monkeypatch):
    import core.video.download_events as ve
    from core.video import download_monitor as dm
    monkeypatch.setattr(ve, "_forwarders", [engine.emit])
    dm._publish_terminal({"kind": "movie", "title": "M"}, {"status": "completed"}, [MAYA])
    assert engine.hears(MAYA, "video_download_completed") and not engine.hears(JONAH, "video_download_completed")
    dm._publish_failed({"kind": "movie", "title": "M"}, "nope", [JONAH])
    assert engine.hears(JONAH, "video_download_failed") and not engine.hears(MAYA, "video_download_failed")


def test_wishing_profile_ids_reads_the_wishlist(tmp_path):
    from database.video_database import VideoDatabase
    db = VideoDatabase(str(tmp_path / "v.db"))
    db.add_movie_to_wishlist(603, "The Matrix", profile_id=MAYA)
    db.add_movie_to_wishlist(603, "The Matrix", profile_id=JONAH)
    db.add_movie_to_wishlist(604, "Other", profile_id=KIM)
    assert db.wishing_profile_ids(kind="movie", tmdb_id=603) == [MAYA, JONAH]
    assert db.wishing_profile_ids(kind="movie") == []


# ── signals and chained automations ──────────────────────────────────────

def test_a_members_signal_starts_only_their_own_automations(engine):
    eng = engine.eng
    eng._max_chain_depth = 5
    eng._sanitize_signal_name = lambda n: n
    auto = {"id": 3, "name": "mine", "profile_id": MAYA,
            "then_actions": '[{"type": "fire_signal", "config": {"signal_name": "go"}}]'}
    from core.automation_engine import AutomationEngine
    orig = threading.Thread

    class _Inline:
        def __init__(self, target, args, **_k):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)
    threading.Thread = _Inline
    try:
        AutomationEngine._execute_then_actions(eng, auto, {"status": "ok"}, 0)
    finally:
        threading.Thread = orig
    assert engine.hears(MAYA, "signal:go") and not engine.hears(JONAH, "signal:go")
