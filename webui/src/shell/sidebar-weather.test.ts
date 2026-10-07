import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import {
  bootSidebarWeather,
  buildSceneParticles,
  closePopover,
  computePopoverPlacement,
  dayLabelForDaily,
  formatHiLo,
  formatLocalTime,
  formatTemp,
  glyphForWeatherCode,
  glyphSvg,
  SCENE_SPECS,
  shouldRenderWeather,
  type PopoverGeom,
  type WeatherResponse,
} from './sidebar-weather';

const BASE_PAYLOAD: WeatherResponse = {
  success: true,
  enabled: true,
  location: {
    query: '97501',
    name: 'Medford',
    latitude: 42.32,
    longitude: -122.87,
    country_code: 'US',
  },
  units: 'fahrenheit',
  snapshot: {
    fetched_at: '2026-10-06T21:00:00+00:00',
    utc_offset_seconds: -25200,
    current: { temp: 68, weather_code: 1, condition: 'Mainly clear', wind_speed: 8 },
    daily: [
      {
        date: '2026-10-06',
        temp_max: 70,
        temp_min: 52,
        weather_code: 1,
        condition: 'Mainly clear',
        precip_probability: 5,
      },
      {
        date: '2026-10-07',
        temp_max: 71,
        temp_min: 53,
        weather_code: 61,
        condition: 'Rain',
        precip_probability: 80,
      },
      {
        date: '2026-10-08',
        temp_max: 69,
        temp_min: 51,
        weather_code: 0,
        condition: 'Clear sky',
        precip_probability: 0,
      },
    ],
  },
  scene: 'rain',
};

function shellDom() {
  document.body.innerHTML = `
    <div class="sidebar" id="app-sidebar">
      <div class="sidebar-header">
        <div id="profile-indicator" class="profile-indicator" style="display:none;"></div>
      </div>
      <div class="sidebar-scroll"></div>
    </div>`;
}

function mockWeather(payload: WeatherResponse | null) {
  server.use(
    http.get('*/api/weather', () =>
      payload ? HttpResponse.json(payload) : HttpResponse.json({}, { status: 500 }),
    ),
  );
}

// jsdom ships no canvas 2d context; the scene engine bails on a null context,
// so stub a recording fake for the scene tests.
function fake2dContext() {
  const gradient = () => {
    const stops: Array<[number, string]> = [];
    return {
      stops,
      addColorStop: vi.fn((offset: number, color: string) => {
        stops.push([offset, color]);
      }),
    };
  };
  return {
    fillStyle: '',
    strokeStyle: '',
    lineWidth: 1,
    setTransform: vi.fn(),
    clearRect: vi.fn(),
    beginPath: vi.fn(),
    arc: vi.fn(),
    ellipse: vi.fn(),
    fill: vi.fn(),
    fillRect: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    stroke: vi.fn(),
    save: vi.fn(),
    restore: vi.fn(),
    translate: vi.fn(),
    rotate: vi.fn(),
    scale: vi.fn(),
    createRadialGradient: vi.fn(gradient),
    createLinearGradient: vi.fn(gradient),
    drawImage: vi.fn(),
    globalAlpha: 1,
  };
}

// The plain fake only keeps the LAST fillStyle/strokeStyle/globalAlpha, so a
// draw test asserting every particle's visuals needs the full assignment
// history. Wraps the fake with recording setters for the three properties.
function recording2dContext() {
  const ctx = fake2dContext();
  const rec = ctx as unknown as Record<string, unknown>;
  const fills: unknown[] = [];
  const strokes: unknown[] = [];
  const alphas: number[] = [];
  Object.defineProperty(rec, 'fillStyle', {
    configurable: true,
    get: () => (fills.length > 0 ? fills[fills.length - 1] : ''),
    set: (v: unknown) => {
      fills.push(v);
    },
  });
  Object.defineProperty(rec, 'strokeStyle', {
    configurable: true,
    get: () => (strokes.length > 0 ? strokes[strokes.length - 1] : ''),
    set: (v: unknown) => {
      strokes.push(v);
    },
  });
  Object.defineProperty(rec, 'globalAlpha', {
    configurable: true,
    get: () => (alphas.length > 0 ? alphas[alphas.length - 1] : 1),
    set: (v: number) => {
      alphas.push(v);
    },
  });
  return { ctx: rec, base: ctx, fills, strokes, alphas };
}

/** `rgba(<tint>,<alpha>)` -> [tint, alpha]; throws the test on a bad shape. */
function parseRgba(color: string): [string, number] {
  const m = /^rgba\((\d+,\d+,\d+),([\d.]+)\)$/.exec(color);
  expect(m, `expected an rgba() color, got ${color}`).not.toBeNull();
  return [m![1], parseFloat(m![2])];
}

const origMatchMedia = window.matchMedia;
const origRaf = window.requestAnimationFrame.bind(window);
const origCaf = window.cancelAnimationFrame.bind(window);
const origGetContext = HTMLCanvasElement.prototype.getContext.bind(HTMLCanvasElement.prototype);
const origFonts = document.fonts;

let hiddenFlag = false;

beforeEach(() => {
  shellDom();
  mockWeather(BASE_PAYLOAD);
  // default: no reduced motion, no rAF stubbing (tests opt in)
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: () => ({ matches: false, media: '', addEventListener() {}, removeEventListener() {} }),
  });
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hiddenFlag });
  HTMLCanvasElement.prototype.getContext = vi.fn(() => null) as never;
});

afterEach(() => {
  closePopover();
  document.body.innerHTML = '';
  hiddenFlag = false;
  server.resetHandlers();
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: origMatchMedia });
  Object.defineProperty(window, 'requestAnimationFrame', {
    configurable: true,
    value: origRaf,
  });
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, value: origCaf });
  HTMLCanvasElement.prototype.getContext = origGetContext;
  Object.defineProperty(document, 'fonts', { configurable: true, value: origFonts });
  vi.restoreAllMocks();
});

describe('the render gate', () => {
  it('renders nothing unless enabled && location && snapshot', async () => {
    for (const tweak of [{ enabled: false }, { location: null }, { snapshot: null }]) {
      mockWeather({ ...BASE_PAYLOAD, ...tweak });
      await bootSidebarWeather();
      expect(document.getElementById('sidebar-weather-line')).toBeNull();
      shellDom();
    }
  });

  it('renders nothing when the request fails', async () => {
    mockWeather(null);
    await bootSidebarWeather();
    expect(document.getElementById('sidebar-weather-line')).toBeNull();
  });

  it('shouldRenderWeather mirrors the same gate', () => {
    expect(shouldRenderWeather(BASE_PAYLOAD)).toBe(true);
    expect(shouldRenderWeather({ ...BASE_PAYLOAD, enabled: false })).toBe(false);
    expect(shouldRenderWeather({ ...BASE_PAYLOAD, location: null })).toBe(false);
    expect(shouldRenderWeather({ ...BASE_PAYLOAD, snapshot: null })).toBe(false);
    expect(shouldRenderWeather(null)).toBe(false);
  });
});

