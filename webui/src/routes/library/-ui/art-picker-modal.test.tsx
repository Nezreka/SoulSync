import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { ArtistImagePickerModal } from './art-picker-modal';

describe('ArtistImagePickerModal', () => {
  it('iss27-03: the Refresh button re-queries providers with refresh=1 instead of the cache', async () => {
    const seenRefreshParams: (string | null)[] = [];
    server.use(
      http.get('/api/library/v2/artists/7/art-options', ({ request }) => {
        seenRefreshParams.push(new URL(request.url).searchParams.get('refresh'));
        return HttpResponse.json({
          success: true,
          count: 1,
          candidates: [{ source: 'deezer', url: 'https://dz/img.jpg' }],
        });
      }),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <ArtistImagePickerModal artistId={7} artistName="Adele" onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    await screen.findByAltText('Photo option from deezer');
    expect(seenRefreshParams).toEqual([null]);

    fireEvent.click(
      screen.getByTitle('Refresh — re-query every provider instead of the cached result'),
    );

    await waitFor(() => expect(seenRefreshParams).toEqual([null, '1']));
  });
});
