import { createMemoryHistory } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import type { LibraryV2AlbumSummary } from '../-library-v2.types';

import { lib2Track } from '../-library-v2.test-fixtures';
import { resetGoingBackForTests } from '../../artist-detail/-artist-detail.back-label';

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

/** The artist the page loads: Portishead with the given releases. */
function portishead(patch: Record<string, unknown> = {}) {
  return {
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
    albums: [],
    eps: [],
    singles: [],
    album_count: 1,
    single_count: 0,
    discography_count: 1,
    total_size_bytes: 0,
    user_overrides: {},
    ...patch,
  };
}

/** one release with files here: My Library is offered only for an artist in
 *  the library, the way dev offered its library view (and the tests of that
 *  view need an artist that has one) */
const OWNED_SINGLE = {
  id: 99,
  title: 'Numb',
  album_type: 'single',
  origin: 'library',
  tracks_present: 1,
  tracks_missing: 1,
  track_count: 2,
};

function inLibrary() {
  server.use(
    http.get('/api/library/v2/artists/1', () =>
      HttpResponse.json({
        success: true,
        artist: portishead({
          media_server_sources: ['navidrome', 'plex'],
          monitored: false,
          albums: [album({ id: 1, title: 'Dummy' }), album({ id: 2, title: 'Roseland NYC Live' })],
          singles: [album(OWNED_SINGLE)],
          album_count: 2,
          discography_count: 2,
          total_size_bytes: 2048,
        }),
      }),
    ),
  );
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
  it.each(['/library?artist=1', '/library?artist=1&header=rich'])(
    'files an artist as audiobook from Artist Settings, not from the header: %s',
    async (entry) => {
      const routed: unknown[] = [];
      server.use(
        http.get('/api/audiobook-routes', () =>
          HttpResponse.json({ success: true, artists: [], audiobook_folder_configured: true }),
        ),
        http.post('/api/audiobook-routes/check', () =>
          HttpResponse.json({ success: true, routed: [] }),
        ),
        http.post('/api/audiobook-routes', async ({ request }) => {
          routed.push(await request.json());
          return HttpResponse.json({ success: true });
        }),
        http.post('/api/audiobook-routes/Portishead/move', () =>
          HttpResponse.json({ success: true, albums: [] }),
        ),
      );
      renderArtist(entry);
      fireEvent.click(await screen.findByRole('button', { name: 'Artist Settings' }));
      fireEvent.click(await screen.findByRole('checkbox', { name: /Treat as audiobook/ }));
      await waitFor(() => expect(routed).toEqual([{ name: 'Portishead' }]));
      // the header carries neither the routing switch nor a one-off download
      expect(screen.queryByRole('button', { name: /Treat as audiobook/i })).toBeNull();
      expect(screen.queryByRole('button', { name: 'Download missing' })).toBeNull();
    },
  );

  // the whole artist page and the whole View Settings: slow on a loaded run
  it('hides the toolbar buttons this profile switched off and opens View Settings from Tools', async () => {
    const saved: unknown[] = [];
    server.use(
      http.get('/api/library/v2/ui-preferences', () =>
        HttpResponse.json({
          success: true,
          preferences: { track_table: {}, artist_toolbar: { history: false } },
        }),
      ),
      http.put('/api/library/v2/ui-preferences', async ({ request }) => {
        saved.push(await request.json());
        return HttpResponse.json({
          success: true,
          preferences: { track_table: {}, artist_toolbar: { history: true } },
        });
      }),
    );
    renderArtist('/library?artist=1');
    expect(await screen.findByRole('button', { name: 'Artist Settings' })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('button', { name: 'History' })).toBeNull());

    fireEvent.click(screen.getByRole('button', { name: 'Tools' }));
    fireEvent.click(screen.getByRole('button', { name: 'View Settings…' }));
    // the dialog is the whole View Settings: give it time under a loaded run
    const history = await screen.findByRole('checkbox', { name: 'History' }, { timeout: 5000 });
    expect(history).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Refresh & Scan' })).toBeChecked();
    fireEvent.click(history);

    fireEvent.click(
      within(
        screen.getByRole('radiogroup', { name: 'From Search, Discover and elsewhere' }),
      ).getByRole('radio', { name: /Compact header/ }),
    );
    await waitFor(
      () =>
        expect(saved).toEqual([
          { artist_toolbar: { history: true } },
          { artist_page: { header_elsewhere: 'compact' } },
        ]),
      { timeout: 5000 },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(
      await screen.findByRole('button', { name: 'History' }, { timeout: 5000 }),
    ).toBeInTheDocument();
  }, 30_000);

  it.each(['cards', 'table'])(
    'reads the discography of an artist the library does not hold, writing nothing (%s)',
    async (view) => {
      // a guest credit opened from a search or a track's artist column: the
      // discography walk wrote 70 releases of someone you only looked at. the
      // provider's list is cards only, so a table url still shows it
      const walk = vi.fn();
      server.use(
        http.get('/api/library/v2/artists/1', () =>
          HttpResponse.json({
            success: true,
            artist: portishead({ in_library: false, albums: [], discography_count: 0 }),
          }),
        ),
        http.post('/api/library/v2/artists/1/discography/refresh', () => {
          walk();
          return HttpResponse.json({ success: true, job_id: 'walk-1' });
        }),
        http.get('/api/artist-detail/:id', () =>
          HttpResponse.json({
            success: true,
            artist: { id: 'sp-1', name: 'Portishead' },
            discography: { albums: [{ id: 'al-3', title: 'Third' }], source: 'spotify' },
          }),
        ),
      );
      renderArtist(`/library?artist=1&releases=all&releaseView=${view}`);

      expect(await screen.findByText('Third')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Table View' })).not.toBeInTheDocument();
      expect(walk).not.toHaveBeenCalled();
    },
  );

  it('ends on similar artists in both views, the way the artist page always did', async () => {
    window.loadSimilarArtists = vi.fn();
    window.cancelSimilarArtistsLoad = vi.fn();
    inLibrary();
    renderArtist('/library?artist=1&releases=library');
    await screen.findByRole('button', { name: 'My Library' });
    expect(await screen.findByText('Similar Artists')).toBeInTheDocument();
    await waitFor(() => expect(window.loadSimilarArtists).toHaveBeenCalledWith('Portishead'));

    fireEvent.click(screen.getByRole('button', { name: /Discovery/ }));
    // the discography walk repaints the page, the section is there once it settles
    await waitFor(() => expect(screen.getByText('Similar Artists')).toBeInTheDocument(), {
      timeout: 4000,
    });
    await waitFor(() => expect(window.loadSimilarArtists).toHaveBeenCalledWith('Portishead'));
  });

  it('leaves similar artists out of My Library when View Settings says so', async () => {
    window.loadSimilarArtists = vi.fn();
    window.cancelSimilarArtistsLoad = vi.fn();
    inLibrary();
    server.use(
      http.get('/api/library/v2/ui-preferences', () =>
        HttpResponse.json({
          success: true,
          preferences: {
            track_table: {},
            artist_page: { library_sections: { similar_artists: false } },
          },
        }),
      ),
    );
    renderArtist('/library?artist=1&releases=library');
    await screen.findByRole('button', { name: 'My Library' });
    expect(await screen.findByText('Numb')).toBeInTheDocument();
    expect(screen.queryByText('Similar Artists')).not.toBeInTheDocument();
    expect(window.loadSimilarArtists).not.toHaveBeenCalled();

    // Discovery still suggests them
    fireEvent.click(screen.getByRole('button', { name: /Discovery/ }));
    await waitFor(() => expect(screen.getByText('Similar Artists')).toBeInTheDocument(), {
      timeout: 4000,
    });
    await waitFor(() => expect(window.loadSimilarArtists).toHaveBeenCalledWith('Portishead'));
  });

  it.each(['albums', 'songs', 'wanted'])(
    'goes back from an album picked in %s to that list, not to its artist',
    async (section) => {
      server.use(
        http.get('/api/library/v2/albums/1', () =>
          HttpResponse.json({
            success: true,
            album: {
              ...album({ id: 1, title: 'Dummy' }),
              genres: [],
              quality_profile: null,
              primary_artist: { id: 1, name: 'Portishead' },
              tracks: [],
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
      const { router } = renderArtist(`/library?section=${section}&album=1`);
      fireEvent.click(await screen.findByRole('button', { name: '← Library' }));
      await waitFor(() => expect(router.state.location.search).not.toHaveProperty('album'));
      expect(router.state.location.search).toMatchObject({ section });
      expect(router.state.location.search).not.toHaveProperty('artist');
    },
  );

  it('says a release on the wishlist keeps upgrading to its quality profile', async () => {
    // being on the wishlist is monitoring: it is what quality upgrades act on,
    // and taking it off stops both
    server.use(
      http.get('/api/library/v2/artists/1', () =>
        HttpResponse.json({
          success: true,
          artist: portishead({
            albums: [album({ id: 1, title: 'Dummy', monitored: true })],
            album_count: 1,
          }),
        }),
      ),
    );
    renderArtist('/library?artist=1&releases=library');

    const title =
      'On your Wishlist. SoulSync keeps upgrading it to its quality profile. Click to remove it';
    await waitFor(() => expect(screen.getAllByTitle(title).length).toBeGreaterThan(0));
  });

  it('keeps media-server sync in Tools and reports its background result', async () => {
    const sync = vi.fn();
    server.use(
      http.post('/api/library/v2/artists/1/sync', () => {
        sync();
        return HttpResponse.json({ success: true, job_id: 'artist-sync-1' });
      }),
      http.get('/api/library/v2/jobs/status', () =>
        HttpResponse.json({
          running: false,
          result: { artist_name: 'Portishead', new_tracks: 2 },
          error: null,
        }),
      ),
    );
    renderArtist('/library?artist=1');
    fireEvent.click(await screen.findByRole('button', { name: 'Tools' }));
    fireEvent.click(screen.getByRole('button', { name: 'Sync from media server' }));
    await waitFor(() => expect(sync).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('Portishead: +2 tracks')).toBeInTheDocument();
  });

  beforeEach(() => {
    window.artistDetailLabelStack = [];
    resetGoingBackForTests();
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
          artist: portishead({
            media_server_sources: ['navidrome', 'plex'],
            monitored: false,
            albums: [
              album({ id: 1, title: 'Dummy' }),
              album({ id: 2, title: 'Roseland NYC Live' }),
            ],
            album_count: 2,
            discography_count: 2,
            total_size_bytes: 2048,
          }),
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

  it('uses the existing rich header for immersive arrivals and keeps the artist tools on show', async () => {
    inLibrary();
    const { router } = renderArtist('/library?artist=1&view=discovery&immersive=true');
    await screen.findByRole('heading', { name: 'Portishead' });
    expect(router.state.location.search).toMatchObject({ header: 'rich', releaseView: 'cards' });
    // dev showed the artist tools the moment the page opened
    expect(screen.getByRole('button', { name: 'Artist Settings' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Automatic Search' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Interactive Search' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'History' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Table View' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'My Library' }));
    await screen.findByRole('button', { name: 'Artist Settings' });
    expect(router.state.location.search).toMatchObject({ immersive: false, releases: 'library' });
    fireEvent.click(screen.getByRole('button', { name: /Discovery/ }));
    await screen.findByRole('button', { name: 'Table View' });
    expect(screen.getByRole('button', { name: 'Artist Settings' })).toBeInTheDocument();
    expect(router.state.location.search).toMatchObject({ immersive: false });
  });

  it('automatically checks videos in Discovery and hides an empty shelf without a video button', async () => {
    const searches = vi.fn();
    server.use(
      http.post('/api/enhanced-search/source/youtube_videos', () => {
        searches();
        return HttpResponse.text('{"type":"videos","data":[]}\n');
      }),
    );
    const { router } = renderArtist('/library?artist=1&releases=all&releaseView=cards');
    await screen.findByRole('heading', { name: 'Portishead' });
    await waitFor(() => expect(searches).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Music Videos' })).not.toBeInTheDocument(),
    );
    expect(screen.queryByRole('button', { name: /^Music Videos$/ })).not.toBeInTheDocument();
    expect(screen.queryByText('No music videos found for this artist.')).not.toBeInTheDocument();
    expect(router.state.location.search).toMatchObject({
      artistView: 'releases',
      releases: 'all',
      releaseView: 'cards',
    });
    expect(screen.getByRole('button', { name: 'Discover View' })).toBeInTheDocument();
  });

  it('shows matching music videos inside Discovery without a separate view switch', async () => {
    server.use(
      http.post('/api/enhanced-search/source/youtube_videos', () =>
        HttpResponse.text(
          JSON.stringify({
            type: 'videos',
            data: [
              {
                video_id: 'glory-box',
                title: 'Portishead - Glory Box (Official Video)',
                channel: 'Portishead',
                duration: 270,
              },
            ],
          }) + '\n',
        ),
      ),
    );
    renderArtist('/library?artist=1&view=discovery&immersive=true');
    expect(await screen.findByRole('heading', { name: 'Music Videos' })).toBeInTheDocument();
    expect(await screen.findByText('Portishead - Glory Box (Official Video)')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Music Videos$/ })).not.toBeInTheDocument();
  });

  it('offers the Table ↔ Discover switch only on All Releases (ldp-03)', async () => {
    inLibrary();
    const { router } = renderArtist('/library?artist=1');
    await screen.findByRole('heading', { name: 'Portishead' });
    expect(screen.queryByRole('button', { name: 'Discover View' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Discovery/ }));

    // the discovery tab lands on the cards view
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ releases: 'all', releaseView: 'cards' }),
    );
    expect(await screen.findByRole('button', { name: 'Discover View' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Table View' }));
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ releaseView: 'table' }),
    );

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
      // two albums and the single the artist has here
      expect(document.querySelectorAll('.release-card.album-card')).toHaveLength(3),
    );
    expect(
      document.querySelector('.discography-sections .discography-section .releases-grid'),
    ).not.toBeNull();
  });

  it('applies the discography filters in the table view too (ldp-04)', async () => {
    renderArtist('/library?artist=1&releases=all&releaseView=table');
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

    const { router } = renderArtist('/library?artist=1&releases=all&releaseView=table');
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

  it('opens on the rich hero and switches to compact and back (ldp-05)', async () => {
    const { router } = renderArtist('/library?artist=1');
    await screen.findByRole('heading', { name: 'Portishead' });
    expect(document.querySelector('.artist-hero-section')).not.toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /Compact header/ }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ header: 'compact' }));
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
          artist: portishead({
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
            singles: [album(OWNED_SINGLE)],
          }),
        }),
      ),
    );

    renderArtist('/library?artist=1');
    await screen.findByText('Nothing Wanted');

    expect(screen.getByText('0/10')).toHaveAttribute('data-presence', 'empty');
    expect(screen.queryByText('complete')).not.toBeInTheDocument();
  });

  it('still calls a fully-downloaded release "complete"', async () => {
    server.use(
      http.get('/api/library/v2/artists/1', () =>
        HttpResponse.json({
          success: true,
          artist: portishead({
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
            total_size_bytes: 2048,
          }),
        }),
      ),
    );

    renderArtist('/library?artist=1');
    await screen.findByText('Fully Owned');

    expect(screen.getByText('10/10')).toHaveAttribute('data-presence', 'complete');
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
        releaseView: 'cards',
        header: 'rich',
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

  it('opens a featured artist from the tracklist in the header this profile picked', async () => {
    server.use(
      http.get('/api/library/v2/ui-preferences', () =>
        HttpResponse.json({
          success: true,
          preferences: {
            track_table: {
              columns: { title: true, artists: true },
              column_order: ['title', 'artists'],
              column_widths: {},
            },
            artist_page: { header: 'compact' },
          },
        }),
      ),
      http.get('/api/library/v2/albums/1', () =>
        HttpResponse.json({
          success: true,
          album: {
            ...album({ id: 1, title: 'Dummy' }),
            genres: [],
            quality_profile: null,
            primary_artist: { id: 1, name: 'Portishead' },
            tracks: [
              lib2Track({
                artists: [
                  { id: 1, name: 'Portishead', role: 'primary' },
                  { id: 2, name: 'Beth Gibbons', role: 'featured' },
                ],
              }),
            ],
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
    const { router } = renderArtist('/library?artist=1&album=1');

    // the tracklist loads after the page
    fireEvent.click(await screen.findByRole('button', { name: 'Beth Gibbons' }, { timeout: 3000 }));

    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ artist: 2, header: 'compact' }),
    );
    expect(router.state.location.search).not.toHaveProperty('album');
  });

  it('keeps the page artist as text in the tracklist', async () => {
    server.use(
      http.get('/api/library/v2/ui-preferences', () =>
        HttpResponse.json({
          success: true,
          preferences: {
            track_table: {
              columns: { title: true, artists: true },
              column_order: ['title', 'artists'],
            },
          },
        }),
      ),
      http.get('/api/library/v2/albums/1', () =>
        HttpResponse.json({
          success: true,
          album: {
            ...album({ id: 1, title: 'Dummy' }),
            genres: [],
            quality_profile: null,
            primary_artist: { id: 1, name: 'Portishead' },
            tracks: [lib2Track({ artists: [{ id: 1, name: 'Portishead', role: 'primary' }] })],
          },
        }),
      ),
    );
    renderArtist('/library?artist=1&album=1');

    expect(
      await screen.findByRole('cell', { name: 'Portishead' }, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Portishead' })).toBeNull();
  });

  it('goes back from a guest album to the artist it was opened from, in that header', async () => {
    // Rockstar is Post Malone's; opened from 21 Savage's appears on, back
    // read "Post Malone" and opened his page in the library's own header
    server.use(
      http.get('/api/library/v2/albums/1', () =>
        HttpResponse.json({
          success: true,
          album: {
            ...album({ id: 1, title: 'Rockstar' }),
            genres: [],
            quality_profile: null,
            primary_artist: { id: 2, name: 'Post Malone' },
            tracks: [],
          },
        }),
      ),
      http.get('/api/library/v2/artists/:id', ({ params }) => {
        const id = Number(params.id);
        return HttpResponse.json({
          success: true,
          artist: portishead({ id, name: id === 1 ? '21 Savage' : 'Post Malone' }),
        });
      }),
    );
    const { router } = renderArtist('/library?artist=1&header=compact');
    await screen.findByRole('heading', { name: '21 Savage' });
    // the page's own search with the album added, as the appears-on name does
    await act(() => router.navigate({ href: '/library?artist=1&header=compact&album=1' }));

    fireEvent.click(await screen.findByRole('button', { name: '← 21 Savage' }));
    await waitFor(() => expect(router.state.location.search).not.toHaveProperty('album'));
    expect(router.state.location.search).toMatchObject({ artist: 1, header: 'compact' });
  });

  it('links an album main artist when another artist page is open and returns there', async () => {
    server.use(
      http.get('/api/library/v2/ui-preferences', () =>
        HttpResponse.json({
          success: true,
          preferences: {
            track_table: {
              columns: { title: true, artists: true },
              column_order: ['title', 'artists'],
            },
          },
        }),
      ),
      http.get('/api/library/v2/albums/1', () =>
        HttpResponse.json({
          success: true,
          album: {
            ...album({ id: 1, title: 'Rockstar' }),
            genres: [],
            quality_profile: null,
            primary_artist: { id: 2, name: 'Post Malone' },
            tracks: [
              lib2Track({
                artists: [
                  { id: 1, name: '21 Savage', role: 'featured' },
                  { id: 2, name: 'Post Malone', role: 'primary' },
                ],
              }),
            ],
          },
        }),
      ),
      http.get('/api/library/v2/artists/:id', ({ params }) => {
        const id = Number(params.id);
        return HttpResponse.json({
          success: true,
          artist: portishead({ id, name: id === 1 ? '21 Savage' : 'Post Malone' }),
        });
      }),
      http.get('/api/library/v2/artists/:id/queue-status', () =>
        HttpResponse.json({ success: true, tracks: {}, albums: {} }),
      ),
      http.get('/api/library/v2/artists/:id/aliases', ({ params }) =>
        HttpResponse.json({
          success: true,
          canonical_artist_id: Number(params.id),
          aliases: [],
        }),
      ),
      http.get('/api/library/v2/artists/:id/match-status', () =>
        HttpResponse.json({ success: true, services: [], enrichment_coverage: {} }),
      ),
    );
    const { router } = renderArtist('/wishlist?tab=music');
    await waitFor(() => expect(router.state.resolvedLocation?.pathname).toBe('/wishlist'));
    await act(() => router.navigate({ href: '/library?artist=1&album=1' }));

    expect(
      await screen.findByRole('cell', { name: '21 Savage, Post Malone' }, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '21 Savage' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Post Malone' }));

    await waitFor(() => expect(router.state.location.search).toMatchObject({ artist: 2 }));
    fireEvent.click(await screen.findByRole('button', { name: '← Back to 21 Savage' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ artist: 1 }));
    const sourceBack = await screen.findByRole('button', { name: '← Back to Wishlist' });
    fireEvent.click(sourceBack);
    await waitFor(() => expect(router.state.location.pathname).toBe('/wishlist'));
    expect(router.state.location.search).toMatchObject({ tab: 'music' });
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

  it('falls back to display-only Last.fm rows when the source has no ranking', async () => {
    server.use(
      http.get('/api/artist/0/lastfm-top-tracks', () =>
        HttpResponse.json({ success: true, tracks: [{ name: 'Glory Box', playcount: 4_200_000 }] }),
      ),
    );
    renderArtist('/library?artist=1&header=rich');

    expect(await screen.findByText('Popular on Last.fm')).toBeInTheDocument();
    expect(screen.getByText('Glory Box')).toBeInTheDocument();
    // A Last.fm row is a name and a playcount — no album, no ids. Bookmarking
    // one invented an album that matched nothing, so legacy never offered the
    // action on these rows either.
    expect(document.querySelector('.hero-top-track-download')).toBeNull();
  });

  it('shows an already-wanted top track as bookmarked on a fresh load', async () => {
    // The tick used to live purely in component state, so it disappeared the
    // moment the page was reloaded even though the wishlist row existed.
    server.use(
      http.get('/api/artist/:id/top-tracks', () =>
        HttpResponse.json({
          success: true,
          tracks: [{ id: 'sp-t1', name: 'Glory Box', album: { id: 'sp-a1', name: 'Dummy' } }],
        }),
      ),
      http.get('/api/library/v2/discovery/track-status', () =>
        HttpResponse.json({
          success: true,
          statuses: { 'Glory Box': { track_id: 9, monitored: true } },
        }),
      ),
    );
    renderArtist('/library?artist=1&header=rich');

    expect(await screen.findByTitle('On your Wishlist')).toBeInTheDocument();
  });

  it('monitors every not-yet-wanted top track with Monitor All', async () => {
    const monitored: unknown[] = [];
    inLibrary();
    server.use(
      http.get('/api/artist/:id/top-tracks', () =>
        HttpResponse.json({
          success: true,
          tracks: [
            { id: 'sp-t1', name: 'Glory Box', album: { id: 'sp-a1', name: 'Dummy' } },
            { id: 'sp-t2', name: 'Roads', album: { id: 'sp-a1', name: 'Dummy' } },
            { id: 'sp-t3', name: 'Sour Times', album: { id: 'sp-a1', name: 'Dummy' } },
          ],
        }),
      ),
      http.get('/api/library/v2/discovery/track-status', () =>
        HttpResponse.json({
          success: true,
          statuses: { Roads: { track_id: 9, monitored: true } },
        }),
      ),
      http.post('/api/library/v2/discovery/track', async ({ request }) => {
        monitored.push(((await request.json()) as { track_title?: string }).track_title);
        return HttpResponse.json({ success: true, track_id: monitored.length });
      }),
    );
    renderArtist('/library?artist=1&header=rich');

    fireEvent.click(await screen.findByRole('button', { name: 'Add All to Wishlist' }));
    // the one already wanted is skipped
    await waitFor(() => expect(monitored).toEqual(['Glory Box', 'Sour Times']));
    expect(await screen.findByRole('button', { name: 'All on Wishlist' })).toBeDisabled();
  });

  it.each([
    [{ singles: [album(OWNED_SINGLE)] }, 'In your library', true],
    // only guest verses on other artists' music: in the library, but not theirs
    [{ guest_track_count: 6, guest_release_count: 6 }, 'Featured in your library', true],
    // nothing on disk yet, but a wishlisted track or a watch: monitored, so its
    // releases and their quality profile live in My Library
    [{ in_library: true, monitored: true }, 'Monitored in your library', true],
    // only credited on wanted tracks of other artists (Rockwell on a Michael
    // Jackson compilation): featured, its appears on is its My Library
    [{ in_library: true, monitored: false }, 'Featured in your library', true],
    [{}, 'Not in your library', false],
  ])('labels the artist by what the library holds: %#', async (patch, label, myLibrary) => {
    server.use(
      http.get('/api/library/v2/artists/1', () =>
        HttpResponse.json({ success: true, artist: portishead(patch) }),
      ),
    );
    renderArtist('/library?artist=1&header=rich');
    expect(await screen.findByText(label)).toBeInTheDocument();
    await waitFor(() =>
      expect(Boolean(screen.queryByRole('button', { name: 'My Library' }))).toBe(myLibrary),
    );
  });

  it('draws a track with the Wishlist icon and the artist with the Watchlist icon', async () => {
    inLibrary();
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

    // dev's two lists: a track goes on the Wishlist (the heart), the artist
    // on the Watchlist (the eye). Library v2 drew both as one bookmark
    const track = await screen.findByTitle('Add to Wishlist');
    expect(track.querySelector('svg')?.getAttribute('data-list-icon')).toBe('wishlist');
    const artist = screen.getAllByLabelText(/^(Add to|Remove from) Watchlist$/)[0];
    expect(artist.querySelector('svg')?.getAttribute('data-list-icon')).toBe('watchlist');
  });

  it('lets a read-only profile browse the full provider discography without writing', async () => {
    const writes: string[] = [];
    server.use(
      http.get('/api/library/v2/enabled', () =>
        HttpResponse.json({ success: true, enabled: true, can_write: false, can_wish: false }),
      ),
      http.post('/api/library/v2/*', ({ request }) => {
        writes.push(new URL(request.url).pathname);
        return HttpResponse.json({ success: true, job_id: 'x' });
      }),
      http.get('/api/artist-detail/:id', () =>
        HttpResponse.json({
          success: true,
          artist: { id: 'sp-1', name: 'Portishead' },
          discography: {
            source: 'spotify',
            albums: [
              { id: 'al-dummy', title: 'Dummy', album_type: 'album', track_count: 11 },
              { id: 'al-third', title: 'Third', album_type: 'album', track_count: 11 },
            ],
          },
        }),
      ),
    );
    renderArtist('/library?artist=1&view=discovery&immersive=true');

    expect(await screen.findByText('Third')).toBeInTheDocument();
    // the catalogue's own dummy is not listed twice
    expect(screen.getAllByText('Dummy')).toHaveLength(1);
    expect(writes).toEqual([]);
  });

  it('offers an album its own upgrade picker, dev-style "Upgrade N"', async () => {
    const posts: unknown[] = [];
    server.use(
      http.get('/api/library/v2/artists/1', () =>
        HttpResponse.json({
          success: true,
          artist: portishead({
            albums: [album({ id: 1, title: 'Dummy', tracks_present: 2, origin: 'library' })],
          }),
        }),
      ),
      http.get('/api/library/v2/artists/1/enhance', () =>
        HttpResponse.json({
          success: true,
          tracks: [1, 2].map((n) => ({
            track_id: n,
            title: `Song ${n}`,
            album_id: 1,
            album_title: 'Dummy',
            disc_number: 1,
            track_number: n,
            current: 'MP3 128',
            profile: 'Lossless',
            target: 'FLAC',
            monitored: false,
          })),
        }),
      ),
      http.post('/api/library/v2/albums/1/enhance', async ({ request }) => {
        posts.push(await request.json());
        return HttpResponse.json({ success: true, job_id: 'e1' });
      }),
    );
    renderArtist('/library?artist=1');

    fireEvent.click(await screen.findByRole('button', { name: 'Upgrade 2' }));
    expect(
      await screen.findByRole('dialog', { name: 'Enhance Quality — Dummy' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Enhance/ }));
    await waitFor(() => expect(posts).toEqual([{ track_ids: [1, 2] }]));
  });

  it('labels the back link with where an arriving visit came from', async () => {
    (window as { artistDetailLabelStack?: unknown[] }).artistDetailLabelStack = [
      { type: 'page', pageId: 'search' },
    ];
    try {
      renderArtist('/library?artist=1&view=discovery&immersive=true');
      expect(await screen.findByText('← Back to Search')).toBeInTheDocument();
    } finally {
      delete (window as { artistDetailLabelStack?: unknown[] }).artistDetailLabelStack;
    }
    renderArtist('/library?artist=1');
    expect(await screen.findByText('← Back')).toBeInTheDocument();
  });

  it('names the page of THIS visit, not the first one ever recorded', async () => {
    // a visit from the library grid used to leave "library" in the shared
    // chain for good, so an artist opened from search later still said
    // "Back to Library"
    (window as { artistDetailLabelStack?: unknown[] }).artistDetailLabelStack = [
      { type: 'page', pageId: 'library' },
    ];
    const { router } = renderArtist('/search');
    await waitFor(() => expect(router.state.resolvedLocation?.pathname).toBe('/search'));
    await act(() => router.navigate({ href: '/library?artist=1' }));
    expect(await screen.findByRole('button', { name: '← Back to Search' })).toHaveClass('back-btn');
    delete (window as { artistDetailLabelStack?: unknown[] }).artistDetailLabelStack;
  });

  it('url tweaks after the page opens do not change where you came from', async () => {
    const { router } = renderArtist('/search');
    await waitFor(() => expect(router.state.resolvedLocation?.pathname).toBe('/search'));
    await act(() => router.navigate({ href: '/library?artist=1' }));
    await act(() => router.navigate({ href: '/library?artist=1&header=rich' }));
    expect(await screen.findByRole('button', { name: '← Back to Search' })).toBeInTheDocument();
  });

  it('returns from an owned artist hop to the exact previous artist view', async () => {
    server.use(
      http.get('/api/library/v2/ui-preferences', () =>
        HttpResponse.json({
          success: true,
          preferences: { artist_page: { header: 'rich', header_elsewhere: 'compact' } },
        }),
      ),
      http.get('/api/library/v2/artists/:id', ({ params }) => {
        const id = Number(params.id);
        return HttpResponse.json({
          success: true,
          artist: portishead({ id, name: id === 1 ? '21 Savage' : 'Nicki Minaj' }),
        });
      }),
      http.get('/api/library/v2/artists/:id/queue-status', () =>
        HttpResponse.json({ success: true, tracks: {}, albums: {} }),
      ),
      http.get('/api/library/v2/artists/:id/aliases', ({ params }) =>
        HttpResponse.json({
          success: true,
          canonical_artist_id: Number(params.id),
          aliases: [],
        }),
      ),
      http.get('/api/library/v2/artists/:id/match-status', () =>
        HttpResponse.json({ success: true, services: [], enrichment_coverage: {} }),
      ),
    );
    const { router } = renderArtist('/search?q=21%20Savage');
    await waitFor(() => expect(router.state.resolvedLocation?.pathname).toBe('/search'));
    await act(() =>
      router.navigate({
        href: '/library?artist=1&view=discovery&releases=all&releaseView=table&header=compact',
      }),
    );
    await screen.findByRole('heading', { name: '21 Savage' });
    expect(router.state.location.search).toMatchObject({ header: 'compact' });

    await act(() => router.navigate({ href: '/library?artist=2&header=rich' }));
    fireEvent.click(await screen.findByRole('button', { name: '← Back to 21 Savage' }));

    await waitFor(() => expect(router.state.location.search).toMatchObject({ artist: 1 }));
    expect(router.state.location.search).toMatchObject({
      view: 'discovery',
      releases: 'all',
      releaseView: 'table',
      header: 'compact',
    });
  });

  it('back to library leaves the artist, however often the view was switched', async () => {
    const { router } = renderArtist('/library?section=albums');
    await waitFor(() => expect(router.state.resolvedLocation?.pathname).toBe('/library'));
    await act(() => router.navigate({ href: '/library?artist=1' }));
    fireEvent.click(await screen.findByRole('button', { name: /Discovery/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Table View' }));
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ releases: 'all', releaseView: 'table' }),
    );

    fireEvent.click(screen.getByRole('button', { name: '← Back to Library' }));

    await waitFor(() => expect(router.state.location.search).not.toHaveProperty('artist'));
    expect(router.state.location.search).toMatchObject({ section: 'albums' });
  });

  it('a second artist opened from the same grid goes back to the library, not the first', async () => {
    // the grid's url is the same both times, so "the artist last shown" was
    // still the first one: the second read "Back to 21 Savage" and led there
    server.use(
      http.get('/api/library/v2/artists/:id', ({ params }) => {
        const id = Number(params.id);
        return HttpResponse.json({
          success: true,
          artist: portishead({ id, name: id === 1 ? '21 Savage' : 'Nicki Minaj' }),
        });
      }),
    );
    const { router } = renderArtist('/library?section=artists');
    await waitFor(() => expect(router.state.resolvedLocation?.pathname).toBe('/library'));
    await act(() => router.navigate({ href: '/library?section=artists&artist=1' }));
    await screen.findByRole('heading', { name: '21 Savage' });
    fireEvent.click(screen.getByRole('button', { name: '← Back to Library' }));
    await waitFor(() => expect(router.state.location.search).not.toHaveProperty('artist'));

    await act(() => router.navigate({ href: '/library?section=artists&artist=2' }));
    await screen.findByRole('heading', { name: 'Nicki Minaj' });
    expect(screen.getByRole('button', { name: '← Back to Library' })).toBeInTheDocument();
  });

  it('back never walks into the album it came up from', async () => {
    // a cold load straight onto a release, then up to its artist: the chain
    // is empty, and stepping back through history went into the album again,
    // and from there to the artist, round and round
    server.use(
      http.get('/api/library/v2/albums/1', () =>
        HttpResponse.json({
          success: true,
          album: {
            ...album({ id: 1, title: 'Dummy' }),
            genres: [],
            quality_profile: null,
            primary_artist: { id: 1, name: 'Portishead' },
            tracks: [],
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
    const { router } = renderArtist('/library?artist=1&album=1');
    fireEvent.click(await screen.findByRole('button', { name: /Portishead/ }));
    await waitFor(() => expect(router.state.location.search).not.toHaveProperty('album'));

    fireEvent.click(await screen.findByRole('button', { name: /^← Back/ }));

    await waitFor(() => expect(router.state.location.search).not.toHaveProperty('artist'));
    expect(router.state.location.pathname).toBe('/library');
  });

  it.each([
    ['/wishlist?tab=music', 'Wishlist', 'compact'],
    ['/search?q=Portishead', 'Search', 'compact'],
    ['/watchlist?tab=artists', 'Watchlist', 'compact'],
    ['/playlist-explorer?playlist=Roadtrip', 'Explorer', 'compact'],
    ['/discover?tab=for-you', 'Discover', 'compact'],
    ['/dashboard', 'Dashboard', 'compact'],
    ['/library?section=albums', 'Library', 'rich'],
  ])('returns to %s after opening an artist and large album', async (origin, label, header) => {
    server.use(
      http.get('/api/library/v2/ui-preferences', () =>
        HttpResponse.json({
          success: true,
          preferences: {
            track_table: { visible_match_providers: {} },
            artist_page: { header: 'rich', header_elsewhere: 'compact' },
          },
        }),
      ),
      http.get('/api/library/v2/albums/1', () =>
        HttpResponse.json({
          success: true,
          album: {
            ...album({ id: 1, title: 'Dummy' }),
            genres: [],
            quality_profile: null,
            primary_artist: { id: 1, name: 'Portishead' },
            tracks: [],
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
    // A stale entry from an earlier visit must never win over this visit's
    // actual route origin.
    (window as { artistDetailLabelStack?: unknown[] }).artistDetailLabelStack = [
      { type: 'page', pageId: 'discover' },
    ];
    const { router } = renderArtist(origin);
    const originPath = new URL(origin, 'http://localhost').pathname;
    await waitFor(() => expect(router.state.resolvedLocation?.pathname).toBe(originPath));
    await act(() => router.navigate({ href: '/library?view=discovery&immersive=true&artist=1' }));
    await waitFor(() => expect(router.state.location.search.header ?? 'rich').toBe(header));
    await act(() => router.navigate({ href: '/library?artist=1&album=1&header=compact' }));
    fireEvent.click(await screen.findByRole('button', { name: '← Portishead' }));
    await waitFor(() => expect(router.state.location.search).not.toHaveProperty('album'));
    // inside the library a switch of the header is the user's and stays
    await act(() => router.navigate({ href: '/library?artist=1&header=rich' }));
    expect(router.state.location.search).toMatchObject({ header: 'rich' });
    fireEvent.click(await screen.findByRole('button', { name: `← Back to ${label}` }));
    await waitFor(() => expect(router.state.location.pathname).toBe(originPath));
    expect(router.state.location.search).toMatchObject(
      Object.fromEntries(new URL(origin, 'http://localhost').searchParams),
    );
  });

  it("is dev's styled back button, labelled with the page you came from", async () => {
    const { router } = renderArtist('/library');
    await waitFor(() => expect(router.state.resolvedLocation?.pathname).toBe('/library'));
    await act(() => router.navigate({ href: '/library?artist=1' }));
    const button = await screen.findByRole('button', { name: '← Back to Library' });
    expect(button).toHaveClass('back-btn');
  });
});
