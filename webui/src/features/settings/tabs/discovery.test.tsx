/**
 * The Discovery tab on the parts (settings-bic phase 5): Recommendations,
 * Playlists and Import Lists, one pill each. Every control on its pill with
 * its key, the dependent rows dimmed with their parent, a line per pill
 * saying what it is for, and the same saves as the page it replaced.
 */

import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { nav } from '../nav';
import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { resolveDiscovery } from './discovery';
import { saveLookback } from './discovery-tab';
import { DiscoveryTab } from './discovery/discovery-page';
import { helpFirstLine } from './parts';
import { videoShown } from './video-shown';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

let store: SettingsStore;
let posted: SettingsPayload[];
let calls: Array<{ url: string; method: string; body: unknown }>;
let replies: Record<string, unknown>;

beforeEach(() => {
  nav.reset();
  act(() => nav.go('discovery', 'recommendations'));
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
      const reply = method === 'POST' ? { success: true } : (replies[url] ?? {});
      return Promise.resolve({ ok: true, json: () => Promise.resolve(reply) });
    }),
  );
  shell.showToast = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete shell._settingsAutosaveOff;
  document.body.removeAttribute('data-side');
  nav.reset();
});

const page = () => render(<DiscoveryTab store={store} part="page" opened={1} active />);
const box = (id: string) => document.getElementById(id) as HTMLInputElement;
const panel = (view: string) =>
  document.querySelector<HTMLElement>(`[data-stg-tab="discovery"][data-stg-view="${view}"]`)!;
const rowOf = (id: string) => document.getElementById(id)!.closest<HTMLElement>('[data-row]')!;
const picked = (id: string) => document.getElementById(id)!.getAttribute('data-value');
const pick = (id: string, value: string) =>
  fireEvent.click(document.querySelector(`#${id} [data-value="${value}"]`)!);
const dimmed = (id: string) => /rowOff/.test(rowOf(id).className);

describe('the Discovery page', () => {
  it('every control is drawn once, on its pill, named without a colon', () => {
    page();
    const where: Record<string, string> = {
      'discover-adventurousness': 'recommendations',
      'listening-stats-enabled': 'recommendations',
      'listening-stats-interval': 'recommendations',
      'discovery-lookback-period': 'recommendations',
      'discovery-hemisphere': 'recommendations',
      'playlist-sync-mode': 'playlists',
      'create-backup': 'playlists',
      'playlist-sync-wishlist-missing': 'playlists',
      'm3u-export-enabled': 'playlists',
      'library-m3u-enabled': 'playlists',
      'library-m3u-path': 'playlists',
      // all M3U settings on one pill: the three that were on Library too
      'm3u-relative-paths': 'playlists',
      'm3u-entry-base-path': 'playlists',
      'm3u-rewrite-from': 'playlists',
      'm3u-rewrite-to': 'playlists',
      'video-import-lists': 'import-lists',
    };
    for (const [id, view] of Object.entries(where)) {
      expect(document.querySelectorAll(`#${id}`), id).toHaveLength(1);
      expect(panel(view).contains(document.getElementById(id)), id).toBe(true);
    }
    expect(panel('recommendations').hidden).toBe(false);
    expect(panel('playlists').hidden).toBe(true);
    // no old section headers, no colon after a label, no em dash
    expect(document.querySelector('.settings-section-header, .form-group')).toBeNull();
    for (const label of document.querySelectorAll('label'))
      expect(label.textContent).not.toMatch(/:\s*$/);
    expect(document.body.textContent).not.toContain('—');
    // the discovery pool is the music side's own
    expect(box('discovery-lookback-period').closest('[data-music-only]')).not.toBeNull();
  });

  it('opening Import Lists on the music side loads the lists', () => {
    page();
    const before = videoShown.get();
    act(() => nav.view('import-lists'));
    expect(panel('import-lists').hidden).toBe(false);
    expect(videoShown.get()).toBe(before + 1);
  });

  it('each pill opens with what it is for, in one line', () => {
    page();
    const about = (view: string) => panel(view).querySelector('[data-about]')!.textContent;
    expect(about('recommendations')).toBe(
      'How new music is picked for you, and what it learns from.',
    );
    expect(about('playlists')).toBe('How playlists sync to your server, and the M3U files.');
    expect(about('import-lists')).toBe('Lists SoulSync watches for new movies and shows to add.');
  });
});

