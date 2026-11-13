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
    const health = syncHealth(ROWS)!;
    expect(health).toMatchObject({ inLibrary: 336, total: 350, pct: 96, short: 4 });
    // every playlist with a total is either whole or short
    expect(health.complete + health.short).toBe(ROWS.filter((r) => r.coverage).length);
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

  it('the board asks for five', () => {
    const many = Array.from({ length: 8 }, (_, n) =>
      row(`P${n}`, { inLibrary: n, total: 20, pct: n * 5 }),
    );
    expect(railRows(many, false, 'missing', () => false, 5)).toHaveLength(5);
    expect(railRows(many, false)).toHaveLength(3);
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
    // the four sorts are a row of pills, one pressed
    const pressed = (v: ReturnType<typeof render>) =>
      v.container.querySelector('.dash-sync-sort[aria-pressed="true"]')?.textContent;
    const pill = (v: ReturnType<typeof render>, label: string) =>
      Array.from(v.container.querySelectorAll<HTMLButtonElement>('.dash-sync-sort')).find(
        (b) => b.textContent === label,
      )!;
    expect(
      Array.from(view.container.querySelectorAll('.dash-sync-sort')).map((b) => b.textContent),
    ).toEqual(['Most missing', 'Last synced', 'Next run', 'Name']);
    expect(pressed(view)).toBe('Most missing');

    fireEvent.click(pill(view, 'Last synced'));
    expect(names(view)).toEqual(['Zeta Mix', 'Alpha Mix']);

    fireEvent.click(pill(view, 'Name'));
    expect(names(view)).toEqual(['Alpha Mix', 'Zeta Mix']);
    cleanup();

    const again = await mount();
    expect(pressed(again)).toBe('Name');
  });

  it('says how many of how many, and draws the share as a ring', async () => {
    const view = await mount();
    const ring = view.container.querySelector<HTMLElement>('.dash-sync-ring')!;
    expect(ring.getAttribute('aria-valuenow')).toBe('70');
    expect(ring.style.getPropertyValue('--pct')).toBe('70%');
    expect(view.container.querySelector('.dash-sync-of')!.textContent).toBe(
      '14 of 20playlist tracks are in your library, across 2 playlists',
    );
  });

  it('each row says what it needs under its name', async () => {
    const view = await mount();
    const subs = Array.from(view.container.querySelectorAll('.dash-sync-row')).map((row) => [
      row.querySelector('.dash-sync-name')?.textContent,
      row.querySelector('.dash-sync-sub')?.textContent,
    ]);
    expect(subs).toEqual([['Alpha Mix', '6 missing']]);
  });

  it('splits the playlists into whole and short, and shows how much of a row is owned', async () => {
    const view = await mount();
    expect(view.container.querySelector('.dash-sync-mixkey')!.textContent).toBe(
      '1 complete1 missing tracks',
    );
    const cover = view.container.querySelector<HTMLElement>('.dash-sync-row .dash-sync-cover')!;
    expect(cover.querySelector('em')!.textContent).toMatch(/^\d+ of \d+$/);
    expect(cover.querySelector<HTMLElement>('u')!.style.width).toMatch(/^\d+%$/);
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

describe('the split sync buttons (#1455)', () => {
  const posts: Array<{ url: string; body: unknown }> = [];

  beforeEach(() => {
    posts.length = 0;
    const fetchMock = vi.fn(async (url: unknown, init?: { body?: unknown }) => {
      const u = String(url);
      if (u.endsWith('/pipeline/run')) {
        posts.push({ url: u, body: JSON.parse(String((init as { body?: string })?.body)) });
        return { ok: true, json: async () => ({}) };
      }
      if (u === '/api/mirrored-playlists')
        return {
          ok: true,
          json: async () => [
            // fully checked: coverage needs library_checked_count since review 5 R5-K-1
            {
              id: 7,
              name: 'Test List',
              source: 'spotify',
              total_count: 10,
              in_library_count: 4,
              library_checked_count: 10,
            },
          ],
        };
      if (u === '/api/automations')
        return {
          ok: true,
          json: async () => [
            {
              id: 1,
              owned_by: 'auto_sync',
              action_type: 'playlist_pipeline',
              action_config: { playlist_id: '7' },
              trigger_type: 'schedule',
              trigger_config: { interval: 24, unit: 'hours' },
              enabled: true,
            },
          ],
        };
      if (u === '/api/playlist-pipeline/history?limit=40')
        return { ok: true, json: async () => ({}) };
      if (u.startsWith('/api/sync/history'))
        return { ok: true, json: async () => ({ entries: [] }) };
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal('fetch', fetchMock);
    (window as unknown as Record<string, unknown>).showToast = vi.fn();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    delete (window as unknown as Record<string, unknown>).showToast;
  });

  it('scheduled rows offer Sync and Sync + download with the right bodies', async () => {
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(<SyncRail />);
    });
    await vi.waitFor(() =>
      expect(
        view.getByLabelText('Sync Test List without adding missing tracks to the wishlist'),
      ).toBeTruthy(),
    );

    fireEvent.click(
      view.getByLabelText('Sync Test List without adding missing tracks to the wishlist'),
    );
    await vi.waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].url).toContain('/api/mirrored-playlists/7/pipeline/run');
    expect(posts[0].body).toEqual({ skip_wishlist: true });

    // The first click sets the row busy (button disabled) until runNow's
    // finally clears it — wait for the re-enable before clicking again.
    await vi.waitFor(() =>
      expect(
        (view.getByLabelText('Sync Test List and download missing tracks') as HTMLButtonElement)
          .disabled,
      ).toBe(false),
    );
    fireEvent.click(view.getByLabelText('Sync Test List and download missing tracks'));
    await vi.waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1].body).toEqual({ skip_wishlist: false });
  });

  it('syncs an unscheduled mirror without a history entry through its pipeline', async () => {
    // a mirror with no Auto-Sync automation and no pipeline history
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: unknown, init?: { body?: unknown }) => {
        const u = String(url);
        if (u.endsWith('/pipeline/run')) {
          posts.push({ url: u, body: JSON.parse(String((init as { body?: string })?.body)) });
          return { ok: true, json: async () => ({}) };
        }
        if (u === '/api/mirrored-playlists')
          return {
            ok: true,
            json: async () => [
              {
                id: 8,
                name: 'Feelings',
                source: 'spotify',
                total_count: 414,
                in_library_count: 65,
                // fully checked: coverage needs library_checked_count since review 5 R5-K-1
                library_checked_count: 414,
              },
            ],
          };
        if (u === '/api/automations') return { ok: true, json: async () => [] };
        if (u.startsWith('/api/sync/history'))
          return { ok: true, json: async () => ({ entries: [] }) };
        return { ok: true, json: async () => ({}) };
      }),
    );
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(<SyncRail />);
    });
    await vi.waitFor(() => expect(view.container.querySelector('.dash-sync-act')).not.toBeNull());
    fireEvent.click(view.container.querySelector('.dash-sync-act')!);
    await vi.waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({
      url: '/api/mirrored-playlists/8/pipeline/run',
      body: { skip_wishlist: true },
    });
  });
});
