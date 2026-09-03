"""Concerts on the artist page: upcoming dates and what they actually played.

Two providers because they answer two different questions and neither answers
the other's. Bandsintown knows they are playing Berlin on the 14th; Setlist.fm
knows what they played in Berlin last month, song by song - which is the half
that connects to a music library, since those song names can become a playlist.

The rule that shapes most of this: both are OPTIONAL and INDEPENDENT. Most
installs will have one or neither, so one being unset, rate limited or down must
never blank the other. A page section that shows nothing because the half you did
not configure is missing reads as broken, not as partial.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.concerts_client as cc


@pytest.fixture(autouse=True)
def _clean_cache():
    cc.clear_cache()
    yield
    cc.clear_cache()


@pytest.fixture()
def keys(monkeypatch):
    """Configure either/both providers."""
    state = {"concerts.setlistfm_api_key": "", "concerts.bandsintown_app_id": ""}
    monkeypatch.setattr(cc, "_cfg", lambda path, default="": state.get(path, default))
    return state


def _resp(status=200, payload=None):
    return SimpleNamespace(status_code=status, json=lambda: payload,
                           text="", content=b"")


def _get(monkeypatch, handler):
    calls = []

    def fake_get(url, **kw):
        calls.append({"url": url, **kw})
        return handler(url, **kw)

    monkeypatch.setattr(cc.requests, "get", fake_get)
    return calls


SETLIST_PAYLOAD = {"setlist": [{
    "id": "abc", "eventDate": "14-08-2026",
    "venue": {"name": "Berghain", "city": {"name": "Berlin",
                                           "country": {"name": "Germany"}}},
    "tour": {"name": "Syro Live"},
    "url": "https://setlist.fm/x",
    "sets": {"set": [
        {"song": [{"name": "Xtal"}, {"name": "Walk-on", "tape": True}]},
        {"encore": 1, "song": [{"name": "Ageispolis"}]},
    ]},
}]}


# ── Setlist.fm ───────────────────────────────────────────────────────────────
def test_a_setlist_is_flattened_in_play_order_including_the_encore(keys, monkeypatch):
    """An encore is a separate set in the payload. A setlist without it is not
    the set that was played."""
    keys["concerts.setlistfm_api_key"] = "k"
    _get(monkeypatch, lambda url, **kw: _resp(200, SETLIST_PAYLOAD))

    out = cc.setlistfm_recent("Aphex Twin")
    assert out["configured"] is True
    show = out["setlists"][0]
    assert show["songs"] == ["Xtal", "Ageispolis"]
    assert show["song_count"] == 2
    assert show["venue"] == "Berghain" and show["city"] == "Berlin"
    assert show["tour"] == "Syro Live"


def test_walk_on_tape_is_not_something_the_band_played(keys, monkeypatch):
    keys["concerts.setlistfm_api_key"] = "k"
    _get(monkeypatch, lambda url, **kw: _resp(200, SETLIST_PAYLOAD))
    assert "Walk-on" not in cc.setlistfm_recent("Aphex Twin")["setlists"][0]["songs"]


def test_an_empty_stub_setlist_is_dropped(keys, monkeypatch):
    """Someone created the show page and never filled it in. Rendering it as a
    concert with no songs reads as a bug in SoulSync."""
    keys["concerts.setlistfm_api_key"] = "k"
    _get(monkeypatch, lambda url, **kw: _resp(200, {"setlist": [
        {"id": "empty", "eventDate": "01-01-2026", "sets": {"set": []}}]}))
    assert cc.setlistfm_recent("Nobody")["setlists"] == []


def test_the_mbid_is_preferred_because_names_collide(keys, monkeypatch):
    """Two bands sharing a name return each other's shows on a name search."""
    keys["concerts.setlistfm_api_key"] = "k"
    calls = _get(monkeypatch, lambda url, **kw: _resp(200, SETLIST_PAYLOAD))

    cc.setlistfm_recent("Nirvana", mbid="mb-123")
    assert calls[0]["params"]["artistMbid"] == "mb-123"
    assert "artistName" not in calls[0]["params"]

    cc.clear_cache()
    cc.setlistfm_recent("Nirvana")
    assert calls[1]["params"]["artistName"] == "Nirvana"


def test_404_means_no_shows_not_a_failure(keys, monkeypatch):
    keys["concerts.setlistfm_api_key"] = "k"
    _get(monkeypatch, lambda url, **kw: _resp(404, None))
    out = cc.setlistfm_recent("Obscure Band")
    assert out["setlists"] == []
    assert "error" not in out


@pytest.mark.parametrize("status,phrase", [(403, "API key"), (429, "rate limiting")])
def test_a_rejected_key_and_a_rate_limit_say_which(keys, monkeypatch, status, phrase):
    keys["concerts.setlistfm_api_key"] = "k"
    _get(monkeypatch, lambda url, **kw: _resp(status, None))
    assert phrase in cc.setlistfm_recent("X")["error"]


