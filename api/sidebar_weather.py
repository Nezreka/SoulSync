"""Sidebar weather: Open-Meteo fetch, 30-minute cache, /api/weather endpoints.

Opt-in and purely additive. A blank/absent ``weather.location`` means the
feature is off; GET then returns ``snapshot: null`` / ``scene: null`` and the
sidebar renders nothing.

Auth model (matches the project's legacy-route posture):
- ``GET /api/weather`` is undecorated. ``get_current_profile_id()`` defaults
  to profile 1 with no session, so on single-admin installs with no
  login/PIN there is no network gate at all; on multi-profile installs any
  profile may read it (the weather line renders in every profile's sidebar).
- The three PUTs carry ``@admin_only`` from ``core.profile_context``, so on
  multi-profile installs only admins can change the location, units, or
  toggle. On single-admin installs the decorator is a no-op.

Config keys (dot-notation via ConfigManager):
- ``weather.location`` — raw user string (zip or city). Blank = feature off.
- ``weather.location_name`` / ``weather.latitude`` / ``weather.longitude`` /
  ``weather.country_code`` — from the one-time geocode at set-time.
- ``weather.enabled`` — bool, default True. Master toggle.
- ``weather.use_celsius`` — bool. Auto-set from the geocode country
  (US -> False i.e. degF, everything else -> True i.e. degC); the user can
  flip it afterwards via ``PUT /api/weather/units`` without it being
  re-derived until the location is set again.

Snapshot numeric guarantee:
- ``current.temp`` is always an int. A null current temperature fails the
  whole fetch — the caller falls back to stale/null instead of serving a
  half-null payload.
- Every ``daily`` row carries non-null ``temp_max``/``temp_min``/
  ``weather_code``. Provider rows missing any of those are dropped, so
  ``daily`` may hold fewer than 3 rows.
- ``current.weather_code``, ``current.wind_speed`` and ``precip_probability``
  may still be null when the provider omits them; consumers degrade
  gracefully (unknown condition text, wind treated as calm).
- The cached snapshot keeps the RAW wind speed so the wind-scene threshold
  compares the unrounded value; the JSON response rounds ``wind_speed`` for
  display only.

Stale-serving rule: a stale snapshot is served only when it matches the
currently selected units AND the currently configured coordinates — never a
foreign-units or foreign-location snapshot under the current label.

Threading: ``_cache_lock`` guards only the in-memory ``_cache`` dict and is
never held across disk or network I/O. Slow paths re-check the cache after
re-acquiring the lock, since another thread may have refreshed meanwhile.

Privacy: the JSON responses round latitude/longitude to 2 decimals (~1 km);
the frontend only ever shows the location name. Full precision stays in the
config and sidecar for refetching.

Persistence: the forecast snapshot lives in a small sidecar JSON file next
to the app config (``sidebar_weather_snapshot.json``) — NOT in the main
config — so the 30-minute refresh cadence never rewrites the whole app
config, and restarts keep the last snapshot + its units/coords.

Failure posture: all Open-Meteo HTTP has a 10s timeout. A failed refresh
serves the stale snapshot when one exists, otherwise ``snapshot: null`` —
weather never 500s the sidebar.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Blueprint, jsonify, request

from core.profile_context import admin_only
from utils.logging_config import get_logger

logger = get_logger("api.sidebar_weather")

bp = Blueprint("sidebar_weather", __name__)

# Injected by configure() at boot.
config_manager = None

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HTTP_TIMEOUT = 10  # seconds, on every Open-Meteo call
CACHE_TTL_SECONDS = 30 * 60  # lazy 30-min refresh on GET; no scheduler
WIND_SCENE_MPH = 15  # >= 15 mph + no precip -> wind scene
SNAPSHOT_FILENAME = "sidebar_weather_snapshot.json"

# WMO weather_code -> scene. Precipitation wins over wind: the wind scene only
# applies when the code is NOT in the rain/snow sets.
_RAIN_CODES = frozenset(
    {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}
)
_SNOW_CODES = frozenset({71, 73, 75, 77, 85, 86})
# 0/1/2/3 + fog (45/48) -> "clear" scene (client renders cloud wisps).

_CONDITION_TEXT = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

# In-memory cache: {"snapshot": dict|None, "units": str|None,
#                   "latitude": float|None, "longitude": float|None}.
# The sidecar file is the durable copy; memory is just the hot path.
_cache = {"snapshot": None, "units": None, "latitude": None, "longitude": None}
_cache_lock = threading.Lock()


def configure(*, config_manager):
    globals()["config_manager"] = config_manager


def create_blueprint():
    return bp


# ---------------------------------------------------------------------------
# pure helpers (no I/O) — the units tests pin these directly
# ---------------------------------------------------------------------------

def scene_for(weather_code, wind_mph):
    """WMO code + wind speed (mph) -> 'rain' | 'snow' | 'wind' | 'clear'.

    Precipitation wins over wind: a rainy code with 40 mph wind is still
    'rain'. Unknown codes fall through to 'clear' (or 'wind' when gusty).
    """
    try:
        code = int(weather_code)
    except (TypeError, ValueError):
        code = None
    if code in _RAIN_CODES:
        return "rain"
    if code in _SNOW_CODES:
        return "snow"
    try:
        wind = float(wind_mph or 0)
    except (TypeError, ValueError):
        wind = 0.0
    if wind >= WIND_SCENE_MPH:
        return "wind"
    return "clear"


def condition_text(weather_code):
    """Human condition string for a WMO weather code."""
    try:
        code = int(weather_code)
    except (TypeError, ValueError):
        return "Unknown"
    return _CONDITION_TEXT.get(code, "Unknown")


def _utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Open-Meteo HTTP
# ---------------------------------------------------------------------------

def _http_get_json(url, params):
    """One GET with a 10s timeout; raises on network/HTTP/JSON failure."""
    resp = requests.get(
        url,
        params=params,
        timeout=HTTP_TIMEOUT,
        headers={"User-Agent": "SoulSyncSidebarWeather/1.0"},
    )
    resp.raise_for_status()
    return resp.json()


def geocode_location(query):
    """Resolve a zip/city string once, at set-time.

    Returns {"name", "latitude", "longitude", "country_code"} or None when the
    geocoder finds nothing / returns garbage. Raises on transport failure.
    """
    data = _http_get_json(GEOCODE_URL, {"name": query, "count": 1, "format": "json"})
    results = (data or {}).get("results") or []
    if not results:
        return None
    first = results[0]
    try:
        lat = float(first["latitude"])
        lon = float(first["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    name = str(first.get("name") or query)
    country = str(first.get("country_code") or "").upper() or None
    return {"name": name, "latitude": lat, "longitude": lon, "country_code": country}


def _round_or_none(value):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_snapshot(latitude, longitude, use_celsius):
    """Fetch the 3-day forecast snapshot. Raises on any failure.

    Numeric guarantee (see module docstring): ``current["temp"]`` is always
    an int — a null temperature fails the whole fetch; ``daily`` rows
    missing temp_max/temp_min/weather_code are dropped. The cached
    ``wind_speed`` stays RAW so the scene threshold compares the unrounded
    value (rounding happens in the wire copy only).
    """
    data = _http_get_json(
        FORECAST_URL,
        {
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                     "precipitation_probability_max",
            "temperature_unit": "celsius" if use_celsius else "fahrenheit",
            "wind_speed_unit": "mph",
            "timezone": "auto",
            "forecast_days": 3,
        },
    )
    current = (data or {}).get("current") or {}
    daily = (data or {}).get("daily") or {}

    temp = _round_or_none(current.get("temperature_2m"))
    if temp is None:
        # A snapshot without a current temperature is useless to the
        # sidebar; fail the fetch so the caller falls back to stale/null
        # instead of serving a half-null payload.
        raise ValueError("forecast response has no current temperature_2m")
    code = _round_or_none(current.get("weather_code"))
    wind = _float_or_none(current.get("wind_speed_10m"))

    dates = daily.get("time") or []
    codes = daily.get("weather_code") or []
    tmaxs = daily.get("temperature_2m_max") or []
    tmins = daily.get("temperature_2m_min") or []
    precips = daily.get("precipitation_probability_max") or []
    days = []
    for i in range(min(3, len(dates))):
        tmax = _round_or_none(tmaxs[i]) if i < len(tmaxs) else None
        tmin = _round_or_none(tmins[i]) if i < len(tmins) else None
        dcode = _round_or_none(codes[i]) if i < len(codes) else None
        if tmax is None or tmin is None or dcode is None:
            continue  # incomplete provider row: never serve a half-null day
        days.append(
            {
                "date": str(dates[i]),
                "temp_max": tmax,
                "temp_min": tmin,
                "weather_code": dcode,
                "condition": condition_text(dcode),
                "precip_probability": (
                    _round_or_none(precips[i]) if i < len(precips) else None
                ),
            }
        )

    try:
        offset = int(data.get("utc_offset_seconds") or 0)
    except (TypeError, ValueError):
        offset = 0
    return {
        "fetched_at": _utcnow().isoformat(),
        "utc_offset_seconds": offset,
        "current": {
            "temp": temp,
            "weather_code": code,
            "condition": condition_text(code),
            "wind_speed": wind,  # raw float; the wire copy rounds it
        },
        "daily": days,
    }


# ---------------------------------------------------------------------------
# config + sidecar persistence
# ---------------------------------------------------------------------------

def _snapshot_path():
    base = getattr(config_manager, "config_path", None)
    directory = Path(base).parent if base else Path("config")
    return directory / SNAPSHOT_FILENAME


def _load_sidecar():
    try:
        with open(_snapshot_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("snapshot"), dict):
        return None
    return data


def _save_sidecar(snapshot, units, latitude, longitude):
    try:
        _snapshot_path().parent.mkdir(parents=True, exist_ok=True)
        with open(_snapshot_path(), "w", encoding="utf-8") as fh:
            json.dump(
                {"snapshot": snapshot, "units": units,
                 "latitude": latitude, "longitude": longitude},
                fh,
            )
    except OSError as exc:
        # best-effort: a failed sidecar write must not break the request
        logger.warning("sidebar weather: could not persist snapshot: %s", exc)


def _use_celsius():
    """False for US locations (degF), True elsewhere; default False unset."""
    value = config_manager.get("weather.use_celsius", None)
    if value is None:
        return False
    return bool(value)


def _location_payload():
    """Configured location block, or None when the feature is off."""
    query = (config_manager.get("weather.location", "") or "").strip()
    if not query:
        return None
    try:
        lat = float(config_manager.get("weather.latitude"))
        lon = float(config_manager.get("weather.longitude"))
    except (TypeError, ValueError):
        return None
    return {
        "query": query,
        "name": config_manager.get("weather.location_name") or query,
        "latitude": lat,
        "longitude": lon,
        "country_code": config_manager.get("weather.country_code"),
    }


def _snapshot_age_seconds(snapshot):
    try:
        fetched = datetime.fromisoformat(snapshot["fetched_at"])
    except (KeyError, TypeError, ValueError):
        return float("inf")
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return (_utcnow() - fetched).total_seconds()


def _entry_matches(entry, units, latitude, longitude):
    """Identity check for a cache entry: same units AND same coords.

    Freshness is deliberately NOT checked — the stale fallback in
    ``_get_snapshot`` uses this so a stale snapshot is only ever served when
    it is in the currently selected units and for the currently configured
    coordinates. A snapshot in the wrong units or for a previous location
    is never served under the current label.
    """
    entry = entry or {}
    if not isinstance(entry.get("snapshot"), dict):
        return False
    return (entry.get("units") == units
            and entry.get("latitude") == latitude
            and entry.get("longitude") == longitude)


def _entry_usable(entry, units, latitude, longitude):
    """A cached snapshot is usable only if fresh AND matching units+coords."""
    if not _entry_matches(entry, units, latitude, longitude):
        return False
    return _snapshot_age_seconds(entry["snapshot"]) < CACHE_TTL_SECONDS


def _get_snapshot(location):
    """Fresh-enough cached snapshot, else refresh; stale on failure; None if
    nothing is available. Never raises.

    Threading: ``_cache_lock`` is only ever held for the in-memory dict
    read/mutation — never across the sidecar disk read or the network
    fetch. After each slow step the cache is re-checked under the lock,
    since another thread may have refreshed it meanwhile.
    """
    latitude = location["latitude"]
    longitude = location["longitude"]
    units = "celsius" if _use_celsius() else "fahrenheit"

    with _cache_lock:
        if _entry_usable(_cache, units, latitude, longitude):
            return _cache["snapshot"]
        # Copy for the stale fallback below; the lock is released before
        # any disk or network I/O happens.
        mem_entry = dict(_cache)

    # Disk I/O outside the lock.
    sidecar = _load_sidecar()
    if _entry_usable(sidecar, units, latitude, longitude):
        with _cache_lock:
            # Another thread may have refreshed while we read the disk.
            if _entry_usable(_cache, units, latitude, longitude):
                return _cache["snapshot"]
            _cache.update(sidecar)
            return sidecar["snapshot"]

    # Network I/O outside the lock.
    try:
        snapshot = fetch_snapshot(latitude, longitude, units == "celsius")
    except Exception as exc:  # noqa: BLE001 — any failure serves stale/null
        logger.warning("sidebar weather: forecast refresh failed: %s", exc)
        # Stale fallback: only a snapshot matching the current units+coords
        # may be served — never foreign-units or foreign-location data.
        for entry in (mem_entry, sidecar):
            if _entry_matches(entry, units, latitude, longitude):
                return entry["snapshot"]
        return None

    with _cache_lock:
        # Another thread may have refreshed while we were fetching; prefer
        # the fresh entry instead of a redundant sidecar write.
        if _entry_usable(_cache, units, latitude, longitude):
            return _cache["snapshot"]
        _cache.update({"snapshot": snapshot, "units": units,
                       "latitude": latitude, "longitude": longitude})
    _save_sidecar(snapshot, units, latitude, longitude)
    return snapshot


def _clear_cached_snapshot():
    with _cache_lock:
        _cache.update(
            {"snapshot": None, "units": None, "latitude": None, "longitude": None}
        )
    try:
        _snapshot_path().unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("sidebar weather: could not clear snapshot file: %s", exc)


def _public_location(location):
    """Wire copy of the location block with coords rounded to 2 decimals.

    ~1 km precision is plenty: the frontend only ever shows the name. Full
    precision stays in the config and sidecar for refetching.
    """
    if not location:
        return None
    public = dict(location)
    public["latitude"] = round(location["latitude"], 2)
    public["longitude"] = round(location["longitude"], 2)
    return public


def _display_snapshot(snapshot):
    """Wire copy of a cached snapshot with display rounding applied.

    The cache keeps the raw wind speed so scene_for() compares the
    unrounded value against the wind threshold; only this wire copy rounds
    it for display.
    """
    current = dict(snapshot.get("current") or {})
    current["wind_speed"] = _round_or_none(current.get("wind_speed"))
    return {
        "fetched_at": snapshot.get("fetched_at"),
        "utc_offset_seconds": snapshot.get("utc_offset_seconds"),
        "current": current,
        "daily": [dict(day) for day in (snapshot.get("daily") or [])],
    }


def _weather_payload():
    enabled = bool(config_manager.get("weather.enabled", True))
    location = _location_payload()
    units = "celsius" if _use_celsius() else "fahrenheit"
    snapshot = None
    scene = None
    if enabled and location:
        snapshot = _get_snapshot(location)
        if snapshot:
            current = snapshot.get("current") or {}
            # Raw wind: the >= 15 mph threshold compares the unrounded
            # value, so 14.9 mph stays "clear" even though it displays
            # as 15 mph.
            scene = scene_for(current.get("weather_code"),
                              current.get("wind_speed"))
            snapshot = _display_snapshot(snapshot)
    return {
        "success": True,
        "enabled": enabled,
        "location": _public_location(location),
        "units": units,
        "snapshot": snapshot,
        "scene": scene,
    }


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

@bp.route("/api/weather", methods=["GET"])
def get_weather():
    # Undecorated, like the project's other legacy routes: no network gate
    # on single-admin installs; any profile may read on multi-profile ones.
    return jsonify(_weather_payload())


@bp.route("/api/weather/location", methods=["PUT"])
@admin_only
def put_weather_location():
    data = request.get_json(silent=True) or {}
    if "location" not in data:
        return jsonify({"success": False, "error": "location is required"}), 400
    raw = data["location"]
    if not isinstance(raw, str):
        return jsonify({"success": False, "error": "location must be a string"}), 400
    query = raw.strip()
    if not query:
        # empty string clears the location -> feature off
        _clear_location_config()
        _clear_cached_snapshot()
        return jsonify(_weather_payload())

    try:
        geo = geocode_location(query)
    except requests.RequestException as exc:
        logger.warning("sidebar weather: geocode request failed for %r: %s", query, exc)
        return jsonify({"success": False,
                        "error": "could not reach the geocoding service"}), 502
    except Exception as exc:  # noqa: BLE001 — never 500 on a lookup problem
        logger.warning("sidebar weather: geocode failed for %r: %s", query, exc)
        return jsonify({"success": False,
                        "error": "could not resolve that location"}), 400
    if not geo:
        return jsonify({"success": False,
                        "error": "could not resolve that location"}), 400

    # Capture the previous coords BEFORE overwriting the config: a failed
    # refresh must not wipe a good stale snapshot when the location is
    # effectively unchanged (see the except branch below).
    prev_location = _location_payload()
    prev_coords = (prev_location["latitude"], prev_location["longitude"]) \
        if prev_location else None

    # one config write: the units auto-derive from the geocoded country
    # (US -> degF, everything else -> degC); flippable afterwards.
    use_celsius = geo["country_code"] != "US"
    units = "celsius" if use_celsius else "fahrenheit"
    with config_manager.batch():
        config_manager.set("weather.location", query)
        config_manager.set("weather.location_name", geo["name"])
        config_manager.set("weather.latitude", geo["latitude"])
        config_manager.set("weather.longitude", geo["longitude"])
        config_manager.set("weather.country_code", geo["country_code"])
        config_manager.set("weather.use_celsius", use_celsius)

    # refresh now so the response carries a live snapshot; a failed fetch
    # still leaves the location saved (stale snapshot preserved when the
    # coords are unchanged, otherwise snapshot: null).
    try:
        snapshot = fetch_snapshot(geo["latitude"], geo["longitude"],
                                  use_celsius)
    except Exception as exc:  # noqa: BLE001 — location saved, snapshot deferred
        logger.warning("sidebar weather: initial forecast fetch failed: %s", exc)
        # Only clear when the coords actually changed, or when the sidecar
        # holds nothing servable for these coords/units: a failed refresh
        # for unchanged coords preserves the stale snapshot.
        if ((prev_coords != (geo["latitude"], geo["longitude"]))
                or not _entry_matches(_load_sidecar(), units,
                                      geo["latitude"], geo["longitude"])):
            _clear_cached_snapshot()
    else:
        # Lock guards only the in-memory dict mutation; the sidecar write
        # (disk I/O) happens after the lock is released.
        with _cache_lock:
            _cache.update({"snapshot": snapshot, "units": units,
                           "latitude": geo["latitude"],
                           "longitude": geo["longitude"]})
        _save_sidecar(snapshot, units, geo["latitude"], geo["longitude"])
    return jsonify(_weather_payload())


@bp.route("/api/weather/display", methods=["PUT"])
@admin_only
def put_weather_display():
    data = request.get_json(silent=True) or {}
    if "enabled" not in data:
        return jsonify({"success": False, "error": "enabled is required"}), 400
    enabled = bool(data["enabled"])
    config_manager.set("weather.enabled", enabled)
    return jsonify({"success": True, "enabled": enabled})


@bp.route("/api/weather/units", methods=["PUT"])
@admin_only
def put_weather_units():
    data = request.get_json(silent=True) or {}
    if "use_celsius" not in data:
        return jsonify({"success": False, "error": "use_celsius is required"}), 400
    use_celsius = bool(data["use_celsius"])
    config_manager.set("weather.use_celsius", use_celsius)
    # the cached snapshot is in the old units -> next payload refreshes
    return jsonify(_weather_payload())


def _clear_location_config():
    with config_manager.batch():
        config_manager.set("weather.location", "")
        config_manager.set("weather.location_name", "")
        config_manager.set("weather.latitude", None)
        config_manager.set("weather.longitude", None)
        config_manager.set("weather.country_code", None)
