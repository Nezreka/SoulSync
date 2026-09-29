import { describe, expect, it } from 'vitest';

import {
  barBeatAt,
  barCount,
  barStartTime,
  beatInterval,
  clamp,
  downsamplePeaks,
  formatTime,
  qualityTier,
  slicesFromOnsets,
  suggestChopName,
  suggestChops,
  tempoBucket,
  visiblePeakSlice,
} from './-sample-studio.helpers';

describe('formatTime', () => {
  it('formats seconds as m:ss.t', () => {
    expect(formatTime(0)).toBe('0:00.0');
    expect(formatTime(83.456)).toBe('1:23.4');
    expect(formatTime(600)).toBe('10:00.0');
  });
  it('guards non-finite and negative input', () => {
    expect(formatTime(NaN)).toBe('0:00.0');
    expect(formatTime(-5)).toBe('0:00.0');
    expect(formatTime(Infinity)).toBe('0:00.0');
  });
});

describe('beat math', () => {
  it('beatInterval is 60/bpm and null-safe', () => {
    expect(beatInterval(120)).toBeCloseTo(0.5);
    expect(beatInterval(null)).toBeNull();
    expect(beatInterval(0)).toBeNull();
    expect(beatInterval(-4)).toBeNull();
  });
  it('barBeatAt gives 1-based bar/beat', () => {
    // 120 BPM -> 0.5s beats, 2s bars
    expect(barBeatAt(0, 120)).toEqual({ bar: 1, beat: 1, beatIndex: 0 });
    expect(barBeatAt(0.5, 120)).toEqual({ bar: 1, beat: 2, beatIndex: 1 });
    expect(barBeatAt(2.0, 120)).toEqual({ bar: 2, beat: 1, beatIndex: 4 });
    expect(barBeatAt(4.75, 120)).toEqual({ bar: 3, beat: 2, beatIndex: 9 });
  });
  it('barBeatAt is null without a BPM', () => {
    expect(barBeatAt(4, null)).toBeNull();
  });
  it('barStartTime and barCount agree', () => {
    expect(barStartTime(1, 120)).toBe(0);
    expect(barStartTime(3, 120)).toBeCloseTo(4);
    expect(barCount(8, 120)).toBe(4);
    expect(barCount(7.9, 120)).toBe(3);
    expect(barStartTime(2, null)).toBeNull();
  });
});

describe('downsamplePeaks', () => {
  it('reduces bucket count preserving extremes', () => {
    const min = [-0.1, -0.9, -0.2, -0.3];
    const max = [0.2, 0.4, 0.8, 0.1];
    const out = downsamplePeaks(min, max, 2);
    expect(out.min).toEqual([-0.9, -0.3]);
    expect(out.max).toEqual([0.4, 0.8]);
  });
  it('passes through when target >= length', () => {
    const out = downsamplePeaks([-0.5], [0.5], 4);
    expect(out.min).toEqual([-0.5]);
    expect(out.max).toEqual([0.5]);
  });
  it('handles empty input', () => {
    expect(downsamplePeaks([], [], 10)).toEqual({ min: [], max: [] });
  });
});

describe('qualityTier', () => {
  it('classifies by container first', () => {
    expect(qualityTier(900, 'song.flac')).toBe('lossless');
    expect(qualityTier(4500, 'song.flac')).toBe('hires');
    expect(qualityTier(null, 'song.wav')).toBe('lossless');
  });
  it('classifies lossy by bitrate', () => {
    expect(qualityTier(320, 'song.mp3')).toBe('high');
    expect(qualityTier(128, 'song.mp3')).toBe('other');
    expect(qualityTier(null, 'song.mp3')).toBe('other');
  });
  it('returns unknown with no signal', () => {
    expect(qualityTier(null, null)).toBe('unknown');
    expect(qualityTier(null, '')).toBe('unknown');
  });
});

describe('tempoBucket', () => {
  it('buckets BPM ranges', () => {
    expect(tempoBucket(90)).toBe('slow');
    expect(tempoBucket(110)).toBe('mid');
    expect(tempoBucket(130)).toBe('fast');
    expect(tempoBucket(150)).toBe('fastest');
    expect(tempoBucket(null)).toBeNull();
  });
});

describe('clamp', () => {
  it('clamps into range', () => {
    expect(clamp(5, 0, 10)).toBe(5);
    expect(clamp(-1, 0, 10)).toBe(0);
    expect(clamp(99, 0, 10)).toBe(10);
  });
});

