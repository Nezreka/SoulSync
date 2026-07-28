import { createMemoryHistory } from '@tanstack/react-router';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/**
 * Driven through the real router: the page reads route params and search, and
 * useProfile needs the root route context.
 */
vi.mock('@/platform/shell/route-manifest', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/platform/shell/route-manifest')>();
  return {
    ...actual,
    getShellRouteByPageId: (pageId: string) =>
      pageId === 'artist-detail'
        ? { ...actual.getShellRouteByPageId('artist-detail'), kind: 'react' }
        : actual.getShellRouteByPageId(pageId as never),
  };
});

let requested: string[] = [];
let streamFrames: string[] = [];

function stubDetail(body: unknown, status = 200) {
  requested = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      requested.push(url);
      if (url.includes('/api/library/completion-stream')) {
        const encoder = new TextEncoder();
        let i = 0;
        return new Response(
          new ReadableStream({
            pull(controller) {
              if (i < streamFrames.length) controller.enqueue(encoder.encode(streamFrames[i++]));
              else controller.close();
            },
          }),
          { status: 200 },
        );
      }
      if (url.includes('/api/album/')) {
        return new Response(JSON.stringify({ success: true, tracks: [{ id: 1 }] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
}

function renderPage(entry = '/artist-detail/library/42') {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [entry] });
  const router = createAppRouter({ history, queryClient });
  return { router, ...render(<AppRouterProvider router={router} queryClient={queryClient} />) };
}

const LIBRARY = {
  success: true,
  artist: { id: 42, name: 'Aphex Twin', server_source: 'plex' },
  discography: { albums: [{ id: 1, title: 'SAW', owned: true }], source: 'spotify' },
};

beforeEach(() => {
  window.SoulSyncWebShellBridge = createShellBridge();
  window.showToast = vi.fn();
  window.loadSimilarArtists = vi.fn();
  window.cancelSimilarArtistsLoad = vi.fn();
  window.checkArtistEnhanceEligibility = vi.fn();
  window.initializeLibraryWatchlistButton = vi.fn();
  window.observeLazyBackgrounds = vi.fn();
  streamFrames = [];
  stubDetail(LIBRARY);
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.SoulSyncWebShellBridge;
  delete document.body.dataset.artistSource;
});

describe('body[data-artist-source]', () => {
  it('tags the body library for an artist with a server_source', async () => {
    // CSS keys off this to hide library-only UI.
    renderPage();
    await waitFor(() => expect(document.body.dataset.artistSource).toBe('library'));
  });

  it('tags it source when there is no library record', async () => {
    stubDetail({ success: true, artist: { id: 'sp1', name: 'X' }, discography: {} });
    renderPage();
    await waitFor(() => expect(document.body.dataset.artistSource).toBe('source'));
  });

  it('clears the flag on unmount so the next page does not inherit it', async () => {
    const { unmount } = renderPage();
    await waitFor(() => expect(document.body.dataset.artistSource).toBe('library'));
    unmount();
    expect(document.body.dataset.artistSource).toBeUndefined();
  });
});

describe('fire-and-forget side effects', () => {
  it('loads similar artists, cancelling any in flight first', async () => {
    renderPage();
    await waitFor(() => expect(window.loadSimilarArtists).toHaveBeenCalledWith('Aphex Twin'));
    expect(window.cancelSimilarArtistsLoad).toHaveBeenCalled();
  });

  it('cancels similar artists on unmount', async () => {
    const { unmount } = renderPage();
    await waitFor(() => expect(window.loadSimilarArtists).toHaveBeenCalled());
    (window.cancelSimilarArtistsLoad as ReturnType<typeof vi.fn>).mockClear();
    unmount();
    expect(window.cancelSimilarArtistsLoad).toHaveBeenCalled();
  });

  it('probes enhance eligibility for a LIBRARY artist only', async () => {
    renderPage();
    await waitFor(() => expect(window.checkArtistEnhanceEligibility).toHaveBeenCalledWith(42));
  });

  it('does not probe eligibility for a source artist', async () => {
    // The endpoint only works on library primary keys.
    stubDetail({ success: true, artist: { id: 'sp1', name: 'X' }, discography: {} });
    renderPage();
    await screen.findByText('X');
    expect(window.checkArtistEnhanceEligibility).not.toHaveBeenCalled();
  });

  it('wires the watchlist button to the canonical Spotify identity', async () => {
    stubDetail({
      ...LIBRARY,
      spotify_artist: { spotify_artist_id: 'sp9', spotify_artist_name: 'Aphex Twin' },
    });
    renderPage();
    await waitFor(() =>
      expect(window.initializeLibraryWatchlistButton).toHaveBeenCalledWith('sp9', 'Aphex Twin'),
    );
  });

  it('warns about a provider error but still renders the page', async () => {
    stubDetail({ ...LIBRARY, provider_error: { error: 'MusicBrainz timed out' } });
    renderPage();
    await screen.findByText('Aphex Twin');
    expect(window.showToast).toHaveBeenCalledWith(
      'Discography provider warning: MusicBrainz timed out',
      'error',
    );
  });
});

describe('failure', () => {
  it('shows the error state with the hero HIDDEN, and toasts', async () => {
    stubDetail({ success: false, error: 'Artist not found' });
    renderPage();
    await screen.findByText('Failed to load artist details');
    expect(document.getElementById('artist-hero-section')).toBeNull();
    expect(document.getElementById('artist-detail-error-message')?.textContent).toBe(
      'Artist not found',
    );
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        'Failed to load artist details: Artist not found',
        'error',
      ),
    );
  });
});

