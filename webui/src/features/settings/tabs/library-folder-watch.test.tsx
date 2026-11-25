/**
 * Library > Folders, "Watching for changes" (Phase 3 item 3.2): the switch,
 * the timer for network shares, the status line and each folder's mode.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import {
  FolderWatchSection,
  ago,
  modeWords,
  statusLine,
  type WatchRoot,
  type WatchStatus,
} from './library-folder-watch';

let store: SettingsStore;
let posted: SettingsPayload[];

const NOW = 1_800_000_000;

function root(over: Partial<WatchRoot>): WatchRoot {
  return {
    path: '/music',
    kind: 'library',
    setting: 'auto',
    mode: 'live',
    reason: 'local disk (ext4)',
    note: '',
    reachable: true,
    poll_minutes: null,
    last_change: null,
    changes: 0,
    pending: 0,
    ...over,
  };
}

function status(over: Partial<WatchStatus> = {}): WatchStatus {
  return {
    enabled: true,
    running: true,
    live_available: true,
    watching: 2,
    last_change: NOW - 120,
    pending: 0,
    poll_minutes: 15,
    roots: [
      root({ path: '/music', last_change: NOW - 120 }),
      root({
        path: '/mnt/nas',
        mode: 'polling',
        reason: 'network share (cifs)',
        poll_minutes: 15,
      }),
    ],
    ...over,
  };
}

beforeEach(() => {
  const sink: SettingsPayload[] = [];
  posted = sink;
  store = createSettingsStore({
    post: (body) => {
      sink.push(structuredClone(body));
      return Promise.resolve({ success: true });
    },
    delay: 0,
  });
});

afterEach(async () => {
  await act(() => store.flush());
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}

function stubStatus(answer: WatchStatus) {
  const calls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      calls.push(`${init?.method || 'GET'} ${url}`);
      return Promise.resolve(new Response(JSON.stringify({ success: true, ...answer })));
    }),
  );
  return calls;
}

describe('the words', () => {
  it('says how long ago', () => {
    expect(ago(null, NOW)).toBe('');
    expect(ago(NOW - 10, NOW)).toBe('just now');
    expect(ago(NOW - 120, NOW)).toBe('2 min ago');
    expect(ago(NOW - 3 * 3600, NOW)).toBe('3 h ago');
    expect(ago(NOW - 3 * 86400, NOW)).toBe('3 days ago');
  });

  it('one line for the whole watcher', () => {
    expect(statusLine(status(), true, NOW)).toBe('Watching 2 folders, last change 2 min ago.');
    expect(statusLine(status({ watching: 1, last_change: null }), true, NOW)).toBe(
      'Watching 1 folder, no changes yet.',
    );
    expect(statusLine(status({ pending: 3 }), true, NOW)).toContain('3 waiting to settle');
    expect(statusLine(null, false)).toContain('Folder watching is off');
    expect(statusLine(null, true)).toBe('Checking what is watched…');
  });

  it('each folder says how it is watched', () => {
    expect(modeWords(root({}))).toEqual({ tone: 'ok', words: 'Live' });
    expect(modeWords(root({ mode: 'polling', poll_minutes: 15 })).words).toBe(
      'Checked every 15 min',
    );
    expect(modeWords(root({ mode: 'polling', poll_minutes: 1 })).words).toBe(
      'Checked every minute',
    );
    expect(modeWords(root({ reachable: false }))).toEqual({ tone: 'warn', words: 'Not reachable' });
    expect(modeWords(root({ mode: 'off' })).words).toBe('Off');
  });
});

describe('the section', () => {
  it('is off until someone turns it on', () => {
    stubStatus(status());
    act(() => store.hydrate({ library: {} }));
    render(<FolderWatchSection store={store} />);
    expect((document.getElementById('watch-folders') as HTMLInputElement).checked).toBe(false);
  });

  it('shows every watched folder and its mode once it is on', async () => {
    stubStatus(status());
    act(() => store.hydrate({ library: { watch_folders: true } }));
    render(<FolderWatchSection store={store} />);
    const sw = document.getElementById('watch-folders') as HTMLInputElement;
    expect(sw.checked).toBe(true);
    await waitFor(() =>
      expect(document.getElementById('folder-watch-status')!.textContent).toMatch(
        /^Watching 2 folders, last change/,
      ),
    );
    const rows = document.querySelectorAll('[data-watch-root]');
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain('Live');
    expect(rows[1].textContent).toContain('Checked every 15 min');
    expect(rows[1].textContent).toContain('network share (cifs)');
  });

  it('turning it off saves the switch and asks the watcher to read it again', async () => {
    const calls = stubStatus(status());
    act(() => store.hydrate({ library: { watch_folders: true } }));
    render(<FolderWatchSection store={store} />);
    fireEvent.click(document.getElementById('watch-folders')!);
    await act(() => store.flush());
    expect(saved().library).toMatchObject({ watch_folders: false });
    expect(document.getElementById('folder-watch-status')!.textContent).toContain(
      'Folder watching is off',
    );
    await waitFor(() => expect(calls).toContain('POST /api/library/folder-watch/refresh'), {
      timeout: 3000,
    });
  });

  it('a folder can be put on a timer or turned off on its own', async () => {
    stubStatus(status());
    act(() =>
      store.hydrate({ library: { watch_folders: true, watch_root_modes: { '/other': 'off' } } }),
    );
    render(<FolderWatchSection store={store} />);
    await waitFor(() => expect(document.getElementById('watch-mode-1')).not.toBeNull());
    fireEvent.change(document.getElementById('watch-mode-1')!, { target: { value: 'off' } });
    await act(() => store.flush());
    expect(saved().library).toMatchObject({
      watch_root_modes: { '/other': 'off', '/mnt/nas': 'off' },
    });
  });

  it('the timer for network shares saves whole minutes', async () => {
    stubStatus(status());
    act(() => store.hydrate({ library: { watch_poll_minutes: 15 } }));
    render(<FolderWatchSection store={store} />);
    const field = document.getElementById('watch-poll-minutes') as HTMLInputElement;
    expect(field.value).toBe('15');
    fireEvent.change(field, { target: { value: '5' } });
    await act(() => store.flush());
    expect(saved().library).toMatchObject({ watch_poll_minutes: 5 });
    fireEvent.change(field, { target: { value: '0' } });
    await act(() => store.flush());
    expect(saved().library).toMatchObject({ watch_poll_minutes: 15 });
  });

  it('a server without watchdog says every folder is on a timer', async () => {
    stubStatus(status({ live_available: false }));
    act(() => store.hydrate({ library: { watch_folders: true } }));
    render(<FolderWatchSection store={store} />);
    await waitFor(() =>
      expect(document.getElementById('folder-watch-status')!.textContent).toContain(
        'needs the watchdog package',
      ),
    );
  });

  it('a failed status read leaves the line waiting, never an error page', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('offline'))),
    );
    act(() => store.hydrate({ library: { watch_folders: true } }));
    render(<FolderWatchSection store={store} />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(document.getElementById('folder-watch-status')!.textContent).toBe(
      'Checking what is watched…',
    );
  });
});
