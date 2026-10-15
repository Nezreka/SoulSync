import { describe, expect, it } from 'vitest';

import type { VdlDownload } from './-vdl.types';

import {
  vdlBinDisplayName,
  vdlBlockSxe,
  vdlBlockWhat,
  vdlCanBlock,
  vdlCardTitle,
  vdlDayKey,
  vdlDayLabel,
  vdlFmtAge,
  vdlFmtBytes,
  vdlFmtElapsed,
  vdlFmtEta,
  vdlFmtHistorySize,
  vdlFmtRuntime,
  vdlFmtSize,
  vdlFmtSpeed,
  vdlFmtViews,
  vdlFmtWhen,
  vdlGroupKey,
  vdlGroupWorthy,
  vdlHistoryOutcome,
  vdlIsActive,
  vdlIsFail,
  vdlIsPackRow,
  vdlIsRetryable,
  vdlKindIcon,
  vdlMatchesFilter,
  vdlMetaURL,
  vdlPad2,
  vdlParseCtx,
  vdlSplitGroup,
  vdlStatusOf,
  vdlType,
} from './-vdl.helpers';

const dl = (over: Partial<VdlDownload> = {}): VdlDownload => ({ id: 1, ...over });

describe('vdlType / vdlKindIcon', () => {
  it('collapses kinds into movie / tv / youtube', () => {
    expect(vdlType('movie')).toBe('movie');
    expect(vdlType('show')).toBe('tv');
    expect(vdlType('episode')).toBe('tv');
    expect(vdlType('youtube')).toBe('youtube');
    expect(vdlType(undefined)).toBe('tv');
  });
  it('picks the kind emoji with a movie fallback', () => {
    expect(vdlKindIcon('movie')).toBe('🎬');
    expect(vdlKindIcon('youtube')).toBe('▶️');
    expect(vdlKindIcon('bogus')).toBe('🎬');
  });
});

describe('status helpers', () => {
  it('maps every known status to a label and an adl css class', () => {
    expect(vdlStatusOf('downloading')).toEqual({ label: 'Downloading', cls: 'active' });
    expect(vdlStatusOf('searching').cls).toBe('active');
    expect(vdlStatusOf('importing').cls).toBe('active');
    expect(vdlStatusOf('import_failed')).toEqual({ label: 'Import failed', cls: 'failed' });
    expect(vdlStatusOf('cancelled').cls).toBe('cancelled');
    expect(vdlStatusOf('nope')).toEqual({ label: 'Downloading', cls: 'active' });
  });
  it('classifies active vs failed the way the pills do', () => {
    for (const s of ['downloading', 'queued', 'searching', 'importing']) {
      expect(vdlIsActive(s)).toBe(true);
      expect(vdlIsFail(s)).toBe(false);
    }
    for (const s of ['failed', 'cancelled', 'import_failed']) {
      expect(vdlIsFail(s)).toBe(true);
      expect(vdlIsActive(s)).toBe(false);
    }
    expect(vdlIsActive('completed')).toBe(false);
    expect(vdlIsFail('completed')).toBe(false);
  });
  it('matches the four filter pills', () => {
    expect(vdlMatchesFilter('downloading', 'all')).toBe(true);
    expect(vdlMatchesFilter('queued', 'active')).toBe(true);
    expect(vdlMatchesFilter('importing', 'active')).toBe(true);
    expect(vdlMatchesFilter('completed', 'active')).toBe(false);
    expect(vdlMatchesFilter('completed', 'completed')).toBe(true);
    expect(vdlMatchesFilter('failed', 'failed')).toBe(true);
    expect(vdlMatchesFilter('cancelled', 'failed')).toBe(true);
    expect(vdlMatchesFilter('import_failed', 'failed')).toBe(true);
    expect(vdlMatchesFilter('downloading', 'failed')).toBe(false);
  });
});

describe('vdlParseCtx', () => {
  it('parses a JSON string ctx', () => {
    expect(vdlParseCtx(dl({ search_ctx: '{"season":2,"episode":5}' }))).toMatchObject({
      season: 2,
      episode: 5,
    });
  });
  it('accepts an already-parsed object', () => {
    expect(vdlParseCtx(dl({ search_ctx: { season: 1 } as never }))).toMatchObject({ season: 1 });
  });
  it('returns {} for missing or broken ctx', () => {
    expect(vdlParseCtx(dl({}))).toEqual({});
    expect(vdlParseCtx(dl({ search_ctx: '{oops' }))).toEqual({});
  });
  it('reads channel + peer fields', () => {
    const ctx = vdlParseCtx(
      dl({
        search_ctx: JSON.stringify({
          channel: 'Some Channel',
          channel_id: 'UCx',
          peer: { slots: 1, queue: 3, speed: 500000 },
        }),
      }),
    );
    expect(ctx.channel).toBe('Some Channel');
    expect(ctx.channel_id).toBe('UCx');
    expect(ctx.peer).toMatchObject({ slots: 1, queue: 3, speed: 500000 });
  });
});