describe('visiblePeakSlice', () => {
  const min = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9];
  const max = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19];

  it('returns the whole array for a full-track view', () => {
    const s = visiblePeakSlice(min, max, 100, 0, 100);
    expect(s.min).toEqual(min);
    expect(s.max).toEqual(max);
  });

  it('slices to the visible window when zoomed', () => {
    // 10 buckets over 100s → bucket 2..5 covers 20..50s.
    const s = visiblePeakSlice(min, max, 100, 20, 50);
    expect(s.min).toEqual([2, 3, 4]);
    expect(s.max).toEqual([12, 13, 14]);
  });

  it('clamps out-of-range windows', () => {
    const s = visiblePeakSlice(min, max, 100, -50, 500);
    expect(s.min).toEqual(min);
    expect(s.max).toEqual(max);
  });

  it('returns at least one bucket for a degenerate window', () => {
    const s = visiblePeakSlice(min, max, 100, 50, 50);
    expect(s.min.length).toBeGreaterThan(0);
  });
});

describe('slicesFromOnsets', () => {
  it('splits the region at onsets inside it', () => {
    const slices = slicesFromOnsets([0.5, 1.0, 1.5, 5.0], 0, 2);
    expect(slices).toEqual([
      { start: 0, end: 0.5 },
      { start: 0.5, end: 1.0 },
      { start: 1.0, end: 1.5 },
      { start: 1.5, end: 2 },
    ]);
  });

  it('returns the whole region when there are no onsets inside', () => {
    expect(slicesFromOnsets([5.0, 6.0], 0, 2)).toEqual([{ start: 0, end: 2 }]);
  });

  it('ignores onsets on the exact region edges', () => {
    expect(slicesFromOnsets([0, 2], 0, 2)).toEqual([{ start: 0, end: 2 }]);
  });

  it('handles a reversed region', () => {
    expect(slicesFromOnsets([1.0], 2, 0)).toEqual([
      { start: 0, end: 1.0 },
      { start: 1.0, end: 2 },
    ]);
  });

  it('returns nothing for an empty region', () => {
    expect(slicesFromOnsets([1.0], 1, 1)).toEqual([]);
  });
});

describe('suggestChopName', () => {
  it('builds a readable default name from the title and start time', () => {
    expect(suggestChopName('Midnight Groove', 12)).toBe('Midnight Groove · 0:12 chop');
  });

  it('falls back to Untitled for blank titles', () => {
    expect(suggestChopName('  ', 65)).toBe('Untitled · 1:05 chop');
    expect(suggestChopName(null, 0)).toBe('Untitled · 0:00 chop');
  });
});

describe('suggestChops', () => {
  const peaks = {
    min: Array.from({ length: 1500 }, (_, i) => -Math.abs(Math.sin(i / 37)) * 0.8),
    max: Array.from({ length: 1500 }, (_, i) => Math.abs(Math.sin(i / 37)) * 0.8),
  };
  // 120 BPM -> 2s bars; 60s -> 30 bars.
  const onsets = [2.1, 4.2, 10.5, 10.9, 11.3, 30.2, 50.1];

  it('returns bar-grid chops labeled by bar when BPM is known', () => {
    const out = suggestChops({ bpm: 120, durationS: 60, peaks, onsets, count: 4 });
    expect(out.length).toBeLessThanOrEqual(4);
    expect(out.length).toBeGreaterThan(0);
    for (const c of out) {
      expect(c.label).toMatch(/^Bars \d+–\d+$/);
      expect(c.end - c.start).toBeCloseTo(4, 1); // 2 bars @120bpm
      expect(c.end).toBeLessThanOrEqual(60);
    }
  });

  it('picks non-overlapping chops sorted by score', () => {
    const out = suggestChops({ bpm: 120, durationS: 60, peaks, onsets, count: 8 });
    for (let i = 1; i < out.length; i++)
      expect(out[i - 1].score).toBeGreaterThanOrEqual(out[i].score);
    for (let i = 0; i < out.length; i++)
      for (let j = i + 1; j < out.length; j++)
        expect(out[i].end <= out[j].start || out[j].end <= out[i].start).toBe(true);
  });

  it('is deterministic', () => {
    const a = suggestChops({ bpm: 120, durationS: 60, peaks, onsets });
    const b = suggestChops({ bpm: 120, durationS: 60, peaks, onsets });
    expect(a).toEqual(b);
  });

  it('falls back to time windows without a BPM', () => {
    const out = suggestChops({ bpm: null, durationS: 60, peaks, onsets, count: 3 });
    expect(out.length).toBe(3);
    for (const c of out) expect(c.label).toMatch(/^\d+:\d\d\.\d–\d+:\d\d\.\d$/);
  });

  it('returns nothing for empty input', () => {
    expect(suggestChops({ bpm: 120, durationS: 0, peaks, onsets })).toEqual([]);
    expect(suggestChops({ bpm: 120, durationS: -5, peaks, onsets })).toEqual([]);
  });
});
