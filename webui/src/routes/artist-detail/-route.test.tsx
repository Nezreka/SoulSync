import { createMemoryHistory } from '@tanstack/react-router';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

function stubDetail(body: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
    ),
  );
}

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
 * Every URL the page requested, so the source/name round-trip can be checked.
 *
 * ky hands fetch a Request object rather than a string, so the url has to be
 * read off it — String() would give "[object Request]" and every assertion
 * below would pass vacuously.
 */
const requestedUrls = () =>
  (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.map((call) =>
    call[0] instanceof Request ? call[0].url : String(call[0]),
  );

const detailUrl = () => requestedUrls().find((url) => url.includes('/api/artist-detail')) ?? '';

beforeEach(() => {
  window.SoulSyncWebShellBridge = createShellBridge();
  window.showToast = vi.fn();
  window.loadSimilarArtists = vi.fn();
  window.cancelSimilarArtistsLoad = vi.fn();
  window.checkArtistEnhanceEligibility = vi.fn();
  window.observeLazyBackgrounds = vi.fn();
  stubDetail({
    success: true,
    artist: { id: 42, name: 'Aphex Twin', server_source: 'plex' },
    discography: { albums: [{ id: 1, title: 'SAW', owned: true }], source: 'spotify' },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.SoulSyncWebShellBridge = undefined;
  delete document.body.dataset.artistSource;
});

describe('artist-detail route', () => {
  it('renders the React page rather than handing off to the legacy shell', async () => {
    renderArtistDetailRoute(['/artist-detail/spotify/2YZyLoL8N0Wb9xBt1NhZWg']);

    await screen.findByText('Aphex Twin');
    expect(window.SoulSyncWebShellBridge?.navigateToArtistDetail).not.toHaveBeenCalled();
  });

  it('survives an all-digits artist name (311) in ?name=', async () => {
    // TanStack's search parser JSON-parses param values, so name=311 arrives as
    // a NUMBER. A bare z.string() schema threw SearchParamError, the route died
    // in its error boundary, and clicking the artist "did nothing". This has
    // regressed once already.
    renderArtistDetailRoute(['/artist-detail/deezer/2481?name=311']);

    await screen.findByText('Aphex Twin');
    await waitFor(() => expect(detailUrl()).toContain('name=311'));
  });

  it('round-trips the ?name= search param into the request', async () => {
    // Bandcamp (and any other source with no numeric-ID lookup API) can only
    // resolve an artist by name — the URL is the only channel that survives a
    // page load or a browser-back.
    renderArtistDetailRoute(['/artist-detail/bandcamp/3957198221?name=Radiohead']);

    await screen.findByText('Aphex Twin');
    expect(detailUrl()).toContain('name=Radiohead');
    expect(detailUrl()).toContain('source=bandcamp');
  });

  it('normalizes a library source away rather than sending it', async () => {
    // "library" is not a metadata source; sending it would ask the backend to
    // resolve the artist somewhere it does not exist.
    renderArtistDetailRoute(['/artist-detail/library/42']);

    await screen.findByText('Aphex Twin');
    // Assert the request was found first — a missing url would make the
    // not.toContain below pass for the wrong reason.
    expect(detailUrl()).toContain('/api/artist-detail/42');
    expect(detailUrl()).not.toContain('source=library');
  });

  it('cancels the similar-artists stream when the route unmounts', async () => {
    const { unmount } = renderArtistDetailRoute(['/artist-detail/spotify/2YZyLoL8N0Wb9xBt1NhZWg']);

    await waitFor(() => expect(window.loadSimilarArtists).toHaveBeenCalledWith('Aphex Twin'));
    (window.cancelSimilarArtistsLoad as ReturnType<typeof vi.fn>).mockClear();

    unmount();
    expect(window.cancelSimilarArtistsLoad).toHaveBeenCalled();
  });
});
