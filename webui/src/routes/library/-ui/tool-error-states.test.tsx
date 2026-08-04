import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

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
