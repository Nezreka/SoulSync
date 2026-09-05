import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import type { LibraryV2AlbumSummary } from '../-library-v2.types';

function album(patch: Partial<LibraryV2AlbumSummary> & { id: number; title: string }) {
  return {
    album_type: 'album',
    release_date: '2001-01-01',
    year: 2001,
    image_url: '/api/library/v2/artwork/album/1',
    remote_image_url: null,
    monitored: false,
    quality_profile_id: 1,
    origin: 'discography',
    spotify_id: null,
    explicit: null,
    label: null,
    style: null,
    mood: null,
    track_count: 10,
    tracks_present: 0,
    tracks_missing: 10,
    total_size_bytes: 0,
    user_overrides: {},
    ...patch,
  };
}

function renderArtist(entry: string) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [entry] });
  const router = createAppRouter({ history, queryClient });
  return {
    history,
    router,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

/** ldp-03/ldp-04/ldp-05 on an artist that IS in the catalogue. */
describe('Library V2 artist detail — All Releases views', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    server.use(
      http.get('/api/library/v2/enabled', () =>
        HttpResponse.json({ success: true, enabled: true, can_write: true }),
      ),
      http.get('/api/library/v2/mirror-status', () =>
        HttpResponse.json({ success: true, pending: 0, failed: 0 }),
      ),
      http.get('/api/library/v2/artists/1', () =>
        HttpResponse.json({
          success: true,
          artist: {
            id: 1,
            name: 'Portishead',
            image_url: '/api/library/v2/artwork/artist/1',
            remote_image_url: null,
            provider_ids: { spotify: 'sp-1' },
            media_server_sources: ['navidrome', 'plex'],
            summary: null,
            style: null,
            mood: null,
            label: null,
            genres: ['trip hop'],
            monitored: false,
            monitor_new_items: 'all',
            quality_profile: null,
            albums: [
              album({ id: 1, title: 'Dummy' }),
              album({ id: 2, title: 'Roseland NYC Live' }),
            ],
            eps: [],
            singles: [],
            album_count: 2,
            single_count: 0,
            discography_count: 2,
            total_size_bytes: 2048,
            user_overrides: {},
          },
        }),
      ),
      http.get('/api/library/v2/artists/1/queue-status', () =>
        HttpResponse.json({ success: true, tracks: {}, albums: {} }),
      ),
      http.get('/api/library/v2/artists/1/aliases', () =>
        HttpResponse.json({
          success: true,
          canonical_artist_id: 1,
          aliases: [],
        }),
      ),
      http.get('/api/library/v2/artists/1/match-status', () =>
        HttpResponse.json({
          success: true,
          services: [
            {
              service: 'spotify',
              label: 'Spotify',
              status: 'matched',
              external_id: 'sp-1',
              library_v2_entity_id: 1,
              legacy_entity_id: null,
              available: true,
            },
          ],
          enrichment_coverage: {
            total_tracks: 20,
            spotify: 80,
            musicbrainz: 25,
          },
        }),
      ),
      http.get('/api/library/v2/ui-preferences', () =>
        HttpResponse.json({ success: true, preferences: null }),
      ),
      http.get('/api/artist/hero-stats', () =>
        HttpResponse.json({
          success: true,
          listeners: 900_000,
          playcount: 12_000_000,
          followers: 3_400_000,
          bio: null,
        }),
      ),
      http.get('/api/artist/:id/top-tracks', () =>
        HttpResponse.json({ success: false, tracks: [] }),
      ),
      http.get('/api/artist/0/lastfm-top-tracks', () =>
        HttpResponse.json({ success: true, tracks: [] }),
      ),
    );
  });

  it('offers the Table ↔ Discover switch only on All Releases (ldp-03)', async () => {
    const { router } = renderArtist('/library?artist=1');
    await screen.findByRole('heading', { name: 'Portishead' });
    expect(screen.queryByRole('button', { name: 'Discover View' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /All Releases/ }));

    const legacy = await screen.findByRole('button', { name: 'Discover View' });
    fireEvent.click(legacy);
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({
        releaseView: 'cards',
      }),
    );
    // The legacy card markup — including the `.discography-sections` ancestry
    // the card CSS depends on to cancel `.release-card`'s fixed 300px height,
    // without which the covers overlapped.
    await waitFor(() =>
      expect(document.querySelectorAll('.release-card.album-card')).toHaveLength(2),
    );
    expect(
      document.querySelector('.discography-sections .discography-section .releases-grid'),
    ).not.toBeNull();
  });

  it('applies the discography filters in the table view too (ldp-04)', async () => {
    renderArtist('/library?artist=1&releases=all');
    await screen.findByText('Roseland NYC Live');

    fireEvent.click(await screen.findByRole('button', { name: 'Live' }));

    await waitFor(() => expect(screen.queryByText('Roseland NYC Live')).not.toBeInTheDocument());
    expect(screen.getByText('Dummy')).toBeInTheDocument();
  });

  it('uses single-click for inline expansion and double-click for album detail', async () => {
    server.use(
      http.get('/api/library/v2/albums/:id', ({ params }) => {
        const id = Number(params.id);
        const title = id === 1 ? 'Dummy' : 'Roseland NYC Live';
        return HttpResponse.json({
          success: true,
          album: {
            ...album({ id, title }),
            quality_profile: null,
            primary_artist: { id: 1, name: 'Portishead' },
            genres: [],
            tracks: [
              {
                id: 100 + id,
                title: `Inline track ${id}`,
                track_number: 1,
                disc_number: 1,
                duration: null,
                bpm: null,
                explicit: null,
                style: null,
                mood: null,
                isrc: null,
                monitored: false,
                quality_profile_id: 1,
                canonical_track_id: null,
                artists: [],
                file: null,
                file_status: 'missing',
                metadata_gaps: [],
              },
            ],
          },
        });
      }),
      http.get('/api/library/v2/albums/:id/match-status', () =>
        HttpResponse.json({ success: true, album: [], tracks: {} }),
      ),
      http.get('/api/library/v2/quality-profiles', () =>
        HttpResponse.json({ success: true, profiles: [] }),
      ),
    );

    const { router } = renderArtist('/library?artist=1&releases=all');
    const dummyTitle = await screen.findByRole('button', { name: 'Dummy' });
    const dummyRow = dummyTitle.parentElement?.parentElement as HTMLElement;
    const now = vi.spyOn(Date, 'now');

    // The blank/header area and the title now share the same one-click rule.
    now.mockReturnValue(1_000);
    fireEvent.click(dummyRow);
    expect(await screen.findByText('Inline track 1')).toBeInTheDocument();
    expect(router.state.location.search).not.toHaveProperty('album');

    const roselandTitle = screen.getByRole('button', { name: 'Roseland NYC Live' });
    now.mockReturnValue(1_000);
    fireEvent.click(roselandTitle);
    expect(await screen.findByText('Inline track 2')).toBeInTheDocument();
    expect(router.state.location.search).not.toHaveProperty('album');

    // Repeated, deliberate single clicks keep toggling and never accumulate
    // into a later full-view navigation.
    now.mockReturnValue(2_000);
    fireEvent.click(roselandTitle);
    now.mockReturnValue(3_000);
    fireEvent.click(roselandTitle);
    expect(router.state.location.search).not.toHaveProperty('album');

    // Two clicks on the same album inside the explicit window are the only
    // gesture that opens its full detail page.
    now.mockReturnValue(4_000);
    fireEvent.click(roselandTitle);
    now.mockReturnValue(4_100);
    fireEvent.click(roselandTitle);
    now.mockRestore();
    await waitFor(() => expect(router.state.location.search).toMatchObject({ album: 2 }));
  });

  it('switches the header between compact and the rich legacy hero (ldp-05)', async () => {
    const { router } = renderArtist('/library?artist=1');
    await screen.findByRole('heading', { name: 'Portishead' });
    expect(document.querySelector('.artist-hero-section')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /Rich header/ }));

    await waitFor(() => expect(router.state.location.search).toMatchObject({ header: 'rich' }));
    expect(document.querySelector('.artist-hero-section')).not.toBeNull();
    expect(await screen.findByText('900K')).toBeInTheDocument();
    expect(screen.getByText('12M')).toBeInTheDocument();
    // Followers share the same row, so the header does not grow vertically —
    // and they are the one number that still resolves without a Last.fm key.
    expect(screen.getByText('3.4M')).toBeInTheDocument();
    expect(document.querySelectorAll('.artist-hero-numbers')).toHaveLength(1);
  });

  it('places compact media-server recognition beside the size in the compact header', async () => {
    renderArtist('/library?artist=1');

    const recognition = await screen.findByLabelText('Recognised by Navidrome and Plex');
    expect(recognition).toHaveAttribute('title', 'Recognised by Navidrome and Plex');
    expect(recognition).toHaveTextContent('✓2');
    expect(recognition.parentElement).toContainElement(screen.getByText('2.00 KB'));
    expect(screen.queryByText('Navidrome')).not.toBeInTheDocument();
    expect(screen.queryByText('Plex')).not.toBeInTheDocument();
  });

  /** "Missing" is monitored+absent now (§44/LV2-CNT-01) — so a release the
   *  user unmonitored down to nothing can reach 0 present AND 0 missing at
   *  once. That is not completion; it means nothing was ever asked for. */
  it('does not call an empty, nothing-wanted release "complete"', async () => {
    server.use(
      http.get('/api/library/v2/artists/1', () =>
        HttpResponse.json({
          success: true,
          artist: {
            id: 1,
            name: 'Portishead',
            image_url: '/api/library/v2/artwork/artist/1',
            remote_image_url: null,
            provider_ids: { spotify: 'sp-1' },
            media_server_sources: [],
            summary: null,
            style: null,
            mood: null,
            label: null,
            genres: ['trip hop'],
            monitored: true,
            monitor_new_items: 'all',
            quality_profile: null,
            albums: [
              album({
                id: 1,
                title: 'Nothing Wanted',
                origin: 'library',
                tracks_present: 0,
                tracks_missing: 0,
                track_count: 10,
              }),
            ],
            eps: [],
            singles: [],
            album_count: 1,
            single_count: 0,
            discography_count: 1,
            total_size_bytes: 0,
            user_overrides: {},
          },
        }),
      ),
    );

    renderArtist('/library?artist=1');
    await screen.findByText('Nothing Wanted');

    expect(screen.getByText('not in library')).toBeInTheDocument();
    expect(screen.queryByText('complete')).not.toBeInTheDocument();
  });

  it('still calls a fully-downloaded release "complete"', async () => {
    server.use(
      http.get('/api/library/v2/artists/1', () =>
        HttpResponse.json({
          success: true,
          artist: {
            id: 1,
            name: 'Portishead',
            image_url: '/api/library/v2/artwork/artist/1',
            remote_image_url: null,
            provider_ids: { spotify: 'sp-1' },
            media_server_sources: [],
            summary: null,
            style: null,
            mood: null,
            label: null,
            genres: ['trip hop'],
            monitored: true,
            monitor_new_items: 'all',
            quality_profile: null,
            albums: [
              album({
                id: 1,
                title: 'Fully Owned',
                origin: 'library',
                tracks_present: 10,
                tracks_missing: 0,
                track_count: 10,
              }),
            ],
            eps: [],
            singles: [],
            album_count: 1,
            single_count: 0,
            discography_count: 1,
            total_size_bytes: 2048,
            user_overrides: {},
          },
        }),
      ),
    );

    renderArtist('/library?artist=1');
    await screen.findByText('Fully Owned');

    expect(screen.getByText('complete')).toBeInTheDocument();
    expect(screen.queryByText('not in library')).not.toBeInTheDocument();
  });

  it('opening an artist from inside Library V2 always starts in the V2 shape', async () => {
    server.use(
      http.get('/api/library/v2/artists', () =>
        HttpResponse.json({
          success: true,
          artists: [
            {
              id: 1,
              name: 'Portishead',
              image_url: null,
              genres: [],
              monitored: false,
              monitor_new_items: 'all',
              quality_profile_id: 1,
              added_at: null,
              album_count: 2,
              single_count: 0,
              track_count: 0,
              tracks_present: 0,
              tracks_missing: 0,
              total_size_bytes: 0,
              user_overrides: {},
            },
          ],
          pagination: {
            page: 1,
            limit: 75,
            total_count: 1,
            total_pages: 1,
            has_prev: false,
            has_next: false,
          },
        }),
      ),
    );
    // Arrive carrying a previous artist's discovery view settings.
    const { router } = renderArtist('/library?releases=all&releaseView=cards&header=rich');

    fireEvent.click(await screen.findByRole('button', { name: 'Open Portishead' }));

    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({
        artist: 1,
        releases: 'library',
        releaseView: 'table',
        header: 'compact',
      }),
    );
  });

  it('bookmarks a release straight from the card, without opening it', async () => {
    const monitorWrites: number[] = [];
    server.use(
      http.post('/api/library/v2/albums/:id/monitor', ({ params }) => {
        monitorWrites.push(Number(params.id));
        return HttpResponse.json({ success: true });
      }),
    );
    const { router } = renderArtist('/library?artist=1&releases=all&releaseView=cards');
    await screen.findByText('Dummy');

    const card = document.querySelectorAll('.release-card.album-card')[0];
    fireEvent.click(card.querySelector('button') as HTMLElement);

    await waitFor(() => expect(monitorWrites).toEqual([1]));
    // The card click that OPENS the release must not have fired as well.
    expect(router.state.location.search).not.toHaveProperty('album');
  });

  it('resolves a provider-only tracklist when a release is opened as a page', async () => {
    const albumRequests: string[] = [];
    server.use(
      http.get('/api/library/v2/albums/1', ({ request }) => {
        albumRequests.push(new URL(request.url).search);
        return HttpResponse.json({ success: false, error: 'stop here' }, { status: 404 });
      }),
    );
    renderArtist('/library?album=1');

    // Without `resolve=1` a discography-only release rendered an empty track
    // list — the inline expand always resolved, the page never did.
    await waitFor(() => expect(albumRequests.some((q) => q.includes('resolve=1'))).toBe(true));
  });

  it('back from a release returns to the view it was opened from', async () => {
    server.use(
      http.get('/api/library/v2/albums/1', () =>
        HttpResponse.json({
          success: true,
          album: {
            id: 1,
            title: 'Dummy',
            album_type: 'album',
            release_date: null,
            year: null,
            image_url: null,
            monitored: false,
            quality_profile: null,
            origin: 'discography',
            genres: [],
            explicit: null,
            label: null,
            style: null,
            mood: null,
            primary_artist: { id: 1, name: 'Portishead' },
            tracks: [],
            track_count: 0,
            tracks_present: 0,
            tracks_missing: 0,
            total_size_bytes: 0,
            user_overrides: {},
          },
        }),
      ),
      http.get('/api/library/v2/albums/1/match-status', () =>
        HttpResponse.json({ success: true, album: [], tracks: {} }),
      ),
      http.get('/api/library/v2/albums/1/queue-status', () =>
        HttpResponse.json({ success: true, tracks: {}, albums: {} }),
      ),
    );
    const { router } = renderArtist(
      '/library?artist=1&album=1&releases=all&releaseView=cards&header=rich',
    );

    fireEvent.click(await screen.findByRole('button', { name: /Portishead/ }));

    await waitFor(() => expect(router.state.location.search).not.toHaveProperty('album'));
    // Not back to My Library — that cost three extra clicks to return to the
    // release you were just looking at.
    expect(router.state.location.search).toMatchObject({
      artist: 1,
      releases: 'all',
      releaseView: 'cards',
      header: 'rich',
    });
  });

  it('shows the legacy enrichment rings and keeps the V2 match chips', async () => {
    renderArtist('/library?artist=1&header=rich');

    expect(await screen.findByText('Enrichment Coverage')).toBeInTheDocument();
    // Per-provider share of the artist's TRACKS, not the artist row itself.
    await waitFor(() => expect(screen.getByText('80')).toBeInTheDocument());
    expect(screen.getByText('25')).toBeInTheDocument();
    // ldp-08: still the V2 chips, in the legacy badge row under the name.
    expect(document.querySelector('.artist-hero-badges')).not.toBeNull();
  });

  it('uses the same bookmark glyph as every other monitor control', async () => {
    server.use(
      http.get('/api/artist/:id/top-tracks', () =>
        HttpResponse.json({
          success: true,
          tracks: [
            {
              id: 'sp-t1',
              name: 'Glory Box',
              album: { id: 'sp-a1', name: 'Dummy' },
            },
          ],
        }),
      ),
      http.get('/api/library/v2/discovery/track-status', () =>
        HttpResponse.json({ success: true, statuses: {} }),
      ),
    );
    renderArtist('/library?artist=1&header=rich');

    const bookmark = await screen.findByTitle('Bookmark — mark this track as wanted');
    // The monitor toggle by its own label, not "the first icon in the hero" —
    // the hero actions gained a Play button in front of it, and a positional
    // lookup silently compares against whatever happens to be leftmost.
    const monitor = screen
      .getByLabelText(/^(Start|Stop) monitoring$/)
      .querySelector('svg path');
    expect(bookmark.querySelector('svg path')?.getAttribute('d')).toBe(monitor?.getAttribute('d'));
  });
});