describe('local time formatting', () => {
  it('formats from utc_offset_seconds, not the viewer timezone', () => {
    // 2026-10-06T21:32:00Z is 2:32 PM in UTC-7 (Medford, PDT)
    const now = Date.UTC(2026, 9, 6, 21, 32, 0);
    expect(formatLocalTime(-25200, now)).toBe('2:32 PM');
    // same instant in UTC+1 is 10:32 PM
    expect(formatLocalTime(3600, now)).toBe('10:32 PM');
  });

  it('handles midnight and noon edges', () => {
    expect(formatLocalTime(0, Date.UTC(2026, 9, 6, 0, 5))).toBe('12:05 AM');
    expect(formatLocalTime(0, Date.UTC(2026, 9, 6, 12, 0))).toBe('12:00 PM');
    expect(formatLocalTime(0, Date.UTC(2026, 9, 6, 23, 59))).toBe('11:59 PM');
  });

  it('rounds temps and picks the unit symbol', () => {
    expect(formatTemp(68.4, 'fahrenheit')).toBe('68°F');
    expect(formatTemp(20.6, 'celsius')).toBe('21°C');
  });
});

describe('scene/glyph mapping', () => {
  it('maps every WMO group to a glyph kind', () => {
    expect(glyphForWeatherCode(0)).toBe('sun');
    expect(glyphForWeatherCode(1)).toBe('sun');
    expect(glyphForWeatherCode(2)).toBe('partly-cloudy');
    expect(glyphForWeatherCode(3)).toBe('cloud');
    expect(glyphForWeatherCode(45)).toBe('fog');
    expect(glyphForWeatherCode(48)).toBe('fog');
    for (const c of [51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99]) {
      expect(glyphForWeatherCode(c)).toBe('rain');
    }
    for (const c of [71, 73, 75, 77, 85, 86]) {
      expect(glyphForWeatherCode(c)).toBe('snow');
    }
    expect(glyphForWeatherCode(12345)).toBe('cloud');
  });

  it('renders an inline svg per glyph kind', () => {
    for (const kind of ['sun', 'partly-cloudy', 'cloud', 'fog', 'rain', 'snow', 'wind'] as const) {
      const svg = glyphSvg(kind);
      expect(svg).toContain('<svg');
      expect(svg).toContain('stroke="currentColor"');
    }
  });

  it('labels the first daily row Today and the rest by weekday', () => {
    expect(dayLabelForDaily('2026-10-06', 0)).toBe('Today');
    expect(dayLabelForDaily('2026-10-07', 1)).toBe('Wed');
    expect(dayLabelForDaily('2026-10-08', 2)).toBe('Thu');
  });
});

describe('the weather line', () => {
  it('mounts directly after #profile-indicator with time, temp and condition', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(Date.UTC(2026, 9, 6, 21, 32, 0));
    await bootSidebarWeather();
    const line = document.getElementById('sidebar-weather-line');
    expect(line).not.toBeNull();
    expect(line!.tagName).toBe('BUTTON');
    expect(document.getElementById('profile-indicator')!.nextElementSibling).toBe(line);
    expect(line!.textContent).toContain('2:32 PM');
    expect(line!.textContent).toContain('68°F');
    expect(line!.textContent).toContain('Mainly clear');
    expect(line!.querySelector('svg')).not.toBeNull();
  });
});

describe('the forecast popover', () => {
  async function openPopover() {
    await bootSidebarWeather();
    const line = document.getElementById('sidebar-weather-line')!;
    // jsdom rects are all zeros; pin one so placement math is observable
    line.getBoundingClientRect = () =>
      ({
        top: 500,
        right: 300,
        bottom: 520,
        left: 100,
        width: 200,
        height: 20,
        x: 100,
        y: 500,
        toJSON() {},
      }) as DOMRect;
    line.click();
    return document.getElementById('sidebar-weather-popover')!;
  }

  it('toggles on line click and shows today + next 2 days', async () => {
    const pop = await openPopover();
    const rows = pop.querySelectorAll('.sidebar-weather-row');
    expect(rows).toHaveLength(3);
    expect(rows[0].textContent).toContain('Today');
    expect(rows[0].textContent).toContain('70°/52°F');
    expect(rows[0].textContent).toContain('Mainly clear');
    expect(rows[0].textContent).toContain('5%');
    expect(rows[1].textContent).toContain('Wed');
    expect(rows[1].textContent).toContain('71°/53°F');
    expect(rows[2].textContent).toContain('Thu');
    // glyphs render per row
    expect(rows[0].querySelector('svg')).not.toBeNull();
    // second click closes
    document.getElementById('sidebar-weather-line')!.click();
    expect(document.getElementById('sidebar-weather-popover')).toBeNull();
  });

  it('sits literally above the weather line (bottom edge touches the line top)', async () => {
    const pop = await openPopover();
    expect(pop.style.bottom).toBe(`${window.innerHeight - 500}px`);
    expect(pop.style.right).toBe(`${window.innerWidth - 300}px`);
  });

  it('closes on Escape', async () => {
    await openPopover();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(document.getElementById('sidebar-weather-popover')).toBeNull();
  });

  it('closes on the page-will-change event (SPA navigation)', async () => {
    await openPopover();
    expect(document.getElementById('sidebar-weather-popover')).not.toBeNull();
    // keyboard-Enter on a nav link / programmatic navigateToPage fires this,
    // not pointerdown/Escape/scroll
    window.dispatchEvent(
      new CustomEvent('ss:webui-page-will-change', {
        detail: { fromPageId: 'dashboard', toPageId: 'library' },
      }),
    );
    expect(document.getElementById('sidebar-weather-popover')).toBeNull();
  });

  it('closes on outside click but not on inside click', async () => {
    const pop = await openPopover();
    pop.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    expect(document.getElementById('sidebar-weather-popover')).not.toBeNull();
    document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    expect(document.getElementById('sidebar-weather-popover')).toBeNull();
  });

  it('ignores scrolls from inside the popover, closes on outside scroll', async () => {
    const pop = await openPopover();
    // a scroll that starts inside the popover (its capped, scrollable region
    // in the neither-fits branch) is reading, not a dismiss gesture
    const inner = pop.querySelector('.sidebar-weather-row')!;
    inner.dispatchEvent(new Event('scroll', { bubbles: false }));
    expect(document.getElementById('sidebar-weather-popover')).not.toBeNull();
    pop.dispatchEvent(new Event('scroll', { bubbles: false }));
    expect(document.getElementById('sidebar-weather-popover')).not.toBeNull();
    // a scroll anywhere else still closes it
    document.body.dispatchEvent(new Event('scroll', { bubbles: false }));
    expect(document.getElementById('sidebar-weather-popover')).toBeNull();
  });
});