describe('dependent rows dim with their parent', () => {
  it('the poll interval, the backup copy and the library M3U folder', () => {
    store.hydrate({ playlist_sync: { mode: 'reconcile' } });
    page();
    expect(dimmed('listening-stats-interval')).toBe(true);
    expect(dimmed('create-backup')).toBe(true);
    expect(rowOf('create-backup').textContent).toContain('Only Replace uses this');
    expect(dimmed('library-m3u-path')).toBe(true);
    fireEvent.click(box('listening-stats-enabled'));
    pick('playlist-sync-mode', 'replace');
    fireEvent.click(box('library-m3u-enabled'));
    expect(dimmed('listening-stats-interval')).toBe(false);
    expect(dimmed('create-backup')).toBe(false);
    expect(dimmed('library-m3u-path')).toBe(false);
    // dimmed, never disabled: each still saves
    expect(box('create-backup').disabled).toBe(false);
  });

  it('the sync mode says what the picked one does', () => {
    store.hydrate({});
    page();
    expect(picked('playlist-sync-mode')).toBe('reconcile');
    const row = rowOf('playlist-sync-mode');
    expect(row.textContent).toContain('Edits the playlist in place');
    pick('playlist-sync-mode', 'append');
    expect(row.textContent).toContain('Only ever adds new tracks.');
  });
});

describe('the values', () => {
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
      holidayMode: 'season',
      holidayStart: '11-15',
      holidayEnd: '01-06',
      holidayTag: false,
    });
    expect(resolveDiscovery({ discover: { adventurousness: 0 } }).adventurousness).toBe(0);
    expect(
      resolveDiscovery({ playlist_sync: { wishlist_missing_tracks: false } }).wishlistMissing,
    ).toBe(false);
  });

  it('cuts the help to its first sentence', () => {
    expect(helpFirstLine('One. Two.')).toEqual({ line: 'One.', rest: true });
    expect(helpFirstLine('Only one.')).toEqual({ line: 'Only one.', rest: false });
    expect(helpFirstLine('')).toEqual({ line: '', rest: false });
    const long = 'word '.repeat(60).trim() + '.';
    expect(helpFirstLine(long).line.endsWith('…')).toBe(true);
  });
});

describe('changing a setting saves only its own key', () => {
  it('every store control of both pills', async () => {
    store.hydrate({ discover: { adventurousness: 0.3 } });
    page();
    fireEvent.click(box('m3u-export-enabled'));
    fireEvent.click(box('library-m3u-enabled'));
    fireEvent.change(box('library-m3u-path'), { target: { value: '/m3u' } });
    pick('playlist-sync-mode', 'append');
    fireEvent.click(box('playlist-sync-wishlist-missing'));
    fireEvent.click(box('create-backup'));
    fireEvent.click(box('listening-stats-enabled'));
    fireEvent.change(box('listening-stats-interval'), { target: { value: '45' } });
    fireEvent.change(box('discover-adventurousness'), { target: { value: '0.75' } });
    expect(document.getElementById('discover-adventurousness-val')!.textContent).toBe('0.75');
    await waitFor(() => expect(store.hasPending()).toBe(false));
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
    page();
    expect(box('listening-stats-interval').value).toBe('60');
    fireEvent.change(box('listening-stats-interval'), { target: { value: '' } });
    expect(box('listening-stats-interval').value).toBe('');
    await waitFor(() => expect(posted.at(-1)).toEqual({ listening_stats: { poll_interval: 30 } }));
  });

  it('an "i" opens the rest of its help, and only its own', () => {
    page();
    const more = rowOf('create-backup').querySelector<HTMLElement>(
      '[aria-label="More about this setting"]',
    )!;
    const body = document.getElementById(more.getAttribute('aria-controls')!)!;
    expect(body.hidden).toBe(true);
    fireEvent.click(more);
    expect(body.hidden).toBe(false);
    expect(body.textContent).toContain('keeps its own copy of the source playlist');
    const other = rowOf('library-m3u-enabled').querySelector<HTMLElement>(
      '[aria-label="More about this setting"]',
    )!;
    expect(other.getAttribute('aria-expanded')).toBe('false');
  });
});