def test_setlistfm_is_not_called_at_all_without_a_key(keys, monkeypatch):
    calls = _get(monkeypatch, lambda url, **kw: _resp(200, SETLIST_PAYLOAD))
    out = cc.setlistfm_recent("Aphex Twin")
    assert out == {"configured": False, "setlists": []}
    assert calls == []


# ── Bandsintown ──────────────────────────────────────────────────────────────
BIT_PAYLOAD = [{
    "id": "1", "datetime": "2026-09-14T20:00:00", "title": "",
    "venue": {"name": "Berghain", "city": "Berlin", "region": "", "country": "Germany"},
    "url": "https://bandsintown.com/e/1",
    "offers": [{"type": "Tickets", "url": "https://tix.example/1"}],
    "lineup": ["Aphex Twin"],
}]


def test_upcoming_dates_carry_the_venue_and_a_ticket_link(keys, monkeypatch):
    keys["concerts.bandsintown_app_id"] = "app"
    _get(monkeypatch, lambda url, **kw: _resp(200, BIT_PAYLOAD))

    ev = cc.bandsintown_upcoming("Aphex Twin")["events"][0]
    assert ev["venue"] == "Berghain" and ev["city"] == "Berlin"
    assert ev["tickets_url"] == "https://tix.example/1"


def test_the_artist_name_is_escaped_into_the_path(keys, monkeypatch):
    """The name goes in the PATH. A slash in a band name would otherwise change
    the route rather than the artist."""
    keys["concerts.bandsintown_app_id"] = "app"
    calls = _get(monkeypatch, lambda url, **kw: _resp(200, []))
    cc.bandsintown_upcoming("AC/DC")
    assert "AC%2FDC" in calls[0]["url"]
    assert "/AC/DC/" not in calls[0]["url"]


def test_an_unknown_artist_comes_back_as_no_events(keys, monkeypatch):
    """Bandsintown answers with {} or a warning string rather than [] for an
    artist it does not know."""
    keys["concerts.bandsintown_app_id"] = "app"
    _get(monkeypatch, lambda url, **kw: _resp(200, {"warn": "not found"}))
    assert cc.bandsintown_upcoming("Nobody")["events"] == []


# ── caching ──────────────────────────────────────────────────────────────────
def test_a_repeat_lookup_does_not_hit_the_provider_again(keys, monkeypatch):
    """Both providers rate limit, and tour dates are not live data."""
    keys["concerts.setlistfm_api_key"] = "k"
    calls = _get(monkeypatch, lambda url, **kw: _resp(200, SETLIST_PAYLOAD))
    cc.setlistfm_recent("Aphex Twin")
    cc.setlistfm_recent("Aphex Twin")
    assert len(calls) == 1


def test_a_failure_is_not_cached(keys, monkeypatch):
    """Caching an error would keep a fixed key locked out for six hours."""
    keys["concerts.setlistfm_api_key"] = "k"
    calls = _get(monkeypatch, lambda url, **kw: _resp(500, None))
    cc.setlistfm_recent("X")
    cc.setlistfm_recent("X")
    assert len(calls) == 2


def test_clearing_the_cache_lets_a_corrected_key_through(keys, monkeypatch):
    keys["concerts.setlistfm_api_key"] = "k"
    calls = _get(monkeypatch, lambda url, **kw: _resp(200, SETLIST_PAYLOAD))
    cc.setlistfm_recent("Aphex Twin")
    cc.clear_cache()
    cc.setlistfm_recent("Aphex Twin")
    assert len(calls) == 2


# ── the combined call ────────────────────────────────────────────────────────
def test_one_provider_being_unconfigured_never_blanks_the_other(keys, monkeypatch):
    keys["concerts.setlistfm_api_key"] = "k"      # bandsintown left unset
    _get(monkeypatch, lambda url, **kw: _resp(200, SETLIST_PAYLOAD))

    out = cc.artist_concerts("Aphex Twin")
    assert out["setlists"], "the configured provider produced nothing"
    assert out["upcoming"] == []
    assert out["providers"]["bandsintown"]["configured"] is False
    assert out["providers"]["setlistfm"]["configured"] is True


def test_one_provider_failing_never_blanks_the_other(keys, monkeypatch):
    keys["concerts.setlistfm_api_key"] = "k"
    keys["concerts.bandsintown_app_id"] = "app"

    def handler(url, **kw):
        if "bandsintown" in url:
            raise RuntimeError("bandsintown down")
        return _resp(200, SETLIST_PAYLOAD)

    _get(monkeypatch, handler)
    out = cc.artist_concerts("Aphex Twin")
    assert out["setlists"], "a dead provider took the working one with it"
    assert "error" in out["providers"]["bandsintown"]


def test_neither_configured_is_a_clean_empty_answer(keys, monkeypatch):
    calls = _get(monkeypatch, lambda url, **kw: _resp(200, []))
    out = cc.artist_concerts("Aphex Twin")
    assert out["upcoming"] == [] and out["setlists"] == []
    assert calls == [], "called a provider that was never configured"
