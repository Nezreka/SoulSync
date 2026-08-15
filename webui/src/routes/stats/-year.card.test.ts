import { describe, expect, it } from 'vitest';

import {
  DEFAULT_YEAR_CARD_OPTIONS,
  MAX_YEAR_CARD_STATS,
  YEAR_CARD_ASPECTS,
  YEAR_CARD_LAYOUTS,
  YEAR_CARD_PALETTES,
  buildYearCardModel,
  toggleCardStat,
  type YearCardOptions,
  type YearCardStatKey,
} from './-year.card';
import type { YearInListening } from './-year.types';

function makeYear(overrides: Partial<YearInListening> = {}): YearInListening {
  return {
    period: { start: '2025-09-01', end: '2026-08-14', label: 'Sep 2025 — Aug 2026', months: 12 },
    has_data: true,
    totals: { plays: 1247, minutes: 4320, artists: 88, albums: 210, tracks: 640, active_days: 233 },
    months: [],
    top_artists: [
      { name: 'Winter Band', plays: 410, months_on_top: 5, image_url: '/img/a1.jpg' },
      { name: 'Autumn Band', plays: 190, months_on_top: 2, image_url: '/img/a2.jpg' },
      { name: 'Spring Band', plays: 90, months_on_top: 1, image_url: '/img/a3.jpg' },
      { name: 'Summer Band', plays: 40, months_on_top: 0, image_url: '/img/a4.jpg' },
      { name: 'Fifth Band', plays: 10, months_on_top: 0, image_url: '/img/a5.jpg' },
    ],
    top_albums: [
      { name: 'Cold', artist: 'Winter Band', plays: 120, image_url: '/img/b1.jpg' },
      { name: 'Warm', artist: 'Summer Band', plays: 60, image_url: '/img/b2.jpg' },
      { name: 'Grey', artist: 'Autumn Band', plays: 40, image_url: '/img/b3.jpg' },
      { name: 'Green', artist: 'Spring Band', plays: 20, image_url: '/img/b4.jpg' },
    ],
    top_tracks: [
      {
        name: 'The One Song',
        artist: 'Winter Band',
        album: 'Cold',
        plays: 61,
        first_played: null,
        last_played: null,
        image_url: '/img/b1.jpg',
      },
    ],
    discoveries: [
      { name: 'Brand New Act', first_played: '2026-02-01', plays: 34, image_url: '/img/c1.jpg' },
    ],
    peak_day: { date: '2026-05-20', plays: 47 },
    top_hour: { hour: 22, plays: 300 },
    ...overrides,
  };
}

const opts = (over: Partial<YearCardOptions> = {}): YearCardOptions => ({
  ...DEFAULT_YEAR_CARD_OPTIONS,
  ...over,
});

describe('buildYearCardModel — content', () => {
  it('sets the stats as label/value pairs, not prose', () => {
    // Split, the draw pass can align the numbers — most of what makes a
    // generated card look designed rather than dumped.
    const model = buildYearCardModel(makeYear());

    expect(model.stats).toEqual([
      { label: 'Plays', value: '1,247' },
      { label: 'Listening time', value: '3 days' },
      { label: 'Artists', value: '88' },
      { label: 'Days with music', value: '233' },
    ]);
  });

  it('keeps stats in DEFINITION order however they were picked', () => {
    // A card whose rows reshuffle as you tick boxes feels broken, not
    // configurable.
    const model = buildYearCardModel(
      makeYear(),
      opts({ stats: ['active_days', 'plays', 'albums'] }),
    );

    expect(model.stats.map((s) => s.label)).toEqual(['Plays', 'Albums', 'Days with music']);
  });

  it('never renders more rows than fit on a card', () => {
    const all: YearCardStatKey[] = ['plays', 'minutes', 'artists', 'albums', 'tracks', 'active_days'];

    expect(buildYearCardModel(makeYear(), opts({ stats: all })).stats).toHaveLength(
      MAX_YEAR_CARD_STATS,
    );
  });

  it('leads the highlight with the number one and what was on repeat', () => {
    expect(buildYearCardModel(makeYear()).highlight).toEqual({
      label: 'Your number one',
      name: 'Winter Band',
      sub: 'On repeat: The One Song',
    });
  });

  it('falls back to a play count when there is no top track to name', () => {
    expect(buildYearCardModel(makeYear({ top_tracks: [] })).highlight?.sub).toBe('410 plays');
  });

  it('omits the highlight rather than printing a blank one', () => {
    const model = buildYearCardModel(makeYear({ top_artists: [] }));

    expect(model.highlight).toBeNull();
    expect(model.runnersUp).toEqual([]);
  });

  it('names ranks two through four as runners-up', () => {
    expect(buildYearCardModel(makeYear()).runnersUp).toEqual([
      'Autumn Band',
      'Spring Band',
      'Summer Band',
    ]);
  });

  it('drops the runners-up when they are switched off', () => {
    expect(buildYearCardModel(makeYear(), opts({ showRunnersUp: false })).runnersUp).toEqual([]);
  });
});