describe('the particle scene', () => {
  it('mounts the canvas as the first child of .sidebar', async () => {
    HTMLCanvasElement.prototype.getContext = vi.fn(() => fake2dContext()) as never;
    await bootSidebarWeather();
    const canvas = document.getElementById('sidebar-weather-scene');
    expect(canvas).not.toBeNull();
    expect(document.getElementById('app-sidebar')!.firstElementChild).toBe(canvas);
  });

  it('renders no canvas at all under prefers-reduced-motion', async () => {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: (q: string) => ({
        matches: q === '(prefers-reduced-motion: reduce)',
        media: q,
        addEventListener() {},
        removeEventListener() {},
      }),
    });
    HTMLCanvasElement.prototype.getContext = vi.fn(() => fake2dContext()) as never;
    await bootSidebarWeather();
    // the line still renders — only the motion is gated
    expect(document.getElementById('sidebar-weather-line')).not.toBeNull();
    expect(document.getElementById('sidebar-weather-scene')).toBeNull();
  });

  it('pauses the rAF loop when the tab hides and resumes when visible', async () => {
    HTMLCanvasElement.prototype.getContext = vi.fn(() => fake2dContext()) as never;
    let nextId = 1;
    const raf = vi.fn(() => nextId++);
    const caf = vi.fn();
    Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, value: raf });
    Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, value: caf });

    await bootSidebarWeather();
    expect(raf).toHaveBeenCalled();
    const scheduled = raf.mock.results[0].value as number;

    hiddenFlag = true;
    document.dispatchEvent(new Event('visibilitychange'));
    expect(caf).toHaveBeenCalledWith(scheduled);

    hiddenFlag = false;
    document.dispatchEvent(new Event('visibilitychange'));
    expect(raf.mock.calls.length).toBeGreaterThan(1);
  });

  it('renders nothing at all when the snapshot is null (the backend never emits a null scene with a snapshot)', async () => {
    HTMLCanvasElement.prototype.getContext = vi.fn(() => fake2dContext()) as never;
    mockWeather({ ...BASE_PAYLOAD, snapshot: null, scene: null });
    await bootSidebarWeather();
    expect(document.getElementById('sidebar-weather-line')).toBeNull();
    expect(document.getElementById('sidebar-weather-scene')).toBeNull();
  });
});

describe('the wind scene redesign', () => {
  const W = 280;
  const H = 900; // full density: no downscaling

  it('design spec: wisps are few, slow and faint', () => {
    const wsp = SCENE_SPECS.wind.wisps;
    expect(wsp.cap).toBeLessThanOrEqual(8);
    expect(wsp.vxMax).toBeLessThanOrEqual(60);
    expect(wsp.alphaMax).toBeLessThanOrEqual(0.09);
    expect(wsp.halfWidthMin * 2).toBeGreaterThanOrEqual(120);
    expect(wsp.halfWidthMax * 2).toBeLessThanOrEqual(220);
  });

  it('design spec: leaves are rare, small, faint and slow-turning', () => {
    const lsp = SCENE_SPECS.wind.leaves;
    expect(lsp.cap).toBeLessThanOrEqual(5);
    expect(lsp.alphaMax).toBeLessThanOrEqual(0.3);
    expect(lsp.vrMax).toBeLessThanOrEqual(1.5);
    expect(lsp.sizeMax).toBeLessThanOrEqual(2.8);
    expect(lsp.vxMax).toBeLessThanOrEqual(70);
  });

  it('built particles honor the exact spec bounds (no slack)', () => {
    const wsp = SCENE_SPECS.wind.wisps;
    const lsp = SCENE_SPECS.wind.leaves;
    for (let i = 0; i < 50; i++) {
      const { drops, leaves } = buildSceneParticles('wind', W, H);
      expect(drops.length).toBeLessThanOrEqual(wsp.cap);
      expect(leaves.length).toBeLessThanOrEqual(lsp.cap);
      for (const p of drops) {
        expect(p.x).toBeGreaterThanOrEqual(-wsp.halfWidthMax);
        expect(p.x).toBeLessThanOrEqual(W + wsp.halfWidthMax);
        expect(p.y).toBeGreaterThanOrEqual(0);
        expect(p.y).toBeLessThanOrEqual(H);
        expect(p.vx).toBeGreaterThanOrEqual(wsp.vxMin);
        expect(p.vx).toBeLessThanOrEqual(wsp.vxMax);
        expect(p.vy).toBeGreaterThanOrEqual(wsp.vyMin);
        expect(p.vy).toBeLessThanOrEqual(wsp.vyMax);
        expect(p.s).toBeGreaterThanOrEqual(wsp.halfWidthMin);
        expect(p.s).toBeLessThanOrEqual(wsp.halfWidthMax);
        expect(p.a).toBeGreaterThanOrEqual(wsp.alphaMin);
        expect(p.a).toBeLessThanOrEqual(wsp.alphaMax);
        expect(p.rot).toBeGreaterThanOrEqual(wsp.tiltMin);
        expect(p.rot).toBeLessThanOrEqual(wsp.tiltMax);
        expect(p.vr).toBeGreaterThanOrEqual(wsp.wanderMin);
        expect(p.vr).toBeLessThanOrEqual(wsp.wanderMax);
      }
      for (const p of leaves) {
        expect(p.x).toBeGreaterThanOrEqual(0);
        expect(p.x).toBeLessThanOrEqual(W);
        expect(p.y).toBeGreaterThanOrEqual(0);
        expect(p.y).toBeLessThanOrEqual(H);
        expect(p.vx).toBeGreaterThanOrEqual(lsp.vxMin);
        expect(p.vx).toBeLessThanOrEqual(lsp.vxMax);
        expect(p.vy).toBeGreaterThanOrEqual(lsp.vyMin);
        expect(p.vy).toBeLessThanOrEqual(lsp.vyMax);
        expect(p.s).toBeGreaterThanOrEqual(lsp.sizeMin);
        expect(p.s).toBeLessThanOrEqual(lsp.sizeMax);
        expect(p.a).toBeGreaterThanOrEqual(lsp.alphaMin);
        expect(p.a).toBeLessThanOrEqual(lsp.alphaMax);
        expect(Math.abs(p.vr)).toBeLessThanOrEqual(lsp.vrMax);
      }
    }
  });

  it('draws the wind scene (wash + wisps + leaves) with the spec visuals', async () => {
    const rec = recording2dContext();
    HTMLCanvasElement.prototype.getContext = vi.fn(() => rec.ctx) as never;
    mockWeather({ ...BASE_PAYLOAD, scene: 'wind' });
    await bootSidebarWeather();
    // the static first frame ran: wash gradient + wisp radial gradients used
    const ctx = (HTMLCanvasElement.prototype.getContext as ReturnType<typeof vi.fn>).mock.results[0]
      .value;
    const wsp = SCENE_SPECS.wind.wisps;
    const lsp = SCENE_SPECS.wind.leaves;
    expect(ctx.createLinearGradient).toHaveBeenCalled(); // the cool tint wash
    expect(ctx.createRadialGradient).toHaveBeenCalled(); // soft wisp ellipses

    // every wisp gradient carries the spec tint, fading from the particle's
    // own alpha to transparent
    const wispGrads = ctx.createRadialGradient.mock.results.map((r: { value: unknown }) => r.value);
    expect(wispGrads.length).toBeGreaterThan(0);
    for (const g of wispGrads) {
      const [tint, alpha] = parseRgba(g.stops[0][1]);
      expect(tint).toBe(wsp.tint);
      expect(alpha).toBeGreaterThanOrEqual(wsp.alphaMin);
      expect(alpha).toBeLessThanOrEqual(wsp.alphaMax);
      expect(g.stops[1][1]).toBe(`rgba(${wsp.tint},0)`);
    }
    // the cool tint wash is the only linear gradient in the wind scene: its
    // stops pin the spec values, a deliberately distinct palette from the
    // wisp tint
    const wash = SCENE_SPECS.wind.wash;
    const washGrads = ctx.createLinearGradient.mock.results.map((r: { value: unknown }) => r.value);
    expect(washGrads.length).toBeGreaterThan(0);
    for (const g of washGrads) {
      expect(g.stops).toEqual(
        wash.stops.map(([offset, alpha]) => [offset, `rgba(${wash.tint},${alpha})`]),
      );
    }
    expect(wash.tint).not.toBe(wsp.tint);
    // the wisp squash factor is the spec value, not a draw() literal
    const scales = ctx.scale.mock.calls;
    expect(scales.length).toBeGreaterThan(0);
    for (const [sx, sy] of scales) {
      expect(sy).toBeCloseTo(sx * wsp.squash, 10);
    }

    // leaves render as tinted ellipses with per-particle alpha
    expect(ctx.ellipse).toHaveBeenCalled();
    const leafFills = rec.fills.filter(
      (f): f is string => typeof f === 'string' && f.startsWith(`rgba(${lsp.tint},`),
    );
    expect(leafFills.length).toBeGreaterThan(0);
    for (const fill of leafFills) {
      const alpha = parseRgba(fill)[1];
      expect(alpha).toBeGreaterThanOrEqual(lsp.alphaMin);
      expect(alpha).toBeLessThanOrEqual(lsp.alphaMax);
    }
    // the leaf ry/rx ratio is the spec's aspect, not a draw() literal —
    // in the wind scene only leaves use ctx.ellipse
    const leafEllipses = ctx.ellipse.mock.calls as unknown[][];
    expect(leafEllipses.length).toBeGreaterThan(0);
    for (const call of leafEllipses) {
      const rx = call[2] as number;
      const ry = call[3] as number;
      expect(ry).toBeCloseTo(rx * lsp.aspect, 10);
    }
    expect(document.getElementById('sidebar-weather-scene')).not.toBeNull();
  });

  it('keeps wisps within vertical bounds over simulated time', async () => {
    const rec = recording2dContext();
    HTMLCanvasElement.prototype.getContext = vi.fn(() => rec.ctx) as never;
    const frames: FrameRequestCallback[] = [];
    let nextId = 1;
    Object.defineProperty(window, 'requestAnimationFrame', {
      configurable: true,
      value: (cb: FrameRequestCallback): number => {
        frames.push(cb);
        return nextId++;
      },
    });
    // pin the build: every rand() ~0, so wisps start top-left with the
    // slowest drift and the strongest upward pull — the shape that used to
    // leak off the top edge and stay invisible until the next x-wrap
    const randSpy = vi.spyOn(Math, 'random').mockReturnValue(0.001);
    mockWeather({ ...BASE_PAYLOAD, scene: 'wind' });
    await bootSidebarWeather();
    randSpy.mockRestore();
    const ctx = (HTMLCanvasElement.prototype.getContext as ReturnType<typeof vi.fn>).mock.results[0]
      .value;

    // pump ~35s of frames at the clamped 50ms step; draw() records every
    // wisp/leaf position through ctx.translate
    let t = performance.now();
    for (let i = 0; i < 700; i++) {
      frames[frames.length - 1]!(t);
      t += 50;
    }
    const ys = ctx.translate.mock.calls.map((c: unknown[]) => c[1] as number);
    expect(ys.length).toBeGreaterThan(0);
    // jsdom rects are all zeros, so fit() falls back to h = 600
    const wsp = SCENE_SPECS.wind.wisps;
    for (const y of ys) {
      expect(y).toBeGreaterThanOrEqual(-wsp.wrapMarginY);
      expect(y).toBeLessThanOrEqual(600 + wsp.wrapMarginY);
    }
  });
});

