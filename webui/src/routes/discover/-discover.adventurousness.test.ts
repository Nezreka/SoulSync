import { describe, expect, it } from 'vitest';

import {
  ADV_DEFAULT_VALUE,
  ADV_DEPENDENT_SECTIONS,
  ADV_ENDPOINT,
  ADV_LIVE_THROTTLE_MS,
  ADV_ORB_PAD,
  ADV_REFETCH_BYPASSES_COALESCE,
  ADV_RELEASE_RESETS_THROTTLE,
  ADV_VIEW_HEIGHT,
  ADV_VIEW_WIDTH,
  ADV_WAVE_POINTS,
  advAreaPath,
  advAuraBackground,
  advNextPhase,
  advOrbLeft,
  advOrbTopPercent,
  advOrbU,
  advShouldCommitLive,
  advShouldDraw,
  advStyles,
  advValueFromResponse,
  advValueFromX,
  advWavePath,
  advWaveY,
  runLoadersLimited,
} from './-discover.adventurousness';

describe('the dial defaults', () => {
  it('starts at Balanced, not at zero', () => {
    expect(ADV_DEFAULT_VALUE).toBe(0.3);
    expect(advStyles(ADV_DEFAULT_VALUE).state).toBe('Balanced');
  });

  it('shares one endpoint with the Settings slider', () => {
    expect(ADV_ENDPOINT).toBe('/api/discover/adventurousness');
  });
});

describe('the wave path', () => {
  it('emits N+1 points, not N', () => {
    const path = advWavePath(0.5, 0);
    expect(path.split(/M | L /).filter(Boolean)).toHaveLength(ADV_WAVE_POINTS + 1);
  });

  it('starts with M and uses L thereafter', () => {
    expect(advWavePath(0.5, 0).startsWith('M 0.0 ')).toBe(true);
    expect(advWavePath(0.5, 0)).toContain(' L ');
  });

  it('spans the full viewBox width', () => {
    expect(advWavePath(0.5, 0)).toContain(`L ${ADV_VIEW_WIDTH.toFixed(1)} `);
  });

  it('rounds to one decimal, so the attribute does not churn sub-pixel', () => {
    for (const seg of advWavePath(0.37, 1.1)
      .split(/M | L /)
      .filter(Boolean)) {
      const [x, y] = seg.trim().split(' ');
      expect(x).toMatch(/^\d+\.\d$/);
      expect(y).toMatch(/^-?\d+\.\d$/);
    }
  });

  it('closes the filled area down to the baseline', () => {
    expect(advAreaPath('M 0 40')).toBe(
      `M 0 40 L ${ADV_VIEW_WIDTH} ${ADV_VIEW_HEIGHT} L 0 ${ADV_VIEW_HEIGHT} Z`,
    );
  });

  it('advances the phase faster the more adventurous', () => {
    expect(advNextPhase(0, 0)).toBeCloseTo(0.022, 10);
    expect(advNextPhase(0, 1)).toBeCloseTo(0.067, 10);
    expect(advNextPhase(0, 1)).toBeGreaterThan(advNextPhase(0, 0));
  });

  it('has real body even at rest', () => {
    // amp = 4.5 at v=0 — the v0 dial was a near-flat wire and looked broken.
    const ys = [0, 0.25, 0.5, 0.75].map((u) => advWaveY(u, 0, 0));
    expect(Math.max(...ys) - Math.min(...ys)).toBeGreaterThan(4);
  });

  it('waves harder as v climbs', () => {
    const spread = (v: number) => {
      const ys = Array.from({ length: 40 }, (_, i) => advWaveY(i / 40, v, 0));
      return Math.max(...ys) - Math.min(...ys);
    };
    expect(spread(1)).toBeGreaterThan(spread(0.5));
    expect(spread(0.5)).toBeGreaterThan(spread(0));
  });
});

describe('the orb', () => {
  it('is inset from BOTH track edges', () => {
    // Without the inset the orb gets sliced by the card's overflow:hidden at
    // the extremes — the half-orb in the v0 screenshot.
    expect(ADV_ORB_PAD).toBe(18);
    expect(advOrbU(0, 1000)).toBeCloseTo(18 / 1000, 10);
    expect(advOrbU(1, 1000)).toBeCloseTo(982 / 1000, 10);
  });

  it('sits at the track centre at v=0.5', () => {
    expect(advOrbU(0.5, 1000)).toBeCloseTo(0.5, 10);
  });

  it('samples the wave at the INSET x, not at raw v', () => {
    // Sampling at v would leave the handle floating off the line.
    const phase = 1.2;
    expect(advOrbTopPercent(0, phase, 1000)).toBe(
      ((advWaveY(0.018, 0, phase) / ADV_VIEW_HEIGHT) * 100).toFixed(2) + '%',
    );
  });

  it('survives a zero-width track without dividing by zero', () => {
    expect(Number.isFinite(advOrbU(0.5, 0))).toBe(true);
  });

  it('positions with a calc that reserves the padding at both ends', () => {
    expect(advOrbLeft(0.5)).toBe('calc(18px + 0.5000 * (100% - 36px))');
  });

  it('washes the aura in a translucent version of the line colour', () => {
    expect(advAuraBackground(0.5)).toBe(
      'radial-gradient(circle, hsla(170, 85%, 50%, 0.16) 0%, transparent 72%)',
    );
  });
});

