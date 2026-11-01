import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { openProviderRelease, ReleaseCardExtras } from './owned-artist-release-cards';

const artist = { id: 'sp-a', name: 'Portishead', imageUrl: '', source: 'spotify' };
const gapCard = {
  providerId: 'dz-77',
  gapSource: 'deezer',
  title: 'Roseland NYC Live',
  albumType: 'album',
  imageUrl: '',
  totalTracks: 11,
  ownedTracks: 0,
};

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={createTestQueryClient()}>{node}</QueryClientProvider>);
}

describe('owned release cards', () => {
  let trackRequests: URL[];
  beforeEach(() => {
    trackRequests = [];
    window.showToast = vi.fn();
    window.showLoadingOverlay = vi.fn();
    window.hideLoadingOverlay = vi.fn();
    window.openDownloadMissingModalForArtistAlbum = vi.fn();
    window.registerArtistDownload = vi.fn();
    window.playTrackList = vi.fn();
    window.open = vi.fn() as unknown as typeof window.open;
    server.use(
      http.get('/api/album/:id/tracks', ({ request }) => {
        trackRequests.push(new URL(request.url));
        return HttpResponse.json({
          success: true,
          album: { album_type: 'album', total_tracks: 11 },
          tracks: [{ id: 't1', name: 'Humming' }],
        });
      }),
      http.post('/api/library/check-tracks', () =>
        HttpResponse.json({ success: true, owned_tracks: {} }),
      ),
    );
  });

  it('opens a "+ other sources" card in the download modal from its own source', async () => {
    await openProviderRelease(gapCard, artist);

    expect(trackRequests[0]?.pathname).toBe('/api/album/dz-77/tracks');
    expect(trackRequests[0]?.searchParams.get('source')).toBe('deezer');
    expect(window.openDownloadMissingModalForArtistAlbum).toHaveBeenCalledWith(
      'artist_album_sp-a_dz-77',
      '[Portishead] Roseland NYC Live',
      [{ id: 't1', name: 'Humming' }],
      expect.objectContaining({ id: 'dz-77' }),
      expect.objectContaining({ name: 'Portishead', source: 'deezer' }),
      false,
    );
  });

  it('plays a provider card through the shared player', async () => {
    wrap(<ReleaseCardExtras card={gapCard} artist={artist} />);
    fireEvent.click(screen.getByRole('button', { name: 'Play Roseland NYC Live' }));
    await waitFor(() => expect(window.playTrackList).toHaveBeenCalled());
  });

  it('links a release with a musicbrainz id', () => {
    wrap(
      <ReleaseCardExtras
        card={{ ...gapCard, albumId: 5, ownedTracks: 0, musicbrainzId: 'mb-1' }}
        artist={artist}
      />,
    );
    // a catalogue card with nothing on disk has no play button
    expect(screen.queryByRole('button', { name: /Play/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('link'));
    expect(window.open).toHaveBeenCalledWith('https://musicbrainz.org/release/mb-1', '_blank');
  });
});
