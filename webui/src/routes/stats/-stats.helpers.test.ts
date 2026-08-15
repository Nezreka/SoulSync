import { describe, expect, it } from 'vitest';

import {
  formatBytes,
  formatDbStorageValue,
  formatListeningTime,
  formatHourLabel,
  formatPeakSlot,
  formatRelativePlayedAt,
  heatIntensity,
  getTopArtistBubbles,
  groupDbStorageTables,
  hasStatsData,
  isNewSincePrevious,
  statDelta,
  visibleStatsEnrichmentServices,
} from './-stats.helpers';
import { statsSearchSchema } from './-stats.types';

describe('statsSearchSchema', () => {
  it('falls back to 7d for unknown ranges', () => {
    expect(statsSearchSchema.parse({ range: 'bad' })).toEqual({ range: '7d', tab: 'listening' });
  });

  it('keeps known ranges', () => {
    expect(statsSearchSchema.parse({ range: '12m' })).toEqual({ range: '12m', tab: 'listening' });
  });

  it('defaults to the listening tab — the personal facts are the point', () => {
    expect(statsSearchSchema.parse({}).tab).toBe('listening');
  });

  it('keeps a known tab and falls back on a bad one', () => {
    expect(statsSearchSchema.parse({ tab: 'library' }).tab).toBe('library');
    expect(statsSearchSchema.parse({ tab: 'nonsense' }).tab).toBe('listening');
  });
});

describe('stats helpers', () => {
  it('detects whether the page has listening data', () => {
    expect(hasStatsData({ total_plays: 0 })).toBe(false);
    expect(hasStatsData({ total_plays: 4 })).toBe(true);
  });

  it('formats listening time and bytes', () => {
    expect(formatListeningTime(3_900_000)).toBe('1h 5m');
    expect(formatBytes(2_097_152)).toBe('2.00 MB');
  });

  it('formats relative recent-play times', () => {
    const now = new Date('2026-05-14T12:00:00.000Z').getTime();
    expect(formatRelativePlayedAt('2026-05-14T11:15:00.000Z', now)).toBe('45m ago');
    expect(formatRelativePlayedAt('2026-05-14T08:00:00.000Z', now)).toBe('4h ago');
  });

  it('groups db storage rows into Other after the top eight', () => {
    const grouped = groupDbStorageTables(
      Array.from({ length: 10 }, (_, index) => ({
        name: `table_${index + 1}`,
        size: index + 1,
      })),
    );

    expect(grouped).toHaveLength(9);
    expect(grouped.at(-1)).toEqual({ name: 'Other', size: 19 });
  });

  it('formats db storage by method', () => {
    expect(formatDbStorageValue(2_097_152, 'dbstat')).toBe('2.0 MB');
    expect(formatDbStorageValue(1240, 'rowcount')).toBe('1,240 rows');
  });

  it('shapes top artist bubbles from the highest-play artist', () => {
    const bubbles = getTopArtistBubbles([
      { name: 'A', play_count: 20 },
      { name: 'B', play_count: 10 },
    ]);

    expect(bubbles[0]?.percent).toBe(100);
    expect(bubbles[1]?.percent).toBe(50);
  });

  it('hides experimental enrichment sources unless enabled', () => {
    const keysWhenOff = visibleStatsEnrichmentServices(false, false).map((s) => s.key);
    expect(keysWhenOff).not.toContain('jiosaavn');
    expect(keysWhenOff).not.toContain('bandcamp');

    const keysWhenOn = visibleStatsEnrichmentServices(true, true).map((s) => s.key);
    expect(keysWhenOn).toContain('jiosaavn');
    expect(keysWhenOn).toContain('bandcamp');

    // Each toggles independently.
    expect(visibleStatsEnrichmentServices(false, true).map((s) => s.key)).toContain('bandcamp');
    expect(visibleStatsEnrichmentServices(false, true).map((s) => s.key)).not.toContain('jiosaavn');
  });
});