describe('season-pack grouping', () => {
  const ep = (id: number, season = 1, episode = id) =>
    dl({
      id,
      kind: 'show',
      title: 'The Show',
      media_id: 42,
      search_ctx: JSON.stringify({ season, episode }),
    });
  const pack = (id: number) =>
    dl({
      id,
      kind: 'show',
      title: 'The Show',
      media_id: 42,
      is_pack: true,
      search_ctx: JSON.stringify({ season: 1, scope: 'season' }),
    });

  it('detects the pack row (server flag, then ctx fallbacks)', () => {
    expect(vdlIsPackRow(pack(9))).toBe(true);
    expect(vdlIsPackRow(ep(1))).toBe(false);
    expect(
      vdlIsPackRow(
        dl({ kind: 'show', search_ctx: JSON.stringify({ season: 1, scope: 'season' }) }),
      ),
    ).toBe(true);
    // the server's verdict wins: an explicit is_pack:false is not overridden
    // by a season-looking ctx (otherwise the fallback becomes the real rule).
    expect(
      vdlIsPackRow(
        dl({
          kind: 'show',
          is_pack: false,
          search_ctx: JSON.stringify({ season: 1, scope: 'season' }),
        }),
      ),
    ).toBe(false);
  });
  it('keys same show+season together, nothing else', () => {
    expect(vdlGroupKey(ep(1))).toBe('g:42:s1');
    expect(vdlGroupKey(ep(2, 2, 1))).toBe('g:42:s2');
    // the pack shares its episodes' key — the original bug was groupKey
    // returning null for the pack, so it rendered alone.
    expect(vdlGroupKey(pack(9))).toBe(vdlGroupKey(ep(1)));
    expect(vdlGroupKey(dl({ kind: 'movie', title: 'M' }))).toBeNull();
    expect(vdlGroupKey(dl({ kind: 'show', title: 'NoSeason' }))).toBeNull();
  });
  it('splits the pack head from the episode cards', () => {
    const { pack: p, episodes } = vdlSplitGroup([ep(1), pack(9), ep(2)]);
    expect(p?.id).toBe(9);
    expect(episodes.map((e) => e.id)).toEqual([1, 2]);
  });
  it('a lone pack is still worth a group card', () => {
    expect(vdlGroupWorthy([pack(9)])).toBe(true);
    expect(vdlGroupWorthy([ep(1), ep(2)])).toBe(true);
    expect(vdlGroupWorthy([ep(1)])).toBe(false);
    expect(vdlGroupWorthy([])).toBe(false);
  });
});

describe('formatters', () => {
  it('vdlFmtSize uses GB above 0.1 and MB below', () => {
    expect(vdlFmtSize(2 * 1024 ** 3)).toBe('2 GB');
    expect(vdlFmtSize(50 * 1024 ** 2)).toBe('50 MB');
    // 150 MB is 0.146 GB — the old rule reads GB at >= 0.1
    expect(vdlFmtSize(150 * 1024 ** 2)).toBe('0.1 GB');
  });
  it('vdlFmtElapsed renders m/ss', () => {
    expect(vdlFmtElapsed(5000)).toBe('5s');
    expect(vdlFmtElapsed(65000)).toBe('1m05s');
  });
  it('vdlFmtRuntime renders h/m', () => {
    expect(vdlFmtRuntime(90)).toBe('1h 30m');
    expect(vdlFmtRuntime(45)).toBe('45m');
    expect(vdlFmtRuntime(0)).toBe('');
  });
  it('vdlFmtViews compacts', () => {
    expect(vdlFmtViews(1500)).toBe('1.5K');
    expect(vdlFmtViews(2500000)).toBe('2.5M');
    expect(vdlFmtViews(42)).toBe('42');
  });
  it('vdlFmtSpeed is empty at 0', () => {
    expect(vdlFmtSpeed(0)).toBe('');
    expect(vdlFmtSpeed(2500000)).toBe('2.5 MB/s');
    expect(vdlFmtSpeed(8000)).toBe('8 KB/s');
  });
  it('vdlFmtEta renders the ~N left copy', () => {
    expect(vdlFmtEta(0)).toBe('');
    expect(vdlFmtEta(45)).toBe('~45s left');
    expect(vdlFmtEta(600)).toBe('~10m left');
    expect(vdlFmtEta(5400)).toBe('~1h 30m left');
  });
  it('vdlPad2 zero-pads', () => {
    expect(vdlPad2(3)).toBe('03');
    expect(vdlPad2(12)).toBe('12');
  });
  it('vdlFmtAge renders m/h/d ago', () => {
    expect(vdlFmtAge(120)).toBe('2m ago');
    expect(vdlFmtAge(7200)).toBe('2h ago');
    expect(vdlFmtAge(172800)).toBe('2d ago');
    expect(vdlFmtAge(null)).toBe('age unknown');
  });
  it('vdlFmtBytes matches the old tabs/history unit rule', () => {
    // one decimal under 10 (except bytes): the old page's exact rule
    expect(vdlFmtBytes(2048)).toBe('2.0 KB');
    expect(vdlFmtBytes(15 * 1024)).toBe('15 KB');
    expect(vdlFmtBytes(0)).toBe('');
  });
  it('vdlFmtHistorySize keeps 1 decimal only at GB+', () => {
    expect(vdlFmtHistorySize(1500 * 1024)).toBe('1 MB');
    expect(vdlFmtHistorySize(2.5 * 1024 ** 3)).toBe('2.5 GB');
  });
  it('vdlFmtWhen renders "Oct 11, 2026 · 14:30"', () => {
    expect(vdlFmtWhen('2026-10-11 14:30:00')).toBe('Oct 11, 2026 · 14:30');
    expect(vdlFmtWhen(null)).toBe('');
  });
  it('vdlDayKey / vdlDayLabel group the timeline', () => {
    expect(vdlDayKey('2026-10-11 14:30:00')).toBe('2026-10-11');
    expect(vdlDayLabel('2026-10-11 14:30:00')).toBe('Oct 11, 2026');
    expect(vdlDayLabel(null)).toBe('Earlier');
  });
  it('vdlHistoryOutcome maps every outcome', () => {
    expect(vdlHistoryOutcome('completed')).toEqual(['Imported', 'vdh-oc--ok']);
    expect(vdlHistoryOutcome('import_failed')).toEqual(['Needs import', 'vdh-oc--warn']);
    expect(vdlHistoryOutcome('failed')).toEqual(['Failed', 'vdh-oc--fail']);
    expect(vdlHistoryOutcome('cancelled')).toEqual(['Cancelled', 'vdh-oc--muted']);
  });
});

