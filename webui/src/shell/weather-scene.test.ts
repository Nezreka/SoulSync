import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  conditionsFromWeather,
  createWeatherScene,
  gustEnvelope,
  presetConditions,
  SCENE_PRESETS,
  seasonForMonth,
  windSide,
  windStrength,
  type SceneConditions,
  type SceneWeatherInput,
} from './weather-scene';

/**
 * the scene engine: reading the weather into conditions, and the engine
 * painting them. drawing runs against a recording fake context (jsdom has
 * no canvas), so these pin behaviour, not pixels. the pixels were judged in
 * real chromium (webui/test-results/wx, gitignored).
 */

/** a do-nothing 2d context. `spy` records calls, only for short runs: a
 * long simulation recording every draw call runs the worker out of memory */
function fakeCtx(spy = false) {
  const fn = () => (spy ? vi.fn() : () => {});
  const gradient = () => ({ addColorStop: () => {} });
  return {
    fillStyle: '' as unknown,
    strokeStyle: '' as unknown,
    lineWidth: 1,
    globalAlpha: 1,
    globalCompositeOperation: 'source-over',
    lineCap: 'butt',
    lineJoin: 'miter',
    filter: 'none',
    setTransform: fn(),
    clearRect: fn(),
    fillRect: fn(),
    beginPath: fn(),
    closePath: fn(),
    moveTo: fn(),
    lineTo: fn(),
    quadraticCurveTo: fn(),
    stroke: fn(),
    fill: fn(),
    save: fn(),
    restore: fn(),
    translate: fn(),
    rotate: fn(),
    scale: fn(),
    drawImage: fn(),
    createRadialGradient: gradient,
    createLinearGradient: gradient,
  };
}

const origGetContext = HTMLCanvasElement.prototype.getContext.bind(HTMLCanvasElement.prototype);
beforeEach(() => {
  // sprites bake on offscreen canvases; give them a context too
  HTMLCanvasElement.prototype.getContext = (() => fakeCtx()) as never;
});
afterEach(() => {
  HTMLCanvasElement.prototype.getContext = origGetContext;
});

/** a seeded rng so every run builds the same scene */
function seeded(seed = 7) {
  let s = seed;
  return () => (s = (s * 16807) % 2147483647) / 2147483647;
}

const CALM: SceneConditions = {
  sky: 'clear',
  precip: 'none',
  intensity: 0,
  thunder: false,
  isDay: true,
  cloudCover: 0.04,
  windMph: 3,
  gustMph: 5,
  windFromDeg: 270,
  dayPhase: 0.5,
  twilight: 0,
  season: 'autumn',
};

function run(cond: Partial<SceneConditions>, seconds = 6, spy = false) {
  const ctx = fakeCtx(spy);
  const eng = createWeatherScene(
    ctx as unknown as CanvasRenderingContext2D,
    { ...CALM, ...cond },
    seeded(),
  );
  eng.resize(260, 900, 2);
  let t = 0;
  let peakFlash = 0;
  for (let i = 0; i < seconds * 60; i++) {
    t += 1 / 60;
    eng.frame(1 / 60, t);
    peakFlash = Math.max(peakFlash, eng.stats().flash);
  }
  return { ctx, eng, stats: eng.stats(), peakFlash };
}

function snap(
  current: Partial<SceneWeatherInput['current']>,
  daily?: SceneWeatherInput['daily'],
): SceneWeatherInput {
  return { utc_offset_seconds: 0, current: { weather_code: 0, ...current }, daily };
}

// 2026-10-06 12:00 utc; offset 0 keeps local == utc
const NOON = Date.UTC(2026, 9, 6, 12, 0);

