import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { AlbumVersionsModal, describeVersionPreview, versionFacts } from './album-versions-modal';

const VERSION = {
  source: 'musicbrainz',
  id: 'mb-17',
  title: 'Good Time',
  date: '2008-03-04',
  year: 2008,
  track_count: 17,
  label: 'Arista Nashville',
  country: 'US',
  format: 'CD',
  disambiguation: null,
  status: 'Official',
  album_type: 'album',
  image_url: null,
  current: false,
};

const LIST = {
  success: true,
  album: {
    id: 7,
    title: 'Good Time',
    artist_name: 'Alan Jackson',
    track_count: 34,
    release_ids: { spotify: 'sp-34' },
    pinned: null,
  },
  groups: [
    {
      source: 'spotify',
      label: 'Spotify',
      versions: [{ ...VERSION, source: 'spotify', id: 'sp-34', track_count: 34, current: true }],
    },
    { source: 'musicbrainz', label: 'MusicBrainz', versions: [VERSION] },
  ],
};

const PREVIEW = {
  success: true,
  source: 'musicbrainz',
  release_id: 'mb-17',
  track_count_before: 34,
  track_count_after: 17,
  owned_count: 1,
  mapped_count: 1,
  unmapped_count: 0,
  pairings: [
    {
      local_title: 'Good Time',
      local_track_number: 1,
      target_title: 'Good Time',
      target_track_number: 1,
      matched_by: 'position',
      mapped: true,
    },
  ],
  tracks: [{ title: 'Good Time', track_number: 1, disc_number: 1 }],
  held_by: null,
};

afterEach(() => {
  delete window.showConfirmDialog;
});

describe('#2145 other versions', () => {
  it('reads a version the way picard lists it', () => {
    expect(versionFacts(VERSION)).toBe('17 tracks · 2008-03-04 · US · CD · Arista Nashville');
    expect(describeVersionPreview(PREVIEW)).toBe(
      '34 tracks now, 17 on this version. All 1 of your files have a place on it.',
    );
  });

  it('lists versions with track counts, previews one and switches after the confirm', async () => {
    const applied = vi.fn();
    server.use(
      http.get('/api/library/v2/albums/7/versions', () => HttpResponse.json(LIST)),
      http.post('/api/library/v2/albums/7/versions/preview', () => HttpResponse.json(PREVIEW)),
      http.post('/api/library/v2/albums/7/versions/apply', async ({ request }) => {
        applied(await request.json());
        return HttpResponse.json({ success: true });
      }),
    );
    const confirm = vi.fn().mockResolvedValue(true);
    window.showConfirmDialog = confirm;
    const onClose = vi.fn();
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <AlbumVersionsModal
          albumId={7}
          albumTitle="Good Time"
          artistName="Alan Jackson"
          onClose={onClose}
        />
      </QueryClientProvider>,
    );

    expect(await screen.findByText(/34 tracks · 2008-03-04/)).toBeInTheDocument();
    expect(screen.getByText('Current')).toBeInTheDocument();
    // the current version has nothing to switch to
    expect(screen.getAllByRole('button', { name: 'Preview' })).toHaveLength(1);

    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    expect(await screen.findByText(/34 tracks now, 17 on this version/)).toBeInTheDocument();
    expect(applied).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Switch to this version' }));
    await waitFor(() =>
      expect(applied).toHaveBeenCalledWith({ source: 'musicbrainz', release_id: 'mb-17' }),
    );
    expect(confirm).toHaveBeenCalledOnce();
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('writes nothing when the confirm is declined', async () => {
    const applied = vi.fn();
    server.use(
      http.get('/api/library/v2/albums/7/versions', () => HttpResponse.json(LIST)),
      http.post('/api/library/v2/albums/7/versions/preview', () => HttpResponse.json(PREVIEW)),
      http.post('/api/library/v2/albums/7/versions/apply', () => {
        applied();
        return HttpResponse.json({ success: true });
      }),
    );
    window.showConfirmDialog = vi.fn().mockResolvedValue(false);
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <AlbumVersionsModal
          albumId={7}
          albumTitle="Good Time"
          artistName="Alan Jackson"
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Preview' }));
    await screen.findByText(/17 on this version/);
    fireEvent.click(screen.getByRole('button', { name: 'Switch to this version' }));
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(applied).not.toHaveBeenCalled();
  });
});
