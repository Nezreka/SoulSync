import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { RetagModal } from './retag-modal';

describe('Library v2 retag preview', () => {
  it('groups by stable album id and labels release types even when rows are interleaved', async () => {
    const previewTrack = (trackId: number, albumId: number, albumType: string, title: string) => ({
      track_id: trackId,
      title,
      track_number: trackId,
      album_id: albumId,
      album_title: 'Shared title',
      album_type: albumType,
      file_path: `/music/${trackId}.flac`,
      diff: [{ field: 'title', file_value: 'old', db_value: title, changed: true }],
      has_changes: true,
    });

    server.use(
      http.get('/api/library/v2/artists/7/tag-preview', () =>
        HttpResponse.json({
          success: true,
          tracks: [
            previewTrack(1, 10, 'album', 'Album track one'),
            previewTrack(2, 11, 'single', 'Single track'),
            previewTrack(3, 10, 'album', 'Album track two'),
          ],
          changed_count: 3,
          truncated: false,
        }),
      ),
    );

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <RetagModal entity="artists" id={7} title="Artist" onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    expect(await screen.findAllByText('Shared title')).toHaveLength(2);
    expect(screen.getByText('2 of 2 changing')).toBeInTheDocument();
    expect(screen.getByText('1 of 1 changing')).toBeInTheDocument();
    expect(screen.getByText('Album')).toBeInTheDocument();
    expect(screen.getByText('Single')).toBeInTheDocument();
  });
});
