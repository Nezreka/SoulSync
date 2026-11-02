"""The owned-library counts behind /api/v1/system/stats are served from
cache and refreshed in the background: the queries walk every artist, album
and track through the file-evidence joins (seconds on a 311k-track library)
while the extension popup asks on every open. The track count carries the
caller's library scope, so entries are keyed by scope and a refresh
recomputes under the scope its entry serves; a finished scan clears both
count caches via invalidate_database_info(), and a refresh that started
before the scan must not write its pre-scan counts back over that clear.
A failed count is never cached, and a failed refresh backs off to the TTL
instead of retrying on every ask."""

from __future__ import annotations

import threading
import time

import pytest

from core.library2.sql_util import ANY_OWNER
from database.music_database import MusicDatabase


def _age(cache):
    for key, (at, value) in list(cache.items()):
        cache[key] = (at - 10_000, value)


def _wait_for_refresh(refreshing):
    for _ in range(50):
        if not refreshing:
            return
        time.sleep(0.05)
    raise AssertionError("the background refresh never finished")


@pytest.fixture()
def clean_caches():
    MusicDatabase._stats_cache.clear()
    MusicDatabase._stats_refreshing.clear()
    MusicDatabase._db_info_cache.clear()
    MusicDatabase._db_info_refreshing.clear()
    yield
    MusicDatabase._stats_cache.clear()
    MusicDatabase._stats_refreshing.clear()
    MusicDatabase._db_info_cache.clear()
    MusicDatabase._db_info_refreshing.clear()


@pytest.fixture()
def db(tmp_path, monkeypatch, clean_caches):
    database = MusicDatabase(str(tmp_path / "m.db"))
    calls = []
    gate = threading.Event()

    def slow(scope):
        calls.append(scope)
        # gate only background refreshes (worker threads): a gated
        # foreground compute would just stall the test on the timeout
        if len(calls) > 1 and threading.current_thread() is not threading.main_thread():
            gate.wait(5)
        return {"artists": len(calls), "albums": 0, "tracks": 0}
    monkeypatch.setattr(database, "_compute_statistics", slow)
    yield database, calls, gate
    gate.set()


def test_repeated_asks_within_the_ttl_compute_once(db):
    database, calls, _gate = db
    assert database.get_statistics()["artists"] == 1
    assert database.get_statistics()["artists"] == 1
    assert len(calls) == 1


def test_a_stale_answer_is_served_at_once_and_refreshed_behind(db):
    database, calls, gate = db
    assert database.get_statistics()["artists"] == 1
    _age(MusicDatabase._stats_cache)
    started = time.monotonic()
    assert database.get_statistics()["artists"] == 1
    assert time.monotonic() - started < 1, "the stale answer must not wait for the refresh"
    # a second ask while it refreshes starts no second refresh
    database.get_statistics()
    gate.set()
    _wait_for_refresh(MusicDatabase._stats_refreshing)
    assert len(calls) == 2
    assert database.get_statistics()["artists"] == 2


def test_a_scan_clears_the_counts(db):
    database, calls, _gate = db
    database.get_statistics()
    MusicDatabase.invalidate_database_info()
    assert database.get_statistics()["artists"] == 2


def test_a_scan_landing_mid_refresh_keeps_the_refresh_out(db):
    database, calls, gate = db
    assert database.get_statistics()["artists"] == 1
    _age(MusicDatabase._stats_cache)
    database.get_statistics()  # spawns the gated refresh
    MusicDatabase.invalidate_database_info()
    gate.set()
    _wait_for_refresh(MusicDatabase._stats_refreshing)
    # the pre-scan refresh was dropped, not written back over the clear:
    # the next ask recomputes instead of serving the refreshed value
    assert database.get_statistics()["artists"] == 3
    assert len(calls) == 3


def test_scopes_get_their_own_entries_and_their_own_refreshes(db, monkeypatch):
    database, calls, gate = db
    import core.library2.sql_util as sql_util
    monkeypatch.setattr(sql_util, "ambient_scope", lambda: ANY_OWNER)
    assert database.get_statistics()["artists"] == 1
    monkeypatch.setattr(sql_util, "ambient_scope", lambda: 7)
    assert database.get_statistics()["artists"] == 2
    # and each scope's repeat ask is served from its own entry
    assert database.get_statistics()["artists"] == 2
    monkeypatch.setattr(sql_util, "ambient_scope", lambda: ANY_OWNER)
    assert database.get_statistics()["artists"] == 1
    assert calls[0] is ANY_OWNER and calls[1] == 7
    # a background refresh recomputes under the scope its entry serves —
    # the refresh thread wakes with no ambient scope of its own, so the
    # scope has to travel with the refresh, not be re-read there. Model
    # exactly that: past this point the ambient scope only exists on the
    # main thread, so a re-read inside the refresh thread would record
    # ANY_OWNER and fail here deterministically, no race involved.
    monkeypatch.setattr(sql_util, "ambient_scope",
                        lambda: 7 if threading.current_thread() is threading.main_thread() else ANY_OWNER)
    _age(MusicDatabase._stats_cache)
    database.get_statistics()  # spawns the gated refresh for the 7 entry
    gate.set()
    _wait_for_refresh(MusicDatabase._stats_refreshing)
    assert len(calls) == 3
    assert calls[-1] == 7


