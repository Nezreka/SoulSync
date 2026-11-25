import type { ReactElement } from 'react';

import { QueryClientProvider } from '@tanstack/react-query';
import { render as rtlRender, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { createTestQueryClient } from '@/test/query-client';

const search = vi.hoisted(() => ({ value: {} as { artist?: number } }));
const asked = vi.hoisted(() => ({ list: [] as string[] }));

vi.mock('@tanstack/react-router', () => ({ useSearch: () => search.value }));
vi.mock('@/platform/shell/download-rights', () => ({ useMusicGet: () => ({ mode: 'download' }) }));
vi.mock('../-library-v2.api', () => {
  const track = (id: number, albumId: number) => ({
    track_id: id,
    title: `Song ${id}`,
    album_id: albumId,
    album_title: 'Parachutes',
    disc_number: 1,
    track_number: id,
    current: 'MP3 320kbps',
    profile: 'Lossless',
    target: 'FLAC 16-bit',
    monitored: true,
  });
  return {
    LIBRARY_V2_QUERY_KEY: ['library-v2'],
    libraryV2EnabledQueryOptions: () => ({
      queryKey: ['library-v2', 'enabled'],
      queryFn: async () => ({ canWish: true, canWrite: true, canDownload: true }),
    }),
    libraryV2EnhanceQueryOptions: (entity: string, id: number) => ({
      queryKey: ['library-v2', 'enhance', entity, id],
      queryFn: async () => {
        asked.list.push(`${entity}/${id}`);
        return entity === 'albums'
          ? { tracks: [track(1, id), track(2, id)] }
          : { tracks: [track(1, 7), track(2, 7), track(3, 8)] };
      },
    }),
    startLibraryV2Enhance: vi.fn(),
  };
});

import { AlbumUpgradeButton } from './owned-album-upgrade';

function render(ui: ReactElement) {
  return rtlRender(
    <QueryClientProvider client={createTestQueryClient()}>{ui}</QueryClientProvider>,
  );
}

beforeEach(() => {
  asked.list = [];
});
afterEach(() => {
  search.value = {};
});

it('on the album’s own page it asks for the album’s list and shows the button (#2287)', async () => {
  search.value = {}; // no artist in the address: the album page
  render(<AlbumUpgradeButton albumId={7} albumTitle="Parachutes" />);
  expect(await screen.findByRole('button', { name: 'Upgrade 2' })).toBeTruthy();
  expect(asked.list).toEqual(['albums/7']);
});

it('on an artist’s page it reads the artist’s list and keeps only this album', async () => {
  search.value = { artist: 3 };
  render(<AlbumUpgradeButton albumId={8} albumTitle="X&Y" />);
  expect(await screen.findByRole('button', { name: 'Upgrade 1' })).toBeTruthy();
  expect(asked.list).toEqual(['artists/3']);
});