describe('source artists', () => {
  it('settles unknown ownership to false so cards do not check forever', async () => {
    // Nothing will ever stream a result for a source artist.
    stubDetail({
      success: true,
      artist: { id: 'sp1', name: 'X' },
      discography: { albums: [{ id: 1, title: 'A', owned: null }] },
    });
    renderPage();
    await screen.findByText('A');
    expect(document.querySelector('.release-card')?.className).toContain('missing');
    expect(document.querySelector('.completion-overlay')).toBeNull();
  });
});

describe('MusicBrainz declutter', () => {
  it('applies once the discography source is known', async () => {
    stubDetail({
      ...LIBRARY,
      discography: {
        albums: [{ id: 1, title: 'Studio', secondary_types: [], owned: false }],
        source: 'musicbrainz',
      },
    });
    renderPage();
    await screen.findByText('Studio');
    // The Live toggle is relabelled and starts OFF under the declutter.
    const live = document.querySelector(
      '[data-filter="content"][data-value="live"]',
    ) as HTMLElement;
    expect(live.textContent).toBe('Non-Studio');
    expect(live.className).not.toContain('active');
  });

  it('leaves other sources with the toggle on and labelled Live', async () => {
    renderPage();
    await screen.findByText('SAW');
    const live = document.querySelector(
      '[data-filter="content"][data-value="live"]',
    ) as HTMLElement;
    expect(live.textContent).toBe('Live');
    expect(live.className).toContain('active');
  });
});

describe('ownership completion stream', () => {
  const UNRESOLVED = {
    success: true,
    artist: { id: 42, name: 'Aphex Twin', server_source: 'plex' },
    discography: {
      albums: [
        { id: 1, title: 'SAW', owned: null },
        { id: 2, title: 'Drukqs', owned: null },
      ],
      source: 'spotify',
    },
  };

  it('merges streamed ownership into the bars and the section counts', async () => {
    streamFrames = [
      'data: {"type":"completion","id":1,"category":"albums","status":"ok","formats":["FLAC"]}\n',
      'data: {"type":"completion","id":2,"category":"albums","status":"missing"}\n',
      'data: {"type":"complete","processed_count":2}\n',
    ];
    stubDetail(UNRESOLVED);
    renderPage();

    await waitFor(() => expect(document.getElementById('albums-stats')?.textContent).toBe('1/2'));
    // The cards move with the bar — the section counts read from the same
    // merged discography, so this proves the merge reaches the grid too.
    expect(screen.getByText('1 owned')).toBeTruthy();
    expect(screen.getByText('1 missing')).toBeTruthy();
    // Formats are artist-level and only appear on the terminal frame.
    expect(document.querySelector('.artist-format-tag')?.textContent).toBe('FLAC');
  });

  it('does not open a stream when every release is already resolved', async () => {
    stubDetail(LIBRARY);
    renderPage();
    await screen.findByText('Aphex Twin');
    await new Promise((r) => setTimeout(r, 20));
    expect(requested.some((u) => u.includes('completion-stream'))).toBe(false);
  });

  it('does not open a stream for a source artist', async () => {
    // No library to check against; settleOwnershipForSourceArtist resolves the
    // cards to "missing" up front instead.
    stubDetail({
      success: true,
      artist: { id: 'sp1', name: 'X' },
      discography: { albums: [{ id: 1, title: 'A', owned: null }], source: 'deezer' },
    });
    renderPage();
    await screen.findByText('X');
    await new Promise((r) => setTimeout(r, 20));
    expect(requested.some((u) => u.includes('completion-stream'))).toBe(false);
  });
});
