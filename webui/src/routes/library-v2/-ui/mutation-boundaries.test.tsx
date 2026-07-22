import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { LibraryV2PlaylistSummary } from '../-library-v2.types';

import {
  AlbumOverflowMenu,
  ArtistAliases,
  MirrorStatusBanner,
  PlaylistPipelineButton,
} from './library-v2-page';

function renderWithQueryClient(node: React.ReactNode) {
  const queryClient = createTestQueryClient();
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

describe('library v2 remaining mutation boundaries', () => {
  it('surfaces an alias-unlink 4xx and retries the same alias', async () => {
    let attempts = 0;
    server.use(
      http.get('/api/library/v2/artists/7/aliases', () =>
        HttpResponse.json({
          success: true,
          canonical_artist_id: 7,
          aliases: [
            { id: 7, name: 'Canonical' },
            { id: 8, name: 'Provider Alias' },
          ],
        }),
      ),
      http.delete('/api/library/v2/artists/8/link-alias', () => {
        attempts += 1;
        return HttpResponse.json(
          attempts === 1
            ? { success: false, error: 'Alias relation is locked' }
            : { success: true },
          { status: attempts === 1 ? 409 : 200 },
        );
      }),
    );

    renderWithQueryClient(<ArtistAliases artistId={7} artistName="Canonical" />);

    fireEvent.click(await screen.findByTitle(/Unlink Provider Alias/));
    expect(await screen.findByRole('alert')).toHaveTextContent('Alias relation is locked');

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(attempts).toBe(2);
  });

  it('surfaces a failed album ReplayGain job and offers retry', async () => {
    let starts = 0;
    server.use(
      http.post('/api/library/v2/albums/12/replaygain', () => {
        starts += 1;
        return HttpResponse.json({ success: true, job_id: `rg-${starts}` });
      }),
      http.get('/api/library/v2/jobs/status', ({ request }) => {
        const jobId = new URL(request.url).searchParams.get('job_id');
        return HttpResponse.json({
          running: false,
          error: jobId === 'rg-1' ? 'ReplayGain scanner crashed' : null,
        });
      }),
    );
    const album = {
      id: 12,
      title: 'Album',
      year: 2026,
      album_type: 'album',
      release_date: '2026-01-01',
      explicit: false,
      label: null,
      style: null,
      mood: null,
      user_overrides: {},
      quality_profile_id: 1,
    } as React.ComponentProps<typeof AlbumOverflowMenu>['album'];

    renderWithQueryClient(<AlbumOverflowMenu album={album} />);
    fireEvent.click(screen.getByTitle('More actions'));
    fireEvent.click(screen.getByRole('button', { name: 'Analyze ReplayGain' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('ReplayGain scanner crashed');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(starts).toBe(2);
  });

  it('shows a failed mirror retry and lets the user retry again', async () => {
    let attempts = 0;
    server.use(
      http.get('/api/library/v2/mirror-status', () =>
        HttpResponse.json({
          success: true,
          pending: 0,
          failed: attempts >= 2 ? 0 : 1,
        }),
      ),
      http.post('/api/library/v2/mirror-retry', () => {
        attempts += 1;
        return HttpResponse.json(
          attempts === 1 ? { success: false, error: 'Mirror database is busy' } : { success: true },
        );
      }),
    );

    renderWithQueryClient(<MirrorStatusBanner />);

    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Mirror database is busy');
    fireEvent.click(screen.getByRole('button', { name: 'Retry again' }));

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(attempts).toBe(2);
  });

  it('labels a rejected playlist start as retryable and repeats it', async () => {
    let attempts = 0;
    server.use(
      http.post('/api/mirrored-playlists/9/pipeline/run', () => {
        attempts += 1;
        return HttpResponse.json(
          attempts === 1
            ? { success: false, error: 'Playlist source is unavailable' }
            : {
                success: true,
                state: { run_id: 'run-9', playlist_id: 9, status: 'running', progress: 0 },
              },
        );
      }),
    );
    const playlist: LibraryV2PlaylistSummary = {
      id: 9,
      source: 'spotify',
      source_playlist_id: 'source-9',
      name: 'Road Trip',
      display_name: 'Road Trip',
      description: null,
      owner: null,
      image_url: null,
      track_count: 2,
      total_count: 2,
      discovered_count: 2,
      wishlisted_count: 0,
      in_library_count: 0,
      updated_at: null,
      pipeline_state: null,
    };

    renderWithQueryClient(<PlaylistPipelineButton playlist={playlist} />);

    fireEvent.click(screen.getByRole('button', { name: 'Run pipeline' }));

    expect(await screen.findByText('Playlist source is unavailable')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry pipeline' }));

    await waitFor(() =>
      expect(screen.queryByText('Playlist source is unavailable')).not.toBeInTheDocument(),
    );
    expect(attempts).toBe(2);
  });
});
