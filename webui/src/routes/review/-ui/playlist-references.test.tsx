import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { PlaylistReferences } from './playlist-references';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('links to the actual playlist and rechecks without changing the saved action', async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        playlists: [
          { id: 'p1', title: '88 Keys', url: 'https://music.example/app/#/playlist/p1/show' },
        ],
      }),
    })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ playlists: [] }) });
  vi.stubGlobal('fetch', fetch);
  const refresh = vi.fn();
  render(
    <PlaylistReferences
      caseId="case"
      path="/music/01.flac"
      removal
      lastError="This copy is in a playlist"
      onRecheck={refresh}
    />,
  );
  expect(await screen.findByRole('link', { name: '88 Keys ↗' })).toHaveAttribute(
    'href',
    'https://music.example/app/#/playlist/p1/show',
  );
  expect(screen.getByText('Removal blocked by playlist references')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Recheck references' }));
  expect(await screen.findByText('No playlist references remain for this copy.')).toBeVisible();
  expect(refresh).toHaveBeenCalledOnce();
  expect(fetch.mock.calls[1][1]).toMatchObject({
    method: 'POST',
    body: JSON.stringify({ path: '/music/01.flac' }),
  });
});

it('does not clear playlist warnings when a recheck fails', async () => {
  const fetch = vi
    .fn()
    .mockResolvedValue({ ok: false, json: async () => ({ error: 'Server offline' }) });
  vi.stubGlobal('fetch', fetch);
  const refresh = vi.fn();
  render(
    <PlaylistReferences
      caseId="case"
      path="/music/01.flac"
      removal
      names={['88 Keys']}
      onRecheck={refresh}
    />,
  );
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Recheck references' }));
  expect(await screen.findByText('Server offline')).toBeVisible();
  expect(screen.getByText('88 Keys')).toBeVisible();
  expect(refresh).not.toHaveBeenCalled();
});