describe('reading the weather', () => {
  it('maps WMO codes to what falls and how hard', () => {
    const c = (code: number) => conditionsFromWeather(snap({ weather_code: code }), NOON);
    expect([c(51).precip, c(51).intensity]).toEqual(['rain', 0.2]);
    expect([c(65).precip, c(65).intensity]).toEqual(['rain', 0.95]);
    expect([c(75).precip, c(75).intensity]).toEqual(['snow', 0.92]);
    expect(c(95).thunder).toBe(true);
    expect(c(63).thunder).toBe(false);
    expect(c(0).precip).toBe('none');
  });

  it('reads the sky from the code, the cover from the data', () => {
    const c = (code: number, cover?: number) =>
      conditionsFromWeather(snap({ weather_code: code, cloud_cover: cover }), NOON);
    expect(c(0).sky).toBe('clear');
    expect(c(2).sky).toBe('partly');
    expect(c(3).sky).toBe('overcast');
    expect(c(45).sky).toBe('fog');
    expect(c(61).sky).toBe('overcast');
    expect(c(2, 90).sky).toBe('cloudy'); // "partly" with 90% cover is cloudy
    expect(c(0, 30).cloudCover).toBeCloseTo(0.3);
    expect(c(0).cloudCover).toBeLessThan(0.1); // no data: a clear sky is clear
    expect(c(61, 10).cloudCover).toBeGreaterThanOrEqual(0.8); // rain needs cloud
  });

  it('the measured amount sharpens the code: a downpour coded as light rain gets heavier', () => {
    const light = conditionsFromWeather(snap({ weather_code: 61 }), NOON).intensity;
    const measured = conditionsFromWeather(
      snap({ weather_code: 61, precipitation: 3.6 }),
      NOON,
    ).intensity;
    expect(measured).toBeGreaterThan(light);
  });

  it('day or night: is_day wins, then sunrise/sunset, then the clock', () => {
    const sun = [{ sunrise: '2026-10-06T07:00', sunset: '2026-10-06T19:00' }];
    expect(conditionsFromWeather(snap({ is_day: false }, sun), NOON).isDay).toBe(false);
    expect(conditionsFromWeather(snap({}, sun), NOON).isDay).toBe(true);
    expect(conditionsFromWeather(snap({}, sun), Date.UTC(2026, 9, 6, 21, 0)).isDay).toBe(false);
    expect(conditionsFromWeather(snap({}), Date.UTC(2026, 9, 6, 3, 0)).isDay).toBe(false);
  });

  it('places the sun through the day and warms it near sunrise and sunset', () => {
    const sun = [{ sunrise: '2026-10-06T07:00', sunset: '2026-10-06T19:00' }];
    const at = (h: number, m = 0) =>
      conditionsFromWeather(snap({}, sun), Date.UTC(2026, 9, 6, h, m));
    expect(at(13).dayPhase).toBeCloseTo(0.5);
    expect(at(13).twilight).toBe(0);
    expect(at(18, 50).twilight).toBeGreaterThan(0.9);
    expect(at(7, 30).twilight).toBeGreaterThan(0.4);
  });

  it('carries the wind as reported, gusts never under the steady speed', () => {
    const c = conditionsFromWeather(
      snap({ wind_speed: 20, wind_gusts: 12, wind_direction: 250 }),
      NOON,
    );
    expect([c.windMph, c.gustMph, c.windFromDeg]).toEqual([20, 20, 250]);
  });

  it('an old cached snapshot with none of the scene fields still paints', () => {
    const c = conditionsFromWeather(snap({ weather_code: 3 }), NOON);
    expect(c.windFromDeg).toBeNull();
    expect(c.dayPhase).toBeNull();
    expect(() => run(c)).not.toThrow();
  });

  it('seasons flip in the southern hemisphere', () => {
    expect(seasonForMonth(9)).toBe('autumn');
    expect(seasonForMonth(9, true)).toBe('spring');
    expect(seasonForMonth(0)).toBe('winter');
    const south = conditionsFromWeather(snap({}), NOON, -33.9);
    expect(south.season).toBe('spring');
  });
});