describe('buildYearCardModel — shape', () => {
  it('takes its dimensions from the chosen aspect', () => {
    const story = buildYearCardModel(makeYear(), opts({ aspect: 'story' }));

    expect(story.width).toBe(YEAR_CARD_ASPECTS.story.width);
    expect(story.height).toBe(YEAR_CARD_ASPECTS.story.height);
  });

  it('scales the type with the card, so a story is not a post with more air', () => {
    const post = buildYearCardModel(makeYear(), opts({ aspect: 'post' }));
    const story = buildYearCardModel(makeYear(), opts({ aspect: 'story' }));
    const square = buildYearCardModel(makeYear(), opts({ aspect: 'square' }));

    expect(post.scale).toBe(1);
    expect(story.scale).toBeGreaterThan(1);
    expect(square.scale).toBeLessThan(1);
  });

  it('falls back to a real aspect if the option is unrecognised', () => {
    // Options can arrive from state persisted by an older build.
    const model = buildYearCardModel(makeYear(), opts({ aspect: 'nonsense' as never }));

    expect(model.width).toBe(YEAR_CARD_ASPECTS.post.width);
  });

  it('falls back to a real layout too', () => {
    expect(buildYearCardModel(makeYear(), opts({ layout: 'nope' as never })).layout).toBe('stack');
  });

  it('falls back to a real palette', () => {
    expect(buildYearCardModel(makeYear(), opts({ theme: 'nope' as never })).palette).toBe(
      YEAR_CARD_PALETTES.midnight,
    );
  });
});

describe('buildYearCardModel — artwork', () => {
  it('takes exactly as much art as the layout has slots', () => {
    for (const layout of ['stack', 'poster', 'mosaic', 'minimal'] as const) {
      const model = buildYearCardModel(makeYear(), opts({ layout }));

      expect(model.artUrls.length).toBeLessThanOrEqual(YEAR_CARD_LAYOUTS[layout].artSlots);
    }
  });

  it('gives the poster exactly one cover and the mosaic a wall', () => {
    expect(buildYearCardModel(makeYear(), opts({ layout: 'poster' })).artUrls).toHaveLength(1);
    expect(
      buildYearCardModel(makeYear(), opts({ layout: 'mosaic' })).artUrls.length,
    ).toBeGreaterThan(4);
  });

  it('gives minimal none, by definition', () => {
    expect(buildYearCardModel(makeYear(), opts({ layout: 'minimal' })).artUrls).toEqual([]);
  });

  it('never repeats an image', () => {
    // The top track's album art and the top album's art are commonly the same
    // file; a wall showing one square twice reads as a bug.
    const model = buildYearCardModel(makeYear(), opts({ layout: 'mosaic' }));

    expect(new Set(model.artUrls).size).toBe(model.artUrls.length);
  });

  it('drops every image when artwork is switched off', () => {
    expect(buildYearCardModel(makeYear(), opts({ artwork: false })).artUrls).toEqual([]);
  });

  it('skips rows with no artwork rather than leaving holes', () => {
    const year = makeYear({
      top_artists: [{ name: 'Artless', plays: 5, months_on_top: 0, image_url: null }],
      top_albums: [],
      top_tracks: [],
      discoveries: [],
    });

    expect(buildYearCardModel(year).artUrls).toEqual([]);
  });
});

describe('toggleCardStat', () => {
  it('adds and removes', () => {
    expect(toggleCardStat(['plays'], 'albums')).toEqual(['plays', 'albums']);
    expect(toggleCardStat(['plays', 'albums'], 'albums')).toEqual(['plays']);
  });

  it('refuses to empty the card', () => {
    // A card with zero stats is not minimal, it is broken.
    expect(toggleCardStat(['plays'], 'plays')).toEqual(['plays']);
  });

  it('refuses to overfill it', () => {
    const full: YearCardStatKey[] = ['plays', 'minutes', 'artists', 'albums', 'tracks'];

    expect(toggleCardStat(full, 'active_days')).toEqual(full);
  });

  it('still lets you swap when full', () => {
    const full: YearCardStatKey[] = ['plays', 'minutes', 'artists', 'albums', 'tracks'];
    const freed = toggleCardStat(full, 'tracks');

    expect(toggleCardStat(freed, 'active_days')).toContain('active_days');
  });
});

describe('filename', () => {
  it('names the file after the period and layout', () => {
    expect(buildYearCardModel(makeYear()).filename).toBe(
      'soulsync-year-sep-2025-aug-2026-stack.png',
    );
  });

  it('changes with the layout, so saving two does not overwrite one', () => {
    const a = buildYearCardModel(makeYear(), opts({ layout: 'poster' })).filename;
    const b = buildYearCardModel(makeYear(), opts({ layout: 'mosaic' })).filename;

    expect(a).not.toBe(b);
  });

  it('still produces a filename when the period has no label', () => {
    const year = makeYear({ period: { start: '', end: '', label: '', months: 12 } });

    expect(buildYearCardModel(year).filename).toBe('soulsync-year-in-listening-stack.png');
  });
});

describe('an empty year', () => {
  it('builds a card rather than throwing', () => {
    const empty = makeYear({
      has_data: false,
      totals: { plays: 0, minutes: 0, artists: 0, albums: 0, tracks: 0, active_days: 0 },
      top_artists: [],
      top_albums: [],
      top_tracks: [],
      discoveries: [],
    });

    const model = buildYearCardModel(empty);

    expect(model.stats[0]).toEqual({ label: 'Plays', value: '0' });
    expect(model.highlight).toBeNull();
    expect(model.artUrls).toEqual([]);
  });
});
