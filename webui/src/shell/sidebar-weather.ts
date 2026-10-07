/**
 * Sidebar weather: the time/temp/condition line under the profile row, the
 * forecast popover that opens above the line (or below it when space is
 * tight), and the faint particle scene drifting behind the sidebar nav.
 *
 * Opt-in and additive: nothing renders unless GET /api/weather says the
 * feature is enabled, a location is set, and a snapshot exists. The scene
 * canvas is skipped entirely under prefers-reduced-motion.
 *
 * Known limitations:
 * - `utc_offset_seconds` is captured at fetch time; crossing a DST boundary
 *   inside the ~30-minute server cache window shows location-local time off
 *   by an hour until the next refresh. Twice a year, <=30 minutes — accepted
 *   as-is rather than adding a client-side tz database for a sub-hour glitch.
 */

import {
  conditionsFromWeather,
  createWeatherScene,
  presetConditions,
  SCENE_PRESETS,
  type SceneConditions,
} from './weather-scene';

export interface WeatherDailyRow {
  date: string;
  temp_max: number | null;
  temp_min: number | null;
  weather_code: number | null;
  condition: string | null;
  precip_probability: number | null;
  /** location-local ISO time, absent on snapshots cached before it existed */
  sunrise?: string | null;
  sunset?: string | null;
}

export interface WeatherSnapshot {
  fetched_at: string;
  utc_offset_seconds: number;
  current: {
    temp: number | null;
    weather_code: number | null;
    condition: string | null;
    wind_speed: number;
    // absent on snapshots cached before the scene read the full sky
    wind_gusts?: number | null;
    wind_direction?: number | null;
    is_day?: boolean | null;
    cloud_cover?: number | null;
    precipitation?: number | null;
  };
  daily: WeatherDailyRow[];
}

export type WeatherScene = 'rain' | 'snow' | 'wind' | 'clear';

export interface WeatherResponse {
  success: boolean;
  enabled: boolean;
  location: {
    query: string;
    name: string;
    latitude: number;
    longitude: number;
    country_code: string | null;
  } | null;
  units: 'fahrenheit' | 'celsius';
  snapshot: WeatherSnapshot | null;
  scene: WeatherScene | null;
}

const LINE_ID = 'sidebar-weather-line';
const POPOVER_ID = 'sidebar-weather-popover';
const CANVAS_ID = 'sidebar-weather-scene';
const CLOCK_TICK_MS = 60_000;
const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

/* ------------------------------------------------------------------ */
/* pure helpers (exported for tests)                                   */
/* ------------------------------------------------------------------ */

/** true only when the sidebar should show anything at all. */
export function shouldRenderWeather(d: WeatherResponse | null | undefined): d is WeatherResponse {
  return !!d && d.enabled === true && !!d.location && !!d.snapshot;
}

