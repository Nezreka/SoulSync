# SoulSync Sidebar Weather — Design Doc

## Goal
Additive, opt-in sidebar weather: time/temp/condition line under the profile row
(right-aligned), click → forecast popover opening upward from the weather line,
plus a subtle particle scene behind the sidebar nav. NO holiday/seasonal scenes
(later PR). No API keys, no user setup beyond a location string.

## Backend (`api/sidebar_weather.py`, new file)

Pattern: module-level `bp = Blueprint("sidebar_weather", __name__)`,
`configure(*, config_manager)` injecting deps, `create_blueprint()` returning bp.
Wire in web_server.py next to the other `_bp_xxx()` blocks:
```python
from api.sidebar_weather import configure as _cfg_sw, create_blueprint as _bp_sw
_cfg_sw(config_manager=config_manager)
app.register_blueprint(_bp_sw())
```

Config keys (dot-notation via ConfigManager):
- `weather.location` — raw user string (zip or city). Blank/absent = feature off.
- `weather.location_name`, `weather.latitude`, `weather.longitude`, `weather.country_code` — from geocode, cached at set-time.
- `weather.enabled` — bool, default True. Master toggle (settings + reduced-motion also gate the scene client-side).
- `weather.use_celsius` — bool. Auto-set from geocode country: US → False (°F), else True (°C). Documented; user can flip it in settings.

Open-Meteo (no key):
- Geocode once on location set: `https://geocoding-api.open-meteo.com/v1/search?name=<q>&count=1&format=json`
- Forecast: `https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..&current=temperature_2m,weather_code,wind_speed_10m&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max&temperature_unit=fahrenheit|celsius&wind_speed_unit=mph&timezone=auto&forecast_days=3`
- `timezone=auto` returns `utc_offset_seconds` → location-local time computed server-side as ISO string AND the offset is shipped so the client can tick the clock locally.
- 30-min cache: on GET, refresh if snapshot older than 30 min (lazy; no scheduler). Persist snapshot + coords so restarts don't lose them (small JSON file next to the app config, or in-memory + best-effort — decide and document; must not rewrite the whole app config every 30 min).
- Timeouts on all HTTP (10s), failures → serve stale cache if present, else `snapshot: null` and the frontend renders nothing (never break the sidebar).

WMO weather_code → scene (server computes `scene`):
- rain: 51,53,55,56,57,61,63,65,66,67,80,81,82,95,96,99
- snow: 71,73,75,77,85,86
- clear: 0,1,2,3,45,48 → cloud wisps
- wind: wind_speed_10m >= 15 mph AND code not in rain/snow sets (precip wins)
- condition text + glyph id per code group.

### Endpoint contract (shared with frontend)

`GET /api/weather` → 200
```json
{
  "success": true,
  "enabled": true,
  "location": {"query": "97501", "name": "Medford", "latitude": 42.32, "longitude": -122.87, "country_code": "US"},
  "units": "fahrenheit",
  "snapshot": {
    "fetched_at": "2026-10-06T22:00:00+00:00",
    "utc_offset_seconds": -25200,
    "current": {"temp": 68, "weather_code": 1, "condition": "Mainly clear", "wind_speed": 8},
    "daily": [
      {"date": "2026-10-06", "temp_max": 70, "temp_min": 52, "weather_code": 1, "condition": "Mainly clear", "precip_probability": 5}
    ]
  },
  "scene": "clear"
}
```
- `snapshot: null`, `scene: null` when no location set or fetch failed and no stale cache.
- Auth: session-based like other `/api/*` legacy routes (no API key needed from the browser). Use `@admin_only` from `core.profile_context` on the PUTs; GET may be open to any logged-in profile (decide, document).

`PUT /api/weather/location` body `{"location": "97501"}` → geocode → persist → refresh snapshot → return same shape as GET. `{"location": ""}` clears location (feature off). 400 on empty/unresolvable with `{"success": false, "error": "..."}`.

`PUT /api/weather/display` body `{"enabled": true|false}` → persists `weather.enabled`, returns `{"success": true, "enabled": ...}`.

`PUT /api/weather/units` body `{"use_celsius": true|false}` → persists, refetches in new units, returns GET shape. (Optional but cheap; include it.)

## Frontend

### New shell module `webui/src/shell/sidebar-weather.ts`
Compiled into the shell IIFE (`static/dist/shell.js`); add `initSidebarWeather` to
`SHELL_WINDOW_EXPORTS` in `webui/src/shell/index.ts` and update the census test
that pins window exports.

