import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import {
  bootSidebarWeather,
  closePopover,
  computePopoverPlacement,
  dayLabelForDaily,
  formatHiLo,
  formatLocalTime,
  formatTemp,
  getWeatherPreview,
  glyphForWeatherCode,
  glyphSvg,
  lineGlyph,
  refreshSidebarWeather,
  setWeatherPreview,
  shouldRenderWeather,
  type PopoverGeom,
  type WeatherResponse,
  type WeatherSnapshot,
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
    quadraticCurveTo: vi.fn(),
    closePath: vi.fn(),
    globalAlpha: 1,
    globalCompositeOperation: 'source-over',
    lineCap: 'butt',
    lineJoin: 'miter',
  };
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

describe('the line glyph reads the moment', () => {
  const withCurrent = (
    current: Partial<WeatherSnapshot['current']>,
    scene: WeatherResponse['scene'] = 'clear',
  ): WeatherResponse => ({
    ...BASE_PAYLOAD,
    scene,
    snapshot: {
      ...BASE_PAYLOAD.snapshot!,
      current: { ...BASE_PAYLOAD.snapshot!.current, ...current },
    },
  });

  it('a moon on a clear night, a sun by day', () => {
    expect(lineGlyph(withCurrent({ weather_code: 0, is_day: false }))).toBe('moon');
    expect(lineGlyph(withCurrent({ weather_code: 2, is_day: false }))).toBe('partly-cloudy-night');
    expect(lineGlyph(withCurrent({ weather_code: 0, is_day: true }))).toBe('sun');
    // a snapshot from before is_day existed keeps the day glyph
    expect(lineGlyph(withCurrent({ weather_code: 0 }))).toBe('sun');
  });

  it('the wind glyph when the wind is what you would notice', () => {
    expect(lineGlyph(withCurrent({ weather_code: 0 }, 'wind'))).toBe('wind');
  });

  it('draws the night glyphs', () => {
    expect(glyphSvg('moon')).toContain('<path');
    expect(glyphSvg('partly-cloudy-night')).toContain('<path');
  });
});

describe('popover rain chance', () => {
  it('shows a dash, not 0%, when the chance is unknown', async () => {
    const daily = BASE_PAYLOAD.snapshot!.daily.map((d, i) =>
      i === 1 ? { ...d, precip_probability: null } : d,
    );
    mockWeather({ ...BASE_PAYLOAD, snapshot: { ...BASE_PAYLOAD.snapshot!, daily } });
    await bootSidebarWeather();
    document.getElementById('sidebar-weather-line')!.click();
    const precips = [...document.querySelectorAll('.sidebar-weather-precip')].map(
      (e) => e.textContent,
    );
    expect(precips).toEqual(['5%', '—', '0%']);
  });
});

