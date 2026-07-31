import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import {
  blacklistArtist,
  enrichSimilarArtists,
  fetchAdventurousness,
  fetchArtistInfo,
  fetchDeepCuts,
  fetchHero,
  fetchLabelExplorer,
  fetchLbPlaylist,
  fetchPopularPicks,
  fetchYourAlbums,
  refreshYourAlbums,
  resolveCacheAlbum,
  setAdventurousness,
  unblacklistArtist,
} from './-discover.api';

/** Capture the request that lands on `path`. */
function capture(
  method: 'get' | 'post' | 'delete',
  path: string,
  payload: Record<string, unknown> = { success: true },
) {
  const seen: { url: URL; body: string }[] = [];
  server.use(
    http[method](path, async ({ request }) => {
      let body = '';
      try {
        body = await request.text();
      } catch {
        body = '';
      }
      seen.push({ url: new URL(request.url), body });
      return HttpResponse.json(payload);
    }),
  );
  return seen;
}

describe('a dead shelf never takes the page down', () => {
  // This page fans out ~20 requests, several to external services that can hang
  // or 500 (Last.fm, ListenBrainz). The vanilla gave every loader its own
  // try/catch so one bad shelf renders empty instead of killing the render.

  it('returns empty on a network failure', async () => {
    server.use(http.get('/api/discover/deep-cuts', () => HttpResponse.error()));
    await expect(fetchDeepCuts()).resolves.toEqual([]);
  });

  it('returns empty on a 500', async () => {
    server.use(
      http.get(
        '/api/discover/personalized/popular-picks',
        () => new HttpResponse(null, { status: 500 }),
      ),
    );
    await expect(fetchPopularPicks()).resolves.toEqual([]);
  });

  it('discards a success:false payload rather than half-rendering it', async () => {
    server.use(
      http.get('/api/discover/deep-cuts', () =>
        HttpResponse.json({ success: false, tracks: [{ name: 'ghost' }] }),
      ),
    );
    await expect(fetchDeepCuts()).resolves.toEqual([]);
  });

  it('tolerates a success payload missing its data key', async () => {
    server.use(http.get('/api/discover/deep-cuts', () => HttpResponse.json({ success: true })));
    await expect(fetchDeepCuts()).resolves.toEqual([]);
  });

  it('treats a payload with NO success key as a SUCCESS', async () => {
    // Mirrors the vanilla controller's _isSuccess: only an explicit
    // `success: false` is a failure. Treating absent-as-failure would silently
    // blank any endpoint returning a bare {tracks: [...]}.
    server.use(
      http.get('/api/discover/deep-cuts', () =>
        HttpResponse.json({ tracks: [{ track_name: 'No envelope' }] }),
      ),
    );
    await expect(fetchDeepCuts()).resolves.toHaveLength(1);
  });

  it('still treats an explicit success:false as a failure', async () => {
    server.use(
      http.get('/api/discover/deep-cuts', () =>
        HttpResponse.json({ success: false, tracks: [{ track_name: 'ghost' }] }),
      ),
    );
    await expect(fetchDeepCuts()).resolves.toEqual([]);
  });

  it('passes the rows through when the shelf is healthy', async () => {
    server.use(
      http.get('/api/discover/deep-cuts', () =>
        HttpResponse.json({ success: true, tracks: [{ track_name: 'Xtal' }] }),
      ),
    );
    await expect(fetchDeepCuts()).resolves.toHaveLength(1);
  });

  it('label-explorer returns both lists, or two empties', async () => {
    server.use(
      http.get('/api/discover/label-explorer', () =>
        HttpResponse.json({ success: true, albums: [{ name: 'A' }], labels: ['Warp'] }),
      ),
    );
    await expect(fetchLabelExplorer()).resolves.toEqual({
      albums: [{ name: 'A' }],
      labels: ['Warp'],
    });

    server.use(http.get('/api/discover/label-explorer', () => HttpResponse.error()));
    await expect(fetchLabelExplorer()).resolves.toEqual({ albums: [], labels: [] });
  });
});

describe('the hero', () => {
  it('keeps the watchlist fallback flag', async () => {
    // The UI explains WHY the hero looks different when the active source came
    // back empty. Dropping this makes that explanation disappear.
    server.use(
      http.get('/api/discover/hero', () =>
        HttpResponse.json({ success: true, artists: [{ name: 'A' }], fallback: 'watchlist' }),
      ),
    );
    const hero = await fetchHero();
    expect(hero.fallback).toBe('watchlist');
    expect(hero.source).toBeUndefined();
  });

  it('degrades to an empty billboard rather than throwing', async () => {
    server.use(http.get('/api/discover/hero', () => HttpResponse.error()));
    await expect(fetchHero()).resolves.toEqual({ success: false, artists: [] });
  });
});

