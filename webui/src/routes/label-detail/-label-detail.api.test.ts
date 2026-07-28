import { HttpResponse, http } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import {
  fetchLabelCatalogPage,
  fetchOwnedKeys,
  LABEL_PAGE_SIZE,
  setLabelBacklog,
  setLabelWatched,
} from './-label-detail.api';
import { releaseKey } from './-label-detail.helpers';

afterEach(() => vi.restoreAllMocks());

describe('fetchLabelCatalogPage', () => {
  it('asks for the vanilla page size and passes the name through', async () => {
    let seen = '';
    server.use(
      http.get('/api/labels/:id/catalog', ({ request }) => {
        seen = new URL(request.url).search;
        return HttpResponse.json({ total: 1, releases: [] });
      }),
    );

    await fetchLabelCatalogPage('mb-1', 'Warp Records', 2);
    expect(seen).toContain(`page_size=${LABEL_PAGE_SIZE}`);
    expect(seen).toContain('page=2');
    expect(seen).toContain('name=Warp+Records');
  });

  it('omits an empty name rather than sending name=', async () => {
    // The endpoint falls back to the watchlist row's label_name; an empty
    // name= would overwrite a good name with nothing.
    let seen = '';
    server.use(
      http.get('/api/labels/:id/catalog', ({ request }) => {
        seen = new URL(request.url).search;
        return HttpResponse.json({ releases: [] });
      }),
    );

    await fetchLabelCatalogPage('mb-1', '', 1);
    expect(seen).not.toContain('name=');
  });

  it('escapes a label id that is not url-safe', async () => {
    let path = '';
    server.use(
      http.get('/api/labels/*', ({ request }) => {
        path = new URL(request.url).pathname;
        return HttpResponse.json({ releases: [] });
      }),
    );

    await fetchLabelCatalogPage('a/b c', '', 1);
    expect(path).toContain('a%2Fb%20c');
  });
});

describe('fetchOwnedKeys', () => {
  const releases = [
    { artist: 'A', album: 'One' },
    { artist: 'B', album: 'Two' },
    { artist: 'C', album: 'Three' },
  ];

  it('maps the POSITIONAL response back onto the releases it sent', async () => {
    // The endpoint answers albums[i] for the i-th album sent — the request
    // order IS the contract, which is why this returns keys rather than an
    // array a later caller could re-zip against a different order.
    server.use(
      http.post('/api/enhanced-search/library-check', () =>
        HttpResponse.json({ albums: [false, true, true] }),
      ),
    );

    const owned = await fetchOwnedKeys(releases);
    expect(owned.has(releaseKey(releases[1]))).toBe(true);
    expect(owned.has(releaseKey(releases[2]))).toBe(true);
    expect(owned.has(releaseKey(releases[0]))).toBe(false);
  });

  it('sends name/artist per album and an empty tracks list', async () => {
    let body: unknown;
    server.use(
      http.post('/api/enhanced-search/library-check', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ albums: [] });
      }),
    );

    await fetchOwnedKeys([{ artist: 'A', album: 'One' }]);
    expect(body).toEqual({ albums: [{ name: 'One', artist: 'A' }], tracks: [] });
  });

  it('leaves everything unchecked when the call fails', async () => {
    // Ownership is a nicety: a failure must not mark the whole grid missing.
    server.use(http.post('/api/enhanced-search/library-check', () => HttpResponse.error()));
    await expect(fetchOwnedKeys(releases)).resolves.toEqual(new Set());
  });

  it('does not call out at all for an empty batch', async () => {
    let called = false;
    server.use(
      http.post('/api/enhanced-search/library-check', () => {
        called = true;
        return HttpResponse.json({ albums: [] });
      }),
    );
    await fetchOwnedKeys([]);
    expect(called).toBe(false);
  });
});

describe('watch + backlog', () => {
  it('adds with the name and removes with only the id', async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post('/api/labels/watchlist/add', async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ success: true });
      }),
      http.post('/api/labels/watchlist/remove', async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ success: true });
      }),
    );

    await expect(setLabelWatched('mb-1', 'Warp', true)).resolves.toBe(true);
    await expect(setLabelWatched('mb-1', 'Warp', false)).resolves.toBe(true);
    expect(bodies[0]).toEqual({ musicbrainz_label_id: 'mb-1', label_name: 'Warp' });
    expect(bodies[1]).toEqual({ musicbrainz_label_id: 'mb-1' });
  });

  it('reports a refusal so the caller can put the button back', async () => {
    server.use(http.post('/api/labels/watchlist/add', () => HttpResponse.json({ success: false })));
    await expect(setLabelWatched('mb-1', 'Warp', true)).resolves.toBe(false);
  });

  it('reports a network failure as a refusal rather than throwing', async () => {
    server.use(http.post('/api/labels/watchlist/add', () => HttpResponse.error()));
    await expect(setLabelWatched('mb-1', 'Warp', true)).resolves.toBe(false);
  });

  it('sends the backlog flag and reports the outcome', async () => {
    let body: unknown;
    server.use(
      http.post('/api/labels/watchlist/backlog', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ success: true });
      }),
    );

    await expect(setLabelBacklog('mb-1', true)).resolves.toBe(true);
    expect(body).toEqual({ musicbrainz_label_id: 'mb-1', backlog: true });
  });
});