describe('refresh while the tab stays open', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('updates the line in place and crossfades the scene, then stops the old one', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'setInterval', 'clearTimeout', 'clearInterval'] });
    HTMLCanvasElement.prototype.getContext = vi.fn(() => fake2dContext()) as never;
    await bootSidebarWeather();
    const first = document.getElementById('sidebar-weather-scene')!;
    expect(first.classList.contains('sidebar-weather-scene')).toBe(true);

    const later = {
      ...BASE_PAYLOAD,
      snapshot: {
        ...BASE_PAYLOAD.snapshot!,
        current: { ...BASE_PAYLOAD.snapshot!.current, temp: 41 },
      },
    };
    mockWeather(later);
    await refreshSidebarWeather();

    expect(document.getElementById('sidebar-weather-line')!.textContent).toContain('41°F');
    const next = document.getElementById('sidebar-weather-scene')!;
    expect(next).not.toBe(first);
    // the old one is still there, fading, without the id
    expect(first.isConnected).toBe(true);
    expect(first.id).toBe('');
    expect(first.classList.contains('is-visible')).toBe(false);

    vi.advanceTimersByTime(1600);
    expect(first.isConnected).toBe(false);
    expect(document.querySelectorAll('#app-sidebar canvas').length).toBe(1);
  });

  it('refetches every 15 minutes, and a teardown stops the timer', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'setInterval', 'clearTimeout', 'clearInterval'] });
    // one counting handler for the whole test: a second server.use would
    // shadow it and later fetches would go uncounted
    let hits = 0;
    let payload: WeatherResponse = BASE_PAYLOAD;
    server.use(
      http.get('*/api/weather', () => {
        hits++;
        return HttpResponse.json(payload);
      }),
    );
    await bootSidebarWeather();
    expect(hits).toBe(1);
    await vi.advanceTimersByTimeAsync(15 * 60_000);
    expect(hits).toBe(2);

    payload = { ...BASE_PAYLOAD, enabled: false };
    await bootSidebarWeather(); // turned off: everything goes
    const before = hits;
    await vi.advanceTimersByTimeAsync(30 * 60_000);
    expect(hits).toBe(before);
  });

  it('a failed refresh keeps what is showing', async () => {
    await bootSidebarWeather();
    const line = document.getElementById('sidebar-weather-line')!.textContent;
    mockWeather(null);
    await refreshSidebarWeather();
    expect(document.getElementById('sidebar-weather-line')!.textContent).toBe(line);
  });
});

describe('weather preview (settings > advanced > developer)', () => {
  afterEach(() => {
    sessionStorage.clear();
    vi.useRealTimers();
  });

  it('shows the chosen sky, says Preview on the line, and swaps the scene', async () => {
    HTMLCanvasElement.prototype.getContext = vi.fn(() => fake2dContext()) as never;
    await bootSidebarWeather();
    const live = document.getElementById('sidebar-weather-scene');
    setWeatherPreview({ preset: 'thunderstorm', date: null });
    const line = document.getElementById('sidebar-weather-line')!.textContent!;
    expect(line).toContain('Preview · Thunderstorm');
    expect(line).not.toContain('68°F'); // never passes for the real sky
    const next = document.getElementById('sidebar-weather-scene');
    expect(next).not.toBeNull();
    expect(next).not.toBe(live);
  });

  it('lives in this tab only: session storage, no request to the server', async () => {
    const writes: string[] = [];
    server.use(
      http.put('*/api/weather/*', ({ request }) => {
        writes.push(request.url);
        return HttpResponse.json({});
      }),
    );
    await bootSidebarWeather();
    setWeatherPreview({ preset: 'blizzard', date: '2026-12-24' });
    expect(JSON.parse(sessionStorage.getItem('soulsync-weather-preview')!)).toEqual({
      preset: 'blizzard',
      date: '2026-12-24',
    });
    expect(localStorage.getItem('soulsync-weather-preview')).toBeNull();
    expect(writes).toEqual([]);
  });

  it('survives a reload in the same tab, and paints even when the live sky has no scene', async () => {
    HTMLCanvasElement.prototype.getContext = vi.fn(() => fake2dContext()) as never;
    sessionStorage.setItem(
      'soulsync-weather-preview',
      JSON.stringify({ preset: 'snowfall', date: null }),
    );
    mockWeather({ ...BASE_PAYLOAD, scene: null });
    await bootSidebarWeather();
    expect(document.getElementById('sidebar-weather-line')!.textContent).toContain(
      'Preview · Snowfall',
    );
    expect(document.getElementById('sidebar-weather-scene')).not.toBeNull();
  });

  it('back to live puts the real line back', async () => {
    await bootSidebarWeather();
    setWeatherPreview({ preset: 'gale', date: null });
    setWeatherPreview(null);
    expect(getWeatherPreview()).toBeNull();
    expect(document.getElementById('sidebar-weather-line')!.textContent).toContain('68°F');
  });

  it('ignores a stored preset that no longer exists', () => {
    sessionStorage.setItem(
      'soulsync-weather-preview',
      JSON.stringify({ preset: 'meteor-shower', date: null }),
    );
    expect(getWeatherPreview()).toBeNull();
  });
});
