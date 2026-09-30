import { describe, expect, it } from 'vitest';

import type { DiscoverMix } from './-discover.mixes';

import { greeting, moodForHour, quickTiles, QUICK_TILE_LIMIT } from './-discover.greeting';

const mix = (key: string): DiscoverMix => ({ key, title: key, tracks: [{}] });

describe('greeting', () => {
  it('follows the clock, edges included', () => {
    expect(greeting(4)).toBe('Up late');
    expect(greeting(5)).toBe('Good morning');
    expect(greeting(11)).toBe('Good morning');
    expect(greeting(12)).toBe('Good afternoon');
    expect(greeting(16)).toBe('Good afternoon');
    expect(greeting(17)).toBe('Good evening');
    expect(greeting(21)).toBe('Good evening');
    expect(greeting(22)).toBe('Up late');
  });
});

describe('moodForHour', () => {
  it('picks a mood for each part of the day', () => {
    expect(moodForHour(8)).toBe('mood_feel_good');
    expect(moodForHour(14)).toBe('mood_focus');
    expect(moodForHour(19)).toBe('mood_chill');
    expect(moodForHour(1)).toBe('mood_late_night');
  });
});

describe('quickTiles', () => {
  const mixes = [
    mix('release_radar'),
    mix('daily_mix_1'),
    mix('daily_mix_2'),
    mix('daily_mix_3'),
    mix('daily_mix_4'),
    mix('on_repeat'),
    mix('repeat_rewind'),
    mix('blend_2'),
  ];
  const moods = [mix('mood_chill'), mix('mood_focus')];

  it('leads with flow, then what you go back to, in that order', () => {
    const keys = quickTiles(mixes, moods, 19).map((t) => (t.kind === 'flow' ? 'flow' : t.mix.key));
    expect(keys).toEqual([
      'flow',
      'on_repeat',
      'daily_mix_1',
      'daily_mix_2',
      'mood_chill',
      'repeat_rewind',
      'blend_2',
    ]);
    expect(QUICK_TILE_LIMIT).toBe(7);
    expect(keys).toHaveLength(7);
  });

  it('never pads with filler when there is little to show', () => {
    const keys = quickTiles([mix('daily_mix_1')], [], 9).map((t) =>
      t.kind === 'flow' ? 'flow' : t.mix.key,
    );
    expect(keys).toEqual(['flow', 'daily_mix_1']);
  });

  it("puts the hour's mood in, and skips it when that mood doesn't exist", () => {
    const afternoon = quickTiles([], moods, 14).map((t) => (t.kind === 'mix' ? t.mix.key : 'flow'));
    expect(afternoon).toEqual(['flow', 'mood_focus']);
    const morning = quickTiles([], moods, 8).map((t) => (t.kind === 'mix' ? t.mix.key : 'flow'));
    expect(morning).toEqual(['flow']);
  });
});
