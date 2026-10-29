import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { OwnedArtistReorganizeQueue } from './owned-artist-reorganize-queue';

describe('owned artist reorganize queue', () => {
  it('shows the live queue with dev’s cancel and clear', async () => {
    const posts: string[] = [];
    window.showToast = vi.fn() as never;
    window.showConfirmDialog = vi.fn(async () => true) as never;
    server.use(
      http.get('/api/library/reorganize/queue', () =>
        HttpResponse.json({
          success: true,
          active: { album_id: 7, queue_id: 'q1', artist_id: 3, album_title: 'Dummy' },
          queued: [{ album_id: 8, queue_id: 'q2', artist_id: 3, album_title: 'Third' }],
          recent: [],
        }),
      ),
      http.post('/api/library/reorganize/queue/:id/cancel', ({ params }) => {
        posts.push(`cancel ${String(params.id)}`);
        return HttpResponse.json({ success: true });
      }),
      http.post('/api/library/reorganize/queue/clear', () => {
        posts.push('clear');
        return HttpResponse.json({ success: true, removed: 1 });
      }),
    );
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <OwnedArtistReorganizeQueue artistId={3} />
      </QueryClientProvider>,
    );

    await screen.findAllByText(/Dummy/);
    fireEvent.click(document.querySelector('.reorg-panel-compact') as HTMLElement);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel All' }));
    fireEvent.click(document.querySelector('.reorg-panel-cancel-btn') as HTMLElement);
    await waitFor(() => expect(posts).toEqual(expect.arrayContaining(['clear', 'cancel q2'])));
  });
});