describe('the rain scene polish', () => {
  const W = 280;
  const H = 900;

  it('design spec: thin, faint, restrained — two depth planes', () => {
    const rsp = SCENE_SPECS.rain;
    expect(rsp.cap).toBeLessThanOrEqual(64);
    expect(rsp.far.alphaMax).toBeLessThanOrEqual(0.1);
    expect(rsp.near.alphaMax).toBeLessThanOrEqual(0.17);
    expect(rsp.near.lenMax).toBeLessThanOrEqual(36);
    expect(rsp.far.share + rsp.near.share).toBeCloseTo(1);
  });

  it('built particles honor the spec across many samples', () => {
    const rsp = SCENE_SPECS.rain;
    for (let i = 0; i < 50; i++) {
      const { drops } = buildSceneParticles('rain', W, H);
      expect(drops.length).toBeLessThanOrEqual(rsp.cap);
      let near = 0;
      for (const p of drops) {
        // bounds come from the particle's own depth plane, like the build
        const L = p.layer === 1 ? rsp.near : rsp.far;
        expect(p.s).toBeGreaterThanOrEqual(L.lenMin); // streak length
        expect(p.s).toBeLessThanOrEqual(L.lenMax);
        expect(p.a).toBeGreaterThanOrEqual(L.alphaMin);
        expect(p.a).toBeLessThanOrEqual(L.alphaMax);
        expect(p.vx).toBeGreaterThanOrEqual(L.vxMin);
        expect(p.vx).toBeLessThanOrEqual(L.vxMax);
        expect(p.vy).toBeGreaterThanOrEqual(L.vyMin);
        expect(p.vy).toBeLessThanOrEqual(L.vyMax);
        expect(p.layer === 0 || p.layer === 1).toBe(true);
        if (p.layer === 1) near++;
      }
      // both depth planes are actually populated — a share of 0 or 1 would
      // silently collapse the two-plane design
      expect(near).toBeGreaterThan(0);
      expect(near).toBeLessThan(drops.length);
    }
  });

  it('draws soft gradient streaks with the spec tints and per-plane alphas', async () => {
    const rec = recording2dContext();
    HTMLCanvasElement.prototype.getContext = vi.fn(() => rec.ctx) as never;
    mockWeather({ ...BASE_PAYLOAD, scene: 'rain' }); // BASE_PAYLOAD is rain
    await bootSidebarWeather();
    const ctx = (HTMLCanvasElement.prototype.getContext as ReturnType<typeof vi.fn>).mock.results[0]
      .value;
    const rsp = SCENE_SPECS.rain;
    expect(ctx.createLinearGradient).toHaveBeenCalled(); // soft fading tails
    expect(ctx.lineWidth).toBe(rsp.lineWidth); // the spec'd stroke width, not a draw() literal
    // every streak gradient carries its plane's spec tint, fading from the
    // particle's own alpha to transparent
    const streakGrads = ctx.createLinearGradient.mock.results.map(
      (r: { value: unknown }) => r.value,
    );
    expect(streakGrads.length).toBeGreaterThan(0);
    for (const g of streakGrads) {
      const [tint, alpha] = parseRgba(g.stops[0][1]);
      const plane = tint === rsp.near.tint ? rsp.near : rsp.far;
      expect(tint === rsp.near.tint || tint === rsp.far.tint).toBe(true);
      expect(alpha).toBeGreaterThanOrEqual(plane.alphaMin);
      expect(alpha).toBeLessThanOrEqual(plane.alphaMax);
      expect(g.stops[1][1]).toBe(`rgba(${tint},0)`);
    }
  });
});