describe('the discovery pool', () => {
  it('loads lookback and hemisphere with every settings load, not before', async () => {
    page();
    expect(calls).toEqual([]);
    act(() => store.hydrate({}));
    await waitFor(() => expect(picked('discovery-hemisphere')).toBe('southern'));
    expect((document.getElementById('discovery-lookback-period') as HTMLSelectElement).value).toBe(
      '90',
    );
    act(() => store.hydrate({}));
    await waitFor(() => expect(calls.filter((c) => c.method === 'GET')).toHaveLength(4));
  });

  it('a change saves through its own endpoint, with the autosave rules', async () => {
    shell._settingsAutosaveOff = true;
    page();
    fireEvent.change(document.getElementById('discovery-lookback-period')!, {
      target: { value: 'all' },
    });
    pick('discovery-hemisphere', 'southern');
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
    const first = page();
    act(() => store.hydrate({}));
    const lookback = () =>
      document.getElementById('discovery-lookback-period') as HTMLSelectElement;
    await waitFor(() => expect(lookback().value).toBe('90'));
    // not the server's, nor the field's own first value
    fireEvent.change(lookback(), { target: { value: 'all' } });
    // Settings is left and opened again: the page loads its settings again
    first.unmount();
    page();
    act(() => store.hydrate({}));
    await waitFor(() => expect(picked('discovery-hemisphere')).toBe('southern'));
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

describe('holiday music (#2332)', () => {
  it('an install with none saved is in season, Nov 15 to Jan 6, no tag', () => {
    const values = resolveDiscovery({});
    expect(values.holidayMode).toBe('season');
    expect(values.holidayStart).toBe('11-15');
    expect(values.holidayEnd).toBe('01-06');
    expect(values.holidayTag).toBe(false);
    expect(
      resolveDiscovery({ holiday_music: { mode: 'bogus', season_start: '99-99' } }),
    ).toMatchObject({ holidayMode: 'season', holidayStart: '11-15' });
  });

  it('the card is on Recommendations, music side only, and says when songs come back', () => {
    store.hydrate({
      holiday_music: { mode: 'season', season_start: '12-01', season_end: '01-02' },
    });
    page();
    expect(panel('recommendations').contains(box('holiday-music-mode'))).toBe(true);
    expect(box('holiday-music-mode').closest('[data-music-only]')).not.toBeNull();
    expect(picked('holiday-music-mode')).toBe('season');
    expect(box('holiday-season-start-month').value).toBe('12');
    expect(box('holiday-season-end-day').value).toBe('2');
    expect(rowOf('holiday-season-start-month').textContent).toContain('December 1');
    expect(box('holiday-write-tag').checked).toBe(false);
  });

  it('saves the mode, the dates as MM-DD and the tag', async () => {
    page();
    pick('holiday-music-mode', 'never');
    await waitFor(() => expect(posted.at(-1)).toEqual({ holiday_music: { mode: 'never' } }));
    // the dates dim when they don't apply
    expect(dimmed('holiday-season-start-month')).toBe(true);
    pick('holiday-music-mode', 'season');
    fireEvent.change(box('holiday-season-start-month'), { target: { value: '12' } });
    await waitFor(() =>
      expect(posted.some((p) => p.holiday_music?.season_start === '12-15')).toBe(true),
    );
    fireEvent.click(box('holiday-write-tag'));
    await waitFor(() => expect(posted.at(-1)).toEqual({ holiday_music: { write_tag: true } }));
  });
});
