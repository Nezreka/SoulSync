"""watched goes to the media server account too, so it's only for titles
this profile may see: a kids profile marking any movie id pushed it."""

from __future__ import annotations

import api.player as player_api


def test_a_title_the_profile_may_not_see_is_not_marked(monkeypatch):
    marked = []
    monkeypatch.setattr(player_api.watched, "mark", lambda t, i: marked.append((t, i)) or True)
    monkeypatch.setattr(player_api, "_may_see_title", lambda t, i: i == "ok")
    assert player_api._mark_if_allowed("movie", "rated-r") is False
    assert player_api._mark_if_allowed("episode", "rated-r") is False
    assert player_api._mark_if_allowed("movie", "ok") is True
    assert marked == [("movie", "ok")]


def test_presence_stays_bounded_with_many_fresh_sessions(monkeypatch):
    from core.player import presence
    monkeypatch.setattr(presence, "_sessions", {})
    monkeypatch.setattr(presence, "MAX_SESSIONS", 5)
    for i in range(12):
        presence.beat(1, {"session": f"s{i}", "item_type": "track", "item_id": str(i), "position": 0}, now=1000.0 + i)
    assert len(presence._sessions) == 5
    # the newest stay
    assert {k[1] for k in presence._sessions} == {f"s{i}" for i in range(7, 12)}