describe('the wind', () => {
  it('blows with the real direction: a west wind goes right, an east wind left', () => {
    expect(windSide(270)).toBe(1);
    expect(windSide(90)).toBe(-1);
    // a north wind has no sideways part but still drifts, gently
    expect(Math.abs(windSide(0))).toBeCloseTo(0.45);
    expect(windSide(null)).toBe(1);
  });

  it('shows from a breeze up, at full strength in a gale', () => {
    expect(windStrength({ windMph: 5, gustMph: 8 })).toBe(0);
    expect(windStrength({ windMph: 20, gustMph: 26 })).toBeGreaterThan(0.3);
    expect(windStrength({ windMph: 40, gustMph: 55 })).toBe(1);
  });

  it('gusts swell and settle: 0..1, with calm stretches and real peaks', () => {
    const samples = Array.from({ length: 2000 }, (_, i) => gustEnvelope(i * 0.05));
    expect(Math.min(...samples)).toBeGreaterThanOrEqual(0);
    expect(Math.max(...samples)).toBeLessThanOrEqual(1);
    expect(samples.filter((g) => g < 0.05).length).toBeGreaterThan(200);
    expect(Math.max(...samples)).toBeGreaterThan(0.8);
  });

  it('ribbons travel downwind', () => {
    const west = run({ windMph: 25, gustMph: 35, windFromDeg: 270 });
    const east = run({ windMph: 25, gustMph: 35, windFromDeg: 90 });
    expect(west.stats.driftX).toBeGreaterThan(0);
    expect(east.stats.driftX).toBeLessThan(0);
  });

  it('calm air has no ribbons; more wind, more ribbons', () => {
    expect(run({ windMph: 4, gustMph: 6 }).stats.streams).toBe(0);
    const breezy = run({ windMph: 16, gustMph: 22 }).stats.streams;
    const gale = run({ windMph: 38, gustMph: 52 }).stats.streams;
    expect(breezy).toBeGreaterThan(0);
    expect(gale).toBeGreaterThan(breezy);
  });

  it('leaves only fly in a real wind, and never in rain or snow', () => {
    expect(run({ windMph: 14, gustMph: 18 }).stats.leaves).toBe(0);
    expect(run({ windMph: 34, gustMph: 48 }).stats.leaves).toBeGreaterThan(0);
    expect(run({ windMph: 34, gustMph: 48, precip: 'rain', intensity: 0.6 }).stats.leaves).toBe(0);
  });

  it('under rain or snow the slant shows the wind, not ribbons across the streaks', () => {
    expect(run({ windMph: 30, gustMph: 40, precip: 'rain', intensity: 0.7 }).stats.streams).toBe(0);
    expect(run({ windMph: 30, gustMph: 40, precip: 'snow', intensity: 0.7 }).stats.streams).toBe(0);
  });
});

describe('the sky', () => {
  it('stars only at night, and clouds hide them', () => {
    expect(run({ isDay: true }).stats.stars).toBe(0);
    const clear = run({ isDay: false }).stats.stars;
    const hazy = run({ isDay: false, cloudCover: 0.6 }).stats.stars;
    expect(clear).toBeGreaterThan(30);
    expect(hazy).toBeLessThan(clear);
    expect(run({ isDay: false, cloudCover: 1 }).stats.stars).toBe(0);
  });

  it('cloud count follows the real cover', () => {
    expect(run({ cloudCover: 0.03 }).stats.clouds).toBe(0);
    const some = run({ sky: 'partly', cloudCover: 0.45 }).stats.clouds;
    const lots = run({ sky: 'overcast', cloudCover: 0.95 }).stats.clouds;
    expect(some).toBeGreaterThan(0);
    expect(lots).toBeGreaterThan(some);
  });

  it('fog banks only in fog', () => {
    expect(run({ sky: 'fog', cloudCover: 0.7 }).stats.fog).toBeGreaterThan(0);
    expect(run({ sky: 'overcast', cloudCover: 0.9 }).stats.fog).toBe(0);
  });

  it('lightning only in a thunderstorm', () => {
    const storm = run(
      { sky: 'overcast', precip: 'rain', intensity: 0.8, thunder: true, cloudCover: 1 },
      40,
    );
    const rain = run({ sky: 'overcast', precip: 'rain', intensity: 0.8, cloudCover: 1 }, 40);
    expect(storm.peakFlash).toBeGreaterThan(0.5);
    expect(rain.peakFlash).toBe(0);
  });
});

describe('rain and snow', () => {
  it('heavier weather, more of it', () => {
    const drizzle = run({ precip: 'rain', intensity: 0.2 }).stats.drops;
    const pour = run({ precip: 'rain', intensity: 0.95 }).stats.drops;
    expect(pour).toBeGreaterThan(drizzle * 2);
    const flurries = run({ precip: 'snow', intensity: 0.25 }).stats.drops;
    const blizzard = run({ precip: 'snow', intensity: 0.9 }).stats.drops;
    expect(blizzard).toBeGreaterThan(flurries * 2);
  });

  it('a narrow, short sidebar gets fewer particles', () => {
    const ctx = fakeCtx();
    const eng = createWeatherScene(
      ctx as unknown as CanvasRenderingContext2D,
      { ...CALM, precip: 'rain', intensity: 0.9 },
      seeded(),
    );
    eng.resize(200, 500, 1);
    const small = eng.stats().drops;
    expect(small).toBeLessThan(run({ precip: 'rain', intensity: 0.9 }).stats.drops);
  });
});

