/**
 * The dashboard rail's Playlist sync card (Sept 2026 redesign): one number
 * for how much of your playlists you own, and the playlists missing the most.
 * The band's full table became this, so the numbers have to be honest.
 */

import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { SyncBandRow } from '../-dash.syncband';

import { railRows, rowOpener, syncHealth, SyncRail } from './sync-band';

function row(name: string, coverage: SyncBandRow['coverage']): SyncBandRow {
  return {
    rowKey: name,
    kind: 'manual',
    name,
    schedule: null,
    last: null,
    coverage,
    thumbUrl: null,
    logo: null,
    sourceKey: 'spotify',
    sourceLabel: 'Spotify',
  } as SyncBandRow;
}

const ROWS = [
  row('Hot Hits', { inLibrary: 49, total: 50, pct: 98 }),
  row('Release Radar', { inLibrary: 25, total: 30, pct: 83 }),
  row('Never Run', null),
  row('New Electronic', { inLibrary: 145, total: 150, pct: 97 }),
  row('Radar Weekly', { inLibrary: 97, total: 100, pct: 97 }),
  row('Fully Owned', { inLibrary: 20, total: 20, pct: 100 }),
];

describe('syncHealth', () => {
  it('adds up every playlist with a known total', () => {
    expect(syncHealth(ROWS)).toEqual({ inLibrary: 336, total: 350, pct: 96, short: 4 });
  });

  it('rounds DOWN, it never claims more of your playlists than you own', () => {
    const h = syncHealth([row('a', { inLibrary: 199, total: 200, pct: 100 })]);
    expect(h?.pct).toBe(99);
  });

  it('has nothing to say until a playlist has a total', () => {
    expect(syncHealth([row('Never Run', null)])).toBeNull();
    expect(syncHealth([])).toBeNull();
  });
});

describe('railRows', () => {
  it('lists the three missing the most, skipping complete ones', () => {
    expect(railRows(ROWS, false).map((r) => r.name)).toEqual([
      'Release Radar',
      'New Electronic',
      'Radar Weekly',
    ]);
    // one playlist short: the rail says just that one, not two finished ones
    const oneShort = [
      row('Fully Owned', { inLibrary: 20, total: 20, pct: 100 }),
      row('Hot Hits', { inLibrary: 49, total: 50, pct: 98 }),
      row('Also Done', { inLibrary: 9, total: 9, pct: 100 }),
    ];
    expect(railRows(oneShort, false).map((r) => r.name)).toEqual(['Hot Hits']);
  });

  it('See all shows every playlist, most missing first', () => {
    const names = railRows(ROWS, true).map((r) => r.name);
    expect(names).toHaveLength(ROWS.length);
    expect(names.slice(0, 4)).toEqual([
      'Release Radar',
      'New Electronic',
      'Radar Weekly',
      'Hot Hits',
    ]);
  });
});

describe('the card', () => {
  const fetchMock = vi.fn((..._args: unknown[]) => Promise.reject(new Error('down')));

  beforeEach(() => vi.stubGlobal('fetch', fetchMock));
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('with nothing syncing, offers to set one up instead of a zero', async () => {
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(<SyncRail />);
    });
    await vi.waitFor(() => expect(view.container.querySelector('.dash-side-empty')).not.toBeNull());
    expect(view.container.querySelector('.dash-sync-pct')).toBeNull();
    expect(view.container.textContent).toContain('Set one up');
  });
});

function scheduled(name: string, nextRunAt: number | null, enabled = true): SyncBandRow {
  return {
    ...row(name, null),
    kind: 'scheduled',
    schedule: { key: '1', enabled, nextRunAt } as SyncBandRow['schedule'],
  };
}

describe('railRows sorts', () => {
  const NEXT = [
    scheduled('Later', 5_000),
    scheduled('Paused', 1_000, false),
    row('Manual', null),
    scheduled('Soonest', 2_000),
    scheduled('No Time', null),
  ];

  it('next run: soonest first, paused and unscheduled trail', () => {
    expect(railRows(NEXT, true, 'next').map((r) => r.name)).toEqual([
      'Soonest',
      'Later',
      'Paused',
      'Manual',
      'No Time',
    ]);
  });

  it('last synced keeps the order they arrive in, newest sync first', () => {
    expect(railRows(ROWS, true, 'recent').map((r) => r.name)).toEqual(ROWS.map((r) => r.name));
  });

  it('name is alphabetical', () => {
    expect(railRows(ROWS, true, 'name').map((r) => r.name)).toEqual([
      'Fully Owned',
      'Hot Hits',
      'Never Run',
      'New Electronic',
      'Radar Weekly',
      'Release Radar',
    ]);
  });

  it('collapsed, the other sorts keep complete playlists, only most missing skips them', () => {
    expect(railRows(ROWS, false, 'name').map((r) => r.name)).toEqual([
      'Fully Owned',
      'Hot Hits',
      'Never Run',
    ]);
  });
});

