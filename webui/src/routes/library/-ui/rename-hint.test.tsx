import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { fetchLibraryV2ArtistRenameCheck } from '../-library-v2.api';
import { AlbumRenameBadge } from './rename-hint';

vi.mock('./reorganize-modal', () => ({
  AlbumReorganizeModal: ({ albumTitle, onClose }: { albumTitle: string; onClose: () => void }) => (
    <div role="dialog" aria-label={`Preview ${albumTitle}`}>
      <button type="button" onClick={onClose}>
        Cancel
      </button>
    </div>
  ),
}));

function slices(pages: Record<string, object>) {
  const starts: string[] = [];
  server.use(
    http.get('/api/library/v2/artists/7/rename-check', ({ request }) => {
      const start = new URL(request.url).searchParams.get('start') ?? '0';
      starts.push(start);
      return HttpResponse.json({ success: true, ...pages[start] });
    }),
  );
  return starts;
}

describe('#2141 files that are not where the naming template puts them', () => {
  it('walks every slice the server plans and adds them up', async () => {
    const starts = slices({
      '0': { enabled: true, albums: { '11': 2 }, tracks: [101, 102], next: 1, total: 2 },
      '1': { enabled: true, albums: { '12': 0 }, tracks: [], next: null, total: 2 },
    });
    expect(await fetchLibraryV2ArtistRenameCheck(7)).toEqual({
      enabled: true,
      albums: { '11': 2, '12': 0 },
      tracks: [101, 102],
    });
    expect(starts).toEqual(['0', '1']);
  });

  it('shows the count on the album row and opens its preview without touching the row', async () => {
    slices({
      '0': { enabled: true, albums: { '11': 3 }, tracks: [1, 2, 3], next: null, total: 1 },
    });
    const rowClick = vi.fn();
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <div onClick={rowClick}>
          <AlbumRenameBadge artistId={7} albumId={11} albumTitle="Views" />
          <AlbumRenameBadge artistId={7} albumId={12} albumTitle="Scorpion" />
        </div>
      </QueryClientProvider>,
    );
    const badge = await screen.findByRole('button', { name: '3 to rename' });
    expect(badge.title).toBe(
      '3 files are not where your naming template puts them. Click to preview the rename.',
    );
    // an album with nothing to move shows nothing
    expect(screen.getAllByRole('button')).toHaveLength(1);
    fireEvent.click(badge);
    const dialog = await screen.findByRole('dialog', { name: 'Preview Views' });
    fireEvent.click(dialog.querySelector('button')!);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(rowClick).not.toHaveBeenCalled();
  });

  it('shows nothing while organizing is off or the template does not render', async () => {
    const starts = slices({
      '0': { enabled: false, reason: 'off', albums: {}, tracks: [], next: null, total: 0 },
    });
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <AlbumRenameBadge artistId={7} albumId={11} albumTitle="Views" />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(starts).toEqual(['0']));
    expect(screen.queryByRole('button')).toBeNull();
  });
});
