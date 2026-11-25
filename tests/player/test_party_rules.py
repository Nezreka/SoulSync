"""who may steer a watch party.

the room is for the people in it: before, any profile that knew a party's
id (listed to everyone who may see the title) could pause it, start it,
call a pee break or chat without joining; in "everyone" mode swap the
title to anything; and "start" brought an ended party back.
"""

from __future__ import annotations

import pytest

from core.player import parties

HOST, MEMBER, STRANGER = 1, 2, 3


@pytest.fixture
def db(tmp_path):
    from database.player_database import PlayerDatabase
    return PlayerDatabase(str(tmp_path / "player.db"))


def _party(db, mode="everyone"):
    p = parties.create_party(db, HOST, {
        "item_type": "movie", "item_id": "m1", "title": "Heat", "control_mode": mode,
    })
    parties.join_party(db, p["id"], MEMBER)
    return p["id"]


def test_a_member_may_steer_in_everyone_mode(db):
    pid = _party(db)
    parties.update_state(db, pid, MEMBER, {"action": "pause", "position": 10})
    parties.update_state(db, pid, MEMBER, {"action": "seek", "position": 30})


def test_a_stranger_may_not_steer_or_chat(db):
    pid = _party(db)
    for action in ("pause", "pee_break", "seek", "play", "start"):
        with pytest.raises(parties.PartyError, match="Join"):
            parties.update_state(db, pid, STRANGER, {"action": action, "position": 5})
    with pytest.raises(parties.PartyError, match="Join"):
        parties.post_message(db, pid, STRANGER, {"body": "hi"})
    # a member and the host can chat
    parties.post_message(db, pid, MEMBER, {"body": "hi"})
    parties.post_message(db, pid, HOST, {"body": "hey"})


def test_an_admin_may_steer_any_room(db):
    pid = _party(db, mode="host")
    parties.update_state(db, pid, STRANGER, {"action": "pause", "position": 5}, is_admin=True)


def test_only_the_host_changes_the_title_even_in_everyone_mode(db):
    pid = _party(db)
    with pytest.raises(parties.PartyError, match="host"):
        parties.update_state(db, pid, MEMBER, {"action": "seek", "position": 1,
                                               "item_type": "movie", "item_id": "m2"})
    out = parties.update_state(db, pid, HOST, {"action": "seek", "position": 1,
                                               "item_type": "movie", "item_id": "m2"})
    assert out["item_id"] == "m2"


def test_an_ended_party_stays_ended(db):
    pid = _party(db)
    parties.update_state(db, pid, HOST, {"action": "end"})
    for action in ("start", "lobby", "play"):
        with pytest.raises(parties.PartyError):
            parties.update_state(db, pid, HOST, {"action": action})


def test_a_bad_position_is_refused_not_a_crash(db):
    pid = _party(db)
    for bad in ("ten", "nan", "inf", [1]):
        with pytest.raises(parties.PartyError, match="number"):
            parties.update_state(db, pid, HOST, {"action": "seek", "position": bad})


def test_lobby_art_is_our_route_or_https_only():
    assert parties._lobby_art("/api/library/v2/artwork/album/3?size=thumb").startswith("/api/")
    assert parties._lobby_art("https://img.example/a.jpg") == "https://img.example/a.jpg"
    for bad in ("http://192.168.1.2/x.jpg", "data:image/png;base64,AAAA", "javascript:x", "", None):
        assert parties._lobby_art(bad) is None


def test_old_cooldowns_and_reactions_are_forgotten(monkeypatch):
    monkeypatch.setattr(parties, "_pee_cooldowns", {(f"p{i}", i): 0.0 for i in range(600)})
    monkeypatch.setattr(parties, "_reaction_timestamps", {(f"p{i}", i): [0.0] for i in range(600)})
    parties._forget_old(10_000_000.0)
    assert parties._pee_cooldowns == {}
    assert parties._reaction_timestamps == {}


# ── a party's id is not a way round the rating cap ──────────────────────────

def test_a_party_over_the_rating_cap_cannot_be_opened_or_joined_by_its_id(db):
    """the list of parties left out titles a profile may not see; opening or
    joining one by its id did not"""
    p = parties.create_party(db, HOST, {"item_type": "movie", "item_id": "r-rated", "title": "Heat"})
    kid_sees = lambda item_type, item_id: item_id != "r-rated"   # noqa: E731
    assert parties.get_party(db, p["id"], STRANGER, may_see=kid_sees) is None
    with pytest.raises(parties.PartyError):
        parties.join_party(db, p["id"], STRANGER, may_see=kid_sees)
    # the host's own party, and a profile the cap doesn't hide it from
    assert parties.get_party(db, p["id"], HOST, may_see=kid_sees)["id"] == p["id"]
    assert parties.get_party(db, p["id"], MEMBER, may_see=lambda *a: True)["id"] == p["id"]
    parties.join_party(db, p["id"], MEMBER, may_see=lambda *a: True)
