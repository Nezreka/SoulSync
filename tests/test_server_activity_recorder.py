"""server activity play log: the recorder turns live sessions into plays.

start, pause, buffering, stop, a session that vanishes, missed polls, a server
that is down, a restart in the middle of a play, binge runs, and the store
underneath (retention, clear, the writer).
"""

from __future__ import annotations

import json

from core.server_activity import recorder as rec
from core.server_activity import store
from core.server_activity.recorder import Recorder, Writer
from tests.sa_helpers import sa_env  # noqa: F401 - fixture


def sess(state="playing", key="27", rk="5101", kind="movie", user="maya", offset=0, bw=8000,
         method="direct_play", parent="", grandparent="", title="Dune", remote=False, server="plex", **extra):
    s = {"server": server, "server_name": "Basement", "session_key": key, "id": f"{server}:{key}", "kind": kind,
         "state": state, "title": title, "user": user, "duration_ms": 1_000_000, "offset_ms": offset,
         "bandwidth_kbps": bw, "keys": {"rating_key": rk, "parent_key": parent, "grandparent_key": grandparent},
         "playback": {"method": method}, "connection": {"remote": remote, "ip": "10.0.0.2"},
         "player": {"product": "Plex Web"}}
    s.update(extra)
    return s


class Sink:
    def __init__(self):
        self.ops = []

    def __call__(self, ops):
        self.ops += ops

    def plays(self):
        out = {}
        for op in self.ops:
            if op[0] == "play":
                out[op[1]["play_key"]] = op[1]
            elif op[0] == "drop":
                out.pop(op[1], None)
        return out


def run(r, timeline):
    """[(t, [sessions] | None)]; None is a failed poll."""
    for t, ss in timeline:
        r.observe("plex", ss is not None, ss or [], now=t)


def test_a_play_records_playing_paused_buffering_and_closes_at_its_last_sighting():
    sink = Sink()
    r = Recorder(sink)
    states = {0: "playing", 10: "playing", 20: "playing", 30: "paused", 40: "paused", 50: "paused",
              60: "buffering", 70: "playing", 80: "playing", 90: "playing", 100: "playing"}
    run(r, [(t, [sess(st, offset=t * 1000)]) for t, st in states.items()] + [(110, [])])
    (p,) = sink.plays().values()
    assert p["started_at"] == 0 and p["stopped_at"] == 100
    assert p["played_ms"] == 60_000 and p["paused_ms"] == 30_000 and p["buffering_ms"] == 10_000
    assert p["pause_count"] == 1 and p["buffer_count"] == 1
    assert [s[0] for s in p["segments"]] == ["playing", "paused", "buffering", "playing"]
    assert p["segments"][0][1:] == [0, 30] and p["segments"][-1][1:] == [70, 100]
    assert p["percent"] == 10 and p["max_position_ms"] == 100_000
    assert r.open == {}


def test_a_failed_poll_closes_nothing_and_a_long_outage_closes_at_the_last_sighting():
    sink = Sink()
    r = Recorder(sink)
    run(r, [(0, [sess()]), (10, [sess()]), (20, [sess()]), (30, None), (40, None)])
    assert r.open and all(p["stopped_at"] is None for p in sink.plays().values())
    run(r, [(20 + rec.STALE + 1, None)])
    (p,) = sink.plays().values()
    assert p["stopped_at"] == 20 and p["played_ms"] == 20_000


def test_time_between_far_apart_sightings_counts_toward_nothing():
    sink = Sink()
    r = Recorder(sink)
    run(r, [(0, [sess()]), (10, [sess()]), (10 + rec.MAX_GAP + 50, [sess()]), (10 + rec.MAX_GAP + 60, [sess()]),
            (500, [])])
    (p,) = sink.plays().values()
    assert p["played_ms"] == 20_000
    assert json.loads(json.dumps(p["detail"]))["gaps"] == 1


def test_a_skip_is_not_a_play():
    sink = Sink()
    r = Recorder(sink)
    run(r, [(0, [sess()]), (3, [sess()]), (6, [])])
    assert sink.plays() == {}
    assert any(op[0] == "drop" for op in sink.ops)


