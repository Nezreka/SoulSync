/**
 * The Rotating row in a playlist's settings (#2039).
 *
 * The row is the only way in to a setting that deletes files, so the tests
 * pin where it is offered, that it shows what it will do, and that flipping
 * it only asks the tab (which owns the confirm).
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { MirroredPlaylistRow } from '../-sync.mirrored';

import { PlaylistSettingsSheet } from './playlist-settings-sheet';

const preview = {
  rotating: true,
  allowed: true,
  cleaner_enabled: true,
  maintenance_enabled: true,
  dry_run: true,
  rotation_grace: '1d',
  membership_known: true,
  downloads: 20,
  left: 14,
  removing: 9,
  kept: 5,
  kept_reasons: { played: 5 },
};

function stubPreview(body: object = preview) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => body });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderSheet(over: Partial<MirroredPlaylistRow> = {}, withRotating = true) {
  const onToggleRotating = vi.fn();
  const row: MirroredPlaylistRow = { id: 7, name: 'Mix', source: 'spotify', ...over };
  render(
    <PlaylistSettingsSheet
      row={row}
      name="Mix"
      sourceLabel="Spotify"
      scheduleLabel="Not scheduled"
      onRename={vi.fn()}
      onToggleOrganize={vi.fn()}
      onToggleRotating={withRotating ? onToggleRotating : undefined}
      onSetSyncMode={vi.fn()}
      onDelete={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  return { onToggleRotating };
}

afterEach(() => vi.unstubAllGlobals());

describe('the Rotating row', () => {
  it('shows what has left and what will be removed', async () => {
    const fetchMock = stubPreview();
    renderSheet({ rotating: true });
    const toggle = screen.getByLabelText('Rotating') as HTMLInputElement;
    expect(toggle.checked).toBe(true);
    await waitFor(() =>
      expect(screen.getByTestId('rotation-summary').textContent).toBe(
        '14 downloaded songs have left · 9 will be removed · 5 kept (played)',
      ),
    );
    expect(fetchMock).toHaveBeenCalledWith('/api/mirrored-playlists/7/rotation');
    expect(screen.getByTestId('rotation-status').textContent).toMatch(/Dry run is on/);
    expect(screen.getByText('Cleaner settings').getAttribute('href')).toBe(
      '/tools?job=expired_download_cleaner',
    );
  });

  it('previews before it is on, with no status line', async () => {
    stubPreview({ ...preview, rotating: false });
    renderSheet({ rotating: false });
    expect((screen.getByLabelText('Rotating') as HTMLInputElement).checked).toBe(false);
    await waitFor(() =>
      expect(screen.getByTestId('rotation-summary').textContent).toContain('9 would be removed'),
    );
    expect(screen.queryByTestId('rotation-status')).toBeNull();
  });

  it('hands the flip to the tab and does not move on its own', () => {
    stubPreview();
    const { onToggleRotating } = renderSheet({ rotating: false });
    const toggle = screen.getByLabelText('Rotating') as HTMLInputElement;
    fireEvent.click(toggle);
    expect(onToggleRotating).toHaveBeenCalledWith(true);
    // the row drives it: a cancelled confirm leaves it off
    expect(toggle.checked).toBe(false);
  });

  it('still renders when the preview fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    renderSheet({ rotating: true });
    expect(screen.getByLabelText('Rotating')).toBeTruthy();
    await Promise.resolve();
    expect(screen.queryByTestId('rotation-summary')).toBeNull();
  });

  it('is not offered on a request list', () => {
    const fetchMock = stubPreview();
    renderSheet({ is_request_list: true });
    expect(screen.queryByLabelText('Rotating')).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('is not offered on Liked Songs, but is on any other playlist of yours', () => {
    stubPreview();
    renderSheet({ source: 'soulsync', source_playlist_id: 'liked' });
    expect(screen.queryByLabelText('Rotating')).toBeNull();
  });

  it('is offered on a playlist you made', () => {
    stubPreview();
    renderSheet({ source: 'soulsync', source_playlist_id: 'mine-1' });
    expect(screen.getByLabelText('Rotating')).toBeTruthy();
  });

  it('is absent when the page gives it no handler', () => {
    stubPreview();
    renderSheet({}, false);
    expect(screen.queryByLabelText('Rotating')).toBeNull();
  });
});
