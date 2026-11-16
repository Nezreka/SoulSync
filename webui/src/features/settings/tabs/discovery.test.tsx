/**
 * The Discovery tab, ported from the vanilla Settings page (SETTINGS_PORT.md,
 * step 4): the same markup as the frozen fixtures (after settings.js's help
 * lines), and the same values, summaries and saves.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { shape, vanilla } from './__fixtures__/shape';
import { discoverySummaries, resolveDiscovery } from './discovery';
import { DiscoveryTab, saveLookback } from './discovery-tab';
import { helpFirstLine } from './parts';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

let store: SettingsStore;
let posted: SettingsPayload[];
let calls: Array<{ url: string; method: string; body: unknown }>;
let replies: Record<string, unknown>;

beforeEach(() => {
  // each store saves into its own list: a save the last test left queued
  // must not land in this one's
  const sink: SettingsPayload[] = [];
  posted = sink;
  calls = [];
  replies = {
    '/api/discovery/lookback-period': { period: '90' },
    '/api/discovery/hemisphere': { hemisphere: 'southern' },
  };
  store = createSettingsStore({
    post: (body) => {
      sink.push(structuredClone(body));
      return Promise.resolve({ success: true });
    },
    delay: 0,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      calls.push({
        url,
        method,
        body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
      });
      const reply = method === 'POST' ? { success: true } : replies[url];
      return Promise.resolve({ json: () => Promise.resolve(reply) });
    }),
  );
  shell.showToast = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete shell._settingsAutosaveOff;
});

const main = () => render(<DiscoveryTab store={store} part="main" opened={1} active />);
const pool = () => render(<DiscoveryTab store={store} part="pool" opened={1} active />);
const box = (id: string) => document.getElementById(id) as HTMLInputElement;

describe('the Discovery tab is the same page it replaced', () => {
  it('main: the same elements, ids, classes, help lines and words', () => {
    const { container } = main();
    const want = shape(vanilla('discovery-main.vanilla.html', { afterInit: true }));
    const got = shape(container);
    expect(got.elements).toEqual(want.elements);
    expect(got.text).toBe(want.text);
  });

  it('pool: the same elements, ids, classes, help lines and words', () => {
    const { container } = pool();
    const want = shape(vanilla('discovery-pool.vanilla.html', { afterInit: true }));
    const got = shape(container);
    expect(got.elements).toEqual(want.elements);
    expect(got.text).toBe(want.text);
  });
});

describe('the values and summaries', () => {
  it('reads saved settings the way loadSettingsData did', () => {
    expect(resolveDiscovery({})).toEqual({
      m3uExport: false,
      libraryM3u: false,
      libraryM3uPath: '',
      syncMode: 'reconcile',
      wishlistMissing: true,
      createBackup: false,
      statsEnabled: false,
      statsInterval: 30,
      adventurousness: 0.3,
    });
    expect(resolveDiscovery({ discover: { adventurousness: 0 } }).adventurousness).toBe(0);
    expect(
      resolveDiscovery({ playlist_sync: { wishlist_missing_tracks: false } }).wishlistMissing,
    ).toBe(false);
  });

  it('summarises each section as settings.js did, the dial bug included', () => {
    const base = resolveDiscovery({});
    expect(discoverySummaries(base)).toEqual({
      playlists: 'off',
      stats: 'off',
      discovery: 'plays it safe',
    });
    expect(
      discoverySummaries({
        ...base,
        m3uExport: true,
        libraryM3u: true,
        statsEnabled: true,
        adventurousness: 1,
      }),
    ).toEqual({
      playlists: 'M3U export · library M3U',
      stats: 'collecting play history',
      discovery: 'plays it safe',
    });
  });

  it('shows the fixed hints until the settings load, then the summaries', () => {
    main();
    const hint = (key: string) => document.querySelector(`[data-stg-summary="${key}"]`)!;
    expect(hint('library-stats').textContent).toBe('Play history from your media server');
    expect(hint('library-stats').classList.contains('is-live')).toBe(false);
    act(() => store.hydrate({ listening_stats: { enabled: true }, m3u_export: { enabled: true } }));
    expect(hint('library-stats').textContent).toBe('collecting play history');
    expect(hint('library-stats').classList.contains('is-live')).toBe(true);
    expect(hint('library-playlists').textContent).toBe('M3U export');
  });

  it('cuts the help to its first sentence as settings.js did', () => {
    expect(helpFirstLine('One. Two.')).toEqual({ line: 'One.', rest: true });
    expect(helpFirstLine('Only one.')).toEqual({ line: 'Only one.', rest: false });
    expect(helpFirstLine('')).toEqual({ line: '', rest: false });
    const long = 'word '.repeat(60).trim() + '.';
    expect(helpFirstLine(long).line.endsWith('…')).toBe(true);
  });
});

describe('changing a setting saves only its own key', () => {
  it('every control of the main part', async () => {
    store.hydrate({ discover: { adventurousness: 0.3 } });
    main();
    fireEvent.click(box('m3u-export-enabled'));
    fireEvent.click(box('library-m3u-enabled'));
    fireEvent.change(box('library-m3u-path'), { target: { value: '/m3u' } });
    fireEvent.change(document.getElementById('playlist-sync-mode')!, {
      target: { value: 'append' },
    });
    fireEvent.click(box('playlist-sync-wishlist-missing'));
    fireEvent.click(box('create-backup'));
    fireEvent.click(box('listening-stats-enabled'));
    fireEvent.change(box('listening-stats-interval'), { target: { value: '45' } });
    fireEvent.change(box('discover-adventurousness'), { target: { value: '0.75' } });
    expect(document.getElementById('discover-adventurousness-val')!.textContent).toBe('0.75');
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(
      Object.assign({}, ...posted.flatMap((p) => Object.entries(p).map(([k, v]) => ({ [k]: v })))),
    ).toBeTruthy();
    const merged: SettingsPayload = {};
    for (const body of posted)
      for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
    expect(merged).toEqual({
      m3u_export: { enabled: true, library_enabled: true, library_path: '/m3u' },
      playlist_sync: { mode: 'append', wishlist_missing_tracks: false, create_backup: true },
      listening_stats: { enabled: true, poll_interval: 45 },
      discover: { adventurousness: 0.75 },
    });
  });

  it('an emptied poll interval keeps the empty field and saves 30', async () => {
    store.hydrate({ listening_stats: { poll_interval: 60 } });
    main();
    expect(box('listening-stats-interval').value).toBe('60');
    fireEvent.change(box('listening-stats-interval'), { target: { value: '' } });
    expect(box('listening-stats-interval').value).toBe('');
    await waitFor(() => expect(posted.at(-1)).toEqual({ listening_stats: { poll_interval: 30 } }));
  });

  it('an "i" opens the rest of its help, and only its own', () => {
    main();
    const group = box('create-backup').closest('.form-group')!;
    // this one's help body is inside its form-group
    const body = group.querySelector('.setting-help-body') as HTMLElement;
    expect(body.hidden).toBe(true);
    fireEvent.click(group.querySelector('.stg-help-line .info-icon')!);
    expect(body.hidden).toBe(false);
    expect(group.querySelector('.info-icon')!.classList.contains('open')).toBe(true);
    const other = box('m3u-export-enabled').closest('.form-group')!
      .nextElementSibling as HTMLElement;
    expect(other.hidden).toBe(true);
    fireEvent.keyDown(group.querySelector('.stg-help-line .info-icon')!, { key: 'Enter' });
    expect(body.hidden).toBe(true);
  });
});

describe('Discovery Pool Settings', () => {
  it('loads lookback and hemisphere with every settings load, not before', async () => {
    pool();
    expect(calls).toEqual([]);
    act(() => store.hydrate({}));
    await waitFor(() =>
      expect((document.getElementById('discovery-hemisphere') as HTMLSelectElement).value).toBe(
        'southern',
      ),
    );
    expect((document.getElementById('discovery-lookback-period') as HTMLSelectElement).value).toBe(
      '90',
    );
    act(() => store.hydrate({}));
    await waitFor(() => expect(calls.filter((c) => c.method === 'GET')).toHaveLength(4));
  });

  it('a change saves through its own endpoint, with the autosave rules', async () => {
    shell._settingsAutosaveOff = true;
    pool();
    fireEvent.change(document.getElementById('discovery-lookback-period')!, {
      target: { value: 'all' },
    });
    fireEvent.change(document.getElementById('discovery-hemisphere')!, {
      target: { value: 'southern' },
    });
    await act(async () => {});
    expect(calls.filter((c) => c.method === 'POST')).toEqual([]);
    expect(store.hasPending()).toBe(true);
    expect(await store.flush()).toBe(true);
    expect(calls.filter((c) => c.method === 'POST')).toEqual([
      { url: '/api/discovery/lookback-period', method: 'POST', body: { period: 'all' } },
      { url: '/api/discovery/hemisphere', method: 'POST', body: { hemisphere: 'southern' } },
    ]);
    expect(posted).toEqual([]);
  });

  it('a pick not saved yet stays on screen when Settings is opened again', async () => {
    shell._settingsAutosaveOff = true;
    const first = pool();
    act(() => store.hydrate({}));
    const lookback = () =>
      document.getElementById('discovery-lookback-period') as HTMLSelectElement;
    const hemisphere = () => document.getElementById('discovery-hemisphere') as HTMLSelectElement;
    await waitFor(() => expect(lookback().value).toBe('90'));
    // not the server's, nor the field's own first value
    fireEvent.change(lookback(), { target: { value: 'all' } });
    // Settings is left and opened again: the page loads its settings again
    first.unmount();
    pool();
    act(() => store.hydrate({}));
    await waitFor(() => expect(hemisphere().value).toBe('southern'));
    expect(lookback().value).toBe('all');
    // only the one with nothing queued was asked for again
    expect(
      calls.filter((c) => c.url === '/api/discovery/lookback-period' && c.method === 'GET'),
    ).toHaveLength(1);
    expect(await store.flush()).toBe(true);
    expect(calls.filter((c) => c.method === 'POST')).toEqual([
      { url: '/api/discovery/lookback-period', method: 'POST', body: { period: 'all' } },
    ]);
  });

  it('a failed lookback is the warning it was, beside "saved"', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve({ json: () => Promise.resolve({ success: false }) })),
    );
    expect(await saveLookback('7')).toBe(true);
    expect(shell.showToast).toHaveBeenCalledWith(
      'Settings saved, but discovery lookback period failed to save',
      'warning',
    );
  });
});