def test_the_next_episode_on_the_same_player_starts_a_new_play_in_the_same_run():
    sink = Sink()
    r = Recorder(sink)
    e4 = dict(kind="episode", grandparent="andor", title="E4")
    run(r, [(0, [sess(rk="e4", **e4)]), (60, [sess(rk="e4", **e4)]),
            (70, [sess(rk="e5", **dict(e4, title="E5"))]), (130, [sess(rk="e5", **dict(e4, title="E5"))]),
            (140, [])])
    plays = sorted(sink.plays().values(), key=lambda p: p["started_at"])
    assert [p["title"] for p in plays] == ["E4", "E5"]
    assert plays[0]["stopped_at"] == 70 and plays[1]["started_at"] == 70
    assert plays[0]["group_key"] == plays[1]["group_key"] == plays[0]["play_key"]


def test_binge_runs_break_after_a_long_pause_and_never_join_two_people():
    sink = Sink()
    r = Recorder(sink)
    ep = dict(kind="episode", grandparent="andor")
    run(r, [(0, [sess(rk="1", **ep)]), (60, [sess(rk="1", **ep)]), (70, [])])
    later = 60 + rec.BINGE_GAP + 100
    run(r, [(later, [sess(rk="2", **ep)]), (later + 60, [sess(rk="2", **ep)]), (later + 70, [])])
    run(r, [(later + 100, [sess(key="99", rk="3", user="dan", **ep)]),
            (later + 160, [sess(key="99", rk="3", user="dan", **ep)]), (later + 170, [])])
    groups = {p["group_key"] for p in sink.plays().values()}
    assert len(groups) == 3


def test_album_tracks_on_new_session_keys_still_group():
    sink = Sink()
    r = Recorder(sink)
    t = dict(kind="track", parent="rumours")
    run(r, [(0, [sess(key="1", rk="a", **t)]), (60, [sess(key="1", rk="a", **t)]),
            (70, [sess(key="2", rk="b", **t)]), (130, [sess(key="2", rk="b", **t)]), (140, [])])
    plays = list(sink.plays().values())
    assert len(plays) == 2 and plays[0]["group_key"] == plays[1]["group_key"]


def test_the_worst_method_sticks_and_the_change_is_kept():
    sink = Sink()
    r = Recorder(sink)
    run(r, [(0, [sess(method="direct_play")]), (30, [sess(method="transcode")]), (60, [sess(method="transcode")]),
            (70, [])])
    (p,) = sink.plays().values()
    assert p["method"] == "transcode"
    assert p["detail"]["method_changes"] == [{"at": 30, "from": "direct_play", "to": "transcode"}]


def test_remote_bandwidth_adds_up_to_data_sent():
    sink = Sink()
    r = Recorder(sink)
    run(r, [(0, [sess(bw=8000, remote=True)]), (50, [sess(bw=8000, remote=True)]),
            (100, [sess(bw=8000, remote=True)]), (110, [])])
    (p,) = sink.plays().values()
    assert p["remote"] == 1 and p["sent_kb"] == 100_000.0        # 8000 kbps for 100 s / 8


def test_a_restart_carries_on_a_play_that_is_still_going(sa_env):
    w = Writer(store.apply_ops, threaded=False)
    r = Recorder(w.put)
    run(r, [(0, [sess()]), (30, [sess()]), (60, [sess()])])
    w.flush()
    # a new process: the play is read back and carries on
    w2 = Writer(store.apply_ops, threaded=False)
    r2 = Recorder(w2.put)
    r2.load(store.load_open_plays(), store.recent_closed(0))
    run(r2, [(80, [sess()]), (100, [sess()]), (120, [])])
    w2.flush()
    rows = store.query("SELECT * FROM server_plays")
    assert len(rows) == 1
    assert rows[0]["started_at"] == 0 and rows[0]["stopped_at"] == 100 and rows[0]["played_ms"] == 100_000


def test_a_restart_closes_a_play_that_ended_while_soulsync_was_down(sa_env):
    w = Writer(store.apply_ops, threaded=False)
    r = Recorder(w.put)
    run(r, [(0, [sess()]), (30, [sess()]), (60, [sess()])])
    w.flush()
    r2 = Recorder(w.put)
    r2.load(store.load_open_plays(), store.recent_closed(0))
    run(r2, [(500, [])])
    w.flush()
    (row,) = store.query("SELECT * FROM server_plays")
    assert row["stopped_at"] == 60 and row["played_ms"] == 60_000


