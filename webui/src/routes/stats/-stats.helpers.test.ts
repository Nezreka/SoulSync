import { describe, expect, it } from 'vitest';

import {
  formatBytes,
  formatDbStorageValue,
  formatListeningTime,
  formatRelativePlayedAt,
  getTopArtistBubbles,
  groupDbStorageTables,
  hasStatsData,
} from './-stats.helpers';
import { statsSearchSchema } from './-stats.types';

describe('statsSearchSchema', () => {
  it('falls back to 7d for unknown ranges', () => {
    expect(statsSearchSchema.parse({ range: 'bad' })).toEqual({ range: '7d' });
  });

  it('keeps known ranges', () => {
    expect(statsSearchSchema.parse({ range: '12m' })).toEqual({ range: '12m' });
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
});
