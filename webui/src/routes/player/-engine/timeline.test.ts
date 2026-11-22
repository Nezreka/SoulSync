import { describe, expect, it } from 'vitest';

import {
  bookTime,
  chapterAt,
  clock,
  locate,
  nextChapterStart,
  previousChapterStart,
} from './timeline';

const parts = [
  { index: 0, start: 0, duration: 100 },
  { index: 1, start: 100, duration: 50 },
];
const chapters = [
  { start: 0, title: 'One' },
  { start: 40, title: 'Two' },
  { start: 100, title: 'Three' },
];

describe('timeline', () => {
  it('maps the book clock to a part and back', () => {
    expect(locate(parts, 120)).toEqual({ index: 1, offset: 20 });
    expect(locate(parts, 0)).toEqual({ index: 0, offset: 0 });
    expect(locate(parts, -5)).toEqual({ index: 0, offset: 0 });
    expect(locate([], 30)).toEqual({ index: 0, offset: 30 });
    expect(bookTime(parts, 1, 20)).toBe(120);
  });

  it('finds chapters and steps through them', () => {
    expect(chapterAt(chapters, 50)).toBe(1);
    expect(chapterAt(chapters, 39.9)).toBe(1); // within the grace before a mark
    expect(nextChapterStart(chapters, 50)).toBe(100);
    expect(nextChapterStart(chapters, 120)).toBeNull();
    expect(previousChapterStart(chapters, 50)).toBe(40); // 10s in: to its start
    expect(previousChapterStart(chapters, 41)).toBe(0); // just started: the one before
    expect(previousChapterStart([], 41)).toBe(0);
  });

  it('formats a clock', () => {
    expect(clock(65)).toBe('1:05');
    expect(clock(3723)).toBe('1:02:03');
    expect(clock(Number.NaN)).toBe('0:00');
  });
});
