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

interface TrayFetch {
  posted: string[];
  bodies: unknown[];
  listUrls: string[];
}

function renderTray(
  items: RepairFinding[],
  opts: {
    types?: unknown[];
    trayGroup?: FindingAlbumGroup;
    actionResult?: Record<string, unknown>;
  } = {},
) {
  const calls: TrayFetch = { posted: [], bodies: [], listUrls: [] };
  const onInspectRedownload = vi.fn();
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      if (options?.method === 'POST') {
        calls.posted.push(url);
        calls.bodies.push(JSON.parse(String(options.body ?? '{}')));
        return {
          ok: true,
          json: async () => opts.actionResult ?? { success: true, fixed: 1, updated: 1 },
        } as Response;
      }
      if (url.includes('/finding-types')) {
        return { ok: true, json: async () => ({ types: opts.types ?? [] }) } as Response;
      }
      calls.listUrls.push(url);
      return {
        ok: true,
        json: async () => ({ items, total: items.length, page: 0 }),
      } as Response;
    }),
  );
  render(
    <AlbumInspectionTray
      group={opts.trayGroup ?? group}
      status="pending"
      onClose={vi.fn()}
      onFixFinding={vi.fn()}
      onDismissFinding={vi.fn()}
      onInspectRedownload={onInspectRedownload}
      onRefresh={vi.fn()}
    />,
  );
  return { ...calls, onInspectRedownload };
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
  const { posted, bodies } = renderTray([fakeLossless, fixableFinding]);
  await screen.findByRole('button', { name: 'Apply Lyrics' });
  fireEvent.click(screen.getByRole('button', { name: 'Resolve Album' }));
  // review 5 W3-TU-1: one server-scoped album call replaced the per-id fix
  // loop, so the server decides which of these ids are this album's
  await waitFor(() => expect(posted).toEqual(['/api/repair/findings/album-action']));
  expect(confirm).toHaveBeenCalledTimes(1);
  expect(bodies[0]).toMatchObject({
    action: 'fix',
    group_by: 'album',
    artist: 'Artist',
    album: 'Album',
    ids: [1, 2],
  });
});

// review 5 W3-TU-1: a short title ("FM") used to be a substring search, and
// when the album's own rows were not in the first 100 hits the tray showed,
// fixed and dismissed the unrelated ones
const fm: FindingAlbumGroup = { ...group, key: 'fm', artist: 'Makeup And Vanity Set', album: 'FM' };

it('asks the server for the album by its group key, never by a title search', async () => {
  const { listUrls } = renderTray([], { trayGroup: fm });
  expect(await screen.findByText(/No issues found on this album/)).toBeInTheDocument();
  expect(listUrls).toHaveLength(1);
  const params = new URL(listUrls[0], 'http://x').searchParams;
  expect(params.get('q')).toBeNull();
  expect(params.get('group_by')).toBe('album');
  expect(params.get('group_artist')).toBe('Makeup And Vanity Set');
  expect(params.get('group_album')).toBe('FM');
});

it('sends album actions through the server group guard, never per id', async () => {
  const other: RepairFinding = {
    ...fixableFinding,
    id: 9,
    title: 'Other album',
    details: { album_title: 'FM Radio', artist_name: 'Someone Else', track_title: 'Elsewhere' },
  };
  // a server that answers rows the tray did not ask for (an old search
  // page) must not reach the album actions either
  const { posted, bodies } = renderTray([other], { trayGroup: fm });
  await screen.findByText('Elsewhere');
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss All' }));
  await waitFor(() => expect(posted).toEqual(['/api/repair/findings/album-action']));
  expect(bodies[0]).toMatchObject({
    action: 'dismiss',
    artist: 'Makeup And Vanity Set',
    album: 'FM',
  });
});

it('shows nothing for a group with no exact identity', async () => {
  const { listUrls } = renderTray([fixableFinding], { trayGroup: { ...group, artist: null } });
  expect(await screen.findByText(/can’t be matched/)).toBeInTheDocument();
  expect(listUrls).toHaveLength(0);
  expect(screen.queryByText('Song')).not.toBeInTheDocument();
});

it('offers a fix the served catalog knows even when the client list does not', async () => {
  const mbid: RepairFinding = {
    ...fixableFinding,
    id: 5,
    finding_type: 'mbid_mismatch',
    entity_type: 'track',
    entity_id: 'lib2:7',
  };
  renderTray([mbid], {
    types: [
      {
        type: 'mbid_mismatch',
        label: 'MBID mismatch',
        verb: 'Fix MBID',
        fixable: true,
        destructive: false,
      },
    ],
  });
  expect(await screen.findByRole('button', { name: 'Fix MBID' })).toBeInTheDocument();
});
