import { beforeEach, describe, expect, it } from 'vitest';

import type { Marker, Skippable } from './timeline';

import {
  countdown,
  creditsUpNext,
  DEFAULT_SKIP,
  isFirstOfSitting,
  noteStarted,
  planFor,
  resetSitting,
  skipPrefs,
} from './skip';

const intro: Skippable = { kind: 'intro', label: 'Skip intro', to: 151, from: 61 };
const credits: Skippable = { kind: 'credits', label: 'Skip credits', to: 1400, from: 1300 };
const recap: Skippable = { kind: 'recap', label: 'Skip recap', to: 40, from: 0 };
const plain = { firstOfSitting: false, choseToWatch: false };

describe('the profile settings', () => {
  it('read with defaults, and nonsense reads as the default', () => {
    expect(skipPrefs(undefined)).toEqual(DEFAULT_SKIP);
    expect(
      skipPrefs({
        skip_intro: 'auto',
        skip_credits: 'never',
        skip_recap: 'bogus',
        next_at_credits: false,
      }),
    ).toEqual({ intro: 'auto', credits: 'never', recap: 'ask', nextAtCredits: false });
  });
});

describe('what a span shows', () => {
  it('ask is a button, never is nothing, auto counts down', () => {
    const p = skipPrefs({ skip_intro: 'ask', skip_credits: 'never', skip_recap: 'auto' });
    expect(planFor(intro, p, plain)).toBe('button');
    expect(planFor(credits, p, plain)).toBe('none');
    expect(planFor(recap, p, plain)).toBe('auto');
    expect(planFor(null, p, plain)).toBe('none');
  });

  it("the sitting's first episode plays its intro once: a button, not an auto-skip", () => {
    const p = skipPrefs({ skip_intro: 'auto', skip_credits: 'auto' });
    expect(planFor(intro, p, { firstOfSitting: true, choseToWatch: false })).toBe('button');
    expect(planFor(credits, p, { firstOfSitting: true, choseToWatch: false })).toBe('auto');
    expect(planFor(intro, p, plain)).toBe('auto');
  });

  it('a span someone chose to watch is never skipped by itself again', () => {
    const p = skipPrefs({ skip_intro: 'auto' });
    expect(planFor(intro, p, { firstOfSitting: false, choseToWatch: true })).toBe('button');
  });

  it('the countdown runs on the playback clock (a pause stops it)', () => {
    expect(countdown(100, 100)).toEqual({ progress: 0, left: 5 });
    expect(countdown(100, 102.5)).toEqual({ progress: 0.5, left: 2.5 });
    expect(countdown(100, 109)).toEqual({ progress: 1, left: 0 });
  });
});

describe('up next at the credits', () => {
  const markers: Marker[] = [
    { kind: 'intro', start: 61, end: 151, source: 'detected' },
    { kind: 'credits', start: 1300, end: 1400, source: 'detected' },
  ];
  const base = { markers, position: 1310, isEpisode: true, hasNext: true, prefs: DEFAULT_SKIP };

  it('starts where the credits start, for an episode with a next one', () => {
    expect(creditsUpNext(base)).toEqual({ start: 1300, scene: null, seconds: 10 });
    expect(creditsUpNext({ ...base, position: 1299 })).toBeNull();
    expect(creditsUpNext({ ...base, prefs: skipPrefs({ skip_credits: 'auto' }) })?.seconds).toBe(4);
  });

  it('nothing for a movie, the last episode, the setting off, or credits set to never', () => {
    expect(creditsUpNext({ ...base, isEpisode: false })).toBeNull();
    expect(creditsUpNext({ ...base, hasNext: false })).toBeNull();
    expect(creditsUpNext({ ...base, prefs: skipPrefs({ next_at_credits: false }) })).toBeNull();
    expect(creditsUpNext({ ...base, prefs: skipPrefs({ skip_credits: 'never' }) })).toBeNull();
    expect(creditsUpNext({ ...base, markers: [] })).toBeNull();
  });

  it('a scene after the credits holds it, and once the scene plays it steps aside', () => {
    const withScene: Marker[] = [
      { ...markers[1], end: 1370, post_credits: { start: 1370, end: 1400 } },
    ];
    expect(creditsUpNext({ ...base, markers: withScene })?.scene).toEqual({
      start: 1370,
      end: 1400,
    });
    expect(creditsUpNext({ ...base, markers: withScene, position: 1375 })).toBeNull();
  });
});

describe('a sitting', () => {
  beforeEach(() => resetSitting());

  it('the first video since the player sat idle is the first of the sitting', () => {
    expect(noteStarted('episode:1', 1000)).toBe(true);
    expect(noteStarted('episode:2', 2000)).toBe(false);
    expect(isFirstOfSitting('episode:1')).toBe(true);
    expect(isFirstOfSitting('episode:2')).toBe(false);
    // two hours with nothing played: a new sitting
    expect(noteStarted('episode:3', 2000 + 3 * 60 * 60 * 1000)).toBe(true);
  });
});