/** Location-local "2:32 PM" from the server's utc_offset_seconds. */
export function formatLocalTime(utcOffsetSeconds: number, nowMs: number = Date.now()): string {
  const d = new Date(nowMs + utcOffsetSeconds * 1000);
  const h = d.getUTCHours();
  const m = d.getUTCMinutes();
  const ap = h < 12 ? 'AM' : 'PM';
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${String(m).padStart(2, '0')} ${ap}`;
}

export function formatTemp(temp: number | null | undefined, units: string): string {
  if (typeof temp !== 'number' || !Number.isFinite(temp)) return '—';
  return `${Math.round(temp)}°${units === 'celsius' ? 'C' : 'F'}`;
}

/** "70°/52°F" for a daily row; "—" wherever a temp is missing, never "0°". */
export function formatHiLo(
  tempMax: number | null | undefined,
  tempMin: number | null | undefined,
  units: string,
): string {
  const part = (t: number | null | undefined): string | null =>
    typeof t === 'number' && Number.isFinite(t) ? `${Math.round(t)}°` : null;
  const hi = part(tempMax);
  const lo = part(tempMin);
  if (hi === null && lo === null) return '—';
  return `${hi ?? '—'}/${lo ?? '—'}${units === 'celsius' ? 'C' : 'F'}`;
}

export type WeatherGlyphKind =
  | 'sun'
  | 'moon'
  | 'partly-cloudy'
  | 'partly-cloudy-night'
  | 'cloud'
  | 'fog'
  | 'rain'
  | 'snow'
  | 'wind';

/** WMO weather_code -> glyph kind (groups mirror the server's scene mapping). */
export function glyphForWeatherCode(code: number | null | undefined): WeatherGlyphKind {
  if (code == null) return 'cloud';
  if (code === 0 || code === 1) return 'sun';
  if (code === 2) return 'partly-cloudy';
  if (code === 3) return 'cloud';
  if (code === 45 || code === 48) return 'fog';
  if (code === 51 || code === 53 || code === 55 || code === 56 || code === 57) return 'rain';
  if (code === 61 || code === 63 || code === 65 || code === 66 || code === 67) return 'rain';
  if (code === 80 || code === 81 || code === 82) return 'rain';
  if (code === 95 || code === 96 || code === 99) return 'rain';
  if (code === 71 || code === 73 || code === 75 || code === 77) return 'snow';
  if (code === 85 || code === 86) return 'snow';
  return 'cloud';
}

/** Tiny inline stroke glyphs, currentColor so the line/popover tint them. */
export function glyphSvg(kind: WeatherGlyphKind): string {
  const open =
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">';
  switch (kind) {
    case 'sun':
      return (
        open +
        '<circle cx="8" cy="8" r="3"/>' +
        '<path d="M8 1.5v1.8M8 12.7v1.8M1.5 8h1.8M12.7 8h1.8M3.4 3.4l1.3 1.3M11.3 11.3l1.3 1.3M12.6 3.4l-1.3 1.3M4.7 11.3l-1.3 1.3"/></svg>'
      );
    case 'moon':
      return open + '<path d="M12.6 10.1A5.2 5.2 0 0 1 5.9 3.4a5.2 5.2 0 1 0 6.7 6.7z"/></svg>';
    case 'partly-cloudy-night':
      return (
        open +
        '<path d="M8.4 4.6A2.6 2.6 0 0 1 5 1.6a2.8 2.8 0 1 0 3.4 3z"/>' +
        '<path d="M4.5 12.5h6.5a2.5 2.5 0 0 0 .4-4.96A3.4 3.4 0 0 0 4.7 8.7 2.1 2.1 0 0 0 4.5 12.5z"/></svg>'
      );
    case 'partly-cloudy':
      return (
        open +
        '<circle cx="5.5" cy="5.5" r="2.2"/>' +
        '<path d="M5.5 1.6v1M1.6 5.5h1M2.8 2.8l.7.7"/>' +
        '<path d="M4.5 12.5h6.5a2.5 2.5 0 0 0 .4-4.96A3.4 3.4 0 0 0 4.7 8.7 2.1 2.1 0 0 0 4.5 12.5z"/></svg>'
      );
    case 'fog':
      return (
        open + '<path d="M2.5 6h11M4 9.5h8M2.5 13h11"/>' + '<circle cx="8" cy="3" r="1.4"/></svg>'
      );
    case 'rain':
      return (
        open +
        '<path d="M4 10.5h8a2.4 2.4 0 0 0 .4-4.76A3.3 3.3 0 0 0 5.9 6.8 2 2 0 0 0 4 10.5z"/>' +
        '<path d="M5.5 12.5l-1 2M9 12.5l-1 2M12.5 12.5l-1 2"/></svg>'
      );
    case 'snow':
      return (
        open +
        '<path d="M8 2v12M2.7 5l10.6 6M13.3 5L2.7 11"/>' +
        '<path d="M8 2L6.6 3.4M8 2l1.4 1.4M8 14l-1.4-1.4M8 14l1.4-1.4"/></svg>'
      );
    case 'wind':
      return (
        open +
        '<path d="M1.5 5.5h7.5a1.9 1.9 0 1 0-1.9-1.9M1.5 8.5h11a1.9 1.9 0 1 1-1.9 1.9M1.5 11.5h4.5"/></svg>'
      );
    case 'cloud':
    default:
      return (
        open +
        '<path d="M3.5 12.5h9a2.6 2.6 0 0 0 .45-5.16A3.6 3.6 0 0 0 5.9 8.5 2.2 2.2 0 0 0 3.5 12.5z"/></svg>'
      );
  }
}

/**
 * the line's glyph says what it's like right now: a moon on a clear night,
 * the wind glyph when it's the wind you'd notice
 */
export function lineGlyph(data: WeatherResponse): WeatherGlyphKind {
  const cur = data.snapshot?.current;
  if (data.scene === 'wind') return 'wind';
  const kind = glyphForWeatherCode(cur?.weather_code);
  if (cur?.is_day === false) {
    if (kind === 'sun') return 'moon';
    if (kind === 'partly-cloudy') return 'partly-cloudy-night';
  }
  return kind;
}

/** "Today" for the first daily row, otherwise the location-local weekday. */
export function dayLabelForDaily(date: string, index: number): string {
  if (index === 0) return 'Today';
  const d = new Date(`${date.slice(0, 10)}T12:00:00Z`);
  if (Number.isNaN(d.getTime())) return date;
  return ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getUTCDay()] ?? date;
}

/* ------------------------------------------------------------------ */
/* module state                                                        */
/* ------------------------------------------------------------------ */

interface ActiveWeather {
  data: WeatherResponse;
  line: HTMLButtonElement;
}

let active: ActiveWeather | null = null;
let clockTimer: ReturnType<typeof setTimeout> | null = null;
let rafId: number | null = null;
let stopScene: (() => void) | null = null;
let stopMotionWatch: (() => void) | null = null;
let listenerInstalled = false;
let refreshTimer: ReturnType<typeof setInterval> | null = null;
/** scenes fading out after a refresh, stopped when their fade ends */
const fadingStops: Array<() => void> = [];
const REFRESH_MS = 15 * 60_000;
/** matches the opacity transition on #sidebar-weather-scene */
const SCENE_FADE_MS = 1600;

/**
 * Boot generation counter: bootSidebarWeather awaits a fetch, so two
 * overlapping calls (double-clicked Save, or a save racing the page-load
 * boot) would both mount after the synchronous teardown — duplicating DOM
 * ids, leaking the 60s interval, the rAF scene loop, and listeners. Each
 * boot captures its generation; only the newest generation may mount.
 */
let bootGen = 0;

function reducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia(REDUCED_MOTION_QUERY).matches;
}

/** The weather line is display:none when collapsed — skip the canvas work too. */
function sidebarCollapsed(): boolean {
  return document.documentElement.dataset.sidebar === 'collapsed';
}

/**
 * Watch documentElement's data-sidebar attribute and fully stop/restart the
 * scene's rAF loop on collapse/expand, instead of rescheduling rAF every
 * frame just to skip the draw. Wired up per startScene call so the observer
 * dies with the scene.
 */
function watchSidebarCollapse(onChange: (collapsed: boolean) => void): () => void {
  if (typeof MutationObserver === 'undefined') return () => {};
  const mo = new MutationObserver(() => onChange(sidebarCollapsed()));
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-sidebar'] });
  return () => mo.disconnect();
}

/* ------------------------------------------------------------------ */
/* reduced-motion follow-up (re-checked, not pinned at boot)            */
/* ------------------------------------------------------------------ */

/** Mount or drop the scene when the reduced-motion preference changes. */
function syncSceneWithMotionPreference(): void {
  if (reducedMotion()) {
    if (stopScene) {
      stopScene();
      stopScene = null;
    }
    return;
  }
  if (stopScene || !active || !hasScene(active.data)) return;
  const sidebar = document.getElementById('app-sidebar');
  if (!sidebar) return;
  mountScene(sidebar, active.data);
}

/**
 * Re-check the preference when the tab becomes visible again or the window
 * regains focus — it can change while the page sits in the background.
 */
function watchMotionPreference(): void {
  const onVisible = () => {
    if (!document.hidden) syncSceneWithMotionPreference();
  };
  const onFocus = () => syncSceneWithMotionPreference();
  document.addEventListener('visibilitychange', onVisible);
  window.addEventListener('focus', onFocus);
  stopMotionWatch = () => {
    document.removeEventListener('visibilitychange', onVisible);
    window.removeEventListener('focus', onFocus);
    stopMotionWatch = null;
  };
}

function teardown(): void {
  if (clockTimer !== null) {
    // armed as either a timeout (waiting for the minute boundary) or an
    // interval; both clear calls are safe in the browser either way
    clearTimeout(clockTimer);
    clearInterval(clockTimer);
    clockTimer = null;
  }
  if (stopMotionWatch) {
    stopMotionWatch();
    stopMotionWatch = null;
  }
  closePopover();
  if (refreshTimer !== null) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
  while (fadingStops.length) fadingStops.pop()!();
  if (stopScene) {
    stopScene();
    stopScene = null;
  }
  if (rafId !== null && typeof window.cancelAnimationFrame === 'function') {
    window.cancelAnimationFrame(rafId);
    rafId = null;
  }
  document.getElementById(LINE_ID)?.remove();
  document.getElementById(CANVAS_ID)?.remove();
  active = null;
}

/* ------------------------------------------------------------------ */
/* weather line + clock                                                */
/* ------------------------------------------------------------------ */

function renderLineText(data: WeatherResponse): string {
  const snap = data.snapshot!;
  const cur = snap.current;
  const time = formatLocalTime(snap.utc_offset_seconds);
  const preview = getWeatherPreview();
  const preset = preview ? SCENE_PRESETS[preview.preset] : null;
  if (preset) {
    // a preview says so on the line, so it never passes for the real sky
    const glyph = glyphSvg(presetGlyph(preview!.preset));
    return `${glyph}<span>${time} &middot; Preview &middot; ${escapeAttr(preset.label)}</span>`;
  }
  const glyph = glyphSvg(lineGlyph(data));
  const temp = formatTemp(cur?.temp, data.units);
  const condition =
    typeof cur?.condition === 'string' && cur.condition.length > 0
      ? escapeAttr(cur.condition)
      : '—';
  return `${glyph}<span>${time} &middot; ${temp} &middot; ${condition}</span>`;
}

function escapeAttr(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function mountLine(data: WeatherResponse): HTMLButtonElement {
  const line = document.createElement('button');
  line.type = 'button';
  line.id = LINE_ID;
  line.className = 'sidebar-weather-line';
  line.setAttribute('aria-label', 'Weather forecast');
  line.setAttribute('aria-haspopup', 'dialog');
  line.innerHTML = renderLineText(data);
  line.addEventListener('click', (e) => {
    e.stopPropagation();
    togglePopover();
  });
  const anchor = document.getElementById('profile-indicator');
  if (anchor?.parentElement) anchor.after(line);
  else document.querySelector('#app-sidebar .sidebar-header')?.appendChild(line);
  return line;
}

/* ------------------------------------------------------------------ */
/* popover                                                             */
/* ------------------------------------------------------------------ */

function buildPopover(data: WeatherResponse): HTMLDivElement {
  const pop = document.createElement('div');
  pop.id = POPOVER_ID;
  pop.setAttribute('role', 'dialog');
  pop.setAttribute('aria-label', `Weather forecast for ${data.location!.name}`);
  for (const [i, row] of data.snapshot!.daily.slice(0, 3).entries()) {
    const el = document.createElement('div');
    el.className = 'sidebar-weather-row';
    const glyph = document.createElement('span');
    glyph.className = 'sidebar-weather-glyph';
    glyph.innerHTML = glyphSvg(glyphForWeatherCode(row.weather_code));
    const day = document.createElement('span');
    day.className = 'sidebar-weather-day';
    day.textContent = dayLabelForDaily(row.date, i);
    const temps = document.createElement('span');
    temps.className = 'sidebar-weather-temps';
    temps.textContent = formatHiLo(row.temp_max, row.temp_min, data.units);
    const cond = document.createElement('span');
    cond.className = 'sidebar-weather-cond';
    cond.textContent = row.condition ?? '—';
    const precip = document.createElement('span');
    precip.className = 'sidebar-weather-precip';
    precip.textContent =
      typeof row.precip_probability === 'number' ? `${Math.round(row.precip_probability)}%` : '—';
    el.append(glyph, day, temps, cond, precip);
    pop.appendChild(el);
  }
  return pop;
}

export interface PopoverGeom {
  lineTop: number;
  lineBottom: number;
  lineRight: number;
  popoverHeight: number;
  popoverWidth: number;
  viewportWidth: number;
  viewportHeight: number;
}

export interface PopoverPlacement {
  top: string;
  bottom: string;
  right: string;
  maxHeight?: string;
}

const POPOVER_MARGIN = 8;

/**
 * Pure placement math: where should the fixed forecast popover sit for a
 * given line rect, popover size and viewport? Empty string = unset, matching
 * how positionPopover writes the styles.
 *
 * Vertical: prefer above the line; flip below when the line sits too close
 * to the viewport top; when neither side fits, take the roomier side and
 * cap the height so the popover stays pinned inside the viewport.
 * Horizontal: right-align to the line's right edge, then clamp so the left
 * edge never crosses the 8px margin (pinning left at 8 when the popover is
 * wider than the viewport minus margins).
 */
export function computePopoverPlacement(g: PopoverGeom): PopoverPlacement {
  const placement: PopoverPlacement = { top: '', bottom: '', right: '' };
  const above = g.lineTop - POPOVER_MARGIN;
  const below = g.viewportHeight - g.lineBottom - POPOVER_MARGIN;
  if (above >= g.popoverHeight) {
    placement.bottom = `${g.viewportHeight - g.lineTop}px`;
  } else if (below >= g.popoverHeight) {
    placement.top = `${g.lineBottom}px`;
  } else if (above >= below) {
    placement.bottom = `${g.viewportHeight - g.lineTop}px`;
    placement.maxHeight = `${Math.max(0, above)}px`;
  } else {
    placement.top = `${g.lineBottom}px`;
    placement.maxHeight = `${Math.max(0, below)}px`;
  }

  let right = Math.max(POPOVER_MARGIN, g.viewportWidth - g.lineRight);
  if (g.viewportWidth - right - g.popoverWidth < POPOVER_MARGIN) {
    right = g.viewportWidth - g.popoverWidth - POPOVER_MARGIN;
  }
  placement.right = `${right}px`;
  return placement;
}

function applyPlacement(pop: HTMLElement, p: PopoverPlacement): void {
  pop.style.top = p.top;
  pop.style.bottom = p.bottom;
  pop.style.right = p.right;
  pop.style.maxHeight = p.maxHeight ?? '';
}

/**
 * Measured popover height. getBoundingClientRect forces a synchronous layout
 * read (never cached), so right after appendChild this returns the real
 * rendered height — including any webfont swap that already landed. When
 * the box has no size yet (jsdom reports 0), estimate from the actual row
 * count instead of a magic number: the popover is exactly its rows.
 */
function popoverHeight(pop: HTMLElement): number {
  const rectH = pop.getBoundingClientRect().height;
  if (rectH > 0) return rectH;
  if (pop.offsetHeight > 0) return pop.offsetHeight;
  return pop.querySelectorAll('.sidebar-weather-row').length * 30 + 20;
}

function popoverWidth(pop: HTMLElement): number {
  const rectW = pop.getBoundingClientRect().width;
  if (rectW > 0) return rectW;
  if (pop.offsetWidth > 0) return pop.offsetWidth;
  return 260; // between the CSS min-width (236) and max-width (300)
}

/**
 * Above the weather line when there is room; below it when the line sits too
 * close to the viewport top (e.g. the profile indicator is hidden); pinned
 * inside the viewport with a capped height when neither side fits.
 * Ends with a self-heal pass: re-reads the real painted box and re-places
 * if any edge escaped the viewport, covering any residual measurement race
 * (late webfont swap, dynamic content).
 */
function positionPopover(): void {
  const line = document.getElementById(LINE_ID);
  const pop = document.getElementById(POPOVER_ID);
  if (!line || !pop) return;
  const r = line.getBoundingClientRect();
  const geom: PopoverGeom = {
    lineTop: r.top,
    lineBottom: r.bottom,
    lineRight: r.right,
    popoverHeight: popoverHeight(pop),
    popoverWidth: popoverWidth(pop),
    viewportWidth: window.innerWidth,
    viewportHeight: window.innerHeight,
  };
  applyPlacement(pop, computePopoverPlacement(geom));

  // Self-heal: the first pass may have measured pre-font-swap; if the
  // painted box now escapes the viewport, re-place with its real size.
  const pr = pop.getBoundingClientRect();
  if (
    pr.top < 0 ||
    pr.bottom > geom.viewportHeight ||
    pr.left < 0 ||
    pr.right > geom.viewportWidth
  ) {
    applyPlacement(
      pop,
      computePopoverPlacement({
        ...geom,
        popoverHeight: Math.max(pr.height, 1),
        popoverWidth: Math.max(pr.width, 1),
      }),
    );
  }
}

function onOutsideDown(e: PointerEvent): void {
  const pop = document.getElementById(POPOVER_ID);
  const line = document.getElementById(LINE_ID);
  if (!pop) return;
  const t = e.target as Node | null;
  if (t && (pop.contains(t) || line?.contains(t))) return;
  closePopover();
}

// core.js declares PAGE_WILL_CHANGE_EVENT as a global lexical const and has
// run by the time the shell bundle loads; the literal fallback keeps this
// module loadable standalone (tests) and matches core.js:3 byte for byte.
declare const PAGE_WILL_CHANGE_EVENT: string | undefined;
const _PAGE_WILL_CHANGE =
  typeof PAGE_WILL_CHANGE_EVENT !== 'undefined'
    ? PAGE_WILL_CHANGE_EVENT
    : 'ss:webui-page-will-change';

function onPopoverKey(e: KeyboardEvent): void {
  if (e.key === 'Escape') closePopover();
}

/**
 * A scroll that starts inside the popover (its capped, scrollable region in
 * the neither-fits branch) is the user reading the forecast, not a dismiss
 * gesture — only outside scrolls close it. Capture phase, like
 * onOutsideDown's guard.
 */
function onPopoverScroll(e: Event): void {
  const pop = document.getElementById(POPOVER_ID);
  const t = e.target as Node | null;
  if (pop && t && pop.contains(t)) return;
  closePopover();
}

/**
 * SPA navigation (keyboard-Enter on nav links, programmatic navigateToPage)
 * fires none of the popover's pointer/keyboard/scroll close paths, so the
 * page-change event closes it explicitly.
 */
function onPageWillChange(): void {
  closePopover();
}

function openPopover(): void {
  if (!active || document.getElementById(POPOVER_ID)) return;
  if (active.data.snapshot!.daily.length === 0) return; // nothing to show: no empty box
  const pop = buildPopover(active.data);
  document.body.appendChild(pop);
  positionPopover();
  // Webfonts load async: a popover measured pre-font-swap grows afterward
  // and clips. Re-run placement once fonts settle AND once the first paint
  // lands — both are idempotent and no-ops if the popover was closed.
  if (typeof document.fonts?.ready?.then === 'function') {
    void document.fonts.ready.then(() => positionPopover());
  }
  if (typeof window.requestAnimationFrame === 'function') {
    window.requestAnimationFrame(() => positionPopover());
  }
  document.addEventListener('pointerdown', onOutsideDown, true);
  document.addEventListener('keydown', onPopoverKey, true);
  window.addEventListener(_PAGE_WILL_CHANGE, onPageWillChange);
  window.addEventListener('resize', positionPopover);
  window.addEventListener('scroll', onPopoverScroll, true);
}

export function closePopover(): void {
  document.getElementById(POPOVER_ID)?.remove();
  document.removeEventListener('pointerdown', onOutsideDown, true);
  document.removeEventListener('keydown', onPopoverKey, true);
  window.removeEventListener(_PAGE_WILL_CHANGE, onPageWillChange);
  window.removeEventListener('resize', positionPopover);
  window.removeEventListener('scroll', onPopoverScroll, true);
}

function togglePopover(): void {
  if (document.getElementById(POPOVER_ID)) closePopover();
  else openPopover();
}

/* ------------------------------------------------------------------ */
/* the scene                                                           */
/* ------------------------------------------------------------------ */

/**
 * mount the scene on a canvas and run it. the drawing lives in
 * weather-scene.ts; this owns the loop: paused while the tab is hidden,
 * fully stopped while the sidebar is collapsed, refit on resize.
 */
function startScene(
  canvas: HTMLCanvasElement,
  sidebar: HTMLElement,
  data: WeatherResponse,
): () => void {
  const rawCtx = canvas.getContext('2d');
  if (!rawCtx) return () => canvas.remove();
  const ctx: CanvasRenderingContext2D = rawCtx;
  const engine = createWeatherScene(ctx, sceneConditions(data));

  function fit(): void {
    const r = sidebar.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, r.width || 280);
    const h = Math.max(1, r.height || 600);
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    engine.resize(w, h, dpr);
  }

  let running = true;
  let last = typeof performance !== 'undefined' ? performance.now() : 0;
  let t = 0;

  function frame(now: number): void {
    if (!running) return;
    const dt = Math.min(0.05, Math.max(0, (now - last) / 1000));
    last = now;
    t += dt;
    engine.frame(dt, t);
    if (typeof window.requestAnimationFrame === 'function') {
      rafId = window.requestAnimationFrame(frame);
    }
  }

  /** cancel the whole loop while collapsed; restart it on expand */
  function setCollapsed(collapsed: boolean): void {
    if (!running || typeof window.requestAnimationFrame !== 'function') return;
    if (collapsed) {
      if (rafId !== null && typeof window.cancelAnimationFrame === 'function') {
        window.cancelAnimationFrame(rafId);
        rafId = null;
      }
    } else if (rafId === null) {
      last = typeof performance !== 'undefined' ? performance.now() : 0;
      rafId = window.requestAnimationFrame(frame);
    }
  }

  function onVisibility(): void {
    if (document.hidden) {
      if (rafId !== null && typeof window.cancelAnimationFrame === 'function') {
        window.cancelAnimationFrame(rafId);
        rafId = null;
      }
    } else if (
      running &&
      rafId === null &&
      !sidebarCollapsed() &&
      typeof window.requestAnimationFrame === 'function'
    ) {
      last = typeof performance !== 'undefined' ? performance.now() : 0;
      rafId = window.requestAnimationFrame(frame);
    }
  }

  fit();
  const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(fit) : null;
  ro?.observe(sidebar);
  document.addEventListener('visibilitychange', onVisibility);
  const stopCollapseWatch = watchSidebarCollapse(setCollapsed);
  engine.frame(0, 0); // one static frame even without rAF
  if (typeof window.requestAnimationFrame === 'function' && !sidebarCollapsed()) {
    rafId = window.requestAnimationFrame(frame);
  }
  // fade in: the next frame, so the transition has a start state
  const reveal = () => canvas.classList.add('is-visible');
  if (typeof window.requestAnimationFrame === 'function') window.requestAnimationFrame(reveal);
  else reveal();

  return () => {
    running = false;
    document.removeEventListener('visibilitychange', onVisibility);
    stopCollapseWatch();
    ro?.disconnect();
    if (rafId !== null && typeof window.cancelAnimationFrame === 'function') {
      window.cancelAnimationFrame(rafId);
      rafId = null;
    }
    canvas.remove();
  };
}

function mountScene(sidebar: HTMLElement, data: WeatherResponse): void {
  const canvas = document.createElement('canvas');
  canvas.id = CANVAS_ID;
  canvas.className = 'sidebar-weather-scene';
  canvas.setAttribute('aria-hidden', 'true');
  sidebar.prepend(canvas);
  stopScene = startScene(canvas, sidebar, data);
}

/* ------------------------------------------------------------------ */
/* entry                                                               */
/* ------------------------------------------------------------------ */

/**
 * Fetch, gate, and mount. Safe to call repeatedly: only the latest
 * in-flight call mounts. Teardown happens after the fetch succeeds (a
 * transient fetch failure never blanks the previous mount), and a stale
 * in-flight boot aborts silently so it can never duplicate or leak a mount.
 */
export async function bootSidebarWeather(): Promise<void> {
  const sidebar = document.getElementById('app-sidebar');
  if (!sidebar) return;
  const gen = ++bootGen;
  let data: WeatherResponse;
  try {
    const resp = await fetch('/api/weather', { headers: { Accept: 'application/json' } });
    if (!resp.ok) return;
    data = (await resp.json()) as WeatherResponse;
  } catch {
    return;
  }
  if (gen !== bootGen) return; // a newer boot is in flight — it owns teardown+mount
  teardown(); // synchronous cleanup of the previous mount; we own the generation
  if (!shouldRenderWeather(data)) return;

  const line = mountLine(data);
  active = { data, line };
  startClockTick();

  if (hasScene(data) && !reducedMotion()) mountScene(sidebar, data);
  watchMotionPreference();
  refreshTimer = setInterval(() => void refreshSidebarWeather(), REFRESH_MS);
}

/**
 * the sky changes under a tab left open: new weather, day turning to
 * night. refetch on a timer (the server caches, this is cheap), update the
 * line in place and crossfade the scene instead of blinking it.
 */
export async function refreshSidebarWeather(): Promise<void> {
  const gen = bootGen;
  let data: WeatherResponse;
  try {
    const resp = await fetch('/api/weather', { headers: { Accept: 'application/json' } });
    if (!resp.ok) return;
    data = (await resp.json()) as WeatherResponse;
  } catch {
    return; // keep what's showing
  }
  if (gen !== bootGen || !active) return;
  if (!shouldRenderWeather(data)) {
    void bootSidebarWeather(); // turned off or lost its location: full teardown path
    return;
  }
  active.data = data;
  active.line.innerHTML = renderLineText(data);
  swapScene();
}

/** fade the current scene out under a fresh one built from active.data */
function swapScene(): void {
  if (!active) return;
  const sidebar = document.getElementById('app-sidebar');
  if (!sidebar || reducedMotion()) return;
  const old = stopScene;
  const oldCanvas = document.getElementById(CANVAS_ID);
  stopScene = null;
  if (old) {
    // let the old one fade out under the new one, then stop it
    oldCanvas?.removeAttribute('id');
    oldCanvas?.classList.remove('is-visible');
    fadingStops.push(old);
    setTimeout(() => {
      const i = fadingStops.indexOf(old);
      if (i >= 0) fadingStops.splice(i, 1);
      old();
    }, SCENE_FADE_MS);
  }
  if (hasScene(active.data)) mountScene(sidebar, active.data);
}

/* ------------------------------------------------------------------ */
/* preview: see any sky on demand (settings > advanced > developer)    */
/* ------------------------------------------------------------------ */

const PREVIEW_KEY = 'soulsync-weather-preview';

export interface WeatherPreview {
  preset: string;
  /** 'YYYY-MM-DD' to pretend it's another day, null for today */
  date: string | null;
}

/**
 * the preview lives in this tab's session only: never saved to the server,
 * gone when the tab closes, so a forgotten test can't stick around
 */
export function getWeatherPreview(): WeatherPreview | null {
  try {
    const raw = sessionStorage.getItem(PREVIEW_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as WeatherPreview;
    return p && typeof p.preset === 'string' && SCENE_PRESETS[p.preset] ? p : null;
  } catch {
    return null;
  }
}

/** pick a sky to preview, or null to go back to the live weather */
export function setWeatherPreview(preview: WeatherPreview | null): void {
  try {
    if (preview && SCENE_PRESETS[preview.preset])
      sessionStorage.setItem(PREVIEW_KEY, JSON.stringify(preview));
    else sessionStorage.removeItem(PREVIEW_KEY);
  } catch {
    // storage blocked: nothing to remember, the live sky stays
  }
  if (!active) return;
  active.line.innerHTML = renderLineText(active.data);
  swapScene();
}

/** true once the weather line is up: a preview paints over it, so it needs it */
export function isSidebarWeatherMounted(): boolean {
  return active !== null;
}

/** every sky the preview can show, for the settings dropdown */
export function weatherPreviewPresets(): Array<{ key: string; label: string }> {
  return Object.entries(SCENE_PRESETS).map(([key, p]) => ({ key, label: p.label }));
}

function previewDate(p: WeatherPreview): Date | null {
  if (!p.date || !/^\d{4}-\d{2}-\d{2}$/.test(p.date)) return null;
  const d = new Date(`${p.date}T12:00:00Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** what the scene paints: the preview when one is on, else the real sky */
function sceneConditions(data: WeatherResponse): SceneConditions {
  const latitude = data.location?.latitude ?? 0;
  const preview = getWeatherPreview();
  if (preview) {
    const c = presetConditions(preview.preset, previewDate(preview), latitude);
    if (c) return c;
  }
  return conditionsFromWeather(data.snapshot!, Date.now(), latitude);
}

function hasScene(data: WeatherResponse): boolean {
  return !!data.scene || !!getWeatherPreview();
}

function presetGlyph(key: string): WeatherGlyphKind {
  const p = SCENE_PRESETS[key];
  if (p.windy) return 'wind';
  const kind = glyphForWeatherCode(p.code);
  if (p.cond.isDay === false) {
    if (kind === 'sun') return 'moon';
    if (kind === 'partly-cloudy') return 'partly-cloudy-night';
  }
  return kind;
}

/**
 * The clock tick aligns to the minute boundary: one setTimeout for the
 * remainder of the current minute, then a steady 60s interval, so the line
 * can never lag a full tick behind the wall clock.
 */
function startClockTick(): void {
  const render = () => {
    if (active) active.line.innerHTML = renderLineText(active.data);
  };
  const msToNextMinute = CLOCK_TICK_MS - (Date.now() % CLOCK_TICK_MS);
  clockTimer = setTimeout(() => {
    render();
    clockTimer = setInterval(render, CLOCK_TICK_MS);
  }, msToNextMinute);
}

/** DOMContentLoaded entry; exported on window through the shell index. */
export function initSidebarWeather(): void {
  if (listenerInstalled) return;
  listenerInstalled = true;
  const start = () => {
    void bootSidebarWeather();
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
}
