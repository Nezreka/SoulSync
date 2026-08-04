import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/** ldp-01/ldp-02: an artist the catalogue has never heard of has to render
 *  inside Library V2, from provider data alone, without writing anything. */
function renderDiscovery(entry: string) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [entry] });
  const router = createAppRouter({ history, queryClient });
  return {
    history,
    router,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

const DISCOVERY_URL =
  '/library?discover=%22spotify%3Asp-1%22&discoverName=%22Boards%20of%20Canada%22';

describe('Library V2 discovery mode', () => {
  let resolveResponse: number | null;
  let materializeCalls: unknown[];

  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    resolveResponse = null;
    materializeCalls = [];
    server.use(
      http.get('/api/library/v2/enabled', () =>
        HttpResponse.json({ success: true, enabled: true, can_write: true }),
      ),
      http.get('/api/library/v2/mirror-status', () =>
        HttpResponse.json({ success: true, pending: 0, failed: 0 }),
      ),
      http.get('/api/library/v2/discovery/artist', () =>
        HttpResponse.json({ success: true, artist_id: resolveResponse }),
      ),
      http.post('/api/library/v2/discovery/artist', async ({ request }) => {
        materializeCalls.push(await request.json());
        return HttpResponse.json({ success: true, artist_id: 55 });
      }),
      http.get('/api/artist-detail/:id', () =>
        HttpResponse.json({
          success: true,
          artist: {
            id: 'sp-1',
            name: 'Boards of Canada',
            image_url: 'https://cdn.test/artist.jpg',
            genres: ['idm'],
            lastfm_listeners: 1_200_000,
            lastfm_playcount: 45_000_000,
          },
          discography: {
            albums: [
              {
                id: 'a1',
                title: 'Music Has the Right to Children',
                release_date: '1998-04-20',
              },
              { id: 'a2', title: 'Live at Warp', release_date: '2001-01-01' },
            ],
            eps: [],
            singles: [],
          },
        }),
      ),
      http.get('/api/artist/:id/top-tracks', () =>
        HttpResponse.json({
          success: true,
          tracks: [
            {
              id: 'sp-t1',
              name: 'Roygbiv',
              album: { id: 'sp-a1', name: 'Music Has the Right' },
            },
          ],
        }),
      ),
      http.get('/api/artist/0/lastfm-top-tracks', () =>
        HttpResponse.json({
          success: true,
          tracks: [{ name: 'Roygbiv', playcount: 4_200_000 }],
        }),
      ),
      http.get('/api/library/v2/artists/55', () =>
        HttpResponse.json({ success: false, error: 'not seeded' }, { status: 404 }),
      ),
      http.get('/api/library/v2/discovery/track-status', () =>
        HttpResponse.json({
          success: true,
          statuses: { Roygbiv: { track_id: 9, monitored: true } },
        }),
      ),
    );
  });

  it('renders a provider-only artist without creating a catalogue row', async () => {
    renderDiscovery(DISCOVERY_URL);

    expect(await screen.findByRole('heading', { name: 'Boards of Canada' })).toBeInTheDocument();
    // Legacy hero facts the user explicitly asked to keep (ldp-05).
    expect(screen.getByText('1.2M')).toBeInTheDocument();
    expect(screen.getByText('45M')).toBeInTheDocument();
    expect(screen.getByText('Music Has the Right to Children')).toBeInTheDocument();
    // No library to compare against, so no ownership badge claims these
    // releases are forever "Checking…" (legacy did the same for a source
    // artist).
    expect(document.querySelector('.completion-overlay')).toBeNull();
    // Read-only until the user asks for something (issues §28.6 question 1).
    expect(materializeCalls).toHaveLength(0);
  });

  it('filters the discography with the ported legacy filter bar (ldp-04)', async () => {
    renderDiscovery(DISCOVERY_URL);
    await screen.findByText('Live at Warp');

    fireEvent.click(screen.getByRole('button', { name: 'Live' }));

    await waitFor(() => expect(screen.queryByText('Live at Warp')).not.toBeInTheDocument());
    expect(screen.getByText('Music Has the Right to Children')).toBeInTheDocument();
  });

  it('falls back to display-only Last.fm rows when the source has no ranking', async () => {
    server.use(
      http.get('/api/artist/:id/top-tracks', () =>
        HttpResponse.json({ success: false, tracks: [] }),
      ),
    );
    renderDiscovery(DISCOVERY_URL);

    expect(await screen.findByText('Popular on Last.fm')).toBeInTheDocument();
    expect(screen.getByText('Roygbiv')).toBeInTheDocument();
    // A Last.fm row is a name and a playcount — no album, no ids. Bookmarking
    // one invented an album that matched nothing, so legacy never offered the
    // action on these rows either.
    expect(document.querySelector('.hero-top-track-download')).toBeNull();
  });

  it('shows an already-wanted top track as bookmarked on a fresh load', async () => {
    // The tick used to live purely in component state, so it disappeared the
    // moment the page was reloaded even though the wishlist row existed.
    renderDiscovery(DISCOVERY_URL);

    expect(await screen.findByTitle('Bookmarked — this track is now wanted')).toBeInTheDocument();
  });

  it('bookmarking the artist materializes it, monitors it, and continues on the real page', async () => {
    const monitorWrites: unknown[] = [];
    server.use(
      http.post('/api/library/v2/artists/55/monitor', async ({ request }) => {
        monitorWrites.push(await request.json());
        return HttpResponse.json({ success: true });
      }),
    );
    const { router } = renderDiscovery(DISCOVERY_URL);
    await screen.findByRole('heading', { name: 'Boards of Canada' });

    fireEvent.click(screen.getByRole('button', { name: /Bookmark artist/ }));

    await waitFor(() => expect(materializeCalls).toHaveLength(1));
    expect(materializeCalls[0]).toMatchObject({
      source: 'spotify',
      provider_id: 'sp-1',
      name: 'Boards of Canada',
    });
    // ldp-06: Bookmark states intent — it must go through the proven monitor
    // path, not just leave an unmonitored catalogue row behind.
    await waitFor(() => expect(monitorWrites).toEqual([{ monitored: true }]));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ artist: 55 }));
  });

  it('opening a release adopts the artist WITHOUT monitoring them', async () => {
    let monitorWrites = 0;
    server.use(
      http.post('/api/library/v2/artists/55/monitor', () => {
        monitorWrites += 1;
        return HttpResponse.json({ success: true });
      }),
    );
    const { router } = renderDiscovery(DISCOVERY_URL);
    const card = await screen.findByText('Music Has the Right to Children');

    fireEvent.click(card);

    await waitFor(() => expect(router.state.location.search).toMatchObject({ artist: 55 }));
    expect(materializeCalls).toHaveLength(1);
    expect(monitorWrites).toBe(0);
  });

  it('hands an artist that already exists straight to the catalogue page', async () => {
    resolveResponse = 42;
    const { router } = renderDiscovery(DISCOVERY_URL);

    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({
        artist: 42,
        releases: 'all',
        releaseView: 'cards',
        header: 'rich',
      }),
    );
    expect(router.state.location.search).not.toHaveProperty('discover');
  });
});