def test_a_caller_cannot_poison_the_cache_by_mutating_its_answer(db):
    database, _calls, _gate = db
    answer = database.get_statistics()
    answer["artists"] = 999_999
    assert database.get_statistics()["artists"] == 1


def test_a_failed_refresh_keeps_the_stale_answer_and_backs_off(tmp_path, monkeypatch, clean_caches):
    database = MusicDatabase(str(tmp_path / "m.db"))
    calls = []

    def flaky(scope):
        calls.append(scope)
        if len(calls) == 2:
            raise RuntimeError("database is locked")
        return {"artists": len(calls), "albums": 0, "tracks": 0}
    monkeypatch.setattr(database, "_compute_statistics", flaky)
    assert database.get_statistics()["artists"] == 1
    _age(MusicDatabase._stats_cache)
    assert database.get_statistics()["artists"] == 1  # spawns the failing refresh
    _wait_for_refresh(MusicDatabase._stats_refreshing)
    assert len(calls) == 2
    # re-stamped by the failure: further asks serve stale without retrying
    assert database.get_statistics()["artists"] == 1
    assert len(calls) == 2
    # once the TTL passes again the refresh retries and lands
    _age(MusicDatabase._stats_cache)
    assert database.get_statistics()["artists"] == 1
    _wait_for_refresh(MusicDatabase._stats_refreshing)
    assert database.get_statistics()["artists"] == 3


def test_a_failed_count_returns_zeros_and_is_not_cached(tmp_path, monkeypatch, clean_caches):
    database = MusicDatabase(str(tmp_path / "m.db"))
    calls = []

    def flaky(scope):
        calls.append(scope)
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        return {"artists": 3, "albums": 2, "tracks": 1}
    monkeypatch.setattr(database, "_compute_statistics", flaky)
    assert database.get_statistics() == {"artists": 0, "albums": 0, "tracks": 0}
    assert database.get_statistics() == {"artists": 3, "albums": 2, "tracks": 1}


def test_a_scan_landing_mid_first_compute_keeps_the_answer_out(tmp_path, monkeypatch, clean_caches):
    database = MusicDatabase(str(tmp_path / "m.db"))
    calls = []
    gate = threading.Event()

    def slow_first(scope):
        calls.append(scope)
        if len(calls) == 1:
            gate.wait(5)
        return {"artists": len(calls), "albums": 0, "tracks": 0}
    monkeypatch.setattr(database, "_compute_statistics", slow_first)
    result = []
    asker = threading.Thread(target=lambda: result.append(database.get_statistics()))
    asker.start()
    for _ in range(50):
        if calls:
            break
        time.sleep(0.05)
    MusicDatabase.invalidate_database_info()
    gate.set()
    asker.join(5)
    # the asker still gets the answer it computed — it was just true —
    # but it was not cached over the clear, so the next ask recomputes
    assert result[0]["artists"] == 1
    assert database.get_statistics()["artists"] == 2
    assert len(calls) == 2


def test_a_scan_clears_both_count_caches(db, monkeypatch):
    database, _calls, _gate = db
    monkeypatch.setattr(database, "_database_info_for_server",
                        lambda server_source=None: {"total_artists": 1})
    database.get_statistics()
    database.get_database_info_for_server("plex")
    assert MusicDatabase._stats_cache and MusicDatabase._db_info_cache
    MusicDatabase.invalidate_database_info()
    assert MusicDatabase._stats_cache == {}
    assert MusicDatabase._db_info_cache == {}


def test_a_scan_landing_mid_db_info_refresh_keeps_the_refresh_out(tmp_path, monkeypatch, clean_caches):
    database = MusicDatabase(str(tmp_path / "m.db"))
    calls = []
    gate = threading.Event()

    def slow_info(server_source=None):
        calls.append(server_source)
        if len(calls) > 1:
            gate.wait(5)
        return {"total_artists": len(calls)}
    monkeypatch.setattr(database, "_database_info_for_server", slow_info)
    assert database.get_database_info_for_server("plex")["total_artists"] == 1
    _age(MusicDatabase._db_info_cache)
    database.get_database_info_for_server("plex")  # spawns the gated refresh
    MusicDatabase.invalidate_database_info()
    gate.set()
    _wait_for_refresh(MusicDatabase._db_info_refreshing)
    # same rule as the stats cache: the pre-scan refresh was dropped, so
    # the next ask recomputes instead of serving the refreshed value
    assert database.get_database_info_for_server("plex")["total_artists"] == 3
    assert len(calls) == 3
