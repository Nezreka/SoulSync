"""iss29-C01: history timestamps must leave the server unambiguous.

The columns behind this feed default to SQLite's ``CURRENT_TIMESTAMP``, which
is UTC rendered as ``"YYYY-MM-DD HH:MM:SS"`` — space-separated, no zone. V8
parses that shape as LOCAL time, so in Europe/Zurich (this project's timezone)
every event arrived two hours in the past.

That is not just a display defect. ``classifyGrabOutcome`` keeps only events
newer than the moment the grab started; east of UTC the quarantine event never
looked fresh, so the poll stayed ``pending`` for its whole window and the UI
then reported "Grabbed ✓" for a file that never arrived — the exact regression
§21.2 / iss27-10 records as fixed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.library2.history_feed import _iso_utc


def test_a_naive_sqlite_timestamp_is_stamped_as_utc():
    assert _iso_utc("2026-07-31 13:04:05") == "2026-07-31T13:04:05Z"


def test_fractional_seconds_survive():
    assert _iso_utc("2026-07-31 13:04:05.123456") == "2026-07-31T13:04:05.123456Z"


def test_an_already_t_separated_naive_value_is_still_marked_utc():
    assert _iso_utc("2026-07-31T13:04:05") == "2026-07-31T13:04:05Z"


def test_a_value_that_already_carries_a_zone_is_left_alone():
    for value in (
        "2026-07-31T13:04:05Z",
        "2026-07-31T13:04:05+02:00",
        "2026-07-31T13:04:05.000+00:00",
    ):
        assert _iso_utc(value) == value


def test_non_timestamps_pass_through_untouched():
    assert _iso_utc(None) is None
    assert _iso_utc("") == ""
    assert _iso_utc("not a date") == "not a date"
    assert _iso_utc(1234) == 1234


def test_the_normalised_value_round_trips_to_the_instant_it_meant():
    """The whole point: parsed back, it is the UTC instant SQLite wrote."""
    normalised = _iso_utc("2026-07-31 13:04:05")
    parsed = datetime.fromisoformat(normalised.replace("Z", "+00:00"))
    assert parsed == datetime(2026, 7, 31, 13, 4, 5, tzinfo=timezone.utc)
