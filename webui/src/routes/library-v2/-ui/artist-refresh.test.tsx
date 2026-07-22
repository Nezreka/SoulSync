import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { ArtistRefreshButton } from './library-v2-page';

describe('library v2 artist refresh mutation', () => {
  it('shows a rejected refresh and turns the same control into a retry', async () => {
    let attempts = 0;
    server.use(
      http.post('/api/library/v2/artists/7/refresh', () => {
        attempts += 1;
        return HttpResponse.json(
          attempts === 1
            ? { success: false, error: 'Music root is temporarily unavailable' }
            : { success: true },
        );
      }),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <ArtistRefreshButton artistId={7} />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Refresh & Scan' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Music root is temporarily unavailable',
    );
    const retry = screen.getByRole('button', { name: 'Retry Refresh & Scan' });
    expect(retry).toBeEnabled();

    fireEvent.click(retry);

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(attempts).toBe(2);
    expect(screen.getByRole('button', { name: 'Refresh & Scan' })).toBeEnabled();
  });
});
