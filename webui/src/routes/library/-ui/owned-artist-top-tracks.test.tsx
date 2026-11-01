import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createShellBridge } from '@/test/shell-bridge';

import { OwnedTopTrackActions, OwnedTopTracksDownloadAll } from './owned-artist-top-tracks';

const track = {
  id: 'sp-t1',
  name: 'Roads',
  artists: [{ name: 'Portishead' }],
  album: { name: 'Dummy' },
};

describe('owned top tracks actions', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    window.showToast = vi.fn();
  });
  afterEach(() => {
    window.SoulSyncWebShellBridge = undefined;
    delete (window as unknown as { canDownload?: unknown }).canDownload;
  });

  it('plays a top track through the library-first player', async () => {
    const resolves: URL[] = [];
    server.use(
      http.get('/api/stats/resolve-track', ({ request }) => {
        resolves.push(new URL(request.url));
        return HttpResponse.json({
          success: true,
          track: { id: 1, file_path: '/m/roads.flac', title: 'Roads' },
        });
      }),
      http.post('/api/stats/resolve-track', async ({ request }) => {
        resolves.push(new URL(request.url));
        return HttpResponse.json({
          success: true,
          track: { id: 1, file_path: '/m/roads.flac', title: 'Roads' },
        });
      }),
    );
    render(
      <OwnedTopTrackActions track={track} artistName="Portishead" artistId="sp-a" downloadable />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Play Roads' }));
    await waitFor(() => expect(resolves.length).toBeGreaterThan(0));
  });

  it('adds a top track to the wishlist for real, or requests it', async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post('/api/add-album-to-wishlist', async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ success: true });
      }),
    );
    render(
      <OwnedTopTrackActions track={track} artistName="Portishead" artistId="sp-a" downloadable />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Add to wishlist Roads' }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({ source_type: 'top_tracks', track: { name: 'Roads' } });
  });

  it('says Request for a profile that cannot download', () => {
    (window as unknown as { canDownload: () => boolean }).canDownload = () => false;
    render(
      <>
        <OwnedTopTrackActions track={track} artistName="Portishead" artistId="sp-a" downloadable />
        <OwnedTopTracksDownloadAll
          tracks={[track]}
          source="spotify"
          artistName="Portishead"
          artistId="sp-a"
        />
      </>,
    );
    expect(screen.getByRole('button', { name: 'Request Roads' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Request All' })).toBeInTheDocument();
  });

  it('opens the shared download modal for the whole list', () => {
    const open = vi.fn();
    window.openDownloadMissingModalForArtistAlbum = open;
    render(
      <OwnedTopTracksDownloadAll
        tracks={[track]}
        source="spotify"
        artistName="Portishead"
        artistId="sp-a"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Download All' }));
    expect(open).toHaveBeenCalledWith(
      'top_tracks_spotify_sp-a',
      'Portishead — Top Tracks',
      [track],
      expect.objectContaining({ album_type: 'compilation' }),
      expect.anything(),
      true,
      'playlist',
    );
  });

  it('keeps last.fm rows to play and add to playlist only', () => {
    render(
      <OwnedTopTrackActions
        track={track}
        artistName="Portishead"
        artistId={null}
        downloadable={false}
      />,
    );
    expect(screen.getByRole('button', { name: 'Play Roads' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /wishlist/ })).not.toBeInTheDocument();
  });
});