describe('the snow scene polish', () => {
  const W = 280;
  const H = 900;

  it('design spec: dust-mote density, two depth planes', () => {
    const ssp = SCENE_SPECS.snow;
    expect(ssp.cap).toBeLessThanOrEqual(56);
    expect(ssp.far.alphaMax).toBeLessThanOrEqual(0.16);
    expect(ssp.near.alphaMax).toBeLessThanOrEqual(0.3);
    expect(ssp.near.sizeMax).toBeLessThanOrEqual(2.8);
    expect(ssp.far.share + ssp.near.share).toBeCloseTo(1);
  });

  it('built particles honor the spec across many samples', () => {
    const ssp = SCENE_SPECS.snow;
    for (let i = 0; i < 50; i++) {
      const { drops } = buildSceneParticles('snow', W, H);
      expect(drops.length).toBeLessThanOrEqual(ssp.cap);
      let near = 0;
      for (const p of drops) {
        // bounds come from the particle's own depth plane, like the build
        const L = p.layer === 1 ? ssp.near : ssp.far;
        expect(p.s).toBeGreaterThanOrEqual(L.sizeMin);
        expect(p.s).toBeLessThanOrEqual(L.sizeMax);
        expect(p.a).toBeGreaterThanOrEqual(L.alphaMin);
        expect(p.a).toBeLessThanOrEqual(L.alphaMax);
        expect(p.vx).toBeGreaterThanOrEqual(L.vxMin);
        expect(p.vx).toBeLessThanOrEqual(L.vxMax);
        expect(p.vy).toBeGreaterThanOrEqual(L.vyMin);
        expect(p.vy).toBeLessThanOrEqual(L.vyMax);
        expect(p.vr).toBe(L.sway); // sway amplitude scales with depth
        expect(p.layer === 0 || p.layer === 1).toBe(true);
        if (p.layer === 1) near++;
      }
      // both planes are actually populated over 50 samples
      expect(near).toBeGreaterThan(0);
      expect(near).toBeLessThan(drops.length);
    }
  });

  it('falls back to hard dots when the soft sprite cannot be baked', async () => {
    // startScene calls getContext twice: first for the scene canvas, then
    // inside makeSoftDot to bake the sprite. The bake gets null, so no
    // sprite is built and every flake (near and far) takes the hard-dot path.
    const rec = recording2dContext();
    HTMLCanvasElement.prototype.getContext = vi
      .fn()
      .mockReturnValueOnce(rec.ctx)
      .mockReturnValue(null) as never;
    mockWeather({ ...BASE_PAYLOAD, scene: 'snow' });
    await bootSidebarWeather();
    const ctx = (HTMLCanvasElement.prototype.getContext as ReturnType<typeof vi.fn>).mock.results[0]
      .value;
    const ssp = SCENE_SPECS.snow;
    expect(ctx.drawImage).not.toHaveBeenCalled(); // no sprite to stamp
    expect(ctx.arc).toHaveBeenCalled(); // the hard-dot fallback path draws
    // hard dots keep the spec tint with each particle's own alpha
    expect(rec.fills.length).toBeGreaterThan(0);
    for (const fill of rec.fills) {
      const [tint, alpha] = parseRgba(fill as string);
      expect(tint).toBe(ssp.tint);
      expect(alpha).toBeGreaterThanOrEqual(ssp.far.alphaMin);
      expect(alpha).toBeLessThanOrEqual(ssp.near.alphaMax);
    }
    expect(document.getElementById('sidebar-weather-scene')).not.toBeNull();
  });

  it('stamps the pre-baked soft sprite for near flakes when the bake succeeds', async () => {
    const scene = recording2dContext();
    const sprite = recording2dContext();
    HTMLCanvasElement.prototype.getContext = vi
      .fn()
      .mockReturnValueOnce(scene.ctx)
      .mockReturnValue(sprite.ctx) as never;
    mockWeather({ ...BASE_PAYLOAD, scene: 'snow' });
    await bootSidebarWeather();
    const ctx = (HTMLCanvasElement.prototype.getContext as ReturnType<typeof vi.fn>).mock.results[0]
      .value;
    const ssp = SCENE_SPECS.snow;
    // the sprite bakes exactly once, with the spec's alpha ramp over the spec tint
    const bakeGrads = sprite.base.createRadialGradient.mock.results;
    expect(bakeGrads).toHaveLength(1);
    expect(bakeGrads[0].value.stops).toEqual(
      ssp.sprite.stops.map(([offset, alpha]) => [offset, `rgba(${ssp.tint},${alpha})`]),
    );
    // near flakes stamp the sprite at their own alpha; far flakes stay hard dots
    expect(ctx.drawImage).toHaveBeenCalled();
    expect(ctx.arc).toHaveBeenCalled();
    expect(scene.alphas.length).toBeGreaterThan(0);
    for (const a of scene.alphas) {
      const nearFlake = a >= ssp.near.alphaMin && a <= ssp.near.alphaMax;
      expect(nearFlake || a === 1, `unexpected globalAlpha ${a}`).toBe(true);
    }
    for (const fill of scene.fills) {
      const [tint, alpha] = parseRgba(fill as string);
      expect(tint).toBe(ssp.tint);
      expect(alpha).toBeGreaterThanOrEqual(ssp.far.alphaMin);
      expect(alpha).toBeLessThanOrEqual(ssp.far.alphaMax);
    }
    expect(document.getElementById('sidebar-weather-scene')).not.toBeNull();
  });
});

