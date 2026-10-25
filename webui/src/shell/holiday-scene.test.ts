import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type { HolidaySky } from './holidays';

import { createHolidaySky } from './holiday-scene';
import { createWeatherScene, type SceneConditions } from './weather-scene';

/**
 * a holiday's sky on the scene canvas. drawing runs against a fake context
 * (jsdom has no canvas), so these pin behaviour, not pixels. the pixels were
 * judged in real chromium (webui/test-results/wx/holiday-full.cjs, gitignored).
 */

function fakeCtx(record?: Array<[number, number]>) {
  const fn = () => () => {};
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
    arc: fn(),
    ellipse: fn(),
    stroke: fn(),
    fill: fn(),
    save: fn(),
    restore: fn(),
    translate: fn(),
    rotate: fn(),
    scale: fn(),
    drawImage: record
      ? (_s: unknown, x: number, y: number, w: number, h: number) =>
          record.push([x + w / 2, y + h / 2])
      : () => {},
    createRadialGradient: gradient,
    createLinearGradient: gradient,
  };
}

const origGetContext = HTMLCanvasElement.prototype.getContext.bind(HTMLCanvasElement.prototype);
beforeEach(() => {
  HTMLCanvasElement.prototype.getContext = (() => fakeCtx()) as never;
});
afterEach(() => {
  HTMLCanvasElement.prototype.getContext = origGetContext;
});

function seeded(seed = 7) {
  let s = seed;
  return () => (s = (s * 16807) % 2147483647) / 2147483647;
}

const CLEAR: SceneConditions = {
  sky: 'clear',
  precip: 'none',
  intensity: 0,
  thunder: false,
  isDay: true,
  cloudCover: 0.04,
  windMph: 4,
  gustMph: 6,
  windFromDeg: 250,
  dayPhase: 0.5,
  twilight: 0,
  season: 'autumn',
};
const NIGHT = { ...CLEAR, isDay: false };
const TOP = 240;

/** run a holiday sky for `seconds`, tracking the most of each thing seen at once */
function run(sky: HolidaySky, cond: SceneConditions, seconds = 60) {
  const ctx = fakeCtx();
  const layer = createHolidaySky(ctx as never, cond, seeded(), sky);
  layer.resize(240, 1000, TOP, 1);
  const most: Record<string, number> = {};
  let t = 0;
  for (let i = 0; i < seconds * 30; i++) {
    t += 1 / 30;
    layer.update(1 / 30, t);
    layer.back(t);
    layer.front(t);
    for (const [k, v] of Object.entries(layer.stats())) most[k] = Math.max(most[k] ?? 0, v);
  }
  return { most, now: layer.stats() };
}

describe('the holiday sky', () => {
  it('halloween night: a moon, wisps out of the fog, bats; the witch only on the night itself', () => {
    const week = run({ id: 'halloween', moment: 0 }, NIGHT, 90);
    expect(week.now.moon).toBe(1);
    expect(week.now.fogBanks).toBe(4);
    expect(week.now.risers).toBeGreaterThan(0);
    expect(week.most.bats).toBeGreaterThan(0);
    expect(week.most.bats).toBeLessThanOrEqual(2);
    expect(week.most.witch).toBe(0);

    const night = run({ id: 'halloween', moment: 1 }, NIGHT, 90);
    expect(night.most.bats).toBeGreaterThan(2);
    expect(night.most.witch).toBe(1);
    expect(night.now.fogBanks).toBe(6);
  });

  it('halloween by day: no bats or wisps, a few leaves, the fog stays', () => {
    const day = run({ id: 'halloween', moment: 1 }, CLEAR, 60);
    expect(day.most.bats).toBe(0);
    expect(day.most.risers).toBe(0);
    expect(day.now.drifters).toBeGreaterThan(0);
    expect(day.now.fogBanks).toBeGreaterThan(0);
  });

  it('thanksgiving: geese fly by day, never at night', () => {
    expect(run({ id: 'thanksgiving', moment: 0 }, CLEAR, 90).most.flock).toBeGreaterThan(0);
    expect(run({ id: 'thanksgiving', moment: 0 }, NIGHT, 90).most.flock).toBe(0);
  });

  it('christmas flurries fall only when the sky is dry: real rain or snow keeps its own', () => {
    expect(run({ id: 'christmas', moment: 0 }, NIGHT, 1).now.drifters).toBeGreaterThan(0);
    const rain = { ...NIGHT, precip: 'rain' as const, intensity: 0.5 };
    expect(run({ id: 'christmas', moment: 0 }, rain, 1).now.drifters).toBe(0);
    const snow = { ...NIGHT, precip: 'snow' as const, intensity: 0.5 };
    expect(run({ id: 'christmas', moment: 0 }, snow, 1).now.drifters).toBe(0);
  });

  it('lunar new year: sky lanterns rise at night, petals fall by day', () => {
    const night = run({ id: 'lunar-new-year', moment: 0 }, NIGHT, 1).now;
    expect(night.risers).toBeGreaterThan(0);
    expect(night.drifters).toBe(0);
    const day = run({ id: 'lunar-new-year', moment: 0 }, CLEAR, 1).now;
    expect(day.risers).toBe(0);
    expect(day.drifters).toBeGreaterThan(0);
  });

  it('the moment means more of it', () => {
    const week = run({ id: 'new-year', moment: 0 }, NIGHT, 1).now.drifters;
    const eve = run({ id: 'new-year', moment: 1 }, NIGHT, 1).now.drifters;
    expect(eve).toBeGreaterThan(week);
  });

  it('nothing falls from behind the header: flakes start at its bottom edge', () => {
    const drawn: Array<[number, number]> = [];
    const ctx = fakeCtx(drawn);
    const layer = createHolidaySky(ctx as never, NIGHT, seeded(), { id: 'christmas', moment: 1 });
    layer.resize(240, 1000, TOP, 1);
    let t = 0;
    for (let i = 0; i < 30 * 40; i++) {
      t += 1 / 30;
      layer.update(1 / 30, t);
    }
    drawn.length = 0;
    layer.front(t);
    expect(drawn.length).toBeGreaterThan(0);
    expect(Math.min(...drawn.map(([, y]) => y))).toBeGreaterThan(TOP - 45);
  });

  it('the weather engine carries the layer: sized with the header line, its counts in the stats', () => {
    const seen: number[] = [];
    const layer = createHolidaySky(fakeCtx() as never, NIGHT, seeded(), {
      id: 'halloween',
      moment: 0,
    });
    const resize = layer.resize.bind(layer);
    layer.resize = (w, h, top, dpr) => {
      seen.push(top);
      resize(w, h, top, dpr);
    };
    const engine = createWeatherScene(fakeCtx() as never, NIGHT, seeded(), { layer });
    engine.resize(240, 1000, 1, TOP);
    engine.frame(1 / 30, 1 / 30);
    expect(seen).toEqual([TOP]);
    expect(engine.stats().moon).toBe(1);
  });
});
