import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { AlbumReorganizeModal, ArtistReorganizeAllModal } from './reorganize-modal';

const PREVIEW_RESPONSE = {
  success: true,
  status: 'planned',
  source: null,
  album: 'Views',
  artist: 'Drake',
  transfer_dir: '/music',
  tracks: [
    {
      track_id: 1,
      title: 'One Dance',
      track_number: 1,
      disc_number: 1,
      current_path: '/old/One Dance.flac',
      new_path: '/new/One Dance.flac',
      file_exists: true,
      unchanged: false,
      collision: false,
      matched: true,
      reason: null,
    },
  ],
};

describe('library v2 album reorganize queue status', () => {
  it('polls the shared queue by queue id and shows live progress through to done', async () => {
    server.use(
      http.get('/api/library/v2/albums/42/reorganize/sources', () =>
        HttpResponse.json({ success: true, sources: [] }),
      ),
      http.post('/api/library/v2/albums/42/reorganize/preview', () =>
        HttpResponse.json(PREVIEW_RESPONSE),
      ),
      http.post('/api/library/v2/albums/42/reorganize', () =>
        HttpResponse.json({ success: true, queued: true, queue_id: 'q-1' }),
      ),
      http.get('/api/library/reorganize/queue', () =>
        HttpResponse.json({
          success: true,
          active: null,
          queued: [],
          recent: [
            {
              queue_id: 'q-1',
              album_id: '99',
              album_title: 'Views',
              artist_name: 'Drake',
              status: 'done',
              result_status: 'moved',
              current_track: null,
              progress_total: 1,
              progress_processed: 1,
              finished_at: 1700000000,
            },
          ],
        }),
      ),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <AlbumReorganizeModal albumId={42} albumTitle="Views" onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: /Reorganize \(1\)/ }));

    expect(await screen.findByText('Reorganize finished (moved).')).toBeInTheDocument();
  });

  it('surfaces an already-queued response without a live-status crash', async () => {
    server.use(
      http.get('/api/library/v2/albums/42/reorganize/sources', () =>
        HttpResponse.json({ success: true, sources: [] }),
      ),
      http.post('/api/library/v2/albums/42/reorganize/preview', () =>
        HttpResponse.json(PREVIEW_RESPONSE),
      ),
      http.post('/api/library/v2/albums/42/reorganize', () =>
        HttpResponse.json({ success: true, queued: false, reason: 'already_queued' }),
      ),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <AlbumReorganizeModal albumId={42} albumTitle="Views" onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: /Reorganize \(1\)/ }));

    expect(await screen.findByText('Not queued (already queued).')).toBeInTheDocument();
  });
});

describe('library v2 artist reorganize-all queue progress', () => {
  it('watches the shared queue by artist name until nothing of this artist is left', async () => {
    server.use(
      http.get('/api/library/v2/reorganize/sources', () =>
        HttpResponse.json({ success: true, sources: [] }),
      ),
      http.post('/api/library/v2/artists/7/reorganize-all', () =>
        HttpResponse.json({ success: true, enqueued: 2, already_queued: 0, total_albums: 2 }),
      ),
      http.get('/api/library/reorganize/queue', () =>
        HttpResponse.json({ success: true, active: null, queued: [], recent: [] }),
      ),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <ArtistReorganizeAllModal artistId={7} artistName="Drake" onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Reorganize All Albums' }));

    expect(await screen.findByText('2 of 2 album(s) queued.')).toBeInTheDocument();
    expect(
      await screen.findByText('All queued albums for this artist have finished.'),
    ).toBeInTheDocument();
  });
});
