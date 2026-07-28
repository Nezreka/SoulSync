import { createMemoryHistory } from '@tanstack/react-router';
import { render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

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

/** ldp-01: this route used to hand off to the vanilla-JS artist page, which is
 *  how a search hit for an artist you don't own yet still landed a user in the
 *  legacy library. It now redirects into Library V2's discovery mode instead —
 *  and because every caller (search, global search, media player, playlist
 *  sync, similar-artist bubbles) navigates through this one URL, that single
 *  redirect routes all of them. The old expectations pinned exactly the
 *  behaviour this change removes, so they are replaced, not repaired. */
describe('artist-detail route', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
  });

  afterEach(() => {
    window.SoulSyncWebShellBridge = undefined;
  });

  /** Asserts against the router's PARSED search, not the raw query string:
   *  TanStack JSON-encodes values on write, so an all-digits name is stored
   *  as `"311"` and only reads back as the string `311`. */
  async function expectRedirect(entry: string, expected: Record<string, string>) {
    const { history, router } = renderArtistDetailRoute([entry]);
    await waitFor(() => expect(history.location.pathname).toBe('/library'));
    expect(router.state.location.search).toMatchObject(expected);
    // The legacy shell must never be involved again.
    expect(window.SoulSyncWebShellBridge?.navigateToArtistDetail).not.toHaveBeenCalled();
  }

  it('redirects a provider artist into Library V2 discovery mode', async () => {
    await expectRedirect('/artist-detail/spotify/2YZyLoL8N0Wb9xBt1NhZWg', {
      discover: 'spotify:2YZyLoL8N0Wb9xBt1NhZWg',
      // Coming from search means landing on what the legacy artist page
      // showed: full discography, card view, rich header (ldp-05).
      releases: 'all',
      releaseView: 'cards',
      header: 'rich',
    });
  });

  it('survives an all-digits artist name (311) in ?name=', async () => {
    // TanStack's search parser JSON-parses param values, so name=311 arrives
    // as a NUMBER. A bare z.string() schema threw SearchParamError, the route
    // died in its error boundary, and clicking the artist "did nothing".
    await expectRedirect('/artist-detail/deezer/2481?name=311', {
      discover: 'deezer:2481',
      discoverName: '311',
    });
  });

  it('does not stringify structured search params as an artist name', async () => {
    const { history, router } = renderArtistDetailRoute([
      '/artist-detail/deezer/2481?name=%7B%22unexpected%22%3Atrue%7D',
    ]);

    await waitFor(() => expect(history.location.pathname).toBe('/library'));
    expect(router.state.location.search).not.toHaveProperty('discoverName');
  });

  it('carries the ?name= search param into discovery', async () => {
    // Bandcamp (and any other source with no numeric-ID lookup API) can only
    // resolve an artist by name — the URL is the only channel that survives
    // a page load / browser-back, so this must round-trip correctly.
    await expectRedirect('/artist-detail/bandcamp/3957198221?name=Radiohead', {
      discover: 'bandcamp:3957198221',
      discoverName: 'Radiohead',
    });
  });

  it('keeps the legacy `library` namespace intact so the id stays resolvable', async () => {
    // Not a provider id: an opaque legacy `artists.id`. The server resolves it
    // through `lib2_artists.legacy_artist_id` and must never mistake it for a
    // provider identity (guide §2.5).
    await expectRedirect('/artist-detail/library/42', { discover: 'library:42' });
  });

  it('lower-cases the source segment', async () => {
    await expectRedirect('/artist-detail/Spotify/abc', { discover: 'spotify:abc' });
  });
});