Behavior:
- Runs on DOMContentLoaded (guard: only if `#app-sidebar` exists).
- Fetches `/api/weather`. Renders nothing unless `enabled && location && snapshot`.
- Weather line: `<button id="sidebar-weather-line">` inserted directly after
  `#profile-indicator` inside `.sidebar-header`, CSS right-aligned. Content:
  tiny SVG glyph + `2:32 PM · 68°F · Clear`. Time computed from
  `utc_offset_seconds` (location-local), ticking every 60s client-side.
- Popover: on click, toggles `#sidebar-weather-popover` positioned directly
  ABOVE the weather line (popover bottom edge touches the weather line's top,
  opening upward over the header zone). Contents: "Today" row + next 2 daily
  rows: glyph, temp max/min, condition, precip %. Closes on outside click / Escape.
- Particle canvas: `<canvas id="sidebar-weather-scene">` as first child of
  `.sidebar`, absolute inset-0, `pointer-events: none`, z-index BELOW
  `.sidebar-header`/`.sidebar-scroll` (nav stays crisp; add a subtle dark scrim
  via CSS gradient on the canvas wrapper or sidebar::after — must not alter
  existing sidebar styles, only add).
  - Scene types: `rain` (thin diagonal streaks), `snow` (slow drifting dots,
    faint dust-mote density), `wind` (horizontal gust streaks + a few tumbling
    leaves), `clear` (barely-visible drifting cloud wisps).
  - Density: restrained — match the approved mock (snow ≈ faint dust motes).
    Cap particle counts (e.g. ≤ 90 rain, ≤ 70 snow, ≤ 40 wind streaks+12 leaves,
    ≤ 8 wisps); scale down on narrow/height-constrained sidebars.
  - `matchMedia('(prefers-reduced-motion: reduce)')` → no canvas at all.
  - `document.visibilitychange` → pause rAF loop when hidden, resume when visible.
  - Resize observer on sidebar → resize canvas (devicePixelRatio-capped at 2).

### CSS `webui/static/sidebar-weather.css` (new file, linked in index.html)
Only new selectors (`#sidebar-weather-line`, `#sidebar-weather-popover`,
`#sidebar-weather-scene`, `.sidebar-weather-*`). No modifications to existing rules.

### HTML `webui/index.html` (additive only)
- After `#profile-indicator` div: `<!-- sidebar weather mounts here -->` anchor
  (the TS module creates the actual nodes; keep index.html diff minimal — or
  hardcode the empty containers, your call, but DO NOT touch existing markup).
- `<link rel="stylesheet" href="...sidebar-weather.css">` near other CSS links.

### Settings UI (Advanced tab)
- New collapsible section in `webui/index.html` advanced tab:
  "Sidebar Weather" — location text input (zip or city), Save/Clear buttons,
  enable toggle, unit note ("°F for US locations, °C elsewhere — auto, flippable"),
  small status line (last fetch time / error).
- Wiring in `webui/static/settings.js`: load current state on Advanced tab open,
  call the PUT endpoints, toast on save. Follow existing patterns in that file.

## Tests
- Backend: `tests/api/test_sidebar_weather.py` — WMO→scene mapping (every group
  incl. boundaries), condition text, geocode parsing, 30-min cache refresh/stale
  behavior, endpoint contracts (GET/PUT shapes, 400 on bad location, clear flow),
  unit auto-detect (US→F, else C). Use monkeypatched HTTP, never hit Open-Meteo.
- Frontend: `webui/src/shell/sidebar-weather.test.ts` (vitest) — scene mapping,
  time formatting from utc_offset_seconds, popover toggle/close behavior,
  reduced-motion gate, visibility pause. DOM via testing-library or direct DOM.
- FAIL-without/PASS-with: verify each new test fails on the pristine base where
  meaningful (esp. endpoint 404s and module absence).

## Non-goals
Holiday/seasonal scenes, full-page particles, weather on any other surface,
scheduler/daemon for refresh (lazy refresh only), API-key auth for these routes.

## Known limitations
- **DST boundary**: `utc_offset_seconds` is captured at fetch time and cached
  ~30 minutes server-side. Crossing a daylight-saving transition inside that
  window shows location-local time off by an hour until the next refresh.
  Twice a year, at most 30 minutes — accepted as-is; a client-side tz
  database is not worth a sub-hour, twice-yearly glitch.
- **Boot overlap**: `bootSidebarWeather` is generation-guarded — overlapping
  calls (double-clicked Save, or a save racing the page-load boot) abort the
  stale call after the fetch resolves; only the newest call mounts. Teardown
  runs only after a successful fetch, so a transient GET failure keeps the
  previous mount instead of blanking the sidebar.
- **Collapsed sidebar**: the particle scene's rAF loop is fully stopped while
  `html[data-sidebar="collapsed"]` (restarted on expand via MutationObserver);
  the tab-hidden pause is separate and orthogonal.
