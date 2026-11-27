/**
 * System > General, API, Maintenance and Developer (settings-bic phase 6):
 * the pills on the parts, and each control doing what the folded sections
 * did: the same endpoints, keys and save model.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { initWeatherPreviewSettings } from '@/shell/weather-preview-settings';

import { nav } from '../nav';
import { pageTabs } from '../page-tabs';
import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { cacheBytes } from './system-tab';
import { SystemPage } from './system/system-page';

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
      wal_size_display: '12 MB',
      vacuum_has_room: true,
    }),
    'POST /api/database/maintenance/vacuum': () => ({
      success: true,
      elapsed_seconds: 42,
      saved_display: '48 MB',
    }),
    'POST /api/database/maintenance/enable-incremental-vacuum': () => ({
      success: true,
      elapsed_seconds: 7,
      saved_display: '1 MB',
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
        { id: 1, name: 'Admin', is_admin: true },
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
    'POST /api/quarantine/clear': () => ({ success: true, message: 'Quarantine cleared' }),
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
  shell.showConfirmDialog = vi.fn(() => Promise.resolve(true));
});

afterEach(async () => {
  await act(() => store.flush());
  vi.unstubAllGlobals();
  vi.useRealTimers();
  delete shell.showToast;
  delete shell.showConfirmDialog;
  delete shell._settingsAutosaveOff;
  nav.reset();
  pageTabs.reset();
  document.body.removeAttribute('data-side');
});

/** the System page, on one of its pills */
function page(view: string, opened = 1) {
  act(() => nav.go('advanced', view));
  return render(<SystemPage store={store} part="page" opened={opened} active />);
}
const input = (id: string) => document.getElementById(id) as HTMLInputElement;
const panel = (view: string) => document.getElementById(`stg-view-advanced-${view}`)!;
const radio = (group: string, value: string) =>
  document.querySelector<HTMLElement>(`#${group} [data-value="${value}"]`)!;
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}

describe('the page', () => {
  it('every control is drawn once, on its pill, named without a colon or em dash', () => {
    page('general');
    const where: Record<string, string[]> = {
      general: ['settings-autosave-enabled', 'max-workers', 'log-level-select', 'log-path-display'],
      security: ['security-require-pin', 'security-require-login', 'security-cors-origins'],
      api: [
        'api-key-label',
        'api-key-scope',
        'mcp-enabled',
        'lidarr-compat-enabled',
        'lidarr-compat-profile',
        'seerr-compat-enabled',
        'seerr-compat-refuse-unknown',
        'seerr-compat-seerr-url',
        'seerr-compat-seerr-api-key',
        'seerr-compat-test-btn',
      ],
      maintenance: [
        'db-size-display',
        'db-wal-display',
        'db-optimize-btn',
        'db-measure-btn',
        'db-vacuum-btn',
        'db-incvacuum-btn',
        'quarantine-clear-btn',
        'imgcache-max-mb',
        'imgcache-thumbnails',
        'imgcache-prune-btn',
        'imgcache-clear-btn',
      ],
      developer: [
        'dev-mode-password',
        'sw-preview-preset',
        'ob-preview-step',
        'experimental-jiosaavn-enabled',
        'experimental-bandcamp-enabled',
      ],
      logs: ['log-viewer-source', 'log-viewer-search', 'log-viewer-autoscroll'],
    };
    for (const [view, ids] of Object.entries(where))
      for (const id of ids) {
        expect(document.querySelectorAll(`#${id}`), id).toHaveLength(1);
        expect(panel(view).contains(input(id)), `${id} on ${view}`).toBe(true);
      }
    // the old page's folded sections and their markup went
    expect(document.querySelector('.settings-section-header, .form-group, [data-stg]')).toBeNull();
    for (const label of document.querySelectorAll('label, h4'))
      expect(label.textContent, label.textContent ?? '').not.toMatch(/:\s*$|—/);
    // Experimental is the music side's
    expect(
      input('experimental-jiosaavn-enabled').closest('[data-music-only]'),
      'Experimental card',
    ).not.toBeNull();
  });

  it('a pill shows only its own panel, and each opens with what it is for', () => {
    act(() => store.hydrate({ ui_appearance: {}, database: { max_workers: 7 } }));
    page('general');
    expect(panel('general').hidden).toBe(false);
    expect(panel('security').hidden).toBe(true);
    const about = (view: string) => panel(view).querySelector('[data-about]')!.textContent;
    expect(about('general')).toBe('How settings save, database workers and the server log level.');
    act(() => nav.view('api'));
    expect(panel('api').hidden).toBe(false);
    expect(about('api')).toBe('Keys that let other apps talk to SoulSync.');
    expect(about('logs')).toBe('What the server writes to its log, and the live viewer.');
  });
});

