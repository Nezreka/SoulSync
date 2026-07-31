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
  WATCH_ALL_BUSY,
  WATCH_ALL_DONE,
  WATCH_ALL_IDLE,
  allWatched,
  heroIndicators,
  watchAllPayload,
  watchAllState,
  heroWatchlistButtonState,
  heroWatchlistCheckBody,
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

describe('the Watch All button', () => {
  it('is actionable when idle', () => {
    expect(watchAllState('idle')).toEqual({
      label: WATCH_ALL_IDLE,
      disabled: false,
      allWatched: false,
    });
  });

  it('disables while adding, so a double-click cannot fire two batches', () => {
    const s = watchAllState('busy');
    expect(s.label).toBe(WATCH_ALL_BUSY);
    expect(s.disabled).toBe(true);
  });

  it('stays DISABLED once everything is watched', () => {
    // Re-posting the batch is a no-op the user cannot distinguish from failure.
    expect(watchAllState('done')).toEqual({
      label: WATCH_ALL_DONE,
      disabled: true,
      allWatched: true,
    });
  });
});

describe('allWatched', () => {
  it('is true only when every check says watching', () => {
    expect(allWatched([{ success: true, is_watching: true }])).toBe(true);
    expect(
      allWatched([
        { success: true, is_watching: true },
        { success: true, is_watching: true },
      ]),
    ).toBe(true);
  });

  it('stops at the first artist that is not watched', () => {
    expect(
      allWatched([
        { success: true, is_watching: true },
        { success: true, is_watching: false },
      ]),
    ).toBe(false);
  });

  it('treats a FAILED check as not-watched', () => {
    // A broken check must never claim everything is watched and disable the
    // button — that would strand the user with no way to add.
    expect(allWatched([{ success: false, is_watching: true }])).toBe(false);
    expect(allWatched([{}])).toBe(false);
  });

  it('is vacuously true for no artists, which the caller guards separately', () => {
    expect(allWatched([])).toBe(true);
  });
});

describe('the Watch All payload', () => {
  it('sends only id and name, not the whole artist', () => {
    const payload = watchAllPayload([
      artist({ artist_id: 'a1', artist_name: 'A', spotify_artist_id: 'sp', popularity: 90 }),
    ]);
    expect(payload).toEqual([{ artist_id: 'a1', artist_name: 'A' }]);
  });

  it('sends null rather than undefined for a missing id', () => {
    expect(watchAllPayload([artist({ artist_id: null })])[0].artist_id).toBeNull();
  });
});

describe('the slide indicators', () => {
  it('emits one dot per artist with the current one active', () => {
    const dots = heroIndicators(3, 1);
    expect(dots.map((d) => d.active)).toEqual([false, true, false]);
  });

  it('labels slides 1-based for screen readers', () => {
    expect(heroIndicators(2, 0).map((d) => d.ariaLabel)).toEqual([
      'Go to slide 1',
      'Go to slide 2',
    ]);
  });

  it('emits nothing for an empty hero', () => {
    expect(heroIndicators(0, 0)).toEqual([]);
  });
});

describe('the hero watchlist state check', () => {
  it('leaves the button ALONE on an unsuccessful check', () => {
    // The response says nothing about membership; guessing either way lies.
    expect(heroWatchlistButtonState({ success: false, is_watching: true })).toBeNull();
    expect(heroWatchlistButtonState(null)).toBeNull();
  });

  it('labels each state and flags the class', () => {
    expect(heroWatchlistButtonState({ success: true, is_watching: true })).toEqual({
      icon: HERO_WATCHLIST_ICON,
      label: 'Watching...',
      watching: true,
    });
    expect(heroWatchlistButtonState({ success: true, is_watching: false })).toEqual({
      icon: HERO_WATCHLIST_ICON,
      label: 'Add to Watchlist',
      watching: false,
    });
  });

  it('uses the SAME icon in both branches, as the vanilla does', () => {
    const on = heroWatchlistButtonState({ success: true, is_watching: true });
    const off = heroWatchlistButtonState({ success: true, is_watching: false });
    expect(on?.icon).toBe(off?.icon);
  });

  it('treats a missing is_watching as not watching', () => {
    expect(heroWatchlistButtonState({ success: true })?.watching).toBe(false);
  });

  it('posts just the artist id', () => {
    expect(heroWatchlistCheckBody('a1')).toEqual({ artist_id: 'a1' });
  });
});
