/**
 * #2235 (carlosjfcasero): "167 tracks · 167 in library" over a 149-track
 * jellyfin playlist, and nothing said why. in the library and on the server
 * playlist are two answers; the modal now gives both, says why each missing
 * one isn't there, and offers to sync again.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { placementChip, placementSegments, serverName } from '../-sync.mirrored';
import { MirroredDetailModal } from './mirrored-detail-modal';

const NOW = Date.UTC(2026, 10, 21, 12, 0, 0);

describe('placement helpers', () => {
  it('names the server, or says "your server"', () => {
    expect(serverName('jellyfin')).toBe('Jellyfin');
    expect(serverName('NAVIDROME')).toBe('Navidrome');
    expect(serverName(null)).toBe('your server');
  });

  it('only the ones that did not make it get a part, in a fixed order', () => {
    const segs = placementSegments(
      { on_playlist: 149, duplicate: 3, not_on_server: 15 },
      'jellyfin',
    );
    expect(segs.map((s) => `${s.count} ${s.label}`)).toEqual([
      '15 not on the Jellyfin playlist',
      '3 share a file with another track',
    ]);
    expect(placementSegments({ on_playlist: 167 }, 'jellyfin')).toEqual([]);
    expect(placementSegments(undefined, 'jellyfin')).toEqual([]);
  });

  it('a row chip only for an in-library track the playlist does not hold', () => {
    expect(
      placementChip({ status: 'in_library', server_placement: 'not_on_server' }, 'jellyfin')?.label,
    ).toBe('Not on Jellyfin yet');
    expect(
      placementChip(
        { status: 'in_library', server_placement: 'duplicate', server_placement_with: 'A - B' },
        'plex',
      ),
    ).toEqual({ label: 'Same file as another track', title: 'On the playlist once, as A - B' });
    expect(
      placementChip({ status: 'in_library', server_placement: 'on_playlist' }, 'plex'),
    ).toBeNull();
    expect(
      placementChip({ status: 'missing', server_placement: 'not_on_server' }, 'plex'),
    ).toBeNull();
  });
});

const tracks = [
  {
    position: 1,
    track_name: 'La Vaca Lola',
    artist_name: 'La Vaca Lola',
    status: 'in_library',
    server_placement: 'on_playlist',
  },
  {
    position: 2,
    track_name: 'Pollito Pio',
    artist_name: 'Pollito',
    status: 'in_library',
    server_placement: 'not_on_server',
  },
  {
    position: 3,
    track_name: 'La Vaca Lola (Remix)',
    artist_name: 'La Vaca Lola',
    status: 'in_library',
    server_placement: 'duplicate',
    server_placement_with: 'La Vaca Lola - La Vaca Lola',
  },
] as const;

function renderModal(placement_counts?: Record<string, number>) {
  const onRunPipeline = vi.fn();
  render(
    <MirroredDetailModal
      playlistId={6}
      data={{
        name: 'Eva',
        source: 'spotify_public',
        tracks: tracks.map((t) => ({ ...t })),
        status_counts: { in_library: 3 },
        placement_counts,
        server_type: 'jellyfin',
      }}
      now={NOW}
      onClose={vi.fn()}
      onDelete={vi.fn()}
      onEditSource={vi.fn()}
      onRunPipeline={onRunPipeline}
      onDiscover={vi.fn()}
      onRefresh={vi.fn()}
    />,
  );
  return { onRunPipeline };
}

const rowTitles = () =>
  Array.from(document.querySelectorAll('.mm-row .mm-row-title')).map((e) => e.textContent);

describe('the detail modal', () => {
  it('says in library and not on the playlist apart, and filters by it', () => {
    renderModal({ on_playlist: 1, not_on_server: 1, duplicate: 1 });
    expect(screen.getByRole('button', { name: /3 in library/ })).toBeTruthy();
    const seg = screen.getByRole('button', { name: /not on the Jellyfin playlist/ });
    fireEvent.click(seg);
    expect(rowTitles()).toEqual(['Pollito Pio']);
    fireEvent.click(seg);
    expect(rowTitles()).toHaveLength(3);
    fireEvent.click(screen.getByRole('button', { name: /a file with another track/ }));
    expect(rowTitles()).toEqual(['La Vaca Lola (Remix)']);
  });

  it('chips the rows that did not make it, and the healthy one says nothing', () => {
    renderModal({ on_playlist: 1, not_on_server: 1, duplicate: 1 });
    const chips = Array.from(document.querySelectorAll('.mm-row-st')).map((e) => e.textContent);
    expect(chips).toEqual(['Not on Jellyfin yet', 'Same file as another track']);
  });

  it('Sync again runs the sync', () => {
    const { onRunPipeline } = renderModal({ on_playlist: 1, not_on_server: 1, duplicate: 1 });
    fireEvent.click(screen.getByRole('button', { name: 'Sync again' }));
    expect(onRunPipeline).toHaveBeenCalledTimes(1);
  });

  it('no extra line when every track made it, and no Sync again for duplicates alone', () => {
    renderModal({ on_playlist: 3 });
    expect(screen.queryByRole('group', { name: /not on the server playlist/ })).toBeNull();
    document.body.innerHTML = '';
    renderModal({ on_playlist: 2, duplicate: 1 });
    expect(screen.queryByRole('button', { name: 'Sync again' })).toBeNull();
  });
});

describe('Find & Add', () => {
  it('shows a track the server has no id for, but it cannot be picked', async () => {
    const { ServerSearchOverlay } = await import('./server-search-overlay');
    const { waitFor } = await import('@testing-library/react');
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        json: async () => ({
          success: true,
          tracks: [
            { id: 'jf-1', title: 'Pollito Pio', on_server: true },
            { id: 18, title: 'La Vaca Lola', on_server: false },
          ],
        }),
      })),
    );
    const onSelect = vi.fn(async () => true);
    render(
      <ServerSearchOverlay
        track={{ source_track: { name: 'La Vaca Lola', artist: 'La Vaca Lola' } } as never}
        mode="add"
        onClose={vi.fn()}
        onSelect={onSelect}
      />,
    );
    const off = await waitFor(() => screen.getByRole('button', { name: 'Not on server yet' }));
    expect((off as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(off.closest('.server-search-result') as HTMLElement);
    expect(onSelect).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Select' }));
    expect(onSelect).toHaveBeenCalledWith('jf-1', expect.any(Function));
    vi.unstubAllGlobals();
  });
});