describe('your-albums paging', () => {
  it('defaults to page 1 at 48 per page', async () => {
    // 48 is load-bearing: the grid is 6-up at its widest, so 48 fills whole
    // rows and the load-more boundary never lands mid-row.
    const seen = capture('get', '/api/discover/your-albums', { success: true, albums: [] });
    await fetchYourAlbums();
    expect(seen[0].url.searchParams.get('page')).toBe('1');
    expect(seen[0].url.searchParams.get('per_page')).toBe('48');
  });

  it('omits optional filters entirely when unset', async () => {
    const seen = capture('get', '/api/discover/your-albums', { success: true, albums: [] });
    await fetchYourAlbums({ page: 2 });
    expect(seen[0].url.searchParams.get('status')).toBeNull();
    expect(seen[0].url.searchParams.get('search')).toBeNull();
    expect(seen[0].url.searchParams.get('sort')).toBeNull();
  });

  it('sends the filters that ARE set', async () => {
    const seen = capture('get', '/api/discover/your-albums', { success: true, albums: [] });
    await fetchYourAlbums({ page: 3, status: 'missing', search: 'aphex', sort: 'year' });
    const p = seen[0].url.searchParams;
    expect([p.get('page'), p.get('status'), p.get('search'), p.get('sort')]).toEqual([
      '3',
      'missing',
      'aphex',
      'year',
    ]);
  });

  it('asks the refresh to clear the cache by default', async () => {
    const seen = capture('post', '/api/discover/your-albums/refresh');
    await refreshYourAlbums();
    expect(seen[0].url.searchParams.get('clear')).toBe('true');
  });

  it('can refresh WITHOUT clearing', async () => {
    const seen = capture('post', '/api/discover/your-albums/refresh');
    await refreshYourAlbums(false);
    expect(seen[0].url.searchParams.get('clear')).toBe('false');
  });
});

describe('endpoint contracts that fail quietly', () => {
  it('resolve-cache-album always sends BOTH name and artist', async () => {
    // The handler is `if not name or not artist: 400` — artist is required,
    // not optional. An earlier draft skipped the param when falsy, which typed
    // an optionality the endpoint does not have.
    const seen = capture('get', '/api/discover/resolve-cache-album');
    await resolveCacheAlbum('Selected Ambient Works', 'Aphex Twin');
    expect(seen[0].url.searchParams.get('name')).toBe('Selected Ambient Works');
    expect(seen[0].url.searchParams.get('artist')).toBe('Aphex Twin');
  });

  it('sends an empty artist rather than dropping the param', async () => {
    // Matches the vanilla, which sends `artist=${encodeURIComponent(x || '')}`.
    // Both empty and missing 400 server-side, but only one of them is what the
    // vanilla actually put on the wire.
    const seen = capture('get', '/api/discover/resolve-cache-album');
    await resolveCacheAlbum('Some Album', '');
    expect(seen[0].url.searchParams.has('artist')).toBe(true);
    expect(seen[0].url.searchParams.get('artist')).toBe('');
  });

  it('artist info sends the name alongside the id', async () => {
    // The id can be from a source that is no longer active; the server falls
    // back to a name lookup, so dropping the name breaks those rows.
    const seen = capture('get', '/api/discover/your-artists/info/:id');
    await fetchArtistInfo('sp/123', 'Aphex Twin');
    expect(seen[0].url.pathname).toBe('/api/discover/your-artists/info/sp%2F123');
    expect(seen[0].url.searchParams.get('name')).toBe('Aphex Twin');
  });

  it('un-blacklisting uses DELETE, not POST', async () => {
    const seen = capture('delete', '/api/discover/artist-blacklist/:id');
    await unblacklistArtist(7);
    expect(seen[0].url.pathname).toBe('/api/discover/artist-blacklist/7');
  });

  it('blacklisting posts the payload', async () => {
    const seen = capture('post', '/api/discover/artist-blacklist');
    await blacklistArtist({ artist_name: 'Nickelback' });
    expect(JSON.parse(seen[0].body)).toEqual({ artist_name: 'Nickelback' });
  });

  it('enrich posts the artists under an artists key', async () => {
    const seen = capture('post', '/api/discover/similar-artists/enrich');
    await enrichSimilarArtists([{ name: 'A' }]);
    expect(JSON.parse(seen[0].body)).toEqual({ artists: [{ name: 'A' }] });
  });

  it('the adventurousness dial round-trips a value', async () => {
    capture('get', '/api/discover/adventurousness', { success: true, value: 0.7 });
    await expect(fetchAdventurousness()).resolves.toMatchObject({ value: 0.7 });

    const seen = capture('post', '/api/discover/adventurousness', { success: true, value: 0.2 });
    await setAdventurousness(0.2);
    expect(JSON.parse(seen[0].body)).toEqual({ value: 0.2 });
  });

  it('sends a zero dial value rather than dropping it', async () => {
    // 0 is a real setting (least adventurous); a falsy check would lose it.
    const seen = capture('post', '/api/discover/adventurousness');
    await setAdventurousness(0);
    expect(JSON.parse(seen[0].body)).toEqual({ value: 0 });
  });

  it('escapes a ListenBrainz mbid', async () => {
    const seen = capture('get', '/api/discover/listenbrainz/playlist/:mbid');
    await fetchLbPlaylist('a b/c');
    expect(seen[0].url.pathname).toBe('/api/discover/listenbrainz/playlist/a%20b%2Fc');
  });
});
