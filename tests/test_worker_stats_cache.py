"""The status broadcast keeps its 2s beat without re-reading every worker's
counts from the database on every beat (Nov 13 2026: the video side ran four
COUNT(*) per kind per worker per tick, 39% of a core while a tab was open)."""

from __future__ import annotations

from core.enrichment.status_cache import ACTIVE_MAX, ACTIVE_MIN, IDLE_TTL, WorkerStatsCache


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Worker:
    def __init__(self, clock, *, idle=False, running=True, cost=0.0):
        self.running, self.paused = running, False
        self.current_item, self.note = None, None
        self.reads, self.idle, self.clock, self.cost = 0, idle, clock, cost

    def get_stats(self):
        self.reads += 1
        self.clock.now += self.cost
        return {"running": self.running and not self.paused, "paused": self.paused,
                "idle": self.idle, "current_item": self.current_item, "note": self.note,
                "stats": {"pending": 0 if self.idle else 5}}


def _tick(cache, worker, clock, seconds, beat=2.0):
    out = None
    for _ in range(int(seconds / beat)):
        clock.now += beat
        out = cache.get("svc", worker)
    return out


def test_an_idle_worker_is_read_once_per_idle_window():
    clock = Clock()
    cache, worker = WorkerStatsCache(clock), Worker(clock, idle=True)
    cache.get("svc", worker)
    _tick(cache, worker, clock, IDLE_TTL - 2)
    assert worker.reads == 1
    _tick(cache, worker, clock, 4)
    assert worker.reads == 2


def test_a_stopped_worker_is_read_once_per_idle_window():
    clock = Clock()
    cache, worker = WorkerStatsCache(clock), Worker(clock, running=False)
    cache.get("svc", worker)
    _tick(cache, worker, clock, IDLE_TTL - 2)
    assert worker.reads == 1


def test_a_working_worker_is_read_every_few_seconds_and_its_item_is_live():
    clock = Clock()
    cache, worker = WorkerStatsCache(clock), Worker(clock)
    worker.current_item = {"name": "first"}
    cache.get("svc", worker)
    worker.current_item, worker.note = {"name": "second"}, "rate limited"
    clock.now += 2
    status = cache.get("svc", worker)
    assert worker.reads == 1
    assert status["current_item"] == {"name": "second"} and status["note"] == "rate limited"
    _tick(cache, worker, clock, 60)
    # on a 2s beat a 5s limit lands every third beat: about ten reads a minute, not thirty
    assert 9 <= worker.reads - 1 <= 60 / ACTIVE_MIN + 1


def test_a_slow_read_is_spaced_out_but_never_past_the_cap():
    clock = Clock()
    cache, worker = WorkerStatsCache(clock), Worker(clock, cost=0.5)   # 0.5s x 25 = 12.5s
    cache.get("svc", worker)
    _tick(cache, worker, clock, 12)
    assert worker.reads == 1
    _tick(cache, worker, clock, 4)
    assert worker.reads == 2
    slow = Worker(clock, cost=20.0)
    cache.get("slow", slow)
    for _ in range(int(ACTIVE_MAX / 2) + 1):
        clock.now += 2
        cache.get("slow", slow)
    assert slow.reads == 2


def test_a_pause_or_resume_is_seen_on_the_next_beat():
    clock = Clock()
    cache, worker = WorkerStatsCache(clock), Worker(clock)
    assert cache.get("svc", worker)["paused"] is False
    worker.paused = True
    clock.now += 2
    assert cache.get("svc", worker)["paused"] is True
    worker.paused = False
    clock.now += 2
    assert cache.get("svc", worker)["running"] is True
    assert worker.reads == 3


def test_an_idle_worker_that_picks_up_an_item_is_read_at_once():
    clock = Clock()
    cache, worker = WorkerStatsCache(clock), Worker(clock, idle=True)
    cache.get("svc", worker)
    worker.idle, worker.current_item = False, {"name": "new movie"}
    clock.now += 2
    status = cache.get("svc", worker)
    assert worker.reads == 2 and status["idle"] is False


def test_the_cached_status_is_not_shared_between_beats():
    clock = Clock()
    cache, worker = WorkerStatsCache(clock), Worker(clock, idle=True)
    first = cache.get("svc", worker)
    first["yield_reason"] = "downloads"
    clock.now += 2
    assert "yield_reason" not in cache.get("svc", worker)


def test_workers_are_kept_apart():
    clock = Clock()
    cache = WorkerStatsCache(clock)
    a, b = Worker(clock, idle=True), Worker(clock, idle=True)
    cache.get("a", a)
    cache.get("b", b)
    assert (a.reads, b.reads) == (1, 1)
