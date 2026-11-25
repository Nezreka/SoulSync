"""the guide runs on the server's clock, and the server says which.

each device counted a channel's day from its own midnight and seeded it by
its own date, so a phone in another timezone had a different guide. the
clock goes out with the day's pool.
"""

import datetime as dt

from core.player import guide_day


def test_the_clock_names_the_day_and_the_offset(monkeypatch):
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    now = dt.datetime(2026, 11, 24, 19, 0, 0)
    clock = guide_day.clock(now)
    assert clock["day"] == "2026-11-24" == guide_day.today(now)
    assert clock["tz"] == "America/Los_Angeles"
    assert isinstance(clock["offset_minutes"], int)


def test_a_zone_the_machine_does_not_know_is_not_handed_out(monkeypatch):
    monkeypatch.setenv("TZ", "Not/AZone")
    monkeypatch.setattr("os.path.realpath", lambda p: "/nowhere")
    name = guide_day.zone_name()
    assert name is None or "/" in name or name.upper() in ("UTC", "GMT")
    assert name != "Not/AZone"


def test_an_abbreviation_is_not_a_zone_name(monkeypatch):
    # "PST" says nothing about when the clocks change
    monkeypatch.setenv("TZ", "PST8PDT")
    assert guide_day.zone_name() != "PST8PDT"
