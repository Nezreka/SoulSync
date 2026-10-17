import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { FindingAlbumGroup } from '../-tools.api';
import type { RepairFinding } from '../-tools.types';

import { AlbumInspectionTray } from './album-inspection-tray';

const group: FindingAlbumGroup = {
  group_by: 'album',
  key: 'artist-album',
  artist: 'Artist',
  album: 'Album',
  count: 2,
  worst_score: null,
  best_score: null,
  worst_quality: '',
  best_quality: '',
  album_thumb_url: null,
  artist_thumb_url: null,
  artist_id: null,
  first_seen: null,
  last_seen: null,
};

// The detector names the FILE (`lib2:<file id>`); its track rides in the
// Library v2 subject details.
const fakeLossless: RepairFinding = {
  id: 1,
  job_id: 'fake_lossless_detector',
  finding_type: 'fake_lossless',
  severity: 'warning',
  status: 'pending',
  title: 'Possible fake lossless',
  entity_type: 'file',
  entity_id: 'lib2:31',
  details: {
    album_title: 'Album',
    artist_name: 'Artist',
    track_title: 'Song',
    library_v2: { file_id: 31, track_id: 7 },
  },
};

const fixableFinding: RepairFinding = {
  ...fakeLossless,
  id: 2,
  job_id: 'lyrics_fetcher',
  finding_type: 'missing_lyrics',
  title: 'Missing lyrics',
};

function renderTray(items: RepairFinding[]) {
  const posted: string[] = [];
  const onInspectRedownload = vi.fn();
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      if (options?.method === 'POST') posted.push(url);
      return {
        ok: true,
        json: async () =>
          options?.method === 'POST' ? { success: true } : { items, total: items.length, page: 0 },
      } as Response;
    }),
  );
  render(
    <AlbumInspectionTray
      group={group}
      status="pending"
      onClose={vi.fn()}
      onFixFinding={vi.fn()}
      onDismissFinding={vi.fn()}
      onInspectRedownload={onInspectRedownload}
      onRefresh={vi.fn()}
    />,
  );
  return { posted, onInspectRedownload };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('opens the upgrade search for a fake-lossless file that has a catalogue track', async () => {
  const { onInspectRedownload } = renderTray([fakeLossless]);
  expect(await screen.findByText('Spectral Transcode:')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Re-download' }));
  expect(onInspectRedownload).toHaveBeenCalledWith(fakeLossless);
});

it('offers only a delete for a fake-lossless file outside the catalogue', async () => {
  renderTray([{ ...fakeLossless, entity_id: null, details: { album_title: 'Album' } }]);
  expect(await screen.findByRole('button', { name: 'Delete File' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Re-download' })).not.toBeInTheDocument();
});

it('asks before resolving an album whose fake-lossless files will be moved aside', async () => {
  const confirm = vi.fn(async () => true);
  vi.stubGlobal('showConfirmDialog', confirm);
  const { posted } = renderTray([fakeLossless, fixableFinding]);
  await screen.findByRole('button', { name: 'Apply Lyrics' });
  fireEvent.click(screen.getByRole('button', { name: 'Resolve Album' }));
  await waitFor(() =>
    expect(posted).toEqual(['/api/repair/findings/1/fix', '/api/repair/findings/2/fix']),
  );
  expect(confirm).toHaveBeenCalledTimes(1);
});