// ── statDelta (stats P1) ─────────────────────────────────────────────────────

describe('statDelta', () => {
  it('reports growth and decline against the previous period', () => {
    expect(statDelta(130, 100)).toEqual({ pct: 30, direction: 'up' });
    expect(statDelta(70, 100)).toEqual({ pct: 30, direction: 'down' });
  });

  it('calls no change flat rather than a 0% arrow', () => {
    expect(statDelta(100, 100)).toEqual({ pct: 0, direction: 'flat' });
  });

  it('rounds before choosing a direction', () => {
    // A +0.4% change displays as "0%". An up-arrow beside "0%" reads as a bug.
    expect(statDelta(1004, 1000)).toEqual({ pct: 0, direction: 'flat' });
    expect(statDelta(996, 1000)).toEqual({ pct: 0, direction: 'flat' });
  });

  it('refuses to turn growth from zero into a percentage', () => {
    // 0 → 5 is not 500% and not ∞. It is "new", which the caller renders
    // instead. Returning a number here is how a stats page starts lying.
    expect(statDelta(5, 0)).toBeNull();
    expect(isNewSincePrevious(5, 0)).toBe(true);
  });

  it('has no delta when there is no previous period at all', () => {
    // range 'all' — the backend sends null, not zeros.
    expect(statDelta(5, null)).toBeNull();
    expect(statDelta(5, undefined)).toBeNull();
    // ...and that is NOT "new" — there is no previous period to be new against.
    expect(isNewSincePrevious(5, null)).toBe(false);
  });

  it('has no delta on a partial payload', () => {
    expect(statDelta(undefined, 100)).toBeNull();
    expect(statDelta(Number.NaN, 100)).toBeNull();
    expect(statDelta(100, Number.NaN)).toBeNull();
  });

  it('handles a drop to zero — that is a real -100%', () => {
    expect(statDelta(0, 40)).toEqual({ pct: 100, direction: 'down' });
  });

  it('is not fooled by both sides being zero', () => {
    // No plays then, no plays now. Not "new", not a percentage.
    expect(statDelta(0, 0)).toBeNull();
    expect(isNewSincePrevious(0, 0)).toBe(false);
  });
});

// ── the listening clock (stats P3) ───────────────────────────────────────────

describe('the listening clock helpers', () => {
  it('labels hours the way people say them', () => {
    expect(formatHourLabel(0)).toBe('12am');
    expect(formatHourLabel(9)).toBe('9am');
    expect(formatHourLabel(12)).toBe('12pm');
    expect(formatHourLabel(21)).toBe('9pm');
    expect(formatHourLabel(23)).toBe('11pm');
  });

  it('gives any play a visible floor', () => {
    // One play beside a peak of 300 is the most interesting cell on the chart.
    // A linear ramp from zero renders it invisible.
    expect(heatIntensity(1, 300)).toBeGreaterThan(0.15);
    expect(heatIntensity(0, 300)).toBe(0);
  });

  it('scales relative to the busiest cell, not an absolute count', () => {
    // The question is when YOU listen, not how you compare to anyone else.
    expect(heatIntensity(5, 10)).toBeCloseTo(heatIntensity(50, 100));
    expect(heatIntensity(10, 10)).toBe(1);
  });

  it('survives a peak of zero without dividing by it', () => {
    expect(heatIntensity(0, 0)).toBe(0);
    expect(heatIntensity(3, 0)).toBe(0);
  });

  it('names the peak slot, and stays quiet when there is not one', () => {
    expect(formatPeakSlot(3, 21)).toBe('Wed 9pm');
    expect(formatPeakSlot(0, 0)).toBe('Sun 12am');
    expect(formatPeakSlot(null, 21)).toBeNull();
    expect(formatPeakSlot(3, null)).toBeNull();
    expect(formatPeakSlot(9, 1)).toBeNull();
  });
});
