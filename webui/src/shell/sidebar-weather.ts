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

export interface WeatherDailyRow {
  date: string;
  temp_max: number | null;
  temp_min: number | null;
  weather_code: number | null;
  condition: string | null;
  precip_probability: number | null;
}

export interface WeatherSnapshot {
  fetched_at: string;
  utc_offset_seconds: number;
  current: {
    temp: number | null;
    weather_code: number | null;
    condition: string | null;
    wind_speed: number;
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

export type WeatherGlyphKind = 'sun' | 'partly-cloudy' | 'cloud' | 'fog' | 'rain' | 'snow' | 'wind';

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
  if (stopScene || !active || !active.data.scene) return;
  const sidebar = document.getElementById('app-sidebar');
  if (!sidebar) return;
  const canvas = document.createElement('canvas');
  canvas.id = CANVAS_ID;
  canvas.setAttribute('aria-hidden', 'true');
  sidebar.prepend(canvas);
  stopScene = startScene(canvas, sidebar, active.data.scene);
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
  const glyph = glyphSvg(glyphForWeatherCode(cur?.weather_code));
  const time = formatLocalTime(snap.utc_offset_seconds);
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
    precip.textContent = `${Math.round(row.precip_probability ?? 0)}%`;
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
/* particle scene                                                      */
/* ------------------------------------------------------------------ */

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  s: number;
  a: number;
  ph: number;
  rot: number;
  vr: number;
  /** depth plane for layered scenes (rain/snow): 0 = far, 1 = near */
  layer?: number;
}

/** Exported for tests: the exact numeric design each scene builds from. */
export const SCENE_SPECS = {
  rain: {
    cap: 64,
    lineWidth: 1, // streak stroke width
    // Two depth planes (atmospheric perspective): far streaks are shorter,
    // fainter and cooler; near ones slightly longer and brighter.
    far: {
      share: 0.55,
      lenMin: 12,
      lenMax: 20,
      alphaMin: 0.05,
      alphaMax: 0.1,
      vxMin: -90,
      vxMax: -60,
      vyMin: 360,
      vyMax: 480,
      tint: '159,195,232', // cooler, dimmer streak
    },
    near: {
      share: 0.45,
      lenMin: 22,
      lenMax: 36,
      alphaMin: 0.1,
      alphaMax: 0.17,
      vxMin: -110,
      vxMax: -80,
      vyMin: 480,
      vyMax: 620,
      tint: '178,208,240', // a touch brighter (atmospheric perspective)
    },
  },
  snow: {
    cap: 56,
    tint: '235,242,252',
    // soft-dot sprite: radial alpha ramp baked once, drawImage'd per flake
    sprite: {
      stops: [
        [0, 1],
        [0.45, 0.55],
        [1, 0],
      ],
    },
    // Dust-mote density: many tiny faint far flakes (cheap hard dots), few
    // larger soft near flakes (bokeh feel via a pre-rendered radial sprite).
    far: {
      share: 0.6,
      sizeMin: 0.5,
      sizeMax: 1.1,
      alphaMin: 0.08,
      alphaMax: 0.16,
      vxMin: -6,
      vxMax: 6,
      vyMin: 10,
      vyMax: 24,
      sway: 4,
    },
    near: {
      share: 0.4,
      sizeMin: 1.6,
      sizeMax: 2.8,
      alphaMin: 0.16,
      alphaMax: 0.3,
      vxMin: -10,
      vxMax: 10,
      vyMin: 22,
      vyMax: 44,
      sway: 9,
    },
  },
  wind: {
    wisps: {
      cap: 8,
      vxMin: 18,
      vxMax: 55,
      vyMin: -6,
      vyMax: 6,
      alphaMin: 0.04,
      alphaMax: 0.08,
      halfWidthMin: 60,
      halfWidthMax: 110,
      tiltMin: -0.35,
      tiltMax: -0.15,
      wanderMin: 8,
      wanderMax: 18,
      wrapMarginY: 40, // a wisp re-enters 40px past the top/bottom edge
      tint: '170,192,222',
      squash: 0.08, // ellipse height as a fraction of the half-width
    },
    // Cool tint wash behind the wind scene: atmosphere without particles.
    // Deliberately a different palette from the wisp tint (cooler, dimmer).
    wash: {
      tint: '130,160,205',
      stops: [
        [0, 0.05],
        [0.7, 0],
        [1, 0],
      ],
    },
    leaves: {
      cap: 5,
      vxMin: 25,
      vxMax: 70,
      vyMin: -12,
      vyMax: 18,
      alphaMin: 0.16,
      alphaMax: 0.28,
      sizeMin: 1.6,
      sizeMax: 2.8,
      aspect: 0.55, // leaf height as a fraction of the width
      vrMax: 1.2,
      tint: '206,158,92',
    },
  },
  clear: {
    cap: 8,
    vxMin: -7,
    vxMax: 7,
    vyMin: -4,
    vyMax: 4,
    sizeMin: 26,
    sizeMax: 60,
    alphaMin: 0.035,
    alphaMax: 0.07,
    layers: {
      count: 3,
      shrinkPerLayer: 0.28,
      sizeMultiplier: 3,
      alphaDecayPerLayer: 0.3,
      tint: '174,189,212',
    },
    // Wrap margins cover the largest drawn ellipse (rx = sizeMax *
    // sizeMultiplier = 180, ry = sizeMax = 60), so a wisp never teleports
    // while any part of it is still on screen.
    wrapMarginX: 190,
    wrapMarginY: 70,
  },
} as const;

export interface SceneParticles {
  drops: Particle[];
  leaves: Particle[];
}

/**
 * Exported for tests: build the particle set for a scene at a given size.
 * startScene uses this on every fit(); the same constants drive the real
 * render, so the design spec tests assert what actually draws.
 */
export function buildSceneParticles(scene: WeatherScene, w: number, h: number): SceneParticles {
  const scale = Math.min(1, Math.max(0.35, Math.min(w / 280, h / 900)));
  const n = (cap: number) => Math.max(4, Math.round(cap * scale));
  const drops: Particle[] = [];
  const leaves: Particle[] = [];
  if (scene === 'rain') {
    const rsp = SCENE_SPECS.rain;
    for (let i = 0; i < n(rsp.cap); i++) {
      const near = Math.random() < rsp.near.share;
      const L = near ? rsp.near : rsp.far;
      drops.push({
        x: rand(-40, w + 40),
        y: rand(0, h),
        vx: rand(L.vxMin, L.vxMax),
        vy: rand(L.vyMin, L.vyMax),
        s: rand(L.lenMin, L.lenMax), // streak length in px
        a: rand(L.alphaMin, L.alphaMax),
        ph: 0,
        rot: 0,
        vr: 0,
        layer: near ? 1 : 0,
      });
    }
  } else if (scene === 'snow') {
    const ssp = SCENE_SPECS.snow;
    for (let i = 0; i < n(ssp.cap); i++) {
      const near = Math.random() < ssp.near.share;
      const L = near ? ssp.near : ssp.far;
      drops.push({
        x: rand(0, w),
        y: rand(0, h),
        vx: rand(L.vxMin, L.vxMax),
        vy: rand(L.vyMin, L.vyMax),
        s: rand(L.sizeMin, L.sizeMax),
        a: rand(L.alphaMin, L.alphaMax),
        ph: rand(0, 6.28),
        rot: 0,
        vr: L.sway, // sway amplitude scales with depth
        layer: near ? 1 : 0,
      });
    }
  } else if (scene === 'wind') {
    const wsp = SCENE_SPECS.wind.wisps;
    // Soft air wisps: wide, faint radial-gradient ellipses drifting slowly —
    // a breeze you sense, not an effect you watch. `s` is the half-width;
    // height derives from it (8%); `rot` is the diagonal tilt; `vr` is the
    // vertical wander amplitude in px/s.
    for (let i = 0; i < n(wsp.cap); i++) {
      drops.push({
        x: rand(-wsp.halfWidthMax, w + wsp.halfWidthMax),
        y: rand(0, h),
        vx: rand(wsp.vxMin, wsp.vxMax),
        vy: rand(wsp.vyMin, wsp.vyMax),
        s: rand(wsp.halfWidthMin, wsp.halfWidthMax),
        a: rand(wsp.alphaMin, wsp.alphaMax),
        ph: rand(0, 6.28),
        rot: rand(wsp.tiltMin, wsp.tiltMax),
        vr: rand(wsp.wanderMin, wsp.wanderMax),
      });
    }
    const lsp = SCENE_SPECS.wind.leaves;
    // Rare, calm leaves: small, faint, drifting with a gentle sine sway and
    // slow rotation — no wild tumbling.
    for (let i = 0; i < n(lsp.cap); i++) {
      leaves.push({
        x: rand(0, w),
        y: rand(0, h),
        vx: rand(lsp.vxMin, lsp.vxMax),
        vy: rand(lsp.vyMin, lsp.vyMax),
        s: rand(lsp.sizeMin, lsp.sizeMax),
        a: rand(lsp.alphaMin, lsp.alphaMax),
        ph: rand(0, 6.28),
        rot: rand(0, 6.28),
        vr: rand(-lsp.vrMax, lsp.vrMax),
      });
    }
  } else {
    const csp = SCENE_SPECS.clear;
    for (let i = 0; i < n(csp.cap); i++) {
      drops.push({
        x: rand(0, w),
        y: rand(0, h),
        vx: rand(csp.vxMin, csp.vxMax),
        vy: rand(csp.vyMin, csp.vyMax),
        s: rand(csp.sizeMin, csp.sizeMax),
        a: rand(csp.alphaMin, csp.alphaMax),
        ph: rand(0, 6.28),
        rot: 0,
        vr: 0,
      });
    }
  }
  return { drops, leaves };
}

function rand(a: number, b: number): number {
  return a + Math.random() * (b - a);
}

/**
 * Faint, restrained particle field per scene. Caps keep it a background
 * texture, never the subject; density scales down on narrow/short sidebars.
 */
function startScene(
  canvas: HTMLCanvasElement,
  sidebar: HTMLElement,
  scene: WeatherScene,
): () => void {
  const rawCtx = canvas.getContext('2d');
  if (!rawCtx) return () => {};
  // non-null binding: TS cannot narrow the nullable capture across closures
  const ctx: CanvasRenderingContext2D = rawCtx;

  let w = 280;
  let h = 600;
  let drops: Particle[] = [];
  let leaves: Particle[] = [];
  let windWash: CanvasGradient | null = null;

  function buildParticles(): void {
    const built = buildSceneParticles(scene, w, h);
    drops = built.drops;
    leaves = built.leaves;
  }

  /** Cool tint wash for the wind scene only: atmosphere without particles. */
  function makeWindWash(): CanvasGradient {
    const wash = SCENE_SPECS.wind.wash;
    const g = ctx.createLinearGradient(0, 0, 0, h);
    for (const [offset, alpha] of wash.stops) {
      g.addColorStop(offset, `rgba(${wash.tint},${alpha})`);
    }
    return g;
  }

  /**
   * Pre-rendered soft dot sprite: one radial gradient baked once, then
   * drawImage'd per near snow flake — cheaper than a gradient per particle
   * per frame, and the soft edge is what sells the bokeh depth feel.
   */
  function makeSoftDot(): HTMLCanvasElement | null {
    const c = document.createElement('canvas');
    c.width = 32;
    c.height = 32;
    const g2 = c.getContext('2d');
    if (!g2) return null;
    const ssp = SCENE_SPECS.snow;
    const grad = g2.createRadialGradient(16, 16, 0, 16, 16, 16);
    for (const [offset, alpha] of ssp.sprite.stops) {
      grad.addColorStop(offset, `rgba(${ssp.tint},${alpha})`);
    }
    g2.fillStyle = grad;
    g2.fillRect(0, 0, 32, 32);
    return c;
  }

  const softDot = scene === 'snow' ? makeSoftDot() : null;

  function fit(): void {
    const r = sidebar.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = Math.max(1, r.width || 280);
    h = Math.max(1, r.height || 600);
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    windWash = scene === 'wind' ? makeWindWash() : null;
    buildParticles();
  }

  function update(dt: number, now: number): void {
    for (const p of drops) {
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      if (scene === 'snow') p.x += Math.sin(now / 1400 + p.ph) * p.vr * dt;
      if (scene === 'rain' && p.y > h + 20) {
        p.y = -20;
        p.x = rand(-40, w + 40);
      }
      // Snow is the only scene on the shared downward wrap: clear has its
      // own margins below (large enough to cover its drawn radii), rain and
      // wind each wrap themselves.
      if (scene === 'snow' && p.y > h + 10) {
        p.y = -10;
        p.x = rand(0, w);
      }
      if (scene === 'wind') {
        // wisps: gentle vertical wander on top of the base drift; wrap once
        // fully off-screen right (x > w + 240), re-entering from the left.
        // The base vy drift would otherwise carry a wisp off the top/bottom
        // edge and leave it invisible for tens of seconds, so wrap y too.
        p.y += Math.sin(now / 1600 + p.ph) * p.vr * dt;
        if (p.x > w + 240) {
          p.x = -p.s - rand(0, 60);
          p.y = rand(0, h);
        }
        const wrapY = SCENE_SPECS.wind.wisps.wrapMarginY;
        if (p.y < -wrapY) p.y = h + wrapY;
        if (p.y > h + wrapY) p.y = -wrapY;
      }
      if (scene === 'clear') {
        // own margins from the spec: the drawn ellipses reach rx = sizeMax *
        // sizeMultiplier, ry = sizeMax, so the wrap must wait until the whole
        // ellipse is off screen — no hard-edged pop-in
        const csp = SCENE_SPECS.clear;
        if (p.x < -csp.wrapMarginX) p.x = w + csp.wrapMarginX;
        if (p.x > w + csp.wrapMarginX) p.x = -csp.wrapMarginX;
        if (p.y < -csp.wrapMarginY) p.y = h + csp.wrapMarginY;
        if (p.y > h + csp.wrapMarginY) p.y = -csp.wrapMarginY;
      }
    }
    for (const p of leaves) {
      p.x += (p.vx + Math.sin(now / 1200 + p.ph) * 10) * dt;
      p.y += (p.vy + Math.cos(now / 1500 + p.ph) * 8) * dt;
      p.rot += p.vr * dt;
      if (p.x > w + 14) {
        p.x = -14;
        p.y = rand(0, h);
      }
      if (p.y > h + 14) p.y = -14;
      if (p.y < -14) p.y = h + 14;
    }
  }

  function draw(): void {
    ctx.clearRect(0, 0, w, h);
    const rsp = SCENE_SPECS.rain;
    const ssp = SCENE_SPECS.snow;
    const wsp = SCENE_SPECS.wind.wisps;
    const lsp = SCENE_SPECS.wind.leaves;
    const csp = SCENE_SPECS.clear;
    if (scene === 'rain') {
      ctx.lineWidth = rsp.lineWidth;
      for (const p of drops) {
        const mag = Math.hypot(p.vx, p.vy) || 1;
        const tx = p.x - (p.vx / mag) * p.s;
        const ty = p.y - (p.vy / mag) * p.s;
        // Soft tail fading into the head: reads as motion blur, not a hard
        // line. Near streaks are a touch brighter (atmospheric perspective).
        const g = ctx.createLinearGradient(p.x, p.y, tx, ty);
        const tint = p.layer === 1 ? rsp.near.tint : rsp.far.tint;
        g.addColorStop(0, `rgba(${tint},${p.a.toFixed(3)})`);
        g.addColorStop(1, `rgba(${tint},0)`);
        ctx.strokeStyle = g;
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(tx, ty);
        ctx.stroke();
      }
    } else if (scene === 'snow') {
      for (const p of drops) {
        if (p.layer === 1 && softDot) {
          // near flakes: soft bokeh dot via the pre-rendered sprite
          ctx.globalAlpha = p.a;
          ctx.drawImage(softDot, p.x - p.s, p.y - p.s, p.s * 2, p.s * 2);
          ctx.globalAlpha = 1;
        } else {
          // far flakes: tiny hard dots are fine at this size
          ctx.fillStyle = `rgba(${ssp.tint},${p.a.toFixed(3)})`;
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.s, 0, 6.2832);
          ctx.fill();
        }
      }
    } else if (scene === 'wind') {
      // cool tint wash first: atmosphere without particles
      if (windWash) {
        ctx.fillStyle = windWash;
        ctx.fillRect(0, 0, w, h);
      }
      for (const p of drops) {
        // wide, faint radial-gradient ellipse, tilted for a diagonal sweep
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.rot);
        ctx.scale(p.s, p.s * wsp.squash);
        const g = ctx.createRadialGradient(0, 0, 0, 0, 0, 1);
        g.addColorStop(0, `rgba(${wsp.tint},${p.a.toFixed(3)})`);
        g.addColorStop(1, `rgba(${wsp.tint},0)`);
        ctx.fillStyle = g;
        ctx.fillRect(-1, -1, 2, 2);
        ctx.restore();
      }
      for (const p of leaves) {
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.rot);
        ctx.fillStyle = `rgba(${lsp.tint},${p.a.toFixed(3)})`;
        ctx.beginPath();
        ctx.ellipse(0, 0, p.s, p.s * lsp.aspect, 0, 0, 6.2832);
        ctx.fill();
        ctx.restore();
      }
    } else {
      // clear: barely-visible drifting cloud wisps, layered soft ellipses
      for (const p of drops) {
        for (let layer = 0; layer < csp.layers.count; layer++) {
          const shrink = 1 - layer * csp.layers.shrinkPerLayer;
          ctx.fillStyle = `rgba(${csp.layers.tint},${(p.a * (1 - layer * csp.layers.alphaDecayPerLayer)).toFixed(4)})`;
          ctx.beginPath();
          ctx.ellipse(
            p.x,
            p.y,
            p.s * csp.layers.sizeMultiplier * shrink,
            p.s * shrink,
            0,
            0,
            6.2832,
          );
          ctx.fill();
        }
      }
    }
  }

  let running = true;
  let last = typeof performance !== 'undefined' ? performance.now() : 0;

  function frame(now: number): void {
    if (!running) return;
    const dt = Math.min(0.05, Math.max(0, (now - last) / 1000));
    last = now;
    update(dt, now);
    draw();
    if (typeof window.requestAnimationFrame === 'function') {
      rafId = window.requestAnimationFrame(frame);
    }
  }

  /** Cancel the whole loop while collapsed; restart it on expand. */
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
  draw(); // one static frame even without rAF
  if (typeof window.requestAnimationFrame === 'function' && !sidebarCollapsed()) {
    rafId = window.requestAnimationFrame(frame);
  }

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

  if (data.scene && !reducedMotion()) {
    const canvas = document.createElement('canvas');
    canvas.id = CANVAS_ID;
    canvas.setAttribute('aria-hidden', 'true');
    sidebar.prepend(canvas);
    stopScene = startScene(canvas, sidebar, data.scene);
  }
  watchMotionPreference();
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
