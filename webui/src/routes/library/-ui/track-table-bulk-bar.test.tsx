import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { LibraryV2Track } from '../-library-v2.types';

import { LibraryV2CanWriteContext, TrackTableBulkBar } from './library-v2-page';

describe('TrackTableBulkBar', () => {
  it('keeps the quality-profile label accessible without rendering it twice', async () => {
    server.use(
      http.get('/api/library/v2/quality-profiles', () =>
        HttpResponse.json({ success: true, profiles: [] }),
      ),
    );
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <LibraryV2CanWriteContext.Provider value>
          <TrackTableBulkBar
            albumId={4}
            tracks={[{ id: 1, file: null } as LibraryV2Track]}
            onClear={vi.fn()}
          />
        </LibraryV2CanWriteContext.Provider>
      </QueryClientProvider>,
    );

    expect(
      screen.getByRole('combobox', { name: 'Quality profile for the selected tracks' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Quality profile', { selector: 'span' }).className).toMatch(/srOnly/);
  });

  it('invalidates partial writes, reports ids and retries only failures', async () => {
    const calls: string[] = [];
    let rejectTrack2 = true;
    server.use(
      http.get('/api/library/v2/quality-profiles', () =>
        HttpResponse.json({ success: true, profiles: [] }),
      ),
      http.post('/api/library/v2/tracks/:trackId/monitor', ({ params }) => {
        const id = String(params.trackId);
        calls.push(id);
        if (id === '2' && rejectTrack2) {
          return HttpResponse.json({ success: false, error: 'conflict' }, { status: 409 });
        }
        return HttpResponse.json({ success: true });
      }),
    );
    const queryClient = createTestQueryClient();
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    const tracks = [
      { id: 1, file: null },
      { id: 2, file: null },
    ] as LibraryV2Track[];
    render(
      <QueryClientProvider client={queryClient}>
        <LibraryV2CanWriteContext.Provider value>
          <TrackTableBulkBar albumId={4} tracks={tracks} onClear={vi.fn()} />
        </LibraryV2CanWriteContext.Provider>
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Monitor' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      '1 succeeded; 1 failed (track IDs: 2)',
    );
    expect(invalidate).toHaveBeenCalled();

    rejectTrack2 = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry failed 1' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(calls).toEqual(['1', '2', '2']);
  });
});
