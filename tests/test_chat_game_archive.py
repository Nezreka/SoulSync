"""Arcade P7 — game carriers survive an slskd restart.

Protocol carriers are normally ephemeral, and rightly so: replaying a stale
jukebox vote would resurrect a dead queue. A GAME is the exception — durable
state that happens to travel as chat messages. slskd forgets the room when it
restarts and the client keeps only the last 300 protocol events, so without a
durable copy a match played across days simply vanishes once nobody in the
room still holds it.

Asking the room (gm.sync) is always the first move; this is the backstop for
when the room has gone cold and there is nobody left to ask.

Hermetic: a tmp MusicDatabase, no network, no live slskd.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def mdb(tmp_path):
    from database.music_database import MusicDatabase
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _ev(p, user="boulder", ts="2026-07-26 10:00:00"):
    return {"username": user, "timestamp": ts, "p": p}


def _move(n, gid="abcd1234", user="boulder", ts=None):
    return _ev({"k": "gm.move", "g": gid, "n": n, "m": "e2e4", "f": "fen-%d" % n},
               user=user, ts=ts or ("2026-07-26 10:%02d:00" % n))


class TestGameCarrierArchive:
    def test_round_trip_oldest_first(self, mdb):
        assert mdb.add_chat_game_carriers("SoulSync", [_move(1), _move(2)]) == 2
        rows = mdb.get_chat_game_carriers("SoulSync")
        assert [r["p"]["n"] for r in rows] == [1, 2]        # the fold needs stream order
        assert rows[0]["username"] == "boulder"
        assert rows[0]["p"]["k"] == "gm.move"

    def test_only_game_carriers_are_kept(self, mdb):
        # Everything else on the bus is live-only coordination. Replaying a
        # jukebox vote or a typing blip would resurrect state meant to be dead.
        noise = [
            _ev({"k": "jbx.vote", "o": "abc"}),
            _ev({"k": "jbx.now", "id": "xyz", "at": 1}),
            _ev({"k": "typ"}),
            _ev({"k": "hello", "av": 7}),
            _ev({"k": "pin.add", "k2": "x"}),
            _ev({"k": "poll.start", "q": "?"}),
        ]
        assert mdb.add_chat_game_carriers("SoulSync", noise) == 0
        assert mdb.get_chat_game_carriers("SoulSync") == []

    def test_every_game_kind_is_kept(self, mdb):
        kinds = ["gm.new", "gm.join", "gm.move", "gm.res", "gm.draw",
                 "gm.claim", "gm.vote", "gm.sync", "gm.state", "gm.cancel"]
        evs = [_ev({"k": k, "g": "abcd1234"}, ts="2026-07-26 10:%02d:00" % i)
               for i, k in enumerate(kinds)]
        assert mdb.add_chat_game_carriers("SoulSync", evs) == len(kinds)

    def test_replays_are_idempotent(self, mdb):
        batch = [_move(1), _move(2)]
        assert mdb.add_chat_game_carriers("SoulSync", batch) == 2
        assert mdb.add_chat_game_carriers("SoulSync", batch) == 0
        assert len(mdb.get_chat_game_carriers("SoulSync")) == 2

    def test_rooms_are_separate(self, mdb):
        mdb.add_chat_game_carriers("SoulSync", [_move(1)])
        mdb.add_chat_game_carriers("nicotine", [_move(2)])
        assert len(mdb.get_chat_game_carriers("SoulSync")) == 1
        assert len(mdb.get_chat_game_carriers("nicotine")) == 1

    def test_retention_keeps_the_most_recent(self, mdb):
        keep = mdb._CHAT_GAME_KEEP
        evs = [_move(i, ts="2026-07-26 %02d:%02d:00" % (i // 60, i % 60))
               for i in range(keep + 25)]
        mdb.add_chat_game_carriers("SoulSync", evs)
        rows = mdb.get_chat_game_carriers("SoulSync", limit=1000)
        assert len(rows) <= keep
        # The newest survived; the oldest were pruned.
        assert rows[-1]["p"]["n"] == keep + 24

    def test_junk_is_dropped_not_stored(self, mdb):
        bad = [
            None, 42, "nope", {},
            {"username": "b", "timestamp": "t"},                       # no payload
            _ev({"k": "gm.move"}),                                      # no game id
            {"username": "", "timestamp": "t", "p": {"k": "gm.move", "g": "abcd"}},
            {"username": "b", "timestamp": "", "p": {"k": "gm.move", "g": "abcd"}},
        ]
        assert mdb.add_chat_game_carriers("SoulSync", bad) == 0
        assert mdb.get_chat_game_carriers("SoulSync") == []

    def test_an_oversized_payload_is_refused(self, mdb):
        huge = _ev({"k": "gm.move", "g": "abcd1234", "f": "x" * 4000})
        assert mdb.add_chat_game_carriers("SoulSync", [huge]) == 0

    def test_a_missing_table_never_raises(self, mdb):
        # The archive is a convenience; chat must work without it.
        assert mdb.get_chat_game_carriers("no-such-room") == []


class TestWiring:
    """The replay has to reach the page, and only the games."""

    _API = (_ROOT / "api" / "chat.py").read_text(encoding="utf-8")

    def test_carriers_are_archived_on_every_hydrate(self):
        # Not on the 60s message throttle: they are rare, the natural-key
        # UNIQUE makes repeats free, and losing one loses a move.
        assert "db.add_chat_game_carriers(room, protocol_events)" in self._API

    def test_archived_carriers_are_replayed_before_the_live_feed(self):
        assert "revived + protocol_events" in self._API

    def test_the_replay_is_deduped(self):
        # The client dedupes too, but sending the same carrier twice wastes
        # its 300-event window.
        assert 'seen = {(e.get("username"), e.get("timestamp"),' in self._API

    def test_the_replay_never_breaks_chat(self):
        # A failure here must not take the room down with it.
        assert "chat: game carrier replay unavailable" in self._API
