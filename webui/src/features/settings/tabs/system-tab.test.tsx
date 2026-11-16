/**
 * System tab, step 9a (SETTINGS_PORT.md): Saving, Database Settings, Database
 * Maintenance and Logging are the markup they replaced and do what the
 * vanilla page did.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { initWeatherPreviewSettings } from '@/shell/weather-preview-settings';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { shape, vanilla } from './__fixtures__/shape';
import { cacheBytes, SystemTab } from './system-tab';

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
    'GET /api/v1/api-keys-internal': () => ({
      data: {
        keys: [
          { id: 'k1', label: 'Bot', scope: 'read', key_prefix: 'sk_ab', created_at: '2026-11-01' },
          { id: 'k2', scope: 'admin', key_prefix: 'sk_cd' },
        ],
      },
    }),
    'POST /api/v1/api-keys-internal/generate': () => ({ success: true, data: { key: 'sk_new' } }),
    'DELETE /api/v1/api-keys-internal/revoke/k1': () => ({ success: true }),
    'GET /api/profiles': () => ({
      profiles: [
        { id: 1, name: 'Admin' },
        { id: 4, name: 'Kid', can_download: false },
      ],
    }),
    'POST /api/dev-mode': () => ({ success: true }),
    'GET /api/image-cache/status': () => ({
      success: true,
      entries: 1200,
      pending: 30,
      bytes: 3 * 1024 * 1024 * 1024,
      max_bytes: 0,
      thumbnails: true,
    }),
    'POST /api/image-cache/prune': () => ({ success: true, expired: 1, evicted: 0 }),
    'POST /api/image-cache/clear': () => ({ success: true, removed: 5 }),
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
    ['tools', 'system-tools.vanilla.html'],
  ])('%s', (name, fixture) => {
    const { container } = part(name);
    expect(shape(container)).toEqual(shape(vanilla(fixture, { afterInit: true })));
  });
});

describe('Developer', () => {
  it('is the markup it replaced, its lists filled as the page filled them', () => {
    // the live page ran the weather preview's init over the old markup at start
    const old = vanilla('system-developer.vanilla.html', { afterInit: true });
    document.body.append(old);
    initWeatherPreviewSettings();
    const want = shape(old);
    old.remove();
    // the shell hands its own copy over on window (a separate bundle)
    shell.initWeatherPreviewSettings = initWeatherPreviewSettings;
    const { container } = part('developer');
    expect(shape(container)).toEqual(want);
    expect(
      (document.getElementById('sw-preview-preset') as HTMLSelectElement).options.length,
    ).toBeGreaterThan(1);
    delete shell.initWeatherPreviewSettings;
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

describe('SoulSync API, Developer Mode, Artwork Cache', () => {
  it('lists the keys, generates one to copy, and revokes after asking', async () => {
    shell.showConfirmDialog = vi.fn(() => Promise.resolve(true));
    const { container } = part('tools');
    act(() => store.hydrate({}));
    await waitFor(() => expect(container.querySelectorAll('.revoke-api-key-btn')).toHaveLength(2));
    const rows = [...container.querySelectorAll('#api-keys-list > div')].map((d) =>
      (d.textContent || '').replace(/\s+/g, ' ').trim(),
    );
    expect(rows[0]).toContain('Bot Read-only');
    expect(rows[1]).toContain('Unnamed Admin');
    fireEvent.change(input('api-key-label'), { target: { value: ' Home ' } });
    fireEvent.change(input('api-key-scope'), { target: { value: 'admin' } });
    fireEvent.click(container.querySelector('.auth-button')!);
    await waitFor(() => expect(input('api-key-value').textContent).toBe('sk_new'));
    expect(input('api-key-generated').style.display).toBe('block');
    expect(input('api-key-label').value).toBe('');
    expect(calls.find((c) => c.url.endsWith('/generate'))?.body).toEqual({
      label: 'Home',
      scope: 'admin',
    });
    fireEvent.click(container.querySelector('.revoke-api-key-btn')!);
    await waitFor(() => expect(shell.showToast).toHaveBeenCalledWith('API key revoked', 'success'));
    expect(shell.showConfirmDialog).toHaveBeenCalledWith(
      expect.objectContaining({
        message: 'Revoke API key "Bot"? Any apps using this key will stop working.',
      }),
    );
    delete shell.showConfirmDialog;
  });

  it('MCP and Lidarr load and save; the profile list marks request-only profiles', async () => {
    act(() => store.hydrate({ mcp: { enabled: true }, lidarr_compat: { profile_id: 4 } }));
    part('tools');
    expect(input('mcp-enabled').checked).toBe(true);
    expect(input('lidarr-compat-enabled').checked).toBe(false);
    await waitFor(() => expect(input('lidarr-compat-profile').value).toBe('4'));
    expect(
      [...document.querySelectorAll('#lidarr-compat-profile option')].map((o) => o.textContent),
    ).toEqual(['Admin', 'Kid (needs approval)']);
    fireEvent.click(input('lidarr-compat-enabled'));
    fireEvent.change(input('lidarr-compat-profile'), { target: { value: '1' } });
    fireEvent.click(input('mcp-enabled'));
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({
      lidarr_compat: { enabled: true, profile_id: 1 },
      mcp: { enabled: false },
    });
  });

  it('Seerr loads and saves its switch and fallback profile (#2151)', async () => {
    act(() => store.hydrate({ seerr_compat: { enabled: true, profile_id: 4 } }));
    part('tools');
    expect(input('seerr-compat-enabled').checked).toBe(true);
    await waitFor(() => expect(input('seerr-compat-profile').value).toBe('4'));
    expect(
      [...document.querySelectorAll('#seerr-compat-profile option')].map((o) => o.textContent),
    ).toEqual(['Admin', 'Kid (needs approval)']);
    // the lidarr list keeps its own value
    expect(input('lidarr-compat-profile').value).toBe('1');
    fireEvent.click(input('seerr-compat-enabled'));
    fireEvent.change(input('seerr-compat-profile'), { target: { value: '1' } });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({ seerr_compat: { enabled: false, profile_id: 1 } });
  });

  it('dev mode: activates, and the status follows every switch of it', async () => {
    part('tools');
    const nav = document.createElement('div');
    nav.id = 'hydrabase-nav';
    nav.style.display = 'none';
    document.body.append(nav);
    expect(input('dev-mode-status').textContent).toBe('Inactive');
    fireEvent.change(input('dev-mode-password'), { target: { value: 'pw' } });
    fireEvent.click(input('dev-mode-password').nextElementSibling!);
    await waitFor(() => expect(input('dev-mode-status').textContent).toBe('Active'));
    expect(nav.style.display).toBe('');
    expect(input('dev-mode-password').value).toBe('');
    // Hydrabase's disconnect turns it off by the same event
    act(() => {
      window.dispatchEvent(new CustomEvent('ss:dev-mode', { detail: { enabled: false } }));
    });
    expect(input('dev-mode-status').textContent).toBe('Inactive');
    nav.remove();
  });

  it('the artwork cache: status on arrival, settings saved, reclaim and clear', async () => {
    shell.showConfirmDialog = vi.fn(() => Promise.resolve(true));
    part('tools', { opened: 1, active: true });
    await waitFor(() =>
      expect(input('imgcache-entries').textContent).toBe(`${(1200).toLocaleString()} (30 pending)`),
    );
    expect(input('imgcache-size').textContent).toBe('3.00 GB (no limit)');
    expect(input('imgcache-max-mb').value).toBe('0');
    expect(input('imgcache-thumbnails').checked).toBe(true);
    fireEvent.change(input('imgcache-max-mb'), { target: { value: '2048' } });
    fireEvent.click(input('imgcache-thumbnails'));
    fireEvent.click(input('imgcache-prune-btn'));
    await waitFor(() =>
      expect(input('imgcache-status').textContent).toBe('Removed 1 cached image.'),
    );
    fireEvent.click(input('imgcache-clear-btn'));
    await waitFor(() =>
      expect(input('imgcache-status').textContent).toBe('Cleared 5 cached images.'),
    );
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({ image_cache: { max_cache_mb: 2048, thumbnails: false } });
    delete shell.showConfirmDialog;
  });
});

describe('the artwork cache size', () => {
  it('reads as _imgCacheBytes did, an empty cache as 0 MB', () => {
    expect(cacheBytes(0)).toBe('0 MB');
    expect(cacheBytes(undefined)).toBe('0 MB');
    expect(cacheBytes(5 * 1024 * 1024)).toBe('5.0 MB');
    expect(cacheBytes(3 * 1024 * 1024 * 1024)).toBe('3.00 GB');
  });
});
