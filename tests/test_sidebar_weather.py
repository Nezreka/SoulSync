"""Sidebar weather backend (api/sidebar_weather.py).

Pins: WMO code -> scene mapping (every group incl. boundaries), condition
text, geocode parsing, 30-min lazy cache refresh + stale-serving, and the
endpoint contracts (GET shape, PUT location save/clear, PUT display toggle,
PUT units, 400 on unresolvable location, US->F / else->C unit auto-detect).

HTTP is monkeypatched throughout — these tests never touch Open-Meteo.

Note: the design doc said tests/api/test_sidebar_weather.py, but the repo
has no tests/api/ directory; tests live flat in tests/, so this follows the
repo convention.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
from flask import Flask

import api.sidebar_weather as sw

_ROOT = Path(sw.__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

class _FakeConfig:
    """Dot-notation get/set/batch + a config_path, like ConfigManager."""

    def __init__(self, tmp_path):
        self._data = {}
        self.config_path = tmp_path / "config.json"

    def get(self, key, default=None):
        node = self._data
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def set(self, key, value):
        node = self._data
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    @contextmanager
    def batch(self):
        yield


_GEOCODE_US = {
    "results": [
        {"name": "Medford", "latitude": 42.3265, "longitude": -122.8756,
         "country_code": "US", "admin1": "Oregon"}
    ]
}
_GEOCODE_DE = {
    "results": [
        {"name": "Berlin", "latitude": 52.52, "longitude": 13.405,
         "country_code": "DE"}
    ]
}


def _forecast(current_code=1, wind=8.2, temp=68.4):
    return {
        "utc_offset_seconds": -25200,
        "current": {"temperature_2m": temp, "weather_code": current_code,
                    "wind_speed_10m": wind},
        "daily": {
            "time": ["2026-10-06", "2026-10-07", "2026-10-08"],
            "weather_code": [current_code, 2, 61],
            "temperature_2m_max": [70.1, 72.5, 65.0],
            "temperature_2m_min": [52.3, 54.0, 50.2],
            "precipitation_probability_max": [5, 10, 80],
        },
    }


def _fake_http(monkeypatch, geocode=_GEOCODE_US, forecast=None,
               fail_prefixes=()):
    """Monkeypatch sw._http_get_json; return the list of (url, params) calls."""
    forecast = _forecast() if forecast is None else forecast
    calls = []

    def _fake(url, params):
        calls.append((url, params))
        for prefix in fail_prefixes:
            if url.startswith(prefix):
                raise requests.ConnectionError("boom")
        if url.startswith(sw.GEOCODE_URL):
            return geocode
        if url.startswith(sw.FORECAST_URL):
            return forecast
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(sw, "_http_get_json", _fake)
    return calls


def _forecast_calls(calls):
    return [c for c in calls if c[0].startswith(sw.FORECAST_URL)]


@pytest.fixture
def cfg(tmp_path):
    fake = _FakeConfig(tmp_path)
    sw.configure(config_manager=fake)
    sw._cache.update(
        {"snapshot": None, "units": None, "latitude": None, "longitude": None}
    )
    return fake


@pytest.fixture
def client(cfg):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(sw.create_blueprint())
    return app.test_client()


def _put_location(client, monkeypatch, query="97501", **kw):
    calls = _fake_http(monkeypatch, **kw)
    resp = client.put("/api/weather/location", json={"location": query})
    return resp, calls


# ---------------------------------------------------------------------------
# WMO -> scene mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", [51, 53, 55, 56, 57, 61, 63, 65, 66, 67,
                                  80, 81, 82, 95, 96, 99])
def test_scene_rain_group(code):
    assert sw.scene_for(code, 0) == "rain"


@pytest.mark.parametrize("code", [71, 73, 75, 77, 85, 86])
def test_scene_snow_group(code):
    assert sw.scene_for(code, 0) == "snow"


@pytest.mark.parametrize("code", [0, 1, 2, 3, 45, 48])
def test_scene_clear_group(code):
    assert sw.scene_for(code, 5) == "clear"


def test_scene_wind_boundary():
    # production-realistic shapes: scene_for compares the RAW wind float
    # against the threshold; rounding is display-only.
    assert sw.scene_for(1, 14.9) == "clear"
    assert sw.scene_for(1, 15.0) == "wind"
    assert sw.scene_for(1, 40.0) == "wind"


def test_scene_precip_wins_over_wind():
    assert sw.scene_for(61, 40) == "rain"
    assert sw.scene_for(71, 40) == "snow"
    assert sw.scene_for(95, 60) == "rain"


def test_scene_unknown_code_falls_back():
    assert sw.scene_for(999, 0) == "clear"
    assert sw.scene_for(None, 0) == "clear"
    assert sw.scene_for(999, 20) == "wind"


# ---------------------------------------------------------------------------
# condition text
# ---------------------------------------------------------------------------

def test_condition_text_groups():
    assert sw.condition_text(0) == "Clear sky"
    assert sw.condition_text(1) == "Mainly clear"
    assert sw.condition_text(2) == "Partly cloudy"
    assert sw.condition_text(3) == "Overcast"
    assert sw.condition_text(45) == "Fog"
    assert sw.condition_text(53) == "Drizzle"
    assert sw.condition_text(63) == "Rain"
    assert sw.condition_text(73) == "Snow"
    assert sw.condition_text(95) == "Thunderstorm"
    assert sw.condition_text(999) == "Unknown"
    assert sw.condition_text(None) == "Unknown"


# ---------------------------------------------------------------------------
# geocode parsing
# ---------------------------------------------------------------------------

def test_geocode_parses_first_result(cfg, monkeypatch):
    _fake_http(monkeypatch)
    geo = sw.geocode_location("97501")
    assert geo == {"name": "Medford", "latitude": 42.3265,
                   "longitude": -122.8756, "country_code": "US"}


@pytest.mark.parametrize("payload", [{}, {"results": []}, {"results": [{}]},
                                      {"results": [{"name": "x"}]}])
def test_geocode_unresolvable_returns_none(cfg, monkeypatch, payload):
    _fake_http(monkeypatch, geocode=payload)
    assert sw.geocode_location("nowhere") is None


def test_http_timeout_is_10s(monkeypatch):
    """Pin the timeout on the actual requests call, not the constant's value."""
    seen = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {}

    def _fake_get(url, params=None, timeout=None, headers=None):
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(sw.requests, "get", _fake_get)
    sw._http_get_json("https://example.test/forecast", {"a": "b"})
    assert seen["timeout"] == 10