describe('General', () => {
  it('switching autosave off flips the flag at once and still saves the switch', async () => {
    vi.useFakeTimers();
    store = createSettingsStore({
      post: (body) => {
        posted.push(structuredClone(body));
        return Promise.resolve({ success: true });
      },
    });
    act(() => store.hydrate({ ui_appearance: {} }));
    page('general');
    expect(input('settings-autosave-enabled').checked).toBe(true);
    expect(input('settings-autosave-enabled').getAttribute('role')).toBe('switch');
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

  it('workers load and save as a number', async () => {
    act(() => store.hydrate({ database: { max_workers: 3 } }));
    page('general');
    expect(input('max-workers').value).toBe('3');
    fireEvent.change(input('max-workers'), { target: { value: '9' } });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({ database: { max_workers: 9 } });
  });

  it('the log level shows what the server runs at, and a pick applies at once', async () => {
    act(() => store.hydrate({ logging: { level: 'WARNING', path: '/var/log/s.log' } }));
    page('general');
    expect(input('log-path-display').textContent).toBe('/var/log/s.log');
    await waitFor(() => expect(input('log-level-select').dataset.value).toBe('DEBUG'));
    expect(radio('log-level-select', 'DEBUG').getAttribute('aria-checked')).toBe('true');
    fireEvent.click(radio('log-level-select', 'ERROR'));
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith('Log level changed to ERROR', 'success'),
    );
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ level: 'ERROR' });
    // not a settings key: nothing goes through the page save
    expect(store.hasPending()).toBe(false);
    // the row says what the level writes
    expect(panel('general').textContent).toContain('Writes errors only.');
  });
});

