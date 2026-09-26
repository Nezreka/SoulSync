import { createMemoryHistory } from '@tanstack/react-router';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/**
 * Which page an artist URL opens on this branch.
 *
 * Library v2 owns every artist the catalogue knows; upstream's artist page is
 * kept, unchanged, for the ones it does not. The route decides before either
 * renders, with a read-only lookup.
 */
function renderAt(entry: string) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [entry] });
  const router = createAppRouter({ history, queryClient });
  return {
    router,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

describe('artist-detail route on a Library v2 catalogue', () => {
  let resolved: number | null;
  let resolveFails: boolean;
  let lookups: URL[];
  let detailRequests: URL[];

  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    window.showToast = vi.fn();
    window.loadSimilarArtists = vi.fn();
    window.cancelSimilarArtistsLoad = vi.fn();
    window.observeLazyBackgrounds = vi.fn();
    resolved = null;
    resolveFails = false;
    lookups = [];
    detailRequests = [];
    server.use(
      http.get('/api/library/v2/discovery/artist', ({ request }) => {
        lookups.push(new URL(request.url));
        if (resolveFails) return HttpResponse.json({ success: false }, { status: 500 });
        return HttpResponse.json({ success: true, artist_id: resolved });
      }),
      http.get('/api/artist-detail/:id', ({ request }) => {
        detailRequests.push(new URL(request.url));
        return HttpResponse.json({
          success: true,
          artist: { id: 'x', name: 'Aphex Twin' },
          discography: { albums: [{ id: 'a1', title: 'Drukqs' }], source: 'deezer' },
        });
      }),
      // What the library side and the page's own panels ask for on the way.
      http.get('/api/library/v2/enabled', () =>
        HttpResponse.json({ success: true, enabled: true, can_write: true }),
      ),
      http.get('/api/library/v2/mirror-status', () =>
        HttpResponse.json({ success: true, pending: 0, failed: 0 }),
      ),
      http.get('/api/library/v2/artists', () =>
        HttpResponse.json({ success: true, artists: [], total: 0, page: 1, pages: 1 }),
      ),
      http.get('/api/library/v2/artists/:id', () =>
        HttpResponse.json({ success: false, error: 'not seeded' }, { status: 404 }),
      ),
      http.post('/api/watchlist/check', () =>
        HttpResponse.json({ success: true, is_watching: false }),
      ),
      http.get('/api/artist/:id/top-tracks', () =>
        HttpResponse.json({ success: true, tracks: [] }),
      ),
      http.get('/api/artist/0/lastfm-top-tracks', () =>
        HttpResponse.json({ success: true, tracks: [] }),
      ),
    );
  });

  afterEach(() => {
    window.SoulSyncWebShellBridge = undefined;
    delete document.body.dataset.artistSource;
  });

  it('opens a numeric library id as its Library v2 artist, without a lookup', async () => {
    const { router } = renderAt('/artist-detail/library/42');

    await waitFor(() => expect(router.state.location.pathname).toBe('/library'));
    expect(router.state.location.search).toMatchObject({ artist: 42 });
    expect(lookups).toHaveLength(0);
    expect(detailRequests).toHaveLength(0);
  });

  it('resolves a media-server id through the catalogue', async () => {
    resolved = 9;
    const { router } = renderAt('/artist-detail/library/abc-guid?name=Aphex%20Twin');

    await waitFor(() => expect(router.state.location.search).toMatchObject({ artist: 9 }));
    expect(lookups[0]?.searchParams.get('source')).toBe('library');
    expect(lookups[0]?.searchParams.get('provider_id')).toBe('abc-guid');
    // An owned artist opened from inside the app keeps the Library v2 shape.
    expect(router.state.location.search).not.toHaveProperty('header', 'rich');
  });

  it('sends a media-server id the catalogue does not know to the library, not a dead page', async () => {
    const { router } = renderAt('/artist-detail/library/abc-guid');

    await waitFor(() => expect(router.state.location.pathname).toBe('/library'));
    expect(router.state.location.search).not.toHaveProperty('artist');
    expect(detailRequests).toHaveLength(0);
  });

  it('opens a provider artist the catalogue holds in Library v2, full discography as cards', async () => {
    resolved = 7;
    const { router } = renderAt('/artist-detail/deezer/2481?name=Aphex%20Twin');

    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({
        artist: 7,
        releases: 'all',
        releaseView: 'cards',
        header: 'rich',
      }),
    );
    expect(lookups[0]?.searchParams.get('source')).toBe('deezer');
    expect(lookups[0]?.searchParams.get('name')).toBe('Aphex Twin');
    expect(detailRequests).toHaveLength(0);
  });

  it("opens any other provider artist on upstream's page", async () => {
    const { router } = renderAt('/artist-detail/deezer/2481?name=Aphex%20Twin');

    expect(await screen.findByText('Drukqs')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/artist-detail/deezer/2481');
    expect(detailRequests[0]?.searchParams.get('source')).toBe('deezer');
    expect(detailRequests[0]?.searchParams.get('name')).toBe('Aphex Twin');
  });

  it('survives an all-digits artist name (311) in ?name=', async () => {
    // TanStack JSON-parses search values, so name=311 arrives as a NUMBER; the
    // lookup and the page request both have to carry it as text.
    renderAt('/artist-detail/deezer/2481?name=311');

    expect(await screen.findByText('Drukqs')).toBeInTheDocument();
    expect(lookups[0]?.searchParams.get('name')).toBe('311');
    expect(detailRequests[0]?.searchParams.get('name')).toBe('311');
  });

  it('still shows the provider page when the catalogue lookup fails', async () => {
    resolveFails = true;
    renderAt('/artist-detail/spotify/sp-1?name=Aphex%20Twin');

    expect(await screen.findByText('Drukqs')).toBeInTheDocument();
  });
});
