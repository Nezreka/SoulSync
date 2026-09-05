import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import {
  ArtistFilesTab,
  LibraryV2CanWriteContext,
  ManageTracksDuplicatesTab,
  TrackInfoPanel,
} from './library-v2-page';

function renderTool(node: React.ReactNode) {
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <LibraryV2CanWriteContext.Provider value>{node}</LibraryV2CanWriteContext.Provider>
    </QueryClientProvider>,
  );
}

describe('Library v2 tool error states', () => {
  it('does not present a duplicates 500 as an empty result', async () => {
    server.use(
      http.get('/api/library/v2/artists/7/duplicates', () =>
        HttpResponse.json({ success: false, error: 'duplicate scan failed' }, { status: 500 }),
      ),
    );
    renderTool(<ManageTracksDuplicatesTab artistId={7} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('duplicate scan failed');
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
    expect(screen.queryByText(/No single.*duplicates/)).not.toBeInTheDocument();
  });

  it('does not present an artist-files 500 as no files', async () => {
    server.use(
      http.get('/api/library/v2/artists/7/track-files', () =>
        HttpResponse.json({ success: false, error: 'file list failed' }, { status: 500 }),
      ),
    );
    renderTool(<ArtistFilesTab artistId={7} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('file list failed');
    expect(screen.queryByText('No files found.')).not.toBeInTheDocument();
  });

  it('groups physical versions and lets a secondary become primary', async () => {
    const setPrimary = vi.fn();
    const base = {
      track_id: 2,
      track_title: 'Teardrop',
      track_number: 1,
      disc_number: 1,
      album_id: 3,
      album_title: 'Mezzanine',
      size: 4096,
      bitrate: null,
      sample_rate: 44100,
      bit_depth: 16,
      quality_tier: 'lossless',
      file_state: 'active',
      added_at: null,
    };
    server.use(
      http.get('/api/library/v2/artists/7/track-files', () =>
        HttpResponse.json({
          success: true,
          files: [
            {
              ...base,
              file_id: 8,
              path: '/m/Teardrop.flac',
              format: 'flac',
              is_primary: true,
              file_role: 'master',
            },
            {
              ...base,
              file_id: 9,
              path: '/m/Teardrop.opus',
              format: 'opus',
              is_primary: false,
              file_role: 'derivative',
              derived_from_file_id: 8,
            },
          ],
          pagination: {
            page: 1,
            limit: 100,
            total_count: 2,
            total_pages: 1,
            has_prev: false,
            has_next: false,
          },
        }),
      ),
      http.post('/api/library/v2/tracks/2/files/9/primary', () => {
        setPrimary();
        return HttpResponse.json({ success: true });
      }),
    );

    renderTool(<ArtistFilesTab artistId={7} />);

    expect(await screen.findByText('2 versions')).toBeInTheDocument();
    expect(
      screen.getByText('derivative', { selector: '[data-role="derivative"]' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Make primary' }));
    await waitFor(() => expect(setPrimary).toHaveBeenCalledOnce());
  });

  it('does not present a network failure as missing source data', async () => {
    server.use(
      http.get('/api/library/v2/tracks/9/history', () =>
        HttpResponse.json({ success: true, history: [] }),
      ),
      http.get('/api/library/v2/tracks/9/source-info', () => HttpResponse.error()),
    );
    renderTool(
      <TrackInfoPanel trackId={9} trackTitle="Teardrop" trackArtist="Massive Attack" file={null} />,
    );

    expect(await screen.findByText(/Failed to fetch|Network/i)).toBeInTheDocument();
    expect(screen.queryByText(/No download source data/)).not.toBeInTheDocument();
  });
});