describe('rowOpener', () => {
  afterEach(() => {
    Reflect.deleteProperty(window, 'openSyncDetailModal');
    Reflect.deleteProperty(window, 'openMirroredPlaylistModal');
  });

  it('opens the run detail when there is a run, else the playlist, else nothing', () => {
    const detail = vi.fn();
    const playlist = vi.fn();
    window.openSyncDetailModal = detail;
    window.openMirroredPlaylistModal = playlist;

    rowOpener({ ...scheduled('A', null), last: { id: 65 } as SyncBandRow['last'] })!();
    expect(detail).toHaveBeenCalledWith(65);

    rowOpener(scheduled('B', null))!();
    expect(playlist).toHaveBeenCalledWith(1);

    expect(rowOpener(row('C', null))).toBeNull();
  });
});

describe('the card with playlists', () => {
  const ENTRIES = [
    {
      id: 11,
      sync_type: 'playlist',
      playlist_name: 'Zeta Mix',
      tracks_found: 10,
      total_tracks: 10,
    },
    {
      id: 12,
      sync_type: 'playlist',
      playlist_name: 'Alpha Mix',
      tracks_found: 4,
      total_tracks: 10,
    },
  ];

  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) =>
        String(url).startsWith('/api/sync/history')
          ? Promise.resolve({ ok: true, status: 200, json: async () => ({ entries: ENTRIES }) })
          : Promise.reject(new Error('down')),
      ),
    );
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    Reflect.deleteProperty(window, 'openSyncDetailModal');
    try {
      window.localStorage.clear();
    } catch {
      // nothing to clear
    }
  });

  async function mount() {
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(<SyncRail />);
    });
    await vi.waitFor(() => expect(view.container.querySelector('.dash-sync-row')).not.toBeNull());
    return view;
  }

  const names = (view: ReturnType<typeof render>) =>
    [...view.container.querySelectorAll('.dash-sync-name')].map((n) => n.textContent);

  it('clicking a playlist opens what that sync did', async () => {
    const detail = vi.fn();
    window.openSyncDetailModal = detail;
    const view = await mount();
    const rowEl = view.container.querySelector('.dash-sync-row')!;
    expect(rowEl.getAttribute('role')).toBe('button');
    fireEvent.click(rowEl);
    expect(detail).toHaveBeenCalledWith(12);
  });

  it('the hover buttons do their own thing, not open the row', async () => {
    const detail = vi.fn();
    window.openSyncDetailModal = detail;
    const view = await mount();
    fireEvent.click(view.container.querySelector('.dash-sync-act')!);
    expect(detail).not.toHaveBeenCalled();
  });

  it('the sort reorders the rows and is remembered', async () => {
    const view = await mount();
    const select = view.container.querySelector<HTMLSelectElement>('.dash-side-sort select')!;
    expect(select.value).toBe('missing');

    fireEvent.change(select, { target: { value: 'recent' } });
    expect(names(view)).toEqual(['Zeta Mix', 'Alpha Mix']);

    fireEvent.change(select, { target: { value: 'name' } });
    expect(names(view)).toEqual(['Alpha Mix', 'Zeta Mix']);
    cleanup();

    const again = await mount();
    expect(again.container.querySelector<HTMLSelectElement>('.dash-side-sort select')!.value).toBe(
      'name',
    );
  });
});

describe('a running sync', () => {
  const live = (name: string) => name === 'Fully Owned';

  it('sits on top whatever the sort', () => {
    for (const sort of ['missing', 'recent', 'next', 'name'] as const) {
      expect(railRows(ROWS, true, sort, (r) => live(r.name))[0].name).toBe('Fully Owned');
    }
  });

  it('shows collapsed even with nothing missing, and the rest fill in under it', () => {
    expect(railRows(ROWS, false, 'missing', (r) => live(r.name)).map((r) => r.name)).toEqual([
      'Fully Owned',
      'Release Radar',
      'New Electronic',
    ]);
  });

  it('every running sync shows, even past three', () => {
    const shown = railRows(ROWS, false, 'name', () => true);
    expect(shown).toHaveLength(ROWS.length);
  });
});