describe('Maintenance', () => {
  it('loads each time its pill opens, not before', async () => {
    page('general');
    expect(calls.some((c) => c.url === '/api/database/maintenance/info')).toBe(false);
    expect(input('db-size-display').textContent).toBe('Loading…');
    act(() => nav.view('maintenance'));
    await waitFor(() => expect(input('db-size-display').textContent).toBe('11.2 GB'));
    expect(input('db-freepages-display').textContent).toBe(
      `48 MB (${(12345).toLocaleString()} pages)`,
    );
    expect(input('db-wal-display').textContent).toBe('12 MB');
    expect(calls.filter((c) => c.url === '/api/image-cache/status')).toHaveLength(1);
    expect(panel('maintenance').textContent).toContain('48 MB (12,345 pages)');
    act(() => nav.view('api'));
    act(() => nav.view('maintenance'));
    await waitFor(() =>
      expect(calls.filter((c) => c.url === '/api/database/maintenance/info')).toHaveLength(2),
    );
  });

  it("a compact asks with SoulSync's own dialog first, and says when there is no room", async () => {
    routes['GET /api/database/maintenance/info'] = () => ({
      success: true,
      total_size_display: '11.2 GB',
      vacuum_has_room: false,
    });
    const confirm = vi.spyOn(window, 'confirm');
    shell.showConfirmDialog = vi.fn().mockResolvedValueOnce(false).mockResolvedValue(true);
    page('maintenance');
    await waitFor(() => expect(input('db-vacuum-room')).toBeTruthy());
    expect(input('db-vacuum-room').textContent).toContain('not enough free disk space');
    // the danger zone holds it
    expect(input('db-vacuum-btn').closest('[data-card="Danger zone"]')).not.toBeNull();
    fireEvent.click(input('db-vacuum-btn'));
    await act(() => Promise.resolve());
    expect(calls.some((c) => c.url.endsWith('/vacuum'))).toBe(false);
    expect(shell.showConfirmDialog).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'Compact the database?',
        destructive: true,
        message: expect.stringContaining('so it will likely fail'),
      }),
    );
    fireEvent.click(input('db-vacuum-btn'));
    await waitFor(() =>
      expect(input('db-vacuum-status').textContent).toBe('Done in 42s. Saved 48 MB.'),
    );
    expect(shell.showToast).toHaveBeenCalledWith(
      'Database compacted in 42s, saved 48 MB',
      'success',
    );
    expect(confirm).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it('tidy up runs without asking, and the biggest tables measure off the page (#2205)', async () => {
    routes['POST /api/database/maintenance/optimize'] = () => ({
      success: true,
      elapsed_seconds: 3,
      pruned: { lib2_provider_attempts: 1200, sync_history: 34 },
      wal_after: 0,
    });
    let measured = false;
    const tables = () =>
      measured
        ? {
            success: true,
            estimates: [],
            measure: {
              state: 'done',
              result: { tables: [{ name: 'lib2_tracks', bytes: 2 * 1024 ** 3 }] },
            },
          }
        : {
            success: true,
            estimates: [{ name: 'lib2_tracks', rows: 312000 }],
            measure: { state: 'running' },
          };
    routes['GET /api/database/maintenance/tables'] = tables;
    routes['GET /api/database/maintenance/tables?measure=1'] = tables;
    page('maintenance');
    await waitFor(() => expect(input('db-wal-display').textContent).toBe('12 MB'));
    fireEvent.click(input('db-optimize-btn'));
    await waitFor(() =>
      expect(input('db-optimize-status').textContent).toBe(
        `Done in 3s. ${(1234).toLocaleString()} old cache and log rows removed, search indexes merged, write-ahead log now 0.0 MB.`,
      ),
    );
    expect(shell.showConfirmDialog).not.toHaveBeenCalled();
    fireEvent.click(input('db-measure-btn'));
    await waitFor(() => expect(input('db-measure-btn').textContent).toBe('Measuring…'));
    expect(calls.some((c) => c.url.endsWith('/tables?measure=1'))).toBe(true);
    expect(input('db-tables').textContent).toContain(`about ${(312000).toLocaleString()} rows`);
    measured = true;
    await waitFor(() => expect(input('db-tables').textContent).toContain('2.0 GB'), {
      timeout: 5000,
    });
    expect(input('db-tables').textContent).toContain('Measured sizes, biggest first.');
  });

  it('an incremental database says so, with no button to press again', async () => {
    routes['GET /api/database/maintenance/info'] = () => ({ success: true, auto_vacuum: 2 });
    page('maintenance');
    await waitFor(() => expect(input('db-incvacuum-btn').hidden).toBe(true));
    expect(input('db-incvacuum-btn').closest('[data-row]')!.textContent).toContain('On');
  });

  it('incremental vacuum asks first, then turns on', async () => {
    page('maintenance');
    await waitFor(() => expect(input('db-size-display').textContent).toBe('11.2 GB'));
    fireEvent.click(input('db-incvacuum-btn'));
    await waitFor(() =>
      expect(input('db-vacuum-status').textContent).toBe('Turned on in 7s, saved 1 MB'),
    );
    expect(shell.showConfirmDialog).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Turn on incremental vacuum?' }),
    );
  });

  it('clear quarantine moved here: it asks, then empties the folder', async () => {
    shell.showConfirmDialog = vi.fn().mockResolvedValueOnce(false).mockResolvedValue(true);
    page('maintenance');
    fireEvent.click(input('quarantine-clear-btn'));
    await act(() => Promise.resolve());
    expect(calls.some((c) => c.url === '/api/quarantine/clear')).toBe(false);
    fireEvent.click(input('quarantine-clear-btn'));
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith('Quarantine cleared', 'success'),
    );
    expect(calls.find((c) => c.url === '/api/quarantine/clear')?.method).toBe('POST');
    expect(input('quarantine-clear-btn').closest('[data-card="Danger zone"]')).not.toBeNull();
  });

  it('the artwork cache: status on arrival, settings saved, reclaim and clear', async () => {
    page('maintenance');
    await waitFor(() =>
      expect(input('imgcache-entries').textContent).toBe(`${(1200).toLocaleString()} (30 waiting)`),
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
    expect(shell.showConfirmDialog).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Clear the artwork cache?' }),
    );
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({ image_cache: { max_cache_mb: 2048, thumbnails: false } });
  });

  it('the artwork cache size reads as _imgCacheBytes did, an empty cache as 0 MB', () => {
    expect(cacheBytes(0)).toBe('0 MB');
    expect(cacheBytes(undefined)).toBe('0 MB');
    expect(cacheBytes(5 * 1024 * 1024)).toBe('5.0 MB');
    expect(cacheBytes(3 * 1024 * 1024 * 1024)).toBe('3.00 GB');
  });
});

