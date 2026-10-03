import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { LibraryV2CanWriteContext, ManageTracksDuplicatesTab } from './library-v2-page';

// Upstream e573bd5fc: removing the copy a server playlist points at drops it
// from that playlist, so the duplicate pair says which version is listed.
const side = (trackId: number, albumTitle: string, playlists: string[]) => ({
  track_id: trackId,
  album_title: albumTitle,
  monitored: true,
  file: {
    path: `/m/${albumTitle}.flac`,
    format: 'flac',
    bitrate: 900,
    sample_rate: 44100,
    bit_depth: 16,
  },
  playlists,
});

it('names the playlists that point at a duplicate version', async () => {
  server.use(
    http.get('/api/library/v2/artists/7/duplicates', () =>
      HttpResponse.json({
        success: true,
        pairs: [
          {
            title: 'Teardrop',
            single: side(1, 'Teardrop (Single)', ['Road Trip', 'Chill']),
            album: side(2, 'Mezzanine', []),
          },
        ],
      }),
    ),
  );
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <LibraryV2CanWriteContext.Provider value>
        <ManageTracksDuplicatesTab artistId={7} />
      </LibraryV2CanWriteContext.Provider>
    </QueryClientProvider>,
  );

  expect(await screen.findByText(/In playlist: Road Trip, Chill/)).toBeInTheDocument();
  expect(screen.getAllByText(/In playlist:/)).toHaveLength(1);
});
