import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { AlbumArtPickerModal, ArtistImagePickerModal, artAppliedMessage } from './art-picker-modal';

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

  it('a pasted image URL is applied, and the toast says where it went', async () => {
    // third #1062 review G14-2 / G14-3: 3.6.1's picker had a paste field and
    // said what got updated; v2 dropped both
    const applied: unknown[] = [];
    server.use(
      http.get('/api/library/v2/albums/5/art-options', () =>
        HttpResponse.json({ success: true, count: 0, candidates: [] }),
      ),
      http.post('/api/library/v2/albums/5/art', async ({ request }) => {
        applied.push(await request.json());
        return HttpResponse.json({
          success: true,
          image_url: '/api/library/v2/artwork/album/5',
          server_updated: true,
          cover_written: true,
        });
      }),
    );
    const toast = vi.fn();
    window.showToast = toast;
    const onClose = vi.fn();
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <AlbumArtPickerModal albumId={5} albumTitle="Views" onClose={onClose} />
      </QueryClientProvider>,
    );
    fireEvent.change(await screen.findByLabelText('Image URL for the cover'), {
      target: { value: 'https://example.com/mine.jpg' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Use this URL' }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(applied).toEqual([{ url: 'https://example.com/mine.jpg' }]);
    expect(toast).toHaveBeenCalledWith(
      'Cover updated in SoulSync, on your media server, as cover.jpg in the album folder',
      'success',
    );
    window.showToast = undefined;
  });

  it('a candidate whose image never loads drops out', async () => {
    server.use(
      http.get('/api/library/v2/artists/8/art-options', () =>
        HttpResponse.json({
          success: true,
          count: 2,
          candidates: [
            { source: 'deezer', url: 'https://dz/ok.jpg' },
            { source: 'itunes', url: 'https://it/dead.jpg' },
          ],
        }),
      ),
    );
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <ArtistImagePickerModal artistId={8} artistName="Adele" onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    fireEvent.error(await screen.findByAltText('Photo option from itunes'));
    await waitFor(() => expect(screen.queryByAltText('Photo option from itunes')).toBeNull());
    expect(screen.getByAltText('Photo option from deezer')).toBeTruthy();
  });

  it('says a folder was not found instead of claiming a write', () => {
    expect(
      artAppliedMessage({ image_url: 'x', disk_skipped: 'No artist folder found.' }, 'photo'),
    ).toBe('Photo updated in SoulSync. No artist folder found.');
  });
});
