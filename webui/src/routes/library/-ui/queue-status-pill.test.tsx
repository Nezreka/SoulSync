import { createMemoryHistory } from '@tanstack/react-router';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import type { LibraryV2AlbumSummary } from '../-library-v2.types';

/**
 * The album row's live pill used to render a bare active-track count as
 * "N downloading". Three tracks merely QUEUED behind a busy client therefore
 * claimed to be downloading, and an album that was only searching looked
 * identical to one actually pulling bytes.
 *
 * These tests pin the distinction at the rendering layer; the roll-up that
 * feeds them is covered in tests/library2/test_queue_status.py.
 */

function album(patch: Partial<LibraryV2AlbumSummary> & { id: number; title: string }) {
  return {
    album_type: 'album',
    release_date: '2001-01-01',
    year: 2001,
    image_url: '/api/library/v2/artwork/album/1',
    remote_image_url: null,
    monitored: true,
    quality_profile_id: 1,
    origin: 'library',
    spotify_id: null,
    explicit: null,
    label: null,
    style: null,
    mood: null,
    track_count: 10,
    tracks_present: 4,
    tracks_missing: 6,
    total_size_bytes: 1024,
    user_overrides: {},
    ...patch,
  };
}

function rollup(patch: Record<string, number>) {
  return {
    active: 0,
    progress_pct: 0,
    queued: 0,
    searching: 0,
    downloading: 0,
    processing: 0,
    ...patch,
  };
}

function renderArtist() {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: ['/library?artist=1'] });
  const router = createAppRouter({ history, queryClient });
  return render(<AppRouterProvider router={router} queryClient={queryClient} />);
}

function useQueueStatus(body: {
  tracks: Record<string, { status: string; progress_pct: number }>;
  albums: Record<string, ReturnType<typeof rollup>>;
}) {
  server.use(
    http.get('/api/library/v2/artists/1/queue-status', () =>
      HttpResponse.json({ success: true, ...body }),
    ),
  );
}

describe('Library V2 live queue pill', () => {
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
            provider_ids: {},
            media_server_sources: [],
            summary: null,
            style: null,
            mood: null,
            label: null,
            genres: [],
            monitored: true,
            monitor_new_items: 'all',
            quality_profile: null,
            albums: [album({ id: 1, title: 'Dummy' })],
            eps: [],
            singles: [],
            album_count: 1,
            single_count: 0,
            discography_count: 1,
            total_size_bytes: 1024,
            user_overrides: {},
          },
        }),
      ),
      http.get('/api/library/v2/artists/1/aliases', () =>
        HttpResponse.json({ success: true, canonical_artist_id: 1, aliases: [] }),
      ),
      http.get('/api/library/v2/artists/1/match-status', () =>
        HttpResponse.json({ success: true, services: [] }),
      ),
      http.get('/api/artist/:id/bio', () => HttpResponse.json({ success: false, bio: null })),
      http.get('/api/artist/:id/top-tracks', () =>
        HttpResponse.json({ success: false, tracks: [] }),
      ),
      http.get('/api/artist/0/lastfm-top-tracks', () =>
        HttpResponse.json({ success: true, tracks: [] }),
      ),
    );
  });

  it('names a searching album as searching, not downloading', async () => {
    useQueueStatus({
      tracks: {
        '11': { status: 'searching', progress_pct: 0 },
        '12': { status: 'searching', progress_pct: 0 },
      },
      albums: { '1': rollup({ active: 2, searching: 2 }) },
    });

    renderArtist();
    await screen.findByRole('heading', { name: 'Portishead' });

    const [pill] = await screen.findAllByText('2 searching');
    // Scoped to the pill: a document-wide /downloading/ would also see the
    // shell's own chrome and fail for reasons that have nothing to do with
    // this row.
    expect(pill.closest('span')?.textContent).not.toMatch(/downloading/i);
    // Nothing is moving, so no bar claims otherwise.
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });

  it('spells out a mixed album instead of collapsing it to one state', async () => {
    useQueueStatus({
      tracks: {
        '11': { status: 'downloading', progress_pct: 64 },
        '12': { status: 'processing', progress_pct: 95 },
        '13': { status: 'queued', progress_pct: 0 },
        '14': { status: 'queued', progress_pct: 0 },
        '15': { status: 'queued', progress_pct: 0 },
      },
      albums: {
        '1': rollup({ active: 5, downloading: 1, processing: 1, queued: 3, progress_pct: 32 }),
      },
    });

    renderArtist();
    await screen.findByRole('heading', { name: 'Portishead' });

    expect(await screen.findAllByText('1 downloading')).not.toHaveLength(0);
    expect(screen.getAllByText('1 processing')).not.toHaveLength(0);
    expect(screen.getAllByText('3 queued')).not.toHaveLength(0);

    // The bar reports the album mean, not the one downloading track's 64%.
    const bars = screen.getAllByRole('progressbar');
    expect(bars.map((bar) => bar.getAttribute('aria-valuenow'))).toContain('32');
  });

  it('shows artist-wide activity in the header while every release is collapsed', async () => {
    useQueueStatus({
      tracks: {
        '11': { status: 'queued', progress_pct: 0 },
        '12': { status: 'queued', progress_pct: 0 },
      },
      // No album roll-up at all: the tracks belong to a release that is not
      // in this response's album map. The header must still report them.
      albums: {},
    });

    renderArtist();
    await screen.findByRole('heading', { name: 'Portishead' });

    expect(await screen.findAllByText('2 queued')).toHaveLength(1);
  });
});
