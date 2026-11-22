"""Tests for Watch Parties (Watch Together + Theater Waiting Rooms)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest

from core.player import parties


class _StubDB:
    def __init__(self, path):
        self.database_path = str(path)

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.database_path)
        try:
            yield conn
        finally:
            conn.close()


def test_lobby_lifecycle_sync_and_chat(tmp_path):
    db = _StubDB(tmp_path / "player.db")
    t0 = 1_700_000_000.0
    lobby_track = {
        "track_id": "42",
        "title": "Midnight City",
        "artist": "M83",
        "album_id": 7,
        "duration_ms": 240_000,
    }

    p = parties.create_party(
        db,
        host_profile_id=1,
        body={
            "title": "Friday Movie Night",
            "item_type": "movie",
            "item_id": "101",
            "meta": {"title": "Dune: Part Two", "subtitle": "2024"},
            "control_mode": "host",
        },
        default_lobby_track=lobby_track,
        now=t0,
    )
    assert p["status"] == "lobby"
    assert p["is_host"] is True
    assert p["lobby_track"]["title"] == "Midnight City"
    assert p["ready_count"] == 1

    # Second profile joins and marks ready
    p2 = parties.join_party(db, p["id"], profile_id=2, ready=True, now=t0 + 10)
    assert p2["member_count"] == 2
    assert p2["ready_count"] == 2
    assert p2["can_control"] is False

    # Guest cannot control in host-only mode
    with pytest.raises(parties.PartyError):
        parties.update_state(db, p["id"], profile_id=2, body={"action": "start"}, now=t0 + 15)

    # Host starts playback
    started = parties.update_state(db, p["id"], profile_id=1, body={"action": "start"}, now=t0 + 20)
    assert started["status"] == "playing"
    assert started["live_position"] == 0.0

    # 35 seconds later, live_position advances automatically
    fetched = parties.get_party(db, p["id"], viewer_id=2, now=t0 + 55)
    assert fetched["live_position"] == pytest.approx(35.0, abs=0.1)

    # Host pauses at 35s
    paused = parties.update_state(db, p["id"], profile_id=1, body={"action": "pause"}, now=t0 + 55)
    assert paused["status"] == "paused"
    assert paused["live_position"] == pytest.approx(35.0, abs=0.1)

    # 100s later while paused, live_position stays frozen at 35s
    still_paused = parties.get_party(db, p["id"], viewer_id=2, now=t0 + 155)
    assert still_paused["live_position"] == pytest.approx(35.0, abs=0.1)

    # Chat & reaction
    msg = parties.post_message(db, p["id"], profile_id=2, body={"kind": "reaction", "body": "🍿"}, now=t0 + 160)
    assert msg["kind"] == "reaction"
    assert msg["body"] == "🍿"

    # End party
    assert parties.delete_party(db, p["id"], profile_id=1) is True
    assert parties.list_parties(db, viewer_id=1, now=t0 + 170) == []


def test_scheduled_party_auto_opens_lobby_and_everyone_control(tmp_path):
    db = _StubDB(tmp_path / "player.db")
    t0 = 1_700_000_000.0
    p = parties.create_party(
        db,
        host_profile_id=1,
        body={
            "title": "Severance Finale Watch Party",
            "item_type": "episode",
            "item_id": "555",
            "show_id": 12,
            "scheduled_for": t0 + 3600,  # 1 hour ahead
            "control_mode": "everyone",
        },
        now=t0,
    )
    assert p["status"] == "scheduled"

    # Within 15 minutes of start, lobby opens automatically
    pre_show = parties.get_party(db, p["id"], viewer_id=2, now=t0 + 3000)
    assert pre_show["status"] == "lobby"
    assert pre_show["can_control"] is True

    # Guest can seek/play in 'everyone' mode
    playing = parties.update_state(db, p["id"], profile_id=2, body={"action": "start"}, now=t0 + 3600)
    assert playing["status"] == "playing"
