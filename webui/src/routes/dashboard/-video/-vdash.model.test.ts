/**
 * The video dashboard's numbers and words. The continue-watching and
 * downloading-now judgements are ported from the vanilla page's tests: the
 * card says what is LEFT, a band with nothing in it is no band, a queued row
 * draws no bar because 0% reads as stalled.
 */

import { describe, expect, it } from 'vitest';

import type { CalendarEpisode } from '../../calendar/-calendar.types';
import type { ActiveDownload, ContinueItem, VideoDashboardStats } from './-vdash.model';

import {
  ago,
  compactCount,
  continueHref,
  coverageRings,
  DL_POLL_ACTIVE_MS,
  DL_POLL_IDLE_MS,
  dlArt,
  dlHasBar,
  dlPct,
  dlStatus,
  dlSub,
  formatBytes,
  inFlight,
  libraryStrip,
  remaining,
  videoHeroNumbers,
  watchedPct,
  weekLine,
  weekSummary,
} from './-vdash.model';

const item = (over: Partial<ContinueItem> = {}): ContinueItem => ({
  kind: 'movie',
  id: 1,
  title: 'Film',
  ...over,
});

const dl = (over: Partial<ActiveDownload> = {}): ActiveDownload => ({
  id: 1,
  status: 'downloading',
  ...over,
});

function stats(over: Partial<VideoDashboardStats['library']> = {}, server: string | null = 'plex') {
  return {
    library: { movies: 8873, shows: 3470, episodes: 105674, size_bytes: 124916902008542, ...over },
    downloads: { active: 0, finished: 0, speed_bps: 0 },
    watchlist: 978,
    wishlist: 382,
    server,
    recent: [],
  } satisfies VideoDashboardStats;
}

describe('the hero', () => {
  it('reads the library in four numbers, big ones compacted to one width', () => {
    expect(videoHeroNumbers(stats())).toEqual([
      { id: 'movies', label: 'Movies', value: '8,873' },
      { id: 'shows', label: 'Shows', value: '3,470' },
      { id: 'episodes', label: 'Episodes', value: '105.7K' },
      { id: 'size', label: 'On disk', value: '114 TB' },
    ]);
  });

  it('says nothing rather than four zeros on an empty library', () => {
    expect(videoHeroNumbers(stats({ movies: 0, shows: 0, episodes: 0, size_bytes: 0 }))).toEqual(
      [],
    );
    expect(videoHeroNumbers(undefined)).toEqual([]);
  });

  it('formats sizes and counts', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(1536)).toBe('1.5 KB');
    expect(compactCount(99_999)).toBe('99,999');
    expect(compactCount(2_000_000)).toBe('2M');
  });

  it('the strip says what the library is doing', () => {
    const now = Date.parse('2026-10-28T08:00:00Z');
    expect(
      libraryStrip(stats(), { state: 'idle', last_scan_at: '2026-10-28T05:09:08+00:00' }, now),
    ).toEqual({
      state: 'ready',
      title: 'Plex library',
      subtitle: 'Last scanned 3h ago',
    });
    expect(libraryStrip(stats(), { state: 'scanning', phase: 'movies' }, now).subtitle).toBe(
      'Movies…',
    );
    expect(libraryStrip(stats({}, null), undefined, now).state).toBe('no-server');
    // still loading is not "no server"
    expect(libraryStrip(undefined, undefined, now).state).toBe('checking');
    expect(libraryStrip(stats({ movies: 0, shows: 0 }), undefined, now).state).toBe('empty');
  });

  it('reads sqlite utc stamps as utc', () => {
    const now = Date.parse('2026-10-28T08:00:00Z');
    expect(ago('2026-10-28 06:00:00', now)).toBe('2h ago');
    expect(ago('2026-10-28T07:30:00Z', now)).toBe('30m ago');
    expect(ago(null, now)).toBe('');
    expect(ago('2025-01-01 00:00:00', now)).toBe('');
  });
});

describe('continue watching', () => {
  it('says how much is LEFT, not how far in you are', () => {
    expect(remaining(item({ runtime_minutes: 100, view_offset_ms: 77 * 60000 }))).toBe(
      '23 min left',
    );
    expect(remaining(item({ runtime_minutes: 180, view_offset_ms: 8 * 60000 }))).toBe(
      '2h 52m left',
    );
    expect(remaining(item({ runtime_minutes: 120, view_offset_ms: 0 }))).toBe('2h left');
  });

  it('says nothing rather than guessing, and never a negative remainder', () => {
    expect(remaining(item({ view_offset_ms: 10 * 60000 }))).toBe('');
    expect(remaining(item({ runtime_minutes: 100, view_offset_ms: 200 * 60000 }))).toBe('');
  });

  it('draws a sliver for a barely started title, nothing for none, and never overflows', () => {
    expect(watchedPct(item({ runtime_minutes: 100, view_offset_ms: 50 * 60000 }))).toBe(50);
    expect(watchedPct(item({ runtime_minutes: 200, view_offset_ms: 30_000 }))).toBe(1);
    expect(watchedPct(item({ runtime_minutes: 100, view_offset_ms: 0 }))).toBe(0);
    expect(watchedPct(item({ runtime_minutes: 100, view_offset_ms: 500 * 60000 }))).toBe(100);
  });

  it('a show goes to the show page, a movie to the movie page', () => {
    expect(continueHref(item({ kind: 'show', id: 5, show_id: 9 }))).toBe(
      '/video-detail/library/show/9',
    );
    expect(continueHref(item({ id: 5 }))).toBe('/video-detail/library/movie/5');
  });
});