describe('vdlMetaURL', () => {
  it('builds the TMDB detail URL', () => {
    expect(vdlMetaURL(dl({ kind: 'movie', media_id: 123 }))).toBe(
      '/api/video/downloads/meta/movie/123',
    );
  });
  it('appends season+episode for episode rows', () => {
    expect(
      vdlMetaURL(
        dl({ kind: 'episode', media_id: 9, search_ctx: JSON.stringify({ season: 2, episode: 4 }) }),
      ),
    ).toBe('/api/video/downloads/meta/show/9?season=2&episode=4');
  });
  it('uses the cached youtube meta for youtube rows', () => {
    expect(vdlMetaURL(dl({ kind: 'youtube', media_id: 'abc' }))).toBe(
      '/api/video/downloads/yt-meta/abc',
    );
  });
  it('skips owned re-grabs (media_id is not a TMDB id)', () => {
    expect(vdlMetaURL(dl({ kind: 'movie', media_id: 5, media_source: 'library' }))).toBeNull();
  });
});

describe('row text', () => {
  it('vdlCardTitle disambiguates episodes with SxxExx', () => {
    expect(vdlCardTitle(dl({ title: 'The Show', search_ctx: '{"season":1,"episode":2}' }))).toBe(
      'The Show · S01E02',
    );
    expect(vdlCardTitle(dl({ title: 'Dune', year: 2021 }))).toBe('Dune  (2021)');
  });
  it('vdlIsRetryable needs a real grab failure with a known release', () => {
    expect(vdlIsRetryable(dl({ status: 'failed', username: 'u', filename: 'f' }))).toBe(true);
    expect(vdlIsRetryable(dl({ status: 'cancelled', username: 'u', filename: 'f' }))).toBe(false);
    expect(vdlIsRetryable(dl({ status: 'failed' }))).toBe(false);
  });
  it('vdlCanBlock needs failed/import_failed + a non-youtube release', () => {
    expect(vdlCanBlock(dl({ status: 'failed', username: 'u', filename: 'f' }))).toBe(true);
    expect(vdlCanBlock(dl({ status: 'import_failed', username: 'u', filename: 'f' }))).toBe(true);
    expect(
      vdlCanBlock(dl({ status: 'failed', kind: 'youtube', username: 'u', filename: 'f' })),
    ).toBe(false);
    expect(vdlCanBlock(dl({ status: 'completed', username: 'u', filename: 'f' }))).toBe(false);
  });
  it('vdlBinDisplayName strips the timestamp prefix', () => {
    expect(vdlBinDisplayName('20260101_120000_Movie.mkv')).toBe('Movie.mkv');
    expect(vdlBinDisplayName('20260101_120000_(1)_Movie.mkv')).toBe('Movie.mkv');
    expect(vdlBinDisplayName('plain.mkv')).toBe('plain.mkv');
  });
  it('vdlBlockWhat / vdlBlockSxe describe a blocklist row', () => {
    expect(vdlBlockWhat({ id: 1, release_title: 'R' })).toBe('R');
    expect(vdlBlockWhat({ id: 1, username: 'u' })).toBe('u');
    expect(vdlBlockWhat({ id: 1 })).toBe('Unknown release');
    expect(vdlBlockSxe({ id: 1, season_number: 2, episode_number: 4 })).toBe('S02E04');
    expect(vdlBlockSxe({ id: 1 })).toBe('');
  });
});
