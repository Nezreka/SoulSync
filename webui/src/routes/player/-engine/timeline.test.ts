import { describe, expect, it } from 'vitest';

import {
  bookTime,
  chapterAt,
  clock,
  locate,
  nextChapterStart,
  creditsStart,
  markerAt,
  type Marker,
  previousChapterStart,
  skippableAt,
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

describe("skip intro, from the file's own chapter names", () => {
  const chapters = [
    { start: 0, title: 'Recap' },
    { start: 40, title: 'Opening Credits' },
    { start: 130, title: 'Part One' },
    { start: 1300, title: 'End Credits' },
  ];

  it('offers the skip while that chapter plays, to where the next one starts', () => {
    expect(skippableAt(chapters, 5, 1400)).toEqual({
      kind: 'recap',
      label: 'Skip recap',
      to: 40,
      from: 0,
    });
    expect(skippableAt(chapters, 60, 1400)).toEqual({
      kind: 'intro',
      label: 'Skip intro',
      to: 130,
      from: 40,
    });
    expect(skippableAt(chapters, 500, 1400)).toBeNull();
  });

  it('credits at the end skip to the end, so the next episode comes up', () => {
    expect(skippableAt(chapters, 1310, 1400)).toEqual({
      kind: 'credits',
      label: 'Skip credits',
      to: 1400,
      from: 1300,
    });
  });

  it('offers nothing in the last two seconds, or with no length to go to', () => {
    expect(skippableAt(chapters, 128.5, 1400)).toBeNull();
    expect(skippableAt(chapters, 1310, 0)).toBeNull();
  });

  it('a chapter is not an intro because a word is in its name, or when it is too long', () => {
    expect(
      skippableAt(
        [
          { start: 0, title: 'Introducing the Team' },
          { start: 90, title: 'x' },
        ],
        5,
        500,
      ),
    ).toBeNull();
    expect(
      skippableAt(
        [
          { start: 0, title: 'Intro' },
          { start: 900, title: 'x' },
        ],
        5,
        2000,
      ),
    ).toBeNull();
    expect(
      skippableAt(
        [
          { start: 0, title: 'OP' },
          { start: 90, title: 'x' },
        ],
        5,
        500,
      )?.kind,
    ).toBe('intro');
    expect(skippableAt([], 5, 500)).toBeNull();
  });
});

describe('skip markers from the server', () => {
  const markers: Marker[] = [
    { kind: 'recap', start: 0, end: 42.5, source: 'detected' },
    { kind: 'intro', start: 61.25, end: 151, source: 'user', label: 'Skip intro' },
    {
      kind: 'credits',
      start: 1300,
      end: 1380,
      source: 'detected',
      post_credits: { start: 1380, end: 1400 },
    },
    { kind: 'commercial', start: 700, end: 760, source: 'sidecar' },
  ];

  it('the server markers win over chapter names', () => {
    const chapters = [
      { start: 0, title: 'Intro' },
      { start: 90, title: 'Part One' },
    ];
    expect(skippableAt(chapters, 70, 1400, markers)).toEqual({
      kind: 'intro',
      label: 'Skip intro',
      to: 151,
      from: 61.25,
      postCredits: null,
    });
    // a chapter "Intro" doesn't count when the server knows better
    expect(skippableAt(chapters, 10, 1400, markers)?.kind).toBe('recap');
    expect(skippableAt(chapters, 55, 1400, markers)).toBeNull();
  });

  it('shows half a second early, so a seek onto the edge still gets it', () => {
    expect(skippableAt([], 60.9, 1400, markers)?.kind).toBe('intro');
    expect(skippableAt([], 60.5, 1400, markers)).toBeNull();
  });

  it('credits skip to the scene after them, with the hint', () => {
    const s = skippableAt([], 1310, 1400, markers);
    expect(s?.to).toBe(1380);
    expect(s?.postCredits).toEqual({ start: 1380, end: 1400 });
    expect(creditsStart(markers)).toBe(1300);
    expect(creditsStart([])).toBeNull();
  });

  it('a commercial span is no skip button, and nothing in the last two seconds', () => {
    expect(markerAt(markers, 720)).toBeNull();
    expect(skippableAt([], 149.5, 1400, markers)).toBeNull();
  });
});