def test_a_binge_run_survives_a_restart(sa_env):
    w = Writer(store.apply_ops, threaded=False)
    r = Recorder(w.put)
    ep = dict(kind="episode", grandparent="andor")
    run(r, [(0, [sess(rk="1", **ep)]), (60, [sess(rk="1", **ep)]), (70, [])])
    w.flush()
    r2 = Recorder(w.put)
    r2.load(store.load_open_plays(), store.recent_closed(0))
    run(r2, [(300, [sess(rk="2", **ep)]), (360, [sess(rk="2", **ep)]), (370, [])])
    w.flush()
    rows = store.query("SELECT group_key FROM server_plays")
    assert len(rows) == 2 and rows[0]["group_key"] == rows[1]["group_key"]


# ── store and writer ────────────────────────────────────────────────────────

def test_the_writer_keeps_the_newest_op_and_the_peak_minute(sa_env):
    applied = []
    w = Writer(applied.extend, threaded=False)
    w.put([("play", {"play_key": "a", "v": 1}), ("minute", 5, 1, 0, 100, 0)])
    w.put([("play", {"play_key": "a", "v": 2}), ("minute", 5, 3, 1, 50, 20)])
    w.flush()
    assert ("play", {"play_key": "a", "v": 2}) in applied
    assert ("minute", 5, 3, 1, 100, 20) in applied


def test_a_failed_write_is_retried_behind_newer_ops():
    calls = []

    def boom(ops):
        calls.append(ops)
        if len(calls) == 1:
            raise RuntimeError("db locked")
    w = Writer(boom, threaded=False)
    w.put([("play", {"play_key": "a", "v": 1})])
    w.flush()
    w.put([("play", {"play_key": "a", "v": 2})])
    w.flush()
    assert calls[-1] == [("play", {"play_key": "a", "v": 2})]


def _row(key, started, stopped=None, **kw):
    base = {c: None for c in store.PLAY_COLUMNS}
    base.update(play_key=key, group_key=key, source="live", server="plex", kind="movie", title=key,
                user="maya", remote=0, started_at=started, stopped_at=stopped, last_seen=stopped or started,
                played_ms=60_000, paused_ms=0, buffering_ms=0, pause_count=0, buffer_count=0, sent_kb=0)
    base.update(kw)
    return base


def test_retention_drops_old_finished_plays_only(sa_env):
    now = 1_000_000_000.0
    store.apply_ops([("play", _row("old", now - 40 * 86400, now - 40 * 86400 + 60)),
                     ("play", _row("new", now - 86400, now - 86400 + 60)),
                     ("play", _row("open-old", now - 40 * 86400)),
                     ("minute", int((now - 40 * 86400) // 60), 2, 0, 100, 0),
                     ("minute", int((now - 60) // 60), 1, 0, 100, 0)])
    assert store.prune(0, now) == 0                      # forever keeps everything
    assert store.prune(30, now) == 1
    keys = {r["play_key"] for r in store.query("SELECT play_key FROM server_plays")}
    assert keys == {"new", "open-old"}
    assert len(store.query("SELECT * FROM server_activity_minutes")) == 1


def test_clear_empties_the_log_but_lets_running_plays_finish(sa_env):
    store.apply_ops([("play", _row("done", 100, 200)), ("play", _row("running", 300)),
                     ("minute", 5, 1, 0, 100, 0)])
    assert store.clear_play_log() == {"ok": True, "removed": 1}
    assert [r["play_key"] for r in store.query("SELECT play_key FROM server_plays")] == ["running"]
    assert store.query("SELECT * FROM server_activity_minutes") == []


def test_the_main_database_creates_the_tables():
    """the schema hook sits in the music database's init, recorded in the ledger."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "database" / "music_database.py").read_text(encoding="utf-8")
    assert "from core.server_activity.store import ensure_schema" in src
    assert "'server_plays_v1':" in src