describe('the clear scene (unchanged)', () => {
  const W = 280;
  const H = 600; // jsdom rects are all zeros, so fit() falls back to 280x600

  it('stays barely-there: few ultra-faint wisps', () => {
    const csp = SCENE_SPECS.clear;
    expect(csp.cap).toBeLessThanOrEqual(8);
    for (let i = 0; i < 50; i++) {
      const { drops } = buildSceneParticles('clear', W, H);
      expect(drops.length).toBeLessThanOrEqual(csp.cap);
      for (const p of drops) {
        expect(p.s).toBeGreaterThanOrEqual(csp.sizeMin);
        expect(p.s).toBeLessThanOrEqual(csp.sizeMax);
        expect(p.a).toBeGreaterThanOrEqual(csp.alphaMin);
        expect(p.a).toBeLessThanOrEqual(csp.alphaMax);
        expect(p.vx).toBeGreaterThanOrEqual(csp.vxMin);
        expect(p.vx).toBeLessThanOrEqual(csp.vxMax);
        expect(p.vy).toBeGreaterThanOrEqual(csp.vyMin);
        expect(p.vy).toBeLessThanOrEqual(csp.vyMax);
      }
    }
  });

  it('draws layered soft ellipses from the spec: tint, per-layer alphas, radii ratio', async () => {
    const rec = recording2dContext();
    HTMLCanvasElement.prototype.getContext = vi.fn(() => rec.ctx) as never;
    mockWeather({ ...BASE_PAYLOAD, scene: 'clear' });
    await bootSidebarWeather();
    const ctx = (HTMLCanvasElement.prototype.getContext as ReturnType<typeof vi.fn>).mock.results[0]
      .value;
    const csp = SCENE_SPECS.clear;
    // the boot's static first frame ran: layers.count ellipses per wisp
    const ellipses = ctx.ellipse.mock.calls as unknown[][];
    expect(ellipses.length).toBeGreaterThan(0);
    expect(ellipses.length % csp.layers.count).toBe(0);
    for (let i = 0; i < ellipses.length; i += csp.layers.count) {
      const radii: Array<[number, number]> = [];
      for (let l = 0; l < csp.layers.count; l++) {
        const [x, y, rx, ry] = ellipses[i + l] as unknown as [number, number, number, number];
        // the same center across the wisp's layers
        expect(x).toBe(ellipses[i][0]);
        expect(y).toBe(ellipses[i][1]);
        // the rx/ry ratio is the spec's size multiplier, not a draw() literal
        expect(ry as number).toBeCloseTo((rx as number) / csp.layers.sizeMultiplier, 10);
        // the largest layer pins the radius the wrap margins are derived from
        expect(rx as number).toBeLessThanOrEqual(csp.sizeMax * csp.layers.sizeMultiplier);
        radii.push([rx as number, ry as number]);
      }
      // layers shrink inward per the spec's decay
      for (let l = 1; l < csp.layers.count; l++) {
        expect(radii[l][0] / radii[0][0]).toBeCloseTo(1 - l * csp.layers.shrinkPerLayer, 10);
      }
    }
    // every layer fill carries the spec tint, fading per the alpha decay
    for (const fill of rec.fills) {
      const [tint, alpha] = parseRgba(fill as string);
      expect(tint).toBe(csp.layers.tint);
      expect(alpha).toBeGreaterThanOrEqual(
        csp.alphaMin * (1 - (csp.layers.count - 1) * csp.layers.alphaDecayPerLayer),
      );
      expect(alpha).toBeLessThanOrEqual(csp.alphaMax);
    }
  });

  it('wraps wisps only once fully off-screen — no visible pop-in', async () => {
    const rec = recording2dContext();
    HTMLCanvasElement.prototype.getContext = vi.fn(() => rec.ctx) as never;
    const frames: FrameRequestCallback[] = [];
    let nextId = 1;
    Object.defineProperty(window, 'requestAnimationFrame', {
      configurable: true,
      value: (cb: FrameRequestCallback): number => {
        frames.push(cb);
        return nextId++;
      },
    });
    // Pin the build with a per-particle rand() sequence: even particles
    // drift horizontally at near-max speed from mid-screen height (so the
    // x-wrap is exercised while the wisp is vertically visible), odd ones
    // drift downward at near-max speed. The idle axis gets exactly 0, so
    // teleports isolate to one axis per wisp.
    let call = 0;
    const randSpy = vi.spyOn(Math, 'random').mockImplementation(() => {
      const slot = call % 7; // x, y, vx, vy, s, a, ph per particle
      const xDrifter = Math.floor(call / 7) % 2 === 0;
      call++;
      if (slot === 0) return 0.99; // x: near the right edge
      if (slot === 1) return xDrifter ? 0.5 : 0.99; // y: mid-screen vs near bottom
      if (slot === 2) return xDrifter ? 0.999 : 0.5; // vx: near-max vs 0
      if (slot === 3) return xDrifter ? 0.5 : 0.999; // vy: 0 vs near-max
      if (slot === 4) return 0.999; // s: max radius, the worst case
      return 0.5; // a, ph
    });
    mockWeather({ ...BASE_PAYLOAD, scene: 'clear' });
    await bootSidebarWeather();
    randSpy.mockRestore();
    const ctx = (HTMLCanvasElement.prototype.getContext as ReturnType<typeof vi.fn>).mock.results[0]
      .value;
    const csp = SCENE_SPECS.clear;

    // pump ~35s of frames at the clamped 50ms step; the boot's static draw
    // is frame 0, every later frame adds layers.count ellipses per wisp
    let t = performance.now();
    for (let i = 0; i < 700; i++) {
      frames[frames.length - 1]!(t);
      t += 50;
    }
    const ellipses = ctx.ellipse.mock.calls as unknown[][];
    const perWisp = ellipses.length / csp.layers.count;
    expect(Number.isInteger(perWisp)).toBe(true);
    const wisps = perWisp / (1 + 700);
    expect(Number.isInteger(wisps) && wisps > 0).toBe(true);
    // frame f, wisp k: the first of its layers carries the center
    const centers: Array<Array<[number, number]>> = [];
    for (let f = 0; f <= 700; f++) {
      const frame: Array<[number, number]> = [];
      for (let k = 0; k < wisps; k++) {
        const e = ellipses[f * wisps * csp.layers.count + k * csp.layers.count];
        frame.push([e[0] as number, e[1] as number]);
      }
      centers.push(frame);
    }
    // A teleport is a frame-to-frame jump far beyond the max per-frame drift
    // (|v| <= 7px/s * 0.05s). Each axis is checked on its own: the x-wrap
    // must only fire while the largest ellipse (rx <= sizeMax *
    // sizeMultiplier) is fully past the left/right edge, and likewise for y
    // (ry <= sizeMax) — otherwise the hard-edged ellipse visibly pops.
    const maxRx = csp.sizeMax * csp.layers.sizeMultiplier;
    const maxRy = csp.sizeMax;
    let xTeleports = 0;
    let yTeleports = 0;
    for (let k = 0; k < wisps; k++) {
      for (let f = 1; f < centers.length; f++) {
        const [px, py] = centers[f - 1][k];
        const [x, y] = centers[f][k];
        if (Math.abs(x - px) > 2) {
          xTeleports++;
          const off = px <= -maxRx || px >= W + maxRx;
          expect(off, `wisp ${k} x-wrapped while visible at (${px},${py})`).toBe(true);
        }
        if (Math.abs(y - py) > 2) {
          yTeleports++;
          const off = py <= -maxRy || py >= H + maxRy;
          expect(off, `wisp ${k} y-wrapped while visible at (${px},${py})`).toBe(true);
        }
      }
    }
    // both axes wrap inside the window — the test is not vacuous
    expect(xTeleports).toBeGreaterThan(0);
    expect(yTeleports).toBeGreaterThan(0);
  });
});

describe('defensive rendering of missing values', () => {
  const NULLABLE_PAYLOAD: WeatherResponse = {
    ...BASE_PAYLOAD,
    snapshot: {
      ...BASE_PAYLOAD.snapshot!,
      current: { temp: null, weather_code: 1, condition: null, wind_speed: 8 },
      daily: [
        {
          date: '2026-10-06',
          temp_max: null,
          temp_min: null,
          weather_code: 1,
          condition: null,
          precip_probability: 5,
        },
        ...BASE_PAYLOAD.snapshot!.daily.slice(1),
      ],
    },
  };

  function openPopoverWithLine() {
    const line = document.getElementById('sidebar-weather-line')!;
    line.getBoundingClientRect = () =>
      ({
        top: 500,
        right: 300,
        bottom: 520,
        left: 100,
        width: 200,
        height: 20,
        x: 100,
        y: 500,
        toJSON() {},
      }) as DOMRect;
    line.click();
    return document.getElementById('sidebar-weather-popover')!;
  }

  it('renders em dashes instead of 0° for null temps', async () => {
    mockWeather(NULLABLE_PAYLOAD);
    await bootSidebarWeather();
    const line = document.getElementById('sidebar-weather-line')!;
    expect(line.textContent).toContain('—');
    expect(line.textContent).not.toContain('0°F');
    expect(line.textContent).not.toContain('NaN');

    const pop = openPopoverWithLine();
    const row = pop.querySelector('.sidebar-weather-row')!;
    expect(row.querySelector('.sidebar-weather-temps')!.textContent).toBe('—');
    expect(row.querySelector('.sidebar-weather-cond')!.textContent).toBe('—');
  });

  it('formatTemp/formatHiLo return dashes for null, undefined, NaN', () => {
    expect(formatTemp(null, 'fahrenheit')).toBe('—');
    expect(formatTemp(undefined, 'celsius')).toBe('—');
    expect(formatTemp(NaN, 'fahrenheit')).toBe('—');
    expect(formatTemp(68.4, 'fahrenheit')).toBe('68°F');
    expect(formatHiLo(null, null, 'fahrenheit')).toBe('—');
    expect(formatHiLo(null, 52, 'fahrenheit')).toBe('—/52°F');
    expect(formatHiLo(70, null, 'celsius')).toBe('70°/—C');
    expect(formatHiLo(70.4, 52.2, 'fahrenheit')).toBe('70°/52°F');
  });

  it('a null weather_code falls back to the neutral cloud glyph', () => {
    expect(glyphForWeatherCode(null)).toBe('cloud');
    expect(glyphForWeatherCode(undefined)).toBe('cloud');
  });
});

