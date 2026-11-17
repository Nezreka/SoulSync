import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import { lib2Track, lib2TrackFile } from '../-library-v2.test-fixtures';
import {
  AlbumUnlockMenuItem,
  OwnedTrackMoreMenu,
  leftoverSlotKeepers,
} from './owned-artist-track-actions';

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={createTestQueryClient()}>{node}</QueryClientProvider>);
}

describe('owned artist track extras', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    window.playNext = vi.fn();
    window.addToQueue = vi.fn();
    window.showToast = vi.fn();
  });
  afterEach(() => {
    window.SoulSyncWebShellBridge = undefined;
  });

  it('queues an owned track next or last, with its lib2 ids', () => {
    const track = lib2Track({
      id: 5,
      title: 'Roads',
      file: lib2TrackFile({ path: '/m/roads.flac' }),
    });
    wrap(
      <OwnedTrackMoreMenu track={track} albumId={9} albumTitle="Dummy" artistName="Portishead" />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'More for Roads' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Play next' }));
    expect(window.playNext).toHaveBeenCalledWith(
      expect.objectContaining({ lib2_track_id: 5, lib2_album_id: 9, file_path: '/m/roads.flac' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'More for Roads' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Add to queue' }));
    expect(window.addToQueue).toHaveBeenCalledWith(expect.objectContaining({ lib2_track_id: 5 }));
  });

  it('offers re-identify to an admin and opens the shared modal', async () => {
    server.use(
      http.get('/api/reidentify/sources', () => HttpResponse.json({ success: true, sources: [] })),
    );
    const track = lib2Track({
      id: 5,
      title: 'Roads',
      file: lib2TrackFile({ path: '/m/roads.flac' }),
    });
    wrap(
      <OwnedTrackMoreMenu track={track} albumId={9} albumTitle="Dummy" artistName="Portishead" />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'More for Roads' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Re-identify…' }));
    expect(await screen.findByText('Re-identify track')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Search for the track…')).toBeInTheDocument();
  });

  it('offers "I Have This" on a missing slot instead of queue actions', () => {
    const track = lib2Track({ id: null, title: 'Glory Box', file: null });
    wrap(
      <OwnedTrackMoreMenu track={track} albumId={9} albumTitle="Dummy" artistName="Portishead" />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'More for Glory Box' }));
    expect(screen.getByRole('menuitem', { name: 'I Have This…' })).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: 'Play next' })).not.toBeInTheDocument();
  });

  it('hides admin-only items from a non-admin profile', () => {
    window.SoulSyncWebShellBridge = createShellBridge({
      getCurrentProfileContext: vi.fn(() => ({ profileId: 2, isAdmin: false })),
    });
    const missing = lib2Track({ id: null, title: 'Glory Box', file: null });
    wrap(<OwnedTrackMoreMenu track={missing} albumId={9} albumTitle="Dummy" artistName="P" />);
    expect(screen.queryByRole('button', { name: 'More for Glory Box' })).not.toBeInTheDocument();
  });

  it('shows the unlock item only for a hand-tagged album and clears the lock', async () => {
    const deletes: string[] = [];
    server.use(
      http.get('/api/album/:id/metadata-lock', ({ params }) =>
        HttpResponse.json({ success: true, metadata_locked: params.id === '7' }),
      ),
      http.delete('/api/album/:id/metadata-lock', ({ params }) => {
        deletes.push(String(params.id));
        return HttpResponse.json({ success: true });
      }),
    );
    const { unmount } = wrap(<AlbumUnlockMenuItem albumId={8} />);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole('button', { name: 'Unlock hand-tagged details' })).toBeNull();
    unmount();

    wrap(<AlbumUnlockMenuItem albumId={7} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Unlock hand-tagged details' }));
    await vi.waitFor(() => expect(deletes).toEqual(['7']));
  });
});

// #2094: a retag moved the files to new rows and left the old ones with no
// file. a row with no file had nothing to remove it by.
describe('leftover rows', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    window.showToast = vi.fn();
  });
  afterEach(() => {
    window.SoulSyncWebShellBridge = undefined;
    window.showConfirmDialog = undefined;
  });

  const file = (path: string) => lib2TrackFile({ path });
  const tracks = [
    lib2Track({ id: 1, title: 'Daylight', track_number: 2, file: file('/m/02.flac') }),
    lib2Track({
      id: 2,
      title: 'Labor Days - Daylight',
      track_number: 2,
      file: null,
      file_status: 'missing',
    }),
    // alone at its position: a missing track, not a leftover
    lib2Track({ id: 3, title: 'Bonus', track_number: 15, file: null, file_status: 'missing' }),
    // two copies with files at one position: no single keeper
    lib2Track({ id: 4, title: 'Coma', track_number: 8, file: file('/m/08a.flac') }),
    lib2Track({ id: 5, title: 'Coma', track_number: 8, file: file('/m/08b.flac') }),
    lib2Track({
      id: 6,
      title: 'Labor Days - Coma',
      track_number: 8,
      file: null,
      file_status: 'missing',
    }),
  ];

  it('pairs a row with no file with the one row at its position that has the file', () => {
    const keepers = leftoverSlotKeepers(tracks);
    expect([...keepers.entries()]).toEqual([[2, { id: 1, title: 'Daylight' }]]);
  });

  it('removes the row after the confirm modal, and not without it', async () => {
    const posts: string[] = [];
    server.use(
      http.post('/api/library/v2/tracks/:id/remove-leftover', ({ params }) => {
        posts.push(String(params.id));
        return HttpResponse.json({ success: true, kept_track_id: 1, kept_title: 'Daylight' });
      }),
    );
    const confirm = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    window.showConfirmDialog = confirm;
    wrap(
      <OwnedTrackMoreMenu
        track={tracks[1]}
        albumId={9}
        albumTitle="Labor Days"
        artistName="Aesop Rock"
        slotKeeper={{ id: 1, title: 'Daylight' }}
      />,
    );
    const open = () =>
      fireEvent.click(screen.getByRole('button', { name: 'More for Labor Days - Daylight' }));

    open();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Remove This Row…' }));
    await vi.waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect(confirm.mock.calls[0][0]).toMatchObject({ destructive: true, confirmText: 'Remove' });
    expect(posts).toEqual([]);

    open();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Remove This Row…' }));
    await vi.waitFor(() => expect(posts).toEqual(['2']));
    await vi.waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Removed "Labor Days - Daylight"', 'success'),
    );
  });

  it('offers no remove for a missing track with nothing at its position', () => {
    wrap(
      <OwnedTrackMoreMenu track={tracks[2]} albumId={9} albumTitle="Labor Days" artistName="A" />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'More for Bonus' }));
    expect(screen.getByRole('menuitem', { name: 'I Have This…' })).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: 'Remove This Row…' })).toBeNull();
  });
});