# ---------------------------------------------------------------------------
# cache: 30-min refresh + stale serving
# ---------------------------------------------------------------------------

def test_get_serves_fresh_cache_without_refetch(client, monkeypatch):
    resp, calls = _put_location(client, monkeypatch)
    assert resp.status_code == 200
    assert len(_forecast_calls(calls)) == 1
    # second GET inside the 30-min window must not hit the network again
    resp = client.get("/api/weather")
    assert resp.status_code == 200
    assert len(_forecast_calls(calls)) == 1
    assert resp.get_json()["snapshot"] is not None


def test_get_refreshes_after_30_minutes(client, monkeypatch):
    resp, calls = _put_location(client, monkeypatch)
    assert resp.status_code == 200
    later = datetime.now(timezone.utc) + timedelta(minutes=31)
    monkeypatch.setattr(sw, "_utcnow", lambda: later)
    resp = client.get("/api/weather")
    assert resp.status_code == 200
    assert len(_forecast_calls(calls)) == 2


def test_get_serves_stale_snapshot_when_refresh_fails(client, monkeypatch):
    resp, calls = _put_location(client, monkeypatch)
    assert resp.status_code == 200
    fresh_fetched_at = resp.get_json()["snapshot"]["fetched_at"]
    # age the cache past the TTL and break the forecast endpoint
    later = datetime.now(timezone.utc) + timedelta(minutes=31)
    monkeypatch.setattr(sw, "_utcnow", lambda: later)
    monkeypatch.setattr(
        sw, "_http_get_json",
        lambda url, params: (_ for _ in ()).throw(
            requests.ConnectionError("down")) if url.startswith(sw.FORECAST_URL)
        else _GEOCODE_US,
    )
    resp = client.get("/api/weather")
    assert resp.status_code == 200
    body = resp.get_json()
    # stale snapshot served, never a 500
    assert body["snapshot"]["fetched_at"] == fresh_fetched_at
    assert body["scene"] == "clear"


