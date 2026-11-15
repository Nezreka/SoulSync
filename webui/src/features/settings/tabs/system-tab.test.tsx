/**
 * System tab, step 9a (SETTINGS_PORT.md): Saving, Database Settings, Database
 * Maintenance and Logging are the markup they replaced and do what the
 * vanilla page did.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { shape, vanilla } from './__fixtures__/shape';
import { SystemTab } from './system-tab';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

let store: SettingsStore;
let posted: SettingsPayload[];
let calls: Array<{ url: string; method: string; body?: unknown }>;
let routes: Record<string, () => unknown>;

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
  calls = [];
  routes = {
    'GET /api/database/maintenance/info': () => ({
      success: true,
      total_size_display: '11.2 GB',
      free_pages: 12345,
      free_size_display: '48 MB',
      auto_vacuum_label: 'Off',
      auto_vacuum: 0,
    }),
    'POST /api/database/maintenance/vacuum': () => ({
      success: true,
      elapsed_seconds: 42,
      saved_display: '48 MB',
    }),
    'GET /api/settings/log-level': () => ({ success: true, level: 'DEBUG' }),
    'POST /api/settings/log-level': () => ({ success: true }),
  };
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method || 'GET';
      const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined;
      calls.push({ url, method, body });
      const data = routes[`${method} ${url}`]?.() ?? { success: false };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data) } as Response);
    }),
  );
  shell.showToast = vi.fn();
});

afterEach(async () => {
  await act(() => store.flush());
  vi.unstubAllGlobals();
  vi.useRealTimers();
  delete shell.showToast;
  delete shell._settingsAutosaveOff;
});

const part = (name: string, props: Partial<{ opened: number; active: boolean }> = {}) =>
  render(<SystemTab store={store} part={name} opened={0} active={false} {...props} />);
const input = (id: string) => document.getElementById(id) as HTMLInputElement;
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}

describe('the sections are the markup they replaced', () => {
  it.each([
    ['saving', 'system-saving.vanilla.html'],
    ['database', 'system-database.vanilla.html'],
  ])('%s', (name, fixture) => {
    const { container } = part(name);
    expect(shape(container)).toEqual(shape(vanilla(fixture, { afterInit: true })));
  });
});

describe('Saving', () => {
  it('switching autosave off flips the flag at once and still saves the switch', async () => {
    vi.useFakeTimers();
    store = createSettingsStore({
      post: (body) => {
        posted.push(structuredClone(body));
        return Promise.resolve({ success: true });
      },
    });
    act(() => store.hydrate({ ui_appearance: {} }));
    part('saving');
    expect(input('settings-autosave-enabled').checked).toBe(true);
    fireEvent.click(input('settings-autosave-enabled'));
    expect(shell._settingsAutosaveOff).toBe(true);
    // a change after it waits for the save button
    store.set('database', 'max_workers', 7);
    await act(() => vi.advanceTimersByTimeAsync(2000));
    // the switch's own save took the waiting change with it, as the page save did
    expect(posted).toEqual([
      { ui_appearance: { settings_autosave: false }, database: { max_workers: 7 } },
    ]);
    store.set('database', 'max_workers', 8);
    await act(() => vi.advanceTimersByTimeAsync(5000));
    expect(posted).toHaveLength(1);
    // switching it back on saves through autosave
    fireEvent.click(input('settings-autosave-enabled'));
    expect(shell._settingsAutosaveOff).toBe(false);
    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(posted[1]).toEqual({
      database: { max_workers: 8 },
      ui_appearance: { settings_autosave: true },
    });
  });
});

describe('Database and Logging', () => {
  it('workers load and save as a number', async () => {
    act(() => store.hydrate({ database: { max_workers: 3 } }));
    part('database');
    expect(input('max-workers').value).toBe('3');
    fireEvent.change(input('max-workers'), { target: { value: '9' } });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({ database: { max_workers: 9 } });
  });

  it('maintenance loads on arriving at the tab, and a compact asks first', async () => {
    const { rerender } = part('database');
    expect(input('db-size-display').textContent).toBe('Loading...');
    rerender(<SystemTab store={store} part="database" opened={1} active />);
    await waitFor(() => expect(input('db-size-display').textContent).toBe('11.2 GB'));
    expect(input('db-freepages-display').textContent).toBe(
      `${(12345).toLocaleString()} (48 MB reclaimable)`,
    );
    const confirm = vi
      .spyOn(window, 'confirm')
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true);
    fireEvent.click(input('db-vacuum-btn'));
    expect(calls.some((c) => c.url.endsWith('/vacuum'))).toBe(false);
    fireEvent.click(input('db-vacuum-btn'));
    await waitFor(() =>
      expect(input('db-vacuum-status').textContent).toBe('Done in 42s. Saved 48 MB.'),
    );
    expect(input('db-vacuum-status').style.display).toBe('block');
    expect(shell.showToast).toHaveBeenCalledWith(
      'Database compacted in 42s — saved 48 MB',
      'success',
    );
    expect(input('db-vacuum-btn').textContent).toBe('Compact Database (VACUUM)');
    confirm.mockRestore();
  });

  it('an incremental database says so on its button', async () => {
    routes['GET /api/database/maintenance/info'] = () => ({ success: true, auto_vacuum: 2 });
    part('database', { opened: 1, active: true });
    await waitFor(() =>
      expect(input('db-incvacuum-btn').textContent).toBe('Incremental Vacuum Enabled'),
    );
    expect(input('db-incvacuum-btn').disabled).toBe(true);
    expect(input('db-incvacuum-btn').style.opacity).toBe('0.5');
  });

  it('the log level shows what the server runs at, and a change applies at once', async () => {
    act(() => store.hydrate({ logging: { level: 'WARNING', path: '/var/log/s.log' } }));
    part('database');
    expect(input('log-path-display').textContent).toBe('/var/log/s.log');
    await waitFor(() => expect(input('log-level-select').value).toBe('DEBUG'));
    fireEvent.change(input('log-level-select'), { target: { value: 'ERROR' } });
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith('Log level changed to ERROR', 'success'),
    );
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ level: 'ERROR' });
    // not a settings key: nothing goes through the page save
    expect(store.hasPending()).toBe(false);
  });
});