describe('API', () => {
  it('lists the keys, generates one to copy, and revokes after asking', async () => {
    page('api');
    act(() => store.hydrate({}));
    await waitFor(() => expect(document.querySelectorAll('.revoke-api-key-btn')).toHaveLength(2));
    const rows = [...document.querySelectorAll('#api-keys-list [data-api-key]')].map((d) =>
      (d.textContent || '').replace(/\s+/g, ' ').trim(),
    );
    expect(rows[0]).toContain('Bot Read-only');
    expect(rows[0]).toContain('never used');
    expect(rows[1]).toContain('Unnamed Admin');
    expect(input('api-key-generated').hidden).toBe(true);
    fireEvent.change(input('api-key-label'), { target: { value: ' Home ' } });
    fireEvent.click(radio('api-key-scope', 'admin'));
    expect(panel('api').textContent).toContain('Can change settings and do everything else.');
    fireEvent.click(screen.getByRole('button', { name: 'Generate key' }));
    await waitFor(() => expect(input('api-key-value').textContent).toBe('sk_new'));
    expect(input('api-key-generated').hidden).toBe(false);
    expect(input('api-key-label').value).toBe('');
    expect(calls.find((c) => c.url.endsWith('/generate'))?.body).toEqual({
      label: 'Home',
      scope: 'admin',
    });
    fireEvent.click(screen.getByRole('button', { name: 'Revoke Bot' }));
    await waitFor(() => expect(shell.showToast).toHaveBeenCalledWith('API key revoked', 'success'));
    expect(shell.showConfirmDialog).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Revoke Bot?', destructive: true }),
    );
    expect(calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/revoke/k1'))).toBe(true);
  });

  it('a revoke said no to sends nothing', async () => {
    shell.showConfirmDialog = vi.fn(() => Promise.resolve(false));
    page('api');
    act(() => store.hydrate({}));
    await waitFor(() => expect(document.querySelectorAll('.revoke-api-key-btn')).toHaveLength(2));
    fireEvent.click(screen.getByRole('button', { name: 'Revoke Bot' }));
    await act(() => Promise.resolve());
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
  });

  it('MCP and Lidarr load and save; the profile list marks request-only profiles', async () => {
    act(() => store.hydrate({ mcp: { enabled: true }, lidarr_compat: { profile_id: 4 } }));
    page('api');
    expect(input('mcp-enabled').checked).toBe(true);
    expect(input('lidarr-compat-enabled').checked).toBe(false);
    // the profile row dims while Lidarr is off (still saved)
    expect(input('lidarr-compat-profile').closest('[data-row]')!.className).toMatch(/rowOff/);
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
    page('api');
    expect(input('seerr-compat-enabled').checked).toBe(true);
    await waitFor(() => expect(input('seerr-compat-profile').value).toBe('4'));
    // the lidarr list keeps its own value
    expect(input('lidarr-compat-profile').value).toBe('1');
    fireEvent.click(input('seerr-compat-enabled'));
    fireEvent.change(input('seerr-compat-profile'), { target: { value: '1' } });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({ seerr_compat: { enabled: false, profile_id: 1 } });
  });

  it('Seerr: an install from before #2166 shows who unknown users act as, and can refuse them', async () => {
    act(() => store.hydrate({ seerr_compat: { enabled: true, profile_id: 4 } }));
    page('api');
    expect(input('seerr-compat-refuse-unknown').checked).toBe(false);
    await waitFor(() => expect(input('seerr-compat-profile').value).toBe('4'));
    expect(panel('api').textContent).toContain('Jellyfin users with no profile act as');
    fireEvent.click(input('seerr-compat-refuse-unknown'));
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({ seerr_compat: { refuse_unknown: true } });
    expect(document.getElementById('seerr-compat-profile')).toBeNull();
    expect(panel('api').textContent).toContain("can't browse or request");
  });

  it('Seerr: a new install refuses unknown users and can go back to a fallback profile', async () => {
    act(() =>
      store.hydrate({ seerr_compat: { enabled: true, profile_id: 1, refuse_unknown: true } }),
    );
    page('api');
    expect(input('seerr-compat-refuse-unknown').checked).toBe(true);
    expect(document.getElementById('seerr-compat-profile')).toBeNull();
    fireEvent.click(input('seerr-compat-refuse-unknown'));
    await waitFor(() => expect(input('seerr-compat-profile').value).toBe('1'));
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({ seerr_compat: { refuse_unknown: false } });
  });
});

