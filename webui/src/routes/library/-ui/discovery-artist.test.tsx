import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/** An artist the catalogue does not hold opens on the same Library v2 artist
 *  page as every other artist (dev had one artist page for everyone), read
 *  from the provider: no My Library, nothing written until you act. */
function renderAt(entry: string) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [entry] });
  const router = createAppRouter({ history, queryClient });
  return {
    history,
    router,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

const ARTIST_URL = '/artist-detail/spotify/sp-1?name=Boards%20of%20Canada';

describe('an artist the catalogue does not hold', () => {
  let resolveResponse: number | null;
  let materializeCalls: unknown[];
  let monitoredAlbums: unknown[];

  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    window.showToast = vi.fn();
    window.loadSimilarArtists = vi.fn();
    window.cancelSimilarArtistsLoad = vi.fn();
    window.observeLazyBackgrounds = vi.fn();
    window.openDownloadMissingModalForArtistAlbum = vi.fn();
    resolveResponse = null;
    materializeCalls = [];
    monitoredAlbums = [];
    server.use(
      http.get('/api/library/v2/enabled', () =>
        HttpResponse.json({ success: true, enabled: true, can_write: true }),
      ),
      http.get('/api/library/v2/mirror-status', () =>
        HttpResponse.json({ success: true, pending: 0, failed: 0 }),
      ),
      http.get('/api/library/v2/discovery/artist', () =>
        HttpResponse.json({ success: true, artist_id: resolveResponse, owned: true }),
      ),
      http.post('/api/library/v2/discovery/artist', async ({ request }) => {
        materializeCalls.push(await request.json());
        return HttpResponse.json({ success: true, artist_id: 55 });
      }),
      http.post('/api/library/v2/discovery/album', async ({ request }) => {
        monitoredAlbums.push(await request.json());
        return HttpResponse.json({ success: true, artist_id: 55, album_id: 77 });
      }),
      http.get('/api/artist-detail/:id', () =>
        HttpResponse.json({
          success: true,
          artist: { id: 'sp-1', name: 'Boards of Canada', image_url: 'https://cdn.test/a.jpg' },
          discography: {
            albums: [
              {
                id: 'a1',
                title: 'Music Has the Right to Children',
                album_type: 'album',
                release_date: '1998-04-20',
                track_count: 1,
              },
            ],
            eps: [],
            singles: [],
            source: 'spotify',
          },
        }),
      ),
      http.get('/api/album/:id/tracks', () =>
        HttpResponse.json({
          success: true,
          tracks: [{ id: 't1', name: 'Roygbiv', track_number: 1, duration_ms: 148000 }],
        }),
      ),
      http.get('/api/library/v2/artists/55', () =>
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
      http.get('/api/artist/:name/concerts', () =>
        HttpResponse.json({ success: true, configured: false, events: [] }),
      ),
      http.get('/api/artist/:id/videos', () => HttpResponse.json({ success: true, videos: [] })),
    );
  });

  afterEach(() => {
    window.SoulSyncWebShellBridge = undefined;
    delete document.body.dataset.artistSource;
  });

  it('renders on the library artist page without creating a catalogue row', async () => {
    const { router } = renderAt(ARTIST_URL);

    expect(await screen.findByText('Music Has the Right to Children')).toBeInTheDocument();
    // the one artist page, not the old provider page
    expect(document.querySelector('.artist-detail-page')).toBeNull();
    expect(router.state.location.pathname).toBe('/library');
    expect(router.state.location.search).toMatchObject({ discover: 'spotify:sp-1' });
    expect(screen.getByText('Not in your library')).toBeInTheDocument();
    // nothing in the library: no My Library view
    expect(screen.queryByRole('button', { name: 'My Library' })).not.toBeInTheDocument();
    // Read-only until the user asks for something (issues §28.6 question 1).
    expect(materializeCalls).toHaveLength(0);
    expect(monitoredAlbums).toHaveLength(0);
  });

  it('monitors a release from the bookmark on its card', async () => {
    const { router } = renderAt(ARTIST_URL);
    await screen.findByText('Music Has the Right to Children');

    fireEvent.click(screen.getByRole('button', { name: 'Start monitoring' }));

    await waitFor(() => expect(monitoredAlbums).toHaveLength(1));
    expect(monitoredAlbums[0]).toMatchObject({
      source: 'spotify',
      artist_source: 'spotify',
      artist_provider_id: 'sp-1',
      artist_name: 'Boards of Canada',
      album_provider_id: 'a1',
      album_name: 'Music Has the Right to Children',
      album_type: 'album',
      track_count: 1,
    });
    expect(await screen.findByRole('button', { name: 'Monitored' })).toBeDisabled();
    expect(router.state.location.pathname).toBe('/library');
    expect(screen.queryByRole('button', { name: 'My Library' })).not.toBeInTheDocument();
    // The bookmark is not a click on the card.
    expect(window.openDownloadMissingModalForArtistAlbum).not.toHaveBeenCalled();
    expect(materializeCalls).toHaveLength(0);
  });

  it('opens a release in the shared download dialog, with its tracks', async () => {
    renderAt(ARTIST_URL);

    fireEvent.click(await screen.findByText('Music Has the Right to Children'));

    await waitFor(() => expect(window.openDownloadMissingModalForArtistAlbum).toHaveBeenCalled());
    const call = vi.mocked(window.openDownloadMissingModalForArtistAlbum!).mock.calls[0]!;
    expect(call[2]).toEqual([expect.objectContaining({ id: 't1', name: 'Roygbiv' })]);
    expect(call[3]).toMatchObject({ id: 'a1', name: 'Music Has the Right to Children' });
    expect(call[4]).toMatchObject({ name: 'Boards of Canada' });
    // Opening is not monitoring.
    expect(monitoredAlbums).toHaveLength(0);
    expect(materializeCalls).toHaveLength(0);
  });

  it('opens a known artist in native Discovery', async () => {
    resolveResponse = 42;
    const { router } = renderAt(ARTIST_URL);
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({
        artist: 42,
        view: 'discovery',
        header: 'rich',
        releases: 'all',
        releaseView: 'cards',
      }),
    );
    expect(router.state.location.pathname).toBe('/library');
    expect(materializeCalls).toHaveLength(0);
  });

  it('still answers the old Library v2 discovery links, album ones included', async () => {
    const { router } = renderAt(
      '/library?discover=%22spotify%3Asp-1%22&discoverName=%22Boards%20of%20Canada%22' +
        '&discoverAlbum=%22spotify%3Aa1%22',
    );

    expect(await screen.findByText('Music Has the Right to Children')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/library');
    // the album link opens that release's download dialog
    await waitFor(() => expect(window.openDownloadMissingModalForArtistAlbum).toHaveBeenCalled());
    const call = vi.mocked(window.openDownloadMissingModalForArtistAlbum!).mock.calls[0]!;
    expect(call[3]).toMatchObject({ id: 'a1' });
  });
});