describe('empty forecast', () => {
  it('does not open the popover when there are no daily rows', async () => {
    mockWeather({ ...BASE_PAYLOAD, snapshot: { ...BASE_PAYLOAD.snapshot!, daily: [] } });
    await bootSidebarWeather();
    // the line still renders (the current conditions exist); the popover doesn't
    expect(document.getElementById('sidebar-weather-line')).not.toBeNull();
    document.getElementById('sidebar-weather-line')!.click();
    expect(document.getElementById('sidebar-weather-popover')).toBeNull();
  });
});

describe('popover placement', () => {
  function pinLineRect(top: number, bottom: number) {
    const line = document.getElementById('sidebar-weather-line')!;
    line.getBoundingClientRect = () =>
      ({
        top,
        right: 300,
        bottom,
        left: 100,
        width: 200,
        height: bottom - top,
        x: 100,
        y: top,
        toJSON() {},
      }) as DOMRect;
  }

  it('opens downward below the line when there is not enough room above it', async () => {
    await bootSidebarWeather();
    // line sits ~112px from the viewport top (profile indicator hidden)
    pinLineRect(112, 132);
    document.getElementById('sidebar-weather-line')!.click();
    const pop = document.getElementById('sidebar-weather-popover')!;
    // measure the real popover height so the flip decision is observable
    Object.defineProperty(pop, 'offsetHeight', { configurable: true, value: 120 });
    window.dispatchEvent(new Event('resize')); // re-runs the placement math
    expect(pop.style.top).toBe('132px');
    expect(pop.style.bottom).toBe('');
  });

  it('keeps opening upward when there is plenty of room above the line', async () => {
    await bootSidebarWeather();
    pinLineRect(500, 520);
    document.getElementById('sidebar-weather-line')!.click();
    const pop = document.getElementById('sidebar-weather-popover')!;
    Object.defineProperty(pop, 'offsetHeight', { configurable: true, value: 120 });
    window.dispatchEvent(new Event('resize'));
    expect(pop.style.bottom).toBe(`${window.innerHeight - 500}px`);
    expect(pop.style.top).toBe('');
  });
});

describe('computePopoverPlacement (pure placement math)', () => {
  const base: PopoverGeom = {
    lineTop: 500,
    lineBottom: 520,
    lineRight: 300,
    popoverHeight: 200,
    popoverWidth: 260,
    viewportWidth: 1280,
    viewportHeight: 800,
  };

  it('(a) line near top with a tall popover flips below', () => {
    const p = computePopoverPlacement({ ...base, lineTop: 60, lineBottom: 80, popoverHeight: 200 });
    expect(p.top).toBe('80px');
    expect(p.bottom).toBe('');
    expect(p.maxHeight).toBeUndefined();
  });

  it('(b) line mid-screen with room above opens above', () => {
    const p = computePopoverPlacement(base);
    expect(p.bottom).toBe('300px'); // 800 - 500: bottom edge touches the line top
    expect(p.top).toBe('');
    expect(p.maxHeight).toBeUndefined();
  });

  it('(c) scrolled sidebar (line rect near top) with 170px popover opens below', () => {
    const p = computePopoverPlacement({
      ...base,
      lineTop: 40,
      lineBottom: 60,
      popoverHeight: 170,
    });
    expect(p.top).toBe('60px');
    expect(p.bottom).toBe('');
  });

  it('(d) near-bottom line with no room below opens above', () => {
    const p = computePopoverPlacement({
      ...base,
      lineTop: 700,
      lineBottom: 720,
      popoverHeight: 150,
    });
    expect(p.bottom).toBe('100px');
    expect(p.top).toBe('');
  });

  it('(e) tiny viewport where neither side fits: roomier side + capped height, pinned inside', () => {
    const p = computePopoverPlacement({
      ...base,
      lineTop: 200,
      lineBottom: 220,
      popoverHeight: 300,
      viewportHeight: 400,
    });
    // above has 192px, below has 172px → above wins, height capped to the room
    expect(p.bottom).toBe('200px');
    expect(p.top).toBe('');
    expect(p.maxHeight).toBe('192px');
  });

  it('(f) narrow viewport: right edge shifts so the popover left edge stays >= 8px', () => {
    const p = computePopoverPlacement({
      ...base,
      lineRight: 318,
      popoverWidth: 310,
      viewportWidth: 320,
    });
    // naive right = max(8, 320-318) = 8 → left edge would be 2px; clamped:
    expect(p.right).toBe('2px');
    const left = 320 - parseFloat(p.right) - 310;
    expect(left).toBe(8);
  });

  it('(g) popover wider than the viewport: left edge pinned at 8', () => {
    const p = computePopoverPlacement({
      ...base,
      lineRight: 300,
      popoverWidth: 400,
      viewportWidth: 320,
    });
    expect(p.right).toBe(`${320 - 400 - 8}px`);
    const left = 320 - parseFloat(p.right) - 400;
    expect(left).toBe(8);
  });

  it('right-aligns to the line right edge with an 8px margin', () => {
    const p = computePopoverPlacement(base);
    expect(p.right).toBe(`${1280 - 300}px`);
  });
});

describe('popover re-positioning after open (webfont race)', () => {
  it('re-runs placement on document.fonts.ready and on the next animation frame', async () => {
    await bootSidebarWeather();
    const line = document.getElementById('sidebar-weather-line')!;
    line.getBoundingClientRect = () =>
      ({
        top: 500,
        right: 300,
        bottom: 520,
        left: 100,
        width: 200,
        height: 20,
        x: 100,
        y: 500,
        toJSON() {},
      }) as DOMRect;

    let resolveFonts!: () => void;
    Object.defineProperty(document, 'fonts', {
      configurable: true,
      value: { ready: new Promise<void>((r) => (resolveFonts = r)) },
    });
    const rafCbs: FrameRequestCallback[] = [];
    Object.defineProperty(window, 'requestAnimationFrame', {
      configurable: true,
      value: (cb: FrameRequestCallback) => {
        rafCbs.push(cb);
        return rafCbs.length;
      },
    });

    line.click();
    const pop = document.getElementById('sidebar-weather-popover')!;
    expect(rafCbs).toHaveLength(1); // rAF re-position was scheduled

    // simulate the line having moved (font swap grew the layout): after the
    // re-position runs, placement must follow the NEW rect, not the old one
    line.getBoundingClientRect = () =>
      ({
        top: 40,
        right: 300,
        bottom: 60,
        left: 100,
        width: 200,
        height: 20,
        x: 100,
        y: 40,
        toJSON() {},
      }) as DOMRect;
    Object.defineProperty(pop, 'offsetHeight', { configurable: true, value: 170 });
    resolveFonts();
    await Promise.resolve(); // let the fonts.ready .then() run
    // line now near the top with a 170px popover → flipped below
    expect(pop.style.top).toBe('60px');
    expect(pop.style.bottom).toBe('');

    // the rAF re-position is a no-op-safe second pass: still below the line
    rafCbs[0]!(0);
    expect(pop.style.top).toBe('60px');

    // and closing first makes both scheduled passes no-ops (no throw)
    line.click(); // closes
    resolveFonts();
    rafCbs[0]!(0);
    expect(document.getElementById('sidebar-weather-popover')).toBeNull();
  });
});

