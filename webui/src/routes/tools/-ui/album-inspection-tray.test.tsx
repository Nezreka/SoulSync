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

const reviewFinding: RepairFinding = {
  id: 1,
  job_id: 'fake_lossless_detector',
  finding_type: 'fake_lossless',
  severity: 'warning',
  status: 'pending',
  title: 'Review this spectral result',
  entity_type: 'track',
  entity_id: 'lib2:7',
  details: { album_title: 'Album', artist_name: 'Artist', track_title: 'Song' },
};

const fixableFinding: RepairFinding = {
  ...reviewFinding,
  id: 2,
  job_id: 'lyrics_fetcher',
  finding_type: 'missing_lyrics',
  title: 'Missing lyrics',
};

function renderTray(items: RepairFinding[]) {
  const posted: string[] = [];
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
      onInspectRedownload={vi.fn()}
      onRefresh={vi.fn()}
    />,
  );
  return posted;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('keeps fake-lossless findings review-only in the album inspector', async () => {
  renderTray([reviewFinding]);
  expect(await screen.findByText('Spectral Transcode:')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Re-download' })).not.toBeInTheDocument();
});

it('resolves actionable album findings while leaving fake-lossless review pending', async () => {
  const posted = renderTray([reviewFinding, fixableFinding]);
  await screen.findByRole('button', { name: 'Apply Lyrics' });
  fireEvent.click(screen.getByRole('button', { name: 'Resolve Album' }));
  await waitFor(() => expect(posted).toEqual(['/api/repair/findings/2/fix']));
  expect(screen.getByText('Spectral Transcode:')).toBeInTheDocument();
});
