import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import {
  OwnedArtistDiscographyButton,
  preferredProviderIdentity,
} from './owned-artist-discography';

function renderButton(providerIds: Record<string, string>) {
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <OwnedArtistDiscographyButton
        artistId={3}
        artistName="Portishead"
        imageUrl=""
        providerIds={providerIds}
      />
    </QueryClientProvider>,
  );
}

describe('owned artist discography modal', () => {
  let detailRequests: URL[];
  beforeEach(() => {
    window.showToast = vi.fn();
    detailRequests = [];
    server.use(
      http.get('/api/artist-detail/:id', ({ request }) => {
        detailRequests.push(new URL(request.url));
        return HttpResponse.json({
          success: true,
          artist: { id: 'sp-1', name: 'Portishead' },
          discography: {
            source: 'spotify',
            albums: [{ id: 'al-1', name: 'Dummy', track_count: 11 }],
            singles: [{ id: 'si-1', name: 'Sour Times', track_count: 2 }],
          },
        });
      }),
      http.get('/api/library/artist/:id/enhanced', () =>
        HttpResponse.json({ success: true, artist: { spotify_artist_id: 'sp-1' } }),
      ),
      http.post('/api/watchlist/check', () =>
        HttpResponse.json({ success: true, is_watching: false }),
      ),
    );
  });
  afterEach(() => {
    delete (window as unknown as { canDownload?: unknown }).canDownload;
  });

  it('picks the provider identity in dev order', () => {
    expect(preferredProviderIdentity({ deezer: '9', spotify: 'sp' })).toEqual({
      source: 'spotify',
      id: 'sp',
    });
    expect(preferredProviderIdentity({ itunes: '5' })).toEqual({ source: 'itunes', id: '5' });
    expect(preferredProviderIdentity({})).toBeNull();
  });

  it('opens dev’s discography modal with the provider’s full release list', async () => {
    renderButton({ spotify: 'sp-1' });
    fireEvent.click(screen.getByRole('button', { name: /Download Discography/ }));

    expect(await screen.findByText('Dummy')).toBeInTheDocument();
    expect(screen.getByText('Sour Times')).toBeInTheDocument();
    expect(detailRequests[0]?.pathname).toBe('/api/artist-detail/sp-1');
    expect(detailRequests[0]?.searchParams.get('source')).toBe('spotify');
  });

  it('says request discography for a profile that cannot download', () => {
    (window as unknown as { canDownload: () => boolean }).canDownload = () => false;
    renderButton({ deezer: '42' });
    expect(screen.getByRole('button', { name: /Request Discography/ })).toBeInTheDocument();
  });

  it('hides itself when the artist has no provider identity', () => {
    renderButton({});
    expect(screen.queryByRole('button', { name: /Discography/ })).not.toBeInTheDocument();
  });
});
