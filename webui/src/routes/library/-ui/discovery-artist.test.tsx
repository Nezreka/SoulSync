import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/** ldp-01/ldp-02: an artist the catalogue has never heard of opens on
 *  upstream's artist page, from provider data alone, without writing anything.
 *  Library v2 adds the release bookmark and its own track list for a release. */
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
  let monitoredTracks: unknown[];

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
    monitoredTracks = [];
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
      http.post('/api/library/v2/discovery/album', async ({ request }) => {
        monitoredAlbums.push(await request.json());
        return HttpResponse.json({ success: true, artist_id: 55, album_id: 77 });
      }),
      http.post('/api/library/v2/discovery/track', async ({ request }) => {
        monitoredTracks.push(await request.json());
        return HttpResponse.json({ success: true, artist_id: 55, album_id: 77, track_id: 88 });
      }),
      http.get('/api/library/v2/discovery/track-status', () =>
        HttpResponse.json({ success: true, statuses: {} }),
      ),
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
      http.get('/api/spotify/album/:id', () =>
        HttpResponse.json({
          id: 'a1',
          name: 'Music Has the Right to Children',
          album_type: 'album',
          release_date: '1998-04-20',
          total_tracks: 1,
          images: [{ url: 'https://cdn.test/album.jpg' }],
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

  it("renders upstream's artist page without creating a catalogue row", async () => {
    renderAt(ARTIST_URL);

    expect(await screen.findByText('Music Has the Right to Children')).toBeInTheDocument();
    expect(document.querySelector('.artist-detail-page')).not.toBeNull();
    // Read-only until the user asks for something (issues §28.6 question 1).
    expect(materializeCalls).toHaveLength(0);
    expect(monitoredAlbums).toHaveLength(0);
  });

  it('monitors a release from the bookmark on its card', async () => {
    renderAt(ARTIST_URL);
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
    // The bookmark is not a click on the card.
    expect(window.openDownloadMissingModalForArtistAlbum).not.toHaveBeenCalled();
    expect(materializeCalls).toHaveLength(0);
  });

  it('opens a release as a Library v2 track list, not the download dialog', async () => {
    const { router } = renderAt(ARTIST_URL);

    fireEvent.click(await screen.findByText('Music Has the Right to Children'));

    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({
        discover: 'spotify:sp-1',
        discoverName: 'Boards of Canada',
        discoverAlbum: 'spotify:a1',
        discoverAlbumName: 'Music Has the Right to Children',
      }),
    );
    expect(
      await screen.findByRole('heading', { name: 'Music Has the Right to Children' }),
    ).toBeInTheDocument();
    expect(window.openDownloadMissingModalForArtistAlbum).not.toHaveBeenCalled();
    expect(monitoredAlbums).toHaveLength(0);
  });

  it('monitors one track from that track list, then goes back to the artist page', async () => {
    const { router } = renderAt(ARTIST_URL);
    fireEvent.click(await screen.findByText('Music Has the Right to Children'));
    await screen.findByRole('heading', { name: 'Music Has the Right to Children' });

    fireEvent.click(screen.getByRole('button', { name: 'Monitor Roygbiv' }));

    await waitFor(() => expect(monitoredTracks).toHaveLength(1));
    expect(monitoredTracks[0]).toMatchObject({
      source: 'spotify',
      artist_source: 'spotify',
      artist_provider_id: 'sp-1',
      album_provider_id: 'a1',
      track_provider_id: 't1',
      track_title: 'Roygbiv',
      track_number: 1,
      monitored: true,
    });
    expect(await screen.findByRole('button', { name: 'Roygbiv is monitored' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: /Boards of Canada/ }));

    await waitFor(() => expect(router.state.location.pathname).toBe('/artist-detail/spotify/sp-1'));
    expect(materializeCalls).toHaveLength(0);
  });

  it('sends an artist the catalogue already holds to Library v2', async () => {
    resolveResponse = 42;
    const { router } = renderAt(ARTIST_URL);

    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({
        artist: 42,
        releases: 'all',
        releaseView: 'cards',
        header: 'rich',
      }),
    );
    expect(router.state.location.pathname).toBe('/library');
  });

  it('still answers the old Library v2 discovery link', async () => {
    const { router } = renderAt(
      '/library?discover=%22spotify%3Asp-1%22&discoverName=%22Boards%20of%20Canada%22',
    );

    await waitFor(() => expect(router.state.location.pathname).toBe('/artist-detail/spotify/sp-1'));
    expect(router.state.location.search).toMatchObject({ name: 'Boards of Canada' });
    expect(await screen.findByText('Music Has the Right to Children')).toBeInTheDocument();
  });
});