describe('cost', () => {
  it('stays cheap per frame even in the busiest scenes', () => {
    for (const cond of [
      { windMph: 40, gustMph: 58 },
      {
        precip: 'rain' as const,
        intensity: 1,
        thunder: true,
        sky: 'overcast' as const,
        cloudCover: 1,
      },
      { precip: 'snow' as const, intensity: 1, sky: 'overcast' as const, cloudCover: 1 },
      { isDay: false, windMph: 30, gustMph: 45 },
    ]) {
      const { ctx, eng } = run(cond, 2, true);
      const spies = [ctx.stroke, ctx.drawImage, ctx.fillRect] as unknown as ReturnType<
        typeof vi.fn
      >[];
      for (const f of spies) f.mockClear();
      eng.frame(1 / 60, 3);
      const calls = spies.reduce((n, f) => n + f.mock.calls.length, 0);
      expect(calls).toBeLessThan(320);
    }
  });

  it('resizing by a pixel or two does not reshuffle the scene', () => {
    const ctx = fakeCtx();
    const rng = vi.fn(seeded());
    const eng = createWeatherScene(
      ctx as unknown as CanvasRenderingContext2D,
      { ...CALM, precip: 'rain', intensity: 0.5 },
      rng,
    );
    eng.resize(260, 900, 2);
    const before = rng.mock.calls.length;
    eng.resize(261, 900, 2);
    expect(rng.mock.calls.length).toBe(before);
  });
});

describe('presets', () => {
  it('every preset builds full conditions and paints', () => {
    for (const key of Object.keys(SCENE_PRESETS)) {
      const c = presetConditions(key);
      expect(c, key).not.toBeNull();
      expect(() => run(c!, 1)).not.toThrow();
    }
    expect(presetConditions('nope')).toBeNull();
  });

  it('the presets cover every kind of sky', () => {
    const all = Object.keys(SCENE_PRESETS).map((k) => presetConditions(k)!);
    expect(new Set(all.map((c) => c.sky))).toEqual(new Set(['clear', 'partly', 'overcast', 'fog']));
    expect(new Set(all.map((c) => c.precip))).toEqual(new Set(['none', 'rain', 'snow']));
    expect(all.some((c) => c.thunder)).toBe(true);
    expect(all.some((c) => !c.isDay)).toBe(true);
    expect(all.some((c) => windStrength(c) > 0.6)).toBe(true);
  });

  it('a pretend date sets the season, flipped south of the equator', () => {
    const xmas = new Date('2026-12-24T12:00:00Z');
    expect(presetConditions('snowfall', xmas)!.season).toBe('winter');
    expect(presetConditions('snowfall', xmas, -33.9)!.season).toBe('summer');
    expect(presetConditions('breezy', new Date('2026-10-31T12:00:00Z'))!.season).toBe('autumn');
  });
});

describe('a clear night', () => {
  function watch(cond: Partial<SceneConditions>, seconds: number) {
    const ctx = fakeCtx();
    const eng = createWeatherScene(
      ctx as unknown as CanvasRenderingContext2D,
      { ...CALM, ...cond },
      seeded(),
    );
    eng.resize(260, 900, 2);
    const glow: number[] = [];
    let t = 0;
    for (let i = 0; i < seconds * 60; i++) {
      t += 1 / 60;
      eng.frame(1 / 60, t);
      glow.push(eng.stats().satellite);
    }
    return { eng, glow };
  }

  it('has a planet, but only at night and only when the sky is clear enough', () => {
    expect(run({ isDay: false }).stats.planet).toBe(1);
    expect(run({ isDay: true }).stats.planet).toBe(0);
    expect(run({ isDay: false, cloudCover: 0.9 }).stats.planet).toBe(0);
  });

  it('a satellite crosses and glints: faint between glints, bright at them', () => {
    const { glow } = watch({ isDay: false }, 40);
    const visible = glow.filter((g) => g > 0.01);
    expect(visible.length).toBeGreaterThan(60 * 10); // it takes its time crossing
    expect(Math.max(...visible)).toBeGreaterThan(0.85);
    // between glints it rests near its faint steady level
    expect(visible.filter((g) => g < 0.5).length).toBeGreaterThan(visible.length / 2);
  });

  it('no satellite by day or under cloud', () => {
    expect(Math.max(...watch({ isDay: true }, 40).glow)).toBe(0);
    expect(Math.max(...watch({ isDay: false, cloudCover: 0.8 }, 40).glow)).toBe(0);
  });
});
