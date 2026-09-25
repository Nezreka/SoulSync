import { createMemoryHistory } from '@tanstack/react-router';
import { render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

function renderArtistDetailRoute(initialEntries = ['/artist-detail/library/42']) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries });
  const router = createAppRouter({ history, queryClient });

  return {
    history,
    router,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

/**
 * The destination's PARSED search params.
 *
 * Read off the router, not the raw query string: TanStack JSON-encodes search
 * values, so an all-digits name is on the wire as `discoverName=%22311%22` and
 * a string comparison against the raw form would fail for a URL that is
 * actually correct. What matters is the value the destination route receives.
 */
const landedSearch = (router: { state: { location: { search: unknown } } }) =>
  router.state.location.search as Record<string, unknown>;

beforeEach(() => {
  window.SoulSyncWebShellBridge = createShellBridge();
  window.showToast = vi.fn();
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(JSON.stringify({ success: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.SoulSyncWebShellBridge = undefined;
  delete document.body.dataset.artistSource;
  delete window.playTrackList;
  delete window.showLoadingOverlay;
  delete window.hideLoadingOverlay;
});

/**
 * ldp-01: this route is a redirect into Library V2, not a page.
 *
 * It was one before the 2026-07-31 upstream sync, briefly became upstream's own
 * React artist page during it, and was restored afterwards — the sync regressed
 * the search → Library V2 flow this fork exists for (iss29-B02). These tests
 * replace the ones that pinned upstream's page rendering here; they are not
 * repairs of them.
 */
describe('artist-detail route', () => {
  it('opens a legacy library id as the owned Library V2 artist', async () => {
    const { history, router } = renderArtistDetailRoute(['/artist-detail/library/42']);

    await waitFor(() => expect(history.location.pathname).toBe('/library'));
    expect(landedSearch(router).artist).toBe(42);
    expect(landedSearch(router).discover).toBeUndefined();
  });

  it('redirects into Library V2 discovery mode instead of rendering a page', async () => {
    const { history, router } = renderArtistDetailRoute([
      '/artist-detail/spotify/2YZyLoL8N0Wb9xBt1NhZWg',
    ]);

    await waitFor(() => expect(history.location.pathname).toBe('/library'));
    expect(landedSearch(router).discover).toBe('spotify:2YZyLoL8N0Wb9xBt1NhZWg');
    // The legacy shell must not be handed the artist as well — two owners of
    // the same navigation is exactly what the sync collision was.
    expect(window.SoulSyncWebShellBridge?.navigateToArtistDetail).not.toHaveBeenCalled();
  });

  it('preselects the view a search arrival expects (ldp-05)', async () => {
    const { history, router } = renderArtistDetailRoute(['/artist-detail/spotify/abc']);

    await waitFor(() => expect(history.location.pathname).toBe('/library'));
    const search = landedSearch(router);
    expect(search.releases).toBe('all');
    expect(search.releaseView).toBe('cards');
    expect(search.header).toBe('rich');
  });

  it('survives an all-digits artist name (311) in ?name=', async () => {
    // TanStack's search parser JSON-parses param values, so name=311 arrives as
    // a NUMBER. A bare z.string() schema threw SearchParamError, the route died
    // in its error boundary, and clicking the artist "did nothing". This has
    // regressed once already, which is why it outlived the page it was written
    // for.
    const { history, router } = renderArtistDetailRoute(['/artist-detail/deezer/2481?name=311']);

    await waitFor(() => expect(history.location.pathname).toBe('/library'));
    expect(landedSearch(router).discoverName).toBe('311');
  });

  it('carries the display name for sources with no ID lookup', async () => {
    // Bandcamp (and any other source with no numeric-ID lookup API) can only
    // resolve an artist by name — the URL is the only channel that survives a
    // page load or a browser-back.
    const { history, router } = renderArtistDetailRoute([
      '/artist-detail/bandcamp/3957198221?name=Radiohead',
    ]);

    await waitFor(() => expect(history.location.pathname).toBe('/library'));
    const search = landedSearch(router);
    expect(search.discover).toBe('bandcamp:3957198221');
    expect(search.discoverName).toBe('Radiohead');
  });

  it('lowercases the source segment', async () => {
    // The discovery resolver keys on a lowercase namespace; a capitalised
    // segment from a hand-built link would miss every provider match.
    const { history, router } = renderArtistDetailRoute(['/artist-detail/Spotify/42']);

    await waitFor(() => expect(history.location.pathname).toBe('/library'));
    expect(landedSearch(router).discover).toBe('spotify:42');
  });

  it('replaces the history entry so Back leaves the artist, not the redirect', async () => {
    const { history } = renderArtistDetailRoute(['/artist-detail/spotify/42']);

    await waitFor(() => expect(history.location.pathname).toBe('/library'));
    expect(history.canGoBack()).toBe(false);
  });
  // Upstream's three play-button tests (`loads an album tracklist...`, `plays an
  // owned album...`, `still plays what it can...`) are not carried here: they
  // click `.release-card-play-btn`, which lives in the legacy artist-detail
  // release card this branch deleted for Library v2. The helper they exercise,
  // -artist-detail.owned-tracks.ts, IS kept with its own unit test - it is what
  // a Library-v2 play button would be built on.
});
