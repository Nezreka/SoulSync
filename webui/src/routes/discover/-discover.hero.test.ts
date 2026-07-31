import { describe, expect, it } from 'vitest';

import type { DiscoverHeroArtist } from './-discover.types';

import {
  HERO_EMPTY_SUBTITLE,
  HERO_EMPTY_TITLE,
  HERO_MAX_GENRES,
  HERO_SLIDE_MS,
  HERO_WATCHLIST_ICON,
  heroArtistId,
  heroAutoAdvances,
  heroGenres,
  heroJumpIndex,
  heroNextIndex,
  heroPopularityClass,
  heroShowsPopularity,
  heroWatchlistLabel,
} from './-discover.hero';

const artist = (over: Partial<DiscoverHeroArtist> = {}): DiscoverHeroArtist =>
  ({ artist_id: 'a1', artist_name: 'Aphex Twin', ...over }) as DiscoverHeroArtist;

describe('the artist id fallback chain', () => {
  it('prefers artist_id, which the backend already resolved for the active source', () => {
    const a = artist({ artist_id: 'active', spotify_artist_id: 'sp', itunes_artist_id: 'it' });
    expect(heroArtistId(a)).toBe('active');
  });

  it('falls back to spotify, then itunes', () => {
    expect(heroArtistId(artist({ artist_id: null, spotify_artist_id: 'sp' }))).toBe('sp');
    expect(
      heroArtistId(artist({ artist_id: null, spotify_artist_id: null, itunes_artist_id: 'it' })),
    ).toBe('it');
  });

  it('is null when the artist has no usable id at all', () => {
    expect(heroArtistId(artist({ artist_id: null }))).toBeNull();
    expect(heroArtistId(null)).toBeNull();
    expect(heroArtistId(undefined)).toBeNull();
  });
});

describe('popularity', () => {
  it('is hidden when absent', () => {
    expect(heroShowsPopularity(artist())).toBe(false);
  });

  it('is hidden at ZERO, which means "no data" not "unpopular"', () => {
    // A "0/100 Popularity" badge reads as a judgement rather than an absence.
    expect(heroShowsPopularity(artist({ popularity: 0 }))).toBe(false);
  });

  it('is shown for any positive value', () => {
    expect(heroShowsPopularity(artist({ popularity: 1 }))).toBe(true);
    expect(heroShowsPopularity(artist({ popularity: 100 }))).toBe(true);
  });

  it('bands at the vanilla thresholds', () => {
    expect(heroPopularityClass(100)).toBe('high');
    expect(heroPopularityClass(80)).toBe('high'); //    boundary is inclusive
    expect(heroPopularityClass(79)).toBe('medium');
    expect(heroPopularityClass(50)).toBe('medium'); //  boundary is inclusive
    expect(heroPopularityClass(49)).toBe('low');
    expect(heroPopularityClass(0)).toBe('low');
  });
});

describe('genres', () => {
  it('shows at most three', () => {
    expect(HERO_MAX_GENRES).toBe(3);
    expect(heroGenres(artist({ genres: ['a', 'b', 'c', 'd', 'e'] }))).toEqual(['a', 'b', 'c']);
  });

  it('shows fewer without padding', () => {
    expect(heroGenres(artist({ genres: ['a'] }))).toEqual(['a']);
  });

  it('copes with the field being absent or not an array', () => {
    expect(heroGenres(artist())).toEqual([]);
    expect(heroGenres(artist({ genres: 'techno' as unknown as string[] }))).toEqual([]);
    expect(heroGenres(null)).toEqual([]);
  });
});

describe('the watchlist button', () => {
  it('uses the SAME icon in both states', () => {
    // Only the label and the `watching` class change; the stylesheet keys off
    // the class. Two different icons would be a visual regression.
    expect(HERO_WATCHLIST_ICON).toBe('👁️');
  });

  it('labels each state as the vanilla does', () => {
    expect(heroWatchlistLabel(true)).toBe('Watching...');
    expect(heroWatchlistLabel(false)).toBe('Add to Watchlist');
  });
});

describe('the slideshow', () => {
  it('advances every 8 seconds', () => {
    expect(HERO_SLIDE_MS).toBe(8000);
  });

  it('only auto-advances with more than one artist', () => {
    // A single artist must not get a timer that re-renders it forever.
    expect(heroAutoAdvances(0)).toBe(false);
    expect(heroAutoAdvances(1)).toBe(false);
    expect(heroAutoAdvances(2)).toBe(true);
  });

  it('wraps forwards off the end', () => {
    expect(heroNextIndex(2, 1, 3)).toBe(0);
  });

  it('wraps BACKWARDS off the start rather than going negative', () => {
    // The `+ length` in the vanilla's modulo is what makes this work.
    expect(heroNextIndex(0, -1, 3)).toBe(2);
  });

  it('steps normally in the middle', () => {
    expect(heroNextIndex(0, 1, 3)).toBe(1);
    expect(heroNextIndex(2, -1, 3)).toBe(1);
  });

  it('survives an empty artist list', () => {
    expect(heroNextIndex(0, 1, 0)).toBe(0);
    expect(heroNextIndex(0, -1, 0)).toBe(0);
  });

  it('jumps to a valid slide', () => {
    expect(heroJumpIndex(0, 2, 5)).toBe(2);
  });

  it('IGNORES an out-of-range jump rather than clamping it', () => {
    // jumpToDiscoverHeroSlide returns early — it does not clamp to the ends.
    expect(heroJumpIndex(1, -1, 5)).toBe(1);
    expect(heroJumpIndex(1, 5, 5)).toBe(1);
    expect(heroJumpIndex(1, 99, 5)).toBe(1);
  });
});

describe('the empty state', () => {
  it('keeps the vanilla copy verbatim', () => {
    expect(HERO_EMPTY_TITLE).toBe('No Recommendations Yet');
    expect(HERO_EMPTY_SUBTITLE).toBe(
      'Run a watchlist scan to generate personalized recommendations',
    );
  });
});
