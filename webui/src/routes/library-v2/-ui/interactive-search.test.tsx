import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { InteractiveSearchModal } from './interactive-search';

describe('library v2 interactive grab', () => {
  it('shows the candidate download error and retries the same result', async () => {
    let attempts = 0;
    const submitted: unknown[] = [];
    server.use(
      http.post('/api/search', () =>
        HttpResponse.json({
          results: [
            {
              result_type: 'track',
              username: 'peer-one',
              filename: 'Artist/Selected.flac',
              title: 'Selected',
              artist: 'Artist',
              quality: 'flac',
              size: 4096,
              free_upload_slots: 1,
              queue_length: 0,
            },
          ],
        }),
      ),
      http.post('/api/download', async ({ request }) => {
        attempts += 1;
        submitted.push(await request.json());
        return HttpResponse.json(
          attempts === 1
            ? { success: false, error: 'Download client rejected the transfer' }
            : { success: true },
        );
      }),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <InteractiveSearchModal initialQuery="Artist Selected" onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Download' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Download client rejected the transfer',
    );
    const retry = screen.getByRole('button', { name: 'Retry' });
    expect(retry).toBeEnabled();

    fireEvent.click(retry);

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Grabbed ✓' })).toBeDisabled();
    expect(attempts).toBe(2);
    expect(submitted[1]).toEqual(submitted[0]);
  });
});