def test_get_snapshot_null_when_no_cache_and_fetch_fails(client, monkeypatch):
    resp, calls = _put_location(client, monkeypatch,
                                fail_prefixes=(sw.FORECAST_URL,))
    # location saved, but no snapshot could be fetched
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["location"]["query"] == "97501"
    assert body["snapshot"] is None
    assert body["scene"] is None
    # a later GET also fails gracefully
    resp = client.get("/api/weather")
    assert resp.status_code == 200
    assert resp.get_json()["snapshot"] is None


def test_stale_snapshot_not_served_after_units_flip(client, monkeypatch):
    # B1: a cached fahrenheit snapshot + a failed refresh after flipping to
    # celsius must NOT serve the stale fahrenheit data labeled as celsius.
    resp, calls = _put_location(client, monkeypatch)
    assert resp.status_code == 200
    assert resp.get_json()["units"] == "fahrenheit"
    # break the forecast endpoint; the fahrenheit snapshot stays cached
    # (PUT /units deliberately does not clear it)
    monkeypatch.setattr(
        sw, "_http_get_json",
        lambda url, params: (_ for _ in ()).throw(
            requests.ConnectionError("down")) if url.startswith(sw.FORECAST_URL)
        else _GEOCODE_US,
    )
    resp = client.put("/api/weather/units", json={"use_celsius": True})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["units"] == "celsius"
    assert body["snapshot"] is None
    assert body["scene"] is None
    # and a plain GET agrees
    body = client.get("/api/weather").get_json()
    assert body["snapshot"] is None


def test_stale_snapshot_not_served_for_changed_location(client, monkeypatch,
                                                        cfg):
    # B1: pins _get_snapshot's stale-fallback contract directly. Simulates
    # the desync window where the config already points at the new location
    # while the cache and sidecar still hold the old city's snapshot: with
    # the refresh fetch failing, the old city's data must never be served
    # under the new location's name.
    resp, _ = _put_location(client, monkeypatch)
    assert resp.status_code == 200
    assert resp.get_json()["snapshot"] is not None
    cfg.set("weather.location", "Berlin")
    cfg.set("weather.location_name", "Berlin")
    cfg.set("weather.latitude", 52.52)
    cfg.set("weather.longitude", 13.405)
    cfg.set("weather.country_code", "DE")
    # keep fahrenheit units so ONLY the coords differ
    monkeypatch.setattr(
        sw, "_http_get_json",
        lambda url, params: (_ for _ in ()).throw(
            requests.ConnectionError("down")) if url.startswith(sw.FORECAST_URL)
        else _GEOCODE_DE,
    )
    body = client.get("/api/weather").get_json()
    assert body["location"]["name"] == "Berlin"
    assert body["snapshot"] is None
    assert body["scene"] is None