describe('re-boot (settings changes without a page reload)', () => {
  it('a second boot replaces the line and scene — never duplicates them', async () => {
    HTMLCanvasElement.prototype.getContext = vi.fn(() => fake2dContext()) as never;
    await bootSidebarWeather();
    expect(document.getElementById('sidebar-weather-line')!.textContent).toContain('68°F');

    mockWeather({
      ...BASE_PAYLOAD,
      snapshot: {
        ...BASE_PAYLOAD.snapshot!,
        current: { ...BASE_PAYLOAD.snapshot!.current, temp: 55 },
      },
    });
    await bootSidebarWeather();

    expect(document.querySelectorAll('#sidebar-weather-line')).toHaveLength(1);
    expect(document.querySelectorAll('#sidebar-weather-scene')).toHaveLength(1);
    expect(document.getElementById('sidebar-weather-line')!.textContent).toContain('55°F');
    expect(document.getElementById('sidebar-weather-popover')).toBeNull();
  });

  it('re-boot after the feature is disabled removes everything', async () => {
    HTMLCanvasElement.prototype.getContext = vi.fn(() => fake2dContext()) as never;
    await bootSidebarWeather();
    expect(document.getElementById('sidebar-weather-line')).not.toBeNull();

    mockWeather({ ...BASE_PAYLOAD, enabled: false, location: null, snapshot: null });
    await bootSidebarWeather();
    expect(document.getElementById('sidebar-weather-line')).toBeNull();
    expect(document.getElementById('sidebar-weather-scene')).toBeNull();
  });
});

describe('the clock tick', () => {
  it('aligns to the minute boundary, then ticks every 60s', async () => {
    // 10.123s into the minute: the first tick should land at the boundary
    const now = Date.UTC(2026, 9, 6, 21, 32, 10, 123);
    const nowSpy = vi.spyOn(Date, 'now').mockReturnValue(now);
    const setTimeoutSpy = vi.spyOn(window, 'setTimeout');
    const setIntervalSpy = vi.spyOn(window, 'setInterval');

    await bootSidebarWeather();

    const expectedDelay = 60_000 - (now % 60_000);
    const call = setTimeoutSpy.mock.calls.find((c) => c[1] === expectedDelay);
    expect(call, 'a boundary-aligned timeout was scheduled').toBeDefined();
    const fire = call![0] as () => void;

    // simulate the boundary firing: the line re-renders and a 60s interval starts
    nowSpy.mockReturnValue(now + expectedDelay);
    fire();
    expect(setIntervalSpy).toHaveBeenCalledWith(expect.any(Function), 60_000);
    expect(document.getElementById('sidebar-weather-line')!.textContent).toContain('2:33 PM');
  });
});

describe('collapsed sidebar', () => {
  afterEach(() => {
    delete document.documentElement.dataset.sidebar;
  });

  it('fully stops the rAF loop while collapsed and restarts it on expand', async () => {
    const ctx = fake2dContext();
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ctx) as never;
    const frames: FrameRequestCallback[] = [];
    let nextId = 1;
    const raf = vi.fn((cb: FrameRequestCallback): number => {
      frames.push(cb);
      return nextId++;
    });
    const caf = vi.fn();
    Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, value: raf });
    Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, value: caf });

    await bootSidebarWeather();
    expect(raf).toHaveBeenCalled();
    const scheduled = 1; // first rAF id handed out by the stub above
    const framesAfterBoot = frames.length;

    // collapse: the data-sidebar MutationObserver fires as a microtask
    document.documentElement.dataset.sidebar = 'collapsed';
    await new Promise((r) => setTimeout(r, 0));
    expect(caf).toHaveBeenCalledWith(scheduled);
    // no new frames scheduled while collapsed — the loop is dead, not skipping
    expect(frames.length).toBe(framesAfterBoot);

    // expand: the loop restarts and draws again
    ctx.clearRect.mockClear();
    delete document.documentElement.dataset.sidebar;
    await new Promise((r) => setTimeout(r, 0));
    expect(frames.length).toBeGreaterThan(framesAfterBoot);
    frames[frames.length - 1]!(2000);
    expect(ctx.clearRect).toHaveBeenCalled();
  });
});

describe('overlapping boots (generation guard)', () => {
  it('a stale in-flight boot mounts nothing; only the latest mounts, once', async () => {
    HTMLCanvasElement.prototype.getContext = vi.fn(() => fake2dContext()) as never;
    const pending: Array<(resp: Response) => void> = [];
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          pending.push(resolve);
        }),
    );
    const setIntervalSpy = vi.spyOn(window, 'setInterval');
    let rafCalls = 0;
    let nextRafId = 1;
    Object.defineProperty(window, 'requestAnimationFrame', {
      configurable: true,
      value: () => {
        rafCalls++;
        return nextRafId++;
      },
    });

    const ok = (payload: WeatherResponse) =>
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });

    // two boots overlap: the second starts before the first fetch resolves
    const first = bootSidebarWeather();
    const second = bootSidebarWeather();
    expect(pending).toHaveLength(2);

    // the stale boot's fetch resolves first: it must mount nothing and arm
    // no clock interval or scene loop
    pending[0]!(ok(BASE_PAYLOAD));
    await first;
    expect(document.getElementById('sidebar-weather-line')).toBeNull();
    expect(document.getElementById('sidebar-weather-scene')).toBeNull();
    expect(setIntervalSpy).not.toHaveBeenCalled();
    const rafAfterStale = rafCalls;

    // the current boot resolves: exactly one line, one canvas, one scene loop
    pending[1]!(ok(BASE_PAYLOAD));
    await second;
    expect(document.querySelectorAll('#sidebar-weather-line')).toHaveLength(1);
    expect(document.querySelectorAll('#sidebar-weather-scene')).toHaveLength(1);
    expect(rafCalls).toBeGreaterThan(rafAfterStale);

    fetchSpy.mockRestore();
    setIntervalSpy.mockRestore();
  });

  it('a failed re-boot keeps the previous mount instead of blanking it', async () => {
    await bootSidebarWeather();
    const line = document.getElementById('sidebar-weather-line');
    expect(line).not.toBeNull();
    const htmlBefore = line!.innerHTML;

    mockWeather(null); // 500 — transient failure
    await bootSidebarWeather();
    const kept = document.getElementById('sidebar-weather-line');
    expect(kept).not.toBeNull();
    expect(kept!.innerHTML).toBe(htmlBefore);
  });
});

describe('reduced-motion follow-up', () => {
  function setReducedMotion(reduced: boolean) {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: (q: string) => ({
        matches: reduced && q === '(prefers-reduced-motion: reduce)',
        media: q,
        addEventListener() {},
        removeEventListener() {},
      }),
    });
  }

  it('drops the scene when reduced-motion turns on, restores it when it turns off', async () => {
    HTMLCanvasElement.prototype.getContext = vi.fn(() => fake2dContext()) as never;
    await bootSidebarWeather();
    expect(document.getElementById('sidebar-weather-scene')).not.toBeNull();

    setReducedMotion(true);
    window.dispatchEvent(new Event('focus'));
    expect(document.getElementById('sidebar-weather-scene')).toBeNull();
    // the line itself is untouched — only the motion is gated
    expect(document.getElementById('sidebar-weather-line')).not.toBeNull();

    setReducedMotion(false);
    document.dispatchEvent(new Event('visibilitychange'));
    expect(document.getElementById('sidebar-weather-scene')).not.toBeNull();
  });
});
