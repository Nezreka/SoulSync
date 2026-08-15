import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  countUpDuration,
  countUpValue,
  easeOutCubic,
  prefersReducedMotion,
  staggerDelay,
} from './-year.animation';

describe('easeOutCubic', () => {
  it('runs from nothing to everything', () => {
    expect(easeOutCubic(0)).toBe(0);
    expect(easeOutCubic(1)).toBe(1);
  });

  it('is past halfway at the midpoint — that is what makes it ease OUT', () => {
    expect(easeOutCubic(0.5)).toBeGreaterThan(0.5);
  });

  it('clamps rather than overshooting on a stray input', () => {
    expect(easeOutCubic(-1)).toBe(0);
    expect(easeOutCubic(4)).toBe(1);
  });
});

describe('countUpValue', () => {
  it('starts at zero', () => {
    expect(countUpValue(1247, 0, 1000)).toBe(0);
  });

  it('lands EXACTLY on the target', () => {
    // An eased tween that stops at 1,246 of 1,247 is worse than no animation:
    // the number on screen is then simply wrong.
    expect(countUpValue(1247, 1000, 1000)).toBe(1247);
    expect(countUpValue(1247, 99999, 1000)).toBe(1247);
  });

  it('is monotonic on the way up', () => {
    const samples = [0, 100, 250, 500, 750, 1000].map((t) => countUpValue(1247, t, 1000));

    for (let i = 1; i < samples.length; i += 1) {
      expect(samples[i]).toBeGreaterThanOrEqual(samples[i - 1]);
    }
  });

  it('returns whole numbers — a play count has no decimals', () => {
    for (const t of [37, 210, 640, 913]) {
      expect(Number.isInteger(countUpValue(1247, t, 1000))).toBe(true);
    }
  });

  it('shows the target immediately when there is no duration to animate over', () => {
    expect(countUpValue(500, 10, 0)).toBe(500);
  });

  it('does not produce NaN from junk', () => {
    expect(countUpValue(Number.NaN, 100, 1000)).toBe(0);
    expect(countUpValue(100, Number.NaN, 1000)).toBe(0);
  });
});

describe('countUpDuration', () => {
  it('gives a bigger number a longer run', () => {
    // 12 ticking over the same span as 40,000 looks broken — the eye reads
    // the individual digits.
    expect(countUpDuration(40_000)).toBeGreaterThan(countUpDuration(12));
  });

  it('never runs instantly or outstays the slide', () => {
    for (const n of [1, 9, 1000, 5_000_000]) {
      expect(countUpDuration(n)).toBeGreaterThanOrEqual(500);
      expect(countUpDuration(n)).toBeLessThanOrEqual(1600);
    }
  });

  it('does not animate zero at all', () => {
    expect(countUpDuration(0)).toBe(0);
  });
});

describe('staggerDelay', () => {
  it('does not delay the first item', () => {
    expect(staggerDelay(0)).toBe(0);
  });

  it('walks the wave along', () => {
    expect(staggerDelay(1)).toBeLessThan(staggerDelay(3));
  });

  it('caps so the last of a long list is not left behind', () => {
    // Twelve months at 60ms each would put the last bar past 700ms, after the
    // reader has already taken the slide in.
    expect(staggerDelay(11)).toBeLessThanOrEqual(520);
    expect(staggerDelay(40)).toBe(520);
  });
});

describe('prefersReducedMotion', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('is true when the OS asks for it', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true }));

    expect(prefersReducedMotion()).toBe(true);
  });

  it('is false by default', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false }));

    expect(prefersReducedMotion()).toBe(false);
  });

  it('does not throw where matchMedia is missing or unhappy', () => {
    vi.stubGlobal('matchMedia', undefined);
    expect(prefersReducedMotion()).toBe(false);

    vi.stubGlobal('matchMedia', () => {
      throw new Error('nope');
    });
    expect(prefersReducedMotion()).toBe(false);
  });
});
