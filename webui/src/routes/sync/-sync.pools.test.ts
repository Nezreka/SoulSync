/**
 * The pools core, pinned against stats-automations.js.
 *
 * Two pieces run DIFFERENTIALLY against the real vanilla bodies:
 * `_wingItMatchedName` is pure, and `_buildPoolMatchedMosaic` only touches the
 * DOM — which jsdom supplies — so the mosaic it builds can be compared element
 * for element against what poolMosaicRows describes. The rest is pinned by
 * literal, with the user-visible copy cross-checked against the vanilla text.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import type { PoolCacheEntry, PoolTrackRow } from './-sync.pools';

import { extractFunction } from '../../test/vanilla-extract';
import {
  POOL_FIX_AUTOSEARCH_MS,
  POOL_FIX_NEEDS_QUERY,
  POOL_FIX_NO_RESULTS,
  POOL_FIX_SEARCH_LIMIT,
  POOL_MOSAIC_ROWS,
  POOL_REMOVE_CACHE_MESSAGE,
  discoveryPoolCounts,
  discoveryPoolEmptyMessage,
  discoveryPoolListTitle,
  poolCacheMatches,
  poolConfidence,
  poolFixConfirmMessage,
  poolFixHeading,
  poolFixMatchedToast,
  poolFixSearchError,
  poolFixSearchFailed,
  poolFixSourceLabel,
  poolFixThrewMessage,
  poolMatchImage,
  poolMosaicImages,
  poolMosaicRows,
  poolQuery,
  poolTrackMatches,
  wingItMatchedName,
  wingItPoolCounts,
  wingItPoolEmptyMessage,
  wingItPoolListTitle,
} from './-sync.pools';

const STATS = readFileSync(resolve(process.cwd(), 'static/stats-automations.js'), 'utf8');

const entry = (over: Partial<PoolCacheEntry> = {}): PoolCacheEntry =>
  ({ id: 1, ...over }) as PoolCacheEntry;

describe('search (1493-1506, 1661-1667, 1687-1695)', () => {
  it('the query is lowercased AND trimmed', () => {
    expect(poolQuery('  MiXeD  ')).toBe('mixed');
    expect(poolQuery('')).toBe('');
  });

  it('track rows match on name, artist OR playlist', () => {
    const row: PoolTrackRow = {
      id: 1,
      track_name: 'Blue Monday',
      artist_name: 'New Order',
      playlist_name: 'Road Trip',
    };
    expect(poolTrackMatches(row, 'monday')).toBe(true);
    expect(poolTrackMatches(row, 'order')).toBe(true);
    // the playlist field is the easy one to drop
    expect(poolTrackMatches(row, 'road')).toBe(true);
    expect(poolTrackMatches(row, 'nope')).toBe(false);
  });

  it('an empty query matches everything, and missing fields never throw', () => {
    expect(poolTrackMatches({ id: 1 }, '')).toBe(true);
    expect(poolTrackMatches({ id: 1 }, 'x')).toBe(false);
    expect(poolCacheMatches(entry(), '')).toBe(true);
    expect(poolCacheMatches(entry(), 'x')).toBe(false);
  });

  it('cache entries match on the ORIGINAL pair and the matched name', () => {
    const e = entry({
      original_title: 'Blue Monday',
      original_artist: 'New Order',
      matched_data: { name: 'Blue Monday (2016 Remaster)' },
    });
    expect(poolCacheMatches(e, 'monday')).toBe(true);
    expect(poolCacheMatches(e, 'new order')).toBe(true);
    expect(poolCacheMatches(e, 'remaster')).toBe(true);
    // NOT the playlist — cache entries have no playlist field at all
    expect(poolCacheMatches(e, 'road')).toBe(false);
  });
});

describe('empty states — eight distinct strings', () => {
  it('the Discovery Pool words its filtered-empty per list', () => {
    expect(discoveryPoolEmptyMessage('failed', true)).toBe('No failed tracks match your filter.');
    expect(discoveryPoolEmptyMessage('failed', false)).toBe(
      'No failed discoveries. All tracks matched successfully.',
    );
    expect(discoveryPoolEmptyMessage('matched', true)).toBe('No matched tracks match your filter.');
    expect(discoveryPoolEmptyMessage('matched', false)).toBe('No cached discovery matches yet.');
  });

  it('Wing It SHARES one filtered-empty across both lists', () => {
    expect(wingItPoolEmptyMessage('attention', true)).toBe('No tracks match your filter.');
    expect(wingItPoolEmptyMessage('matched', true)).toBe('No tracks match your filter.');
    expect(wingItPoolEmptyMessage('attention', false)).toBe('No Wing It guesses to review.');
    expect(wingItPoolEmptyMessage('matched', false)).toBe(
      'No resolved Wing It tracks yet — ones you Fix here will land in this list.',
    );
  });

  it('all eight are the vanilla text', () => {
    for (const message of [
      discoveryPoolEmptyMessage('failed', true),
      discoveryPoolEmptyMessage('failed', false),
      discoveryPoolEmptyMessage('matched', true),
      discoveryPoolEmptyMessage('matched', false),
      wingItPoolEmptyMessage('attention', true),
      wingItPoolEmptyMessage('attention', false),
      wingItPoolEmptyMessage('matched', false),
    ]) {
      expect(STATS).toContain(message);
    }
  });
});

describe('titles and counts', () => {
  it('the list titles are the vanilla strings', () => {
    expect(discoveryPoolListTitle('failed')).toBe('Failed Tracks');
    expect(discoveryPoolListTitle('matched')).toBe('Matched Tracks');
    expect(wingItPoolListTitle('attention')).toBe('⚡ Guesses to review');
    expect(wingItPoolListTitle('matched')).toBe('✓ Resolved Wing It guesses');
    expect(STATS).toContain('Failed Tracks');
    expect(STATS).toContain('⚡ Guesses to review');
  });

  it('the Discovery Pool counts come from stats, Wing It from ARRAY LENGTHS', () => {
    expect(discoveryPoolCounts({ stats: { matched: 12, failed: 3 } })).toEqual({
      matched: 12,
      failed: 3,
    });
    // Its arrays are irrelevant — only `stats` counts.
    expect(discoveryPoolCounts({ stats: { matched: 12 }, failed: [{ id: 1 }, { id: 2 }] })).toEqual(
      {
        matched: 12,
        failed: 0,
      },
    );
    expect(discoveryPoolCounts({})).toEqual({ matched: 0, failed: 0 });
    expect(discoveryPoolCounts(null)).toEqual({ matched: 0, failed: 0 });

    // Wing It has no stats object at all.
    expect(wingItPoolCounts({ tracks: [{ id: 1 }, { id: 2 }], matched: [{ id: 3 }] })).toEqual({
      attention: 2,
      matched: 1,
    });
    expect(wingItPoolCounts({})).toEqual({ attention: 0, matched: 0 });
    expect(wingItPoolCounts(null)).toEqual({ attention: 0, matched: 0 });
  });
});

describe('the matched row (1704-1730)', () => {
  it('confidence rounds to a percent and lands in one of three bands', () => {
    expect(poolConfidence(0.9)).toEqual({ percent: 90, band: 'high' });
    // the boundaries themselves
    expect(poolConfidence(0.8)).toEqual({ percent: 80, band: 'high' });
    expect(poolConfidence(0.799)).toEqual({ percent: 80, band: 'high' });
    expect(poolConfidence(0.7)).toEqual({ percent: 70, band: 'mid' });
    expect(poolConfidence(0.794)).toEqual({ percent: 79, band: 'mid' });
    expect(poolConfidence(0.694)).toEqual({ percent: 69, band: 'low' });
    expect(poolConfidence(undefined)).toEqual({ percent: 0, band: 'low' });
  });

  it('the cover falls back from image_url to the album’s first image', () => {
    expect(poolMatchImage(entry({ matched_data: { image_url: 'a.jpg' } }))).toBe('a.jpg');
    expect(poolMatchImage(entry({ matched_data: { album: { images: [{ url: 'b.jpg' }] } } }))).toBe(
      'b.jpg',
    );
    // image_url WINS when both are present
    expect(
      poolMatchImage(
        entry({ matched_data: { image_url: 'a.jpg', album: { images: [{ url: 'b.jpg' }] } } }),
      ),
    ).toBe('a.jpg');
    // an album that is a bare STRING has no images (the typeof guard, 1710)
    expect(poolMatchImage(entry({ matched_data: { album: 'Technique' } }))).toBe('');
    expect(poolMatchImage(entry({ matched_data: { album: { images: [] } } }))).toBe('');
    expect(poolMatchImage(entry())).toBe('');
  });

  it('the matched row shows the ORIGINAL artist, not the matched one', () => {
    // The vanilla builds `matchedArtists` at 1706 and never renders it, so
    // there is nothing to port. Pinned here so the omission is deliberate and
    // visible rather than looking like a miss.
    const row = STATS.slice(
      STATS.indexOf('const matchedArtists'),
      STATS.indexOf("}).join('');", STATS.indexOf('const matchedArtists')),
    );
    expect(row).toContain('const matchedArtists');
    expect(row).not.toContain('${matchedArtists}');
  });
});

describe('wingItMatchedName — differential against the vanilla body', () => {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const V = new Function(`
    ${extractFunction('_wingItMatchedName', STATS)}
    return { _wingItMatchedName };
  `)() as { _wingItMatchedName: (t: PoolTrackRow) => string };

  it('reads the JSON STRING, and swallows every way it can go wrong', () => {
    const cases: PoolTrackRow[] = [
      { id: 1, extra_data: JSON.stringify({ matched_data: { name: 'Blue Monday' } }) },
      { id: 2, extra_data: JSON.stringify({ matched_data: {} }) },
      { id: 3, extra_data: JSON.stringify({}) },
      { id: 4, extra_data: '{not json' },
      { id: 5, extra_data: '' },
      { id: 6 },
      { id: 7, extra_data: JSON.stringify({ matched_data: { name: '' } }) },
      { id: 8, extra_data: 'null' },
    ];
    for (const track of cases) {
      expect(wingItMatchedName(track)).toBe(V._wingItMatchedName(track));
    }
    expect(wingItMatchedName(cases[0])).toBe('Blue Monday');
    expect(wingItMatchedName(cases[3])).toBe('');
  });
});

describe('the mosaic (1315-1350)', () => {
  it('collects at most 20 DISTINCT urls, in first-seen order', () => {
    const entries = [
      entry({ matched_data: { image_url: 'a.jpg' } }),
      entry({ matched_data: { image_url: 'b.jpg' } }),
      entry({ matched_data: { image_url: 'a.jpg' } }),
      entry({ matched_data: {} }),
      entry(),
    ];
    expect(poolMosaicImages(entries)).toEqual(['a.jpg', 'b.jpg']);

    const many = Array.from({ length: 30 }, (_, i) =>
      entry({ matched_data: { image_url: `${i}.jpg` } }),
    );
    expect(poolMosaicImages(many)).toHaveLength(20);
    expect(poolMosaicImages(many)[19]).toBe('19.jpg');
  });

  it('under four covers there is no mosaic — the flat gradient stays', () => {
    expect(poolMosaicRows([])).toBeNull();
    expect(poolMosaicRows(['a', 'b', 'c'])).toBeNull();
    expect(poolMosaicRows(['a', 'b', 'c', 'd'])).not.toBeNull();
  });

  it('builds exactly what the vanilla builds, element for element', () => {
    // The vanilla only touches the DOM, which jsdom supplies — so this runs
    // the REAL body and compares the result to what poolMosaicRows describes.
    const images = Array.from({ length: 9 }, (_, i) => `cover-${i}.jpg`);
    const data = { matched: images.map((url) => ({ matched_data: { image_url: url } })) };
    document.body.innerHTML = '<div id="pool-matched-bg"></div>';
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const V = new Function(`
      const _discoveryPoolData = ${JSON.stringify(data)};
      ${extractFunction('_buildPoolMatchedMosaic', STATS)}
      return { _buildPoolMatchedMosaic };
    `)() as { _buildPoolMatchedMosaic: () => void };
    V._buildPoolMatchedMosaic();

    const bg = document.getElementById('pool-matched-bg')!;
    expect(bg.className).toBe('wishlist-mosaic-background');
    const wrappers = [...bg.querySelectorAll('.wishlist-mosaic-row-wrapper')];
    const ours = poolMosaicRows(poolMosaicImages(data.matched as PoolCacheEntry[]))!;
    expect(wrappers).toHaveLength(ours.length);

    wrappers.forEach((wrapper, r) => {
      const row = wrapper.firstElementChild as HTMLElement;
      expect(row.className).toBe(
        'wishlist-mosaic-row' + (ours[r].scrollRight ? ' scroll-right' : ''),
      );
      expect(row.style.getPropertyValue('--speed')).toBe(`${ours[r].speedSeconds}s`);
      expect(row.style.animationDelay).toBe(`${ours[r].delaySeconds}s`);
      const tiles = [...row.querySelectorAll('.wishlist-mosaic-image')].map((t) =>
        (t as HTMLElement).style.backgroundImage.replace(/^url\(['"]?|['"]?\)$/g, ''),
      );
      expect(tiles).toEqual(ours[r].tiles);
    });
  });

  it('the vanilla ALSO bails under four covers', () => {
    document.body.innerHTML = '<div id="pool-matched-bg" class="pool-category-fallback"></div>';
    const data = { matched: [{ matched_data: { image_url: 'a.jpg' } }] };
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const V = new Function(`
      const _discoveryPoolData = ${JSON.stringify(data)};
      ${extractFunction('_buildPoolMatchedMosaic', STATS)}
      return { _buildPoolMatchedMosaic };
    `)() as { _buildPoolMatchedMosaic: () => void };
    V._buildPoolMatchedMosaic();
    const bg = document.getElementById('pool-matched-bg')!;
    expect(bg.className).toBe('pool-category-fallback');
    expect(bg.children).toHaveLength(0);
    expect(poolMosaicRows(poolMosaicImages(data.matched as PoolCacheEntry[]))).toBeNull();
  });

  it('four rows, doubled tiles, staggered speed and offset', () => {
    const rows = poolMosaicRows(['a', 'b', 'c', 'd', 'e'])!;
    expect(POOL_MOSAIC_ROWS).toBe(4);
    expect(rows).toHaveLength(4);
    // ceil(5/4) * 2
    expect(rows[0].tiles).toHaveLength(4);
    expect(rows.map((r) => r.speedSeconds)).toEqual([25, 30, 35, 40]);
    expect(rows.map((r) => r.delaySeconds)).toEqual([0, 0.15, 0.3, 0.44999999999999996]);
    expect(rows.map((r) => r.scrollRight)).toEqual([false, true, false, true]);
    // row r starts three images further along
    expect(rows[0].tiles).toEqual(['a', 'b', 'c', 'd']);
    expect(rows[1].tiles).toEqual(['d', 'e', 'a', 'b']);
  });
});

describe('the fix / rematch sub-modal (1739-2022)', () => {
  const FIX = { mode: 'fix', trackId: 7, trackName: 'T', artistName: 'A' } as const;
  const REMATCH = {
    mode: 'rematch',
    cacheId: 9,
    originalTitle: 'T',
    originalArtist: 'A',
    trackName: 'T',
    artistName: 'A',
  } as const;

  it('one modal, two headings and two source labels', () => {
    expect(poolFixHeading(FIX)).toBe('Fix Track Match');
    expect(poolFixHeading(REMATCH)).toBe('Rematch Track');
    expect(poolFixSourceLabel(FIX)).toBe('Original Track');
    expect(poolFixSourceLabel(REMATCH)).toBe('Current Match');
    expect(STATS).toContain('Fix Track Match');
    expect(STATS).toContain('Rematch Track');
    expect(STATS).toContain('Original Track');
    expect(STATS).toContain('Current Match');
  });

  it('auto-searches after 500ms and asks for 20 results', () => {
    expect(POOL_FIX_AUTOSEARCH_MS).toBe(500);
    expect(POOL_FIX_SEARCH_LIMIT).toBe(20);
    expect(STATS).toContain('setTimeout(() => searchPoolFix(), 500)');
    expect(STATS).toContain("params.set('limit', '20')");
  });

  it('a failure is !ok OR an error key — either one alone is enough', () => {
    expect(poolFixSearchFailed(false, undefined)).toBe(true);
    expect(poolFixSearchFailed(true, 'no auth')).toBe(true);
    expect(poolFixSearchFailed(true, undefined)).toBe(false);
    expect(poolFixSearchFailed(true, '')).toBe(false);
  });

  it('the error message prefers the backend text, then statusText, then the code', () => {
    expect(poolFixSearchError(500, 'Server Error', 'spotify not authenticated')).toBe(
      'Search error: spotify not authenticated',
    );
    expect(poolFixSearchError(500, 'Server Error', undefined)).toBe('Search error: Server Error');
    expect(poolFixSearchError(503, '', undefined)).toBe('Search error: request failed (503)');
    expect(STATS).toContain('Search error: ');
    expect(STATS).toContain('request failed (');
  });

  it('the other three search outcomes are the vanilla strings', () => {
    expect(POOL_FIX_NEEDS_QUERY).toBe('Enter a search term');
    expect(POOL_FIX_NO_RESULTS).toBe('No results found');
    expect(poolFixThrewMessage('network down')).toBe('Search failed: network down');
    expect(STATS).toContain('Enter a search term');
    expect(STATS).toContain('No results found');
    expect(STATS).toContain('Search failed: ');
  });

  it('the confirm and success toast name the chosen track', () => {
    const track = { name: 'Blue Monday', artists: ['New Order'] };
    expect(poolFixConfirmMessage(track)).toBe('Match to "Blue Monday" by New Order?');
    expect(poolFixConfirmMessage({ name: 'X' })).toBe('Match to "X" by ?');
    expect(poolFixMatchedToast(track)).toBe('Matched: Blue Monday');
    expect(STATS).toContain('Match to "');
    expect(STATS).toContain('Matched: ');
  });

  it('removing a cache entry warns that the track re-discovers fresh', () => {
    expect(POOL_REMOVE_CACHE_MESSAGE).toBe(
      'Remove this cached match? The track will be re-discovered fresh next time.',
    );
    expect(STATS).toContain(POOL_REMOVE_CACHE_MESSAGE);
  });
});