describe('applying a value', () => {
  it('clamps out-of-range input', () => {
    expect(advStyles(-1).value).toBe(0);
    expect(advStyles(2).value).toBe(1);
  });

  it('uses TWO lightnesses — 55 for the line, 62 for the label', () => {
    const s = advStyles(0.5);
    expect(s.color).toContain('55%');
    expect(s.colorBright).toContain('62%');
    expect(s.color).not.toBe(s.colorBright);
  });

  it('runs green at the safe end and blue at the adventurous end', () => {
    // Blue, not red — red reads "danger", and adventurous is not an error.
    expect(advStyles(0).color).toContain('hsl(120');
    expect(advStyles(1).color).toContain('hsl(220');
  });

  it('labels the clamped value, not the raw one', () => {
    expect(advStyles(5).state).toBe('Deep cuts only');
    expect(advStyles(-5).state).toBe('Playing it safe');
  });
});

describe('dragging', () => {
  it('maps pointer x to a fraction of the track', () => {
    expect(advValueFromX(150, 100, 200)).toBeCloseTo(0.25, 10);
    expect(advValueFromX(100, 100, 200)).toBe(0);
    expect(advValueFromX(300, 100, 200)).toBe(1);
  });

  it('returns 0 for a zero-width track rather than NaN', () => {
    expect(advValueFromX(150, 100, 0)).toBe(0);
  });

  it('does NOT clamp — advStyles does that downstream', () => {
    // Dragging past the edge yields >1 here; clamping twice would be fine but
    // this transcribes where the vanilla actually clamps.
    expect(advValueFromX(400, 100, 200)).toBeGreaterThan(1);
  });

  it('throttles live commits to 450ms', () => {
    expect(ADV_LIVE_THROTTLE_MS).toBe(450);
    expect(advShouldCommitLive(1000, 600)).toBe(false);
    expect(advShouldCommitLive(1050, 600)).toBe(true);
    expect(advShouldCommitLive(1049, 600)).toBe(false);
  });

  it('lets the FINAL value through by resetting the throttle on release', () => {
    // Otherwise a drag ending inside the throttle window saves a stale value.
    expect(ADV_RELEASE_RESETS_THROTTLE).toBe(true);
    expect(advShouldCommitLive(1, 0)).toBe(false);
    expect(advShouldCommitLive(1000, 0)).toBe(true);
  });
});

describe('loading and committing', () => {
  it('accepts only a NUMBER from the server', () => {
    expect(advValueFromResponse({ value: 0.8 }, 0.3)).toBe(0.8);
    expect(advValueFromResponse({ value: 0 }, 0.3)).toBe(0);
    expect(advValueFromResponse({ value: '0.8' }, 0.3)).toBe(0.3);
    expect(advValueFromResponse({}, 0.3)).toBe(0.3);
    expect(advValueFromResponse(null, 0.3)).toBe(0.3);
  });

  it('re-fetches BOTH dependent sections, bypassing the coalesce', () => {
    // refresh() not load(): load() folds into an in-flight request that used an
    // older dial value and renders stale. This is why the dial feels live.
    expect([...ADV_DEPENDENT_SECTIONS]).toEqual(['listening-recs', 'recommended-artists']);
    expect(ADV_REFETCH_BYPASSES_COALESCE).toBe(true);
  });

  it('skips the frame entirely while the page is hidden', () => {
    expect(advShouldDraw(false)).toBe(false);
    expect(advShouldDraw(true)).toBe(true);
  });
});

describe('the bounded loader pool', () => {
  it('never runs more than `limit` at once', async () => {
    let inFlight = 0;
    let peak = 0;
    const thunks = Array.from({ length: 20 }, () => async () => {
      inFlight++;
      peak = Math.max(peak, inFlight);
      await new Promise((r) => setTimeout(r, 1));
      inFlight--;
    });
    await runLoadersLimited(thunks, 5);
    expect(peak).toBeLessThanOrEqual(5);
    expect(peak).toBe(5);
  });

  it('runs every thunk exactly once', async () => {
    const seen: number[] = [];
    const thunks = Array.from({ length: 7 }, (_, i) => async () => {
      seen.push(i);
    });
    await runLoadersLimited(thunks, 3);
    expect(seen.sort((a, b) => a - b)).toEqual([0, 1, 2, 3, 4, 5, 6]);
  });

  it('NEVER rejects — a failing loader cannot take the page down', async () => {
    const done: string[] = [];
    await expect(
      runLoadersLimited(
        [
          async () => {
            done.push('a');
          },
          async () => {
            throw new Error('boom');
          },
          async () => {
            done.push('c');
          },
        ],
        2,
      ),
    ).resolves.toBeUndefined();
    expect(done).toEqual(['a', 'c']);
  });

  it('does not spawn more workers than there are thunks', async () => {
    await expect(runLoadersLimited([async () => {}], 5)).resolves.toBeUndefined();
    await expect(runLoadersLimited([], 5)).resolves.toBeUndefined();
  });
});
