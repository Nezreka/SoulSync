import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import {
  GlobalAutomaticSearchButton,
  ImportButton,
  LibraryV2CanWriteContext,
} from './library-v2-page';

describe('Library v2 global Automatic Search', () => {
  it('queues cutoff upgrades before starting the shared Wishlist processor', async () => {
    const calls: string[] = [];
    server.use(
      http.post('/api/library/v2/upgrade-scan', () => {
        calls.push('upgrade-scan');
        return HttpResponse.json({ success: true, job_id: 'upgrade-1' });
      }),
      http.get('/api/library/v2/jobs/status', ({ request }) => {
        expect(new URL(request.url).searchParams.get('job_id')).toBe('upgrade-1');
        calls.push('upgrade-finished');
        return HttpResponse.json({
          running: false,
          current: 3,
          total: 3,
          result: { checked: 3, queued: 2 },
          error: null,
        });
      }),
      http.post('/api/wishlist/process', () => {
        calls.push('wishlist-process');
        // The public blueprint uses the standard nested data envelope while
        // the legacy route still returns a top-level message.
        return HttpResponse.json({
          success: true,
          data: { message: 'Wishlist processing started.' },
          error: null,
        });
      }),
      http.get('/api/library/v2/import/status', () =>
        HttpResponse.json({
          running: false,
          stage: 'idle',
          current: 0,
          total: 0,
          stats: null,
          error: null,
          finished_at: null,
          artwork_cache: {
            running: false,
            current: 0,
            total: 0,
            stats: null,
            error: null,
            started_at: null,
            finished_at: null,
          },
        }),
      ),
    );

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <LibraryV2CanWriteContext.Provider value>
          <GlobalAutomaticSearchButton />
          <ImportButton hasArtists />
        </LibraryV2CanWriteContext.Provider>
      </QueryClientProvider>,
    );

    const automaticSearch = screen.getByRole('button', {
      name: 'Automatic Search',
    });
    expect(screen.queryByRole('button', { name: 'Re-import library' })).not.toBeInTheDocument();
    expect(automaticSearch.querySelector('svg')).toBeInTheDocument();
    fireEvent.click(automaticSearch);

    expect(
      await screen.findByText(
        'Wishlist processing started. Missing tracks and quality upgrades are queued.',
      ),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(calls).toEqual(['upgrade-scan', 'upgrade-finished', 'wishlist-process']),
    );
  });
});