def test_location_change_with_failed_fetch_serves_no_old_city_data(
        client, monkeypatch):
    # B1 end-to-end: change location with the forecast fetch failing. The
    # old city's snapshot must never appear under the new location's name.
    resp, _ = _put_location(client, monkeypatch)
    assert resp.status_code == 200
    monkeypatch.setattr(
        sw, "_http_get_json",
        lambda url, params: (_ for _ in ()).throw(
            requests.ConnectionError("down")) if url.startswith(sw.FORECAST_URL)
        else _GEOCODE_DE,
    )
    resp = client.put("/api/weather/location", json={"location": "Berlin"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["location"]["name"] == "Berlin"
    assert body["snapshot"] is None
    assert body["scene"] is None


def test_snapshot_survives_restart_via_sidecar(client, monkeypatch, cfg):
    resp, calls = _put_location(client, monkeypatch)
    assert resp.status_code == 200
    sidecar = Path(cfg.config_path).parent / sw.SNAPSHOT_FILENAME
    assert sidecar.exists()
    # simulate a restart: drop the in-memory cache, keep the sidecar file
    sw._cache.update(
        {"snapshot": None, "units": None, "latitude": None, "longitude": None}
    )
    resp = client.get("/api/weather")
    assert resp.status_code == 200
    assert len(_forecast_calls(calls)) == 1  # no refetch, came from sidecar
    assert resp.get_json()["snapshot"] is not None


def test_corrupt_sidecar_json_triggers_refetch(client, monkeypatch, cfg):
    resp, calls = _put_location(client, monkeypatch)
    assert resp.status_code == 200
    assert len(_forecast_calls(calls)) == 1
    sidecar = Path(cfg.config_path).parent / sw.SNAPSHOT_FILENAME
    sidecar.write_text("{not valid json", encoding="utf-8")
    # simulate a restart: drop the in-memory cache, keep the corrupt file
    sw._cache.update(
        {"snapshot": None, "units": None, "latitude": None, "longitude": None}
    )
    resp = client.get("/api/weather")
    assert resp.status_code == 200
    assert len(_forecast_calls(calls)) == 2  # corrupt sidecar ignored, refetched
    assert resp.get_json()["snapshot"] is not None


# ---------------------------------------------------------------------------
# snapshot numeric guarantee (M5): non-nulls or the fetch fails
# ---------------------------------------------------------------------------

def test_fetch_snapshot_drops_daily_rows_with_null_temps(monkeypatch):
    forecast = _forecast()
    forecast["daily"]["temperature_2m_max"] = [70.1, None, 65.0]
    forecast["daily"]["weather_code"] = [1, 2, None]
    _fake_http(monkeypatch, forecast=forecast)
    snap = sw.fetch_snapshot(42.3265, -122.8756, False)
    # row 2 dropped (null temp_max), row 3 dropped (null weather_code)
    assert [d["date"] for d in snap["daily"]] == ["2026-10-06"]
    for day in snap["daily"]:
        assert day["temp_max"] is not None
        assert day["temp_min"] is not None
        assert day["weather_code"] is not None


def test_fetch_snapshot_fails_when_current_temp_null(monkeypatch):
    _fake_http(monkeypatch, forecast=_forecast(temp=None))
    with pytest.raises(ValueError, match="temperature_2m"):
        sw.fetch_snapshot(42.3265, -122.8756, False)


def test_get_snapshot_null_when_current_temp_missing(client, monkeypatch):
    # A null current temp fails the whole fetch -> stale/null path, never 500.
    resp, _ = _put_location(client, monkeypatch, forecast=_forecast(temp=None))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["snapshot"] is None
    assert body["scene"] is None


def test_fetch_snapshot_daily_shorter_than_3_rows(monkeypatch):
    forecast = _forecast()
    forecast["daily"]["time"] = ["2026-10-06", "2026-10-07"]
    _fake_http(monkeypatch, forecast=forecast)
    snap = sw.fetch_snapshot(42.3265, -122.8756, False)
    assert [d["date"] for d in snap["daily"]] == ["2026-10-06", "2026-10-07"]


def test_location_payload_none_when_coords_missing(cfg):
    cfg.set("weather.location", "97501")
    assert sw._location_payload() is None  # no lat/lon configured
    cfg.set("weather.latitude", "not-a-float")
    cfg.set("weather.longitude", -122.8756)
    assert sw._location_payload() is None  # unparseable lat
    cfg.set("weather.location", "   ")
    assert sw._location_payload() is None  # blank query = feature off


# ---------------------------------------------------------------------------
# endpoint contracts
# ---------------------------------------------------------------------------

def test_get_shape_matches_contract(client, monkeypatch):
    resp, calls = _put_location(client, monkeypatch)
    assert resp.status_code == 200
    body = client.get("/api/weather").get_json()
    assert body["success"] is True
    assert body["enabled"] is True
    assert body["units"] == "fahrenheit"
    loc = body["location"]
    # wire coords are rounded to 2 decimals (~1 km); config keeps full precision
    assert loc == {"query": "97501", "name": "Medford", "latitude": 42.33,
                   "longitude": -122.88, "country_code": "US"}
    snap = body["snapshot"]
    assert set(snap) == {"fetched_at", "utc_offset_seconds", "current", "daily"}
    assert snap["utc_offset_seconds"] == -25200
    cur = snap["current"]
    assert cur == {"temp": 68, "weather_code": 1, "condition": "Mainly clear",
                   "wind_speed": 8}
    assert len(snap["daily"]) == 3
    day = snap["daily"][0]
    assert day == {"date": "2026-10-06", "temp_max": 70, "temp_min": 52,
                   "weather_code": 1, "condition": "Mainly clear",
                   "precip_probability": 5}
    assert body["scene"] == "clear"


def test_get_feature_off_with_no_location(client, monkeypatch):
    _fake_http(monkeypatch)
    body = client.get("/api/weather").get_json()
    assert body == {"success": True, "enabled": True, "location": None,
                    "units": "fahrenheit", "snapshot": None, "scene": None}
    assert _forecast_calls([]) == []


def test_put_location_us_gets_fahrenheit(client, monkeypatch, cfg):
    resp, calls = _put_location(client, monkeypatch)
    assert resp.status_code == 200
    assert cfg.get("weather.location") == "97501"
    assert cfg.get("weather.location_name") == "Medford"
    assert cfg.get("weather.latitude") == 42.3265
    assert cfg.get("weather.longitude") == -122.8756
    assert cfg.get("weather.country_code") == "US"
    assert cfg.get("weather.use_celsius") is False
    body = resp.get_json()
    assert body["units"] == "fahrenheit"
    # the forecast itself was requested in fahrenheit
    assert _forecast_calls(calls)[0][1]["temperature_unit"] == "fahrenheit"


def test_put_location_non_us_gets_celsius(client, monkeypatch, cfg):
    resp, calls = _put_location(client, monkeypatch, query="Berlin",
                                geocode=_GEOCODE_DE)
    assert resp.status_code == 200
    assert cfg.get("weather.use_celsius") is True
    body = resp.get_json()
    assert body["units"] == "celsius"
    assert _forecast_calls(calls)[0][1]["temperature_unit"] == "celsius"
    assert body["snapshot"]["current"]["temp"] == 68  # rounded from 68.4


def test_put_location_empty_string_clears(client, monkeypatch):
    resp, _ = _put_location(client, monkeypatch)
    assert resp.status_code == 200
    assert resp.get_json()["snapshot"] is not None
    resp = client.put("/api/weather/location", json={"location": ""})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["location"] is None
    assert body["snapshot"] is None
    assert body["scene"] is None


def test_put_location_missing_key_400(client, monkeypatch):
    _fake_http(monkeypatch)
    resp = client.put("/api/weather/location", json={})
    assert resp.status_code == 400
    assert resp.get_json() == {"success": False, "error": "location is required"}


def test_put_location_unresolvable_400(client, monkeypatch):
    resp, _ = _put_location(client, monkeypatch, query="nowhere",
                            geocode={"results": []})
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


def test_put_location_geocode_transport_failure_is_not_500(client, monkeypatch):
    _fake_http(monkeypatch, fail_prefixes=(sw.GEOCODE_URL,))
    resp = client.put("/api/weather/location", json={"location": "97501"})
    assert resp.status_code == 502
    assert resp.get_json()["success"] is False


def test_put_location_failed_refetch_preserves_stale_for_same_coords(
        client, monkeypatch):
    # N3: re-PUT the identical location with the forecast endpoint down. The
    # coords didn't change and the sidecar holds a good stale snapshot, so
    # the failed refresh must preserve it instead of wiping the cache.
    resp, _ = _put_location(client, monkeypatch)
    assert resp.status_code == 200
    old_snapshot = resp.get_json()["snapshot"]
    assert old_snapshot is not None
    # age the snapshot past the TTL, then re-PUT with the forecast down
    later = datetime.now(timezone.utc) + timedelta(minutes=31)
    monkeypatch.setattr(sw, "_utcnow", lambda: later)
    monkeypatch.setattr(
        sw, "_http_get_json",
        lambda url, params: (_ for _ in ()).throw(
            requests.ConnectionError("down")) if url.startswith(sw.FORECAST_URL)
        else _GEOCODE_US,
    )
    resp = client.put("/api/weather/location", json={"location": "97501"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["location"]["name"] == "Medford"
    assert body["snapshot"] is not None
    assert body["snapshot"]["fetched_at"] == old_snapshot["fetched_at"]


def test_put_display_toggle(client, monkeypatch):
    _fake_http(monkeypatch)
    resp = client.put("/api/weather/display", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.get_json() == {"success": True, "enabled": False}
    # disabled -> GET reports the toggle and no snapshot
    body = client.get("/api/weather").get_json()
    assert body["enabled"] is False
    assert body["snapshot"] is None
    assert body["scene"] is None
    resp = client.put("/api/weather/display", json={"enabled": True})
    assert resp.get_json() == {"success": True, "enabled": True}


def test_put_display_missing_key_400(client, monkeypatch):
    _fake_http(monkeypatch)
    resp = client.put("/api/weather/display", json={})
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


def test_put_units_flips_and_refetches(client, monkeypatch, cfg):
    resp, calls = _put_location(client, monkeypatch)
    assert resp.status_code == 200
    assert resp.get_json()["units"] == "fahrenheit"
    resp = client.put("/api/weather/units", json={"use_celsius": True})
    assert resp.status_code == 200
    assert cfg.get("weather.use_celsius") is True
    body = resp.get_json()
    assert body["units"] == "celsius"
    # the stale fahrenheit snapshot was discarded; a celsius fetch happened
    assert _forecast_calls(calls)[-1][1]["temperature_unit"] == "celsius"


def test_put_units_missing_key_400(client, monkeypatch):
    _fake_http(monkeypatch)
    resp = client.put("/api/weather/units", json={})
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


def test_wind_scene_end_to_end(client, monkeypatch):
    resp, _ = _put_location(client, monkeypatch,
                            forecast=_forecast(current_code=1, wind=22.0))
    assert resp.status_code == 200
    assert resp.get_json()["scene"] == "wind"


def test_wind_threshold_uses_raw_wind_end_to_end(client, monkeypatch):
    # N2: 14.9 mph rounds to 15 for display but must NOT trip the wind
    # scene — the threshold compares the raw value.
    resp, _ = _put_location(client, monkeypatch,
                            forecast=_forecast(current_code=1, wind=14.9))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["snapshot"]["current"]["wind_speed"] == 15  # display rounding
    assert body["scene"] == "clear"  # raw 14.9 < 15


def test_fetch_snapshot_keeps_raw_wind(monkeypatch):
    _fake_http(monkeypatch, forecast=_forecast(current_code=1, wind=14.9))
    snap = sw.fetch_snapshot(42.3265, -122.8756, False)
    assert snap["current"]["wind_speed"] == 14.9  # raw; the scene compares this
    assert sw.scene_for(snap["current"]["weather_code"],
                        snap["current"]["wind_speed"]) == "clear"


def test_rain_scene_end_to_end(client, monkeypatch):
    resp, _ = _put_location(client, monkeypatch,
                            forecast=_forecast(current_code=63, wind=22.0))
    assert resp.status_code == 200
    assert resp.get_json()["scene"] == "rain"


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

def test_puts_require_admin_but_get_is_open(client, monkeypatch):
    # simulate a non-admin profile: the PUTs must 403, GET must stay 200
    monkeypatch.setattr("core.profile_context.is_admin_request", lambda: False)
    _fake_http(monkeypatch)
    assert client.put("/api/weather/location",
                      json={"location": "97501"}).status_code == 403
    assert client.put("/api/weather/display",
                      json={"enabled": False}).status_code == 403
    assert client.put("/api/weather/units",
                      json={"use_celsius": True}).status_code == 403
    assert client.get("/api/weather").status_code == 200


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------

def test_webserver_registers_sidebar_weather_blueprint():
    src = (_ROOT / "web_server.py").read_text(encoding="utf-8")
    assert ("from api.sidebar_weather import configure as _cfg_sw, "
            "create_blueprint as _bp_sw") in src
    assert "_cfg_sw(config_manager=config_manager)" in src
    assert "app.register_blueprint(_bp_sw())" in src