describe('downloading now', () => {
  it('takes the four in-flight states and drops what is over', () => {
    const rows = ['downloading', 'queued', 'searching', 'importing', 'completed', 'failed'].map(
      (status, i) => dl({ id: i, status }),
    );
    expect(inFlight(rows).map((r) => r.status)).toEqual([
      'downloading',
      'queued',
      'searching',
      'importing',
    ]);
    expect(inFlight(undefined)).toEqual([]);
  });

  it('says the import phase when there is one', () => {
    expect(dlStatus(dl({ status: 'importing', import_phase: 'Renaming' }))).toBe('Renaming');
    expect(dlStatus(dl({ status: 'importing' }))).toBe('Importing');
    expect(dlStatus(dl({ status: 'queued' }))).toBe('Queued');
  });

  it('reads a percent or a fraction, clamped, never NaN', () => {
    expect(dlPct(dl({ progress: 42 }))).toBe(42);
    expect(dlPct(dl({ progress: 0.42 }))).toBe(42);
    expect(dlPct(dl({ progress: 250 }))).toBe(100);
    expect(dlPct(dl({ progress: 'x' }))).toBe(0);
    expect(dlPct(dl({ progress: null }))).toBe(0);
  });

  it('draws a bar only where there is real progress', () => {
    expect(dlHasBar(dl({ status: 'downloading' }))).toBe(true);
    expect(dlHasBar(dl({ status: 'importing' }))).toBe(true);
    expect(dlHasBar(dl({ status: 'queued' }))).toBe(false);
    expect(dlHasBar(dl({ status: 'searching' }))).toBe(false);
  });

  it('shows speed and time left', () => {
    expect(dlSub(dl({ speed_bps: 2 * 1024 * 1024, eta_seconds: 3720 }))).toBe(
      '2.0 MB/s · ~1h 2m left',
    );
    expect(dlSub(dl({ status: 'queued', speed_bps: 5000 }))).toBe('');
  });

  it('falls back to the library poster, encoded, when the grab carried none', () => {
    expect(dlArt(dl({ poster_url: 'https://x/p.jpg' }))).toBe('https://x/p.jpg');
    expect(dlArt(dl({ media_source: 'library', media_id: '7 8', kind: 'movie' }))).toBe(
      '/api/video/poster/movie/7%208?w=120',
    );
    expect(dlArt(dl({ media_source: 'tmdb', media_id: 7, kind: 'movie' }))).toBe('');
  });

  it('polls slower than the downloads page, faster only while something moves', () => {
    expect(DL_POLL_ACTIVE_MS).toBeGreaterThan(2000);
    expect(DL_POLL_IDLE_MS).toBeGreaterThan(DL_POLL_ACTIVE_MS);
  });
});

describe('this week', () => {
  const ep = (acq: CalendarEpisode['acq'], air_date = '2026-10-25'): CalendarEpisode => ({
    id: Math.random(),
    show_id: 1,
    show_tmdb_id: 1,
    show_title: 'S',
    season_number: 1,
    episode_number: 1,
    title: null,
    overview: null,
    air_date,
    has_file: 0,
    monitored: 1,
    acq,
    needs_action: false,
  });

  it('counts what aired, apart from what is still coming', () => {
    const w = weekSummary(
      [
        ep('owned'),
        ep('owned'),
        ep('missing'),
        ep('wanted'),
        ep('unaired', '2026-10-30'),
        ep('unaired', '2026-10-28'),
      ],
      '2026-10-28',
    );
    expect(w).toMatchObject({ aired: 4, owned: 2, missing: 1, upcoming: 2 });
    expect(w.segments.map((s) => s.state)).toEqual(['owned', 'wanted', 'missing']);
    expect(weekLine(w)).toBe('2 of 4 that aired are in your library.');
  });

  it('says so when it all landed, or nothing aired', () => {
    expect(weekLine(weekSummary([ep('owned')], '2026-10-28'))).toBe(
      'All 1 that aired are in your library.',
    );
    expect(weekLine(weekSummary([], '2026-10-28'))).toBe(
      'Nothing on your watchlist aired this past week.',
    );
  });
});

describe('metadata coverage', () => {
  it('one ring per source the library has', () => {
    expect(
      coverageRings({
        movies: { total: 200, tmdb_enriched: 199 },
        shows: { total: 10, tmdb_enriched: 9, tvdb_matched: 10 },
      }).map((r) => [r.key, r.pct]),
    ).toEqual([
      ['tmdb-movies', 100],
      ['tmdb-shows', 90],
      ['tvdb-shows', 100],
    ]);
    expect(coverageRings({ movies: { total: 0 } })).toEqual([]);
  });
});