describe('Seerr in front (#2324)', () => {
  it('saves the Seerr URL and key, keeps a saved key masked, and tests the connection', async () => {
    routes['POST /api/test-connection'] = () => ({
      success: true,
      message: 'Connected to Seerr v2.7.3',
    });
    act(() =>
      store.hydrate({
        seerr_compat: {
          enabled: true,
          refuse_unknown: true,
          seerr_url: 'http://seerr:5055',
          seerr_api_key: '__redacted_unchanged__',
        },
      }),
    );
    page('api');
    expect(input('seerr-compat-seerr-url').value).toBe('http://seerr:5055');
    expect(input('seerr-compat-seerr-api-key').value).toBe('__redacted_unchanged__');
    expect(panel('api').textContent).toContain('Saved keys are never sent back');
    fireEvent.change(input('seerr-compat-seerr-url'), { target: { value: 'http://seerr:5056' } });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({ seerr_compat: { seerr_url: 'http://seerr:5056' } });
    fireEvent.click(input('seerr-compat-test-btn'));
    await waitFor(() =>
      expect(input('seerr-compat-test-status').textContent).toBe('Connected to Seerr v2.7.3'),
    );
    expect(calls.find((c) => c.url === '/api/test-connection')?.body).toEqual({
      service: 'seerr_compat',
    });
  });

  it('a failed test says why, and Test waits for a URL and key', async () => {
    routes['POST /api/test-connection'] = () => ({
      success: false,
      error: 'seerr answered HTTP 403',
    });
    act(() => store.hydrate({ seerr_compat: { enabled: true, seerr_url: '', seerr_api_key: '' } }));
    page('api');
    expect((input('seerr-compat-test-btn') as unknown as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(input('seerr-compat-seerr-url'), { target: { value: 'http://seerr:5055' } });
    fireEvent.change(input('seerr-compat-seerr-api-key'), { target: { value: 'abc' } });
    fireEvent.click(input('seerr-compat-test-btn'));
    await waitFor(() =>
      expect(input('seerr-compat-test-status').textContent).toBe('seerr answered HTTP 403'),
    );
  });
});

describe('Developer', () => {
  it('dev mode: activates, and the status follows every switch of it', async () => {
    page('developer');
    const hydra = document.createElement('div');
    hydra.id = 'hydrabase-nav';
    hydra.style.display = 'none';
    document.body.append(hydra);
    expect(input('dev-mode-status').textContent).toBe('Inactive');
    fireEvent.change(input('dev-mode-password'), { target: { value: 'pw' } });
    fireEvent.click(within(panel('developer')).getByRole('button', { name: 'Activate' }));
    await waitFor(() => expect(input('dev-mode-status').textContent).toBe('Active'));
    expect(calls.find((c) => c.url === '/api/dev-mode')?.body).toEqual({ password: 'pw' });
    expect(hydra.style.display).toBe('');
    expect(input('dev-mode-password').value).toBe('');
    expect(input('dev-mode-status').textContent).toBe('Active');
    // Hydrabase's disconnect turns it off by the same event
    act(() => {
      window.dispatchEvent(new CustomEvent('ss:dev-mode', { detail: { enabled: false } }));
    });
    expect(input('dev-mode-status').textContent).toBe('Inactive');
    hydra.remove();
  });

  it('the preview tools are on the page for their own modules, filled as before', () => {
    // the shell hands its own copy over on window (a separate bundle)
    shell.initWeatherPreviewSettings = initWeatherPreviewSettings;
    page('developer');
    expect(
      (document.getElementById('sw-preview-preset') as HTMLSelectElement).options.length,
    ).toBeGreaterThan(1);
    for (const id of [
      'sw-preview-holiday',
      'sw-preview-date',
      'sw-preview-reset',
      'sw-preview-status',
      'ob-preview-env',
      'ob-preview-open',
    ])
      expect(document.getElementById(id), id).not.toBeNull();
    delete shell.initWeatherPreviewSettings;
  });

  it('the video side reads back no experiments', () => {
    document.body.setAttribute('data-side', 'video');
    act(() => store.hydrate({ experimental: { bandcamp_enabled: true } }));
    page('developer');
    expect(panel('developer').textContent).not.toContain('Bandcamp.');
    expect(panel('developer').textContent).not.toContain('Experiments on');
  });
});
