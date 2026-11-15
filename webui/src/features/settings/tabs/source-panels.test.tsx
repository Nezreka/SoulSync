/**
 * The Deezer, SoundCloud and Lidarr source panels, ported from the vanilla
 * Settings page (SETTINGS_PORT.md, step 7c): the same markup, the same saves,
 * the masked-secret rules, and the same tests with the same words.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { shape, vanilla } from './__fixtures__/shape';
import { REDACTED_SECRET_SENTINEL } from './parts';
import {
  DeezerPanel,
  HifiPanel,
  LidarrPanel,
  QobuzPanel,
  SoulseekPanel,
  SoundcloudPanel,
  SourcePanels,
  TidalPanel,
} from './source-panels';
import { createSourcesModel, type SourcesModel } from './sources-model';
import { SourceModal } from './sources-tab';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

let store: SettingsStore;
let model: SourcesModel;
let posted: SettingsPayload[];
let calls: Array<{ url: string; method: string; body: unknown }>;
let routes: Record<string, (body: unknown) => unknown>;

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
  model = createSourcesModel({ store });
  calls = [];
  routes = {};
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method || 'GET';
      const body = init?.body ? JSON.parse(init.body as string) : undefined;
      calls.push({ url, method, body });
      const route = routes[`${method} ${url}`];
      if (!route) return Promise.reject(new Error('down'));
      return Promise.resolve({ ok: true, json: () => Promise.resolve(route(body)) } as Response);
    }),
  );
});

afterEach(async () => {
  await act(() => store.flush());
  vi.unstubAllGlobals();
  delete shell.saveSettings;
  document.getElementById('deezer-connection-arl')?.remove();
});

const input = (id: string) => document.getElementById(id) as HTMLInputElement;
const status = (id: string) => document.getElementById(id)!;
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}

describe('the panels are the markup they replaced', () => {
  it('Deezer, SoundCloud, Lidarr', () => {
    const { container } = render(
      <SourcePanels store={store} part="deezer-soundcloud-lidarr" opened={0} active={false} />,
    );
    const got = shape(container);
    // the React panels carry data-react-panel, which is not in the compared attributes
    const want = shape(vanilla('panels-deezer-soundcloud-lidarr.vanilla.html'));
    expect(got.elements).toEqual(want.elements);
    expect(got.text).toBe(want.text);
    expect(container.querySelectorAll('[data-react-panel]')).toHaveLength(3);
  });
});

describe('the Tidal and Qobuz panels are the markup they replaced', () => {
  it('Tidal, Qobuz', () => {
    const { container } = render(
      <SourcePanels store={store} part="tidal-qobuz" opened={0} active={false} />,
    );
    const got = shape(container);
    const want = shape(vanilla('panels-tidal-qobuz.vanilla.html'));
    expect(got.elements).toEqual(want.elements);
    expect(got.text).toBe(want.text);
  });
});

describe('Soulseek', () => {
  it('is the markup it replaced', () => {
    const { container } = render(
      <SourcePanels store={store} part="soulseek" opened={0} active={false} />,
    );
    const got = shape(container);
    const want = shape(vanilla('panel-soulseek.vanilla.html'));
    expect(got.elements).toEqual(want.elements);
    expect(got.text).toBe(want.text);
  });

  it('reads saved values as loadSettingsData did', () => {
    act(() => store.hydrate({}));
    render(<SoulseekPanel store={store} />);
    const v = (id: string) => input(id).value;
    expect(v('soulseek-search-timeout')).toBe('60');
    expect(v('soulseek-search-timeout-buffer')).toBe('15');
    expect(v('soulseek-search-min-delay-seconds')).toBe('0');
    expect(v('soulseek-min-peer-speed')).toBe('0');
    expect(v('soulseek-max-peer-queue')).toBe('0');
    expect(input('soulseek-observed-speed-fallback-enabled').checked).toBe(false);
    expect(v('soulseek-min-observed-download-speed')).toBe('250');
    expect(v('soulseek-download-timeout')).toBe('10');
    expect(input('soulseek-auto-clear-searches').checked).toBe(true);
    expect(v('soulseek-cleanup-scope')).toBe('own');
    act(() =>
      store.hydrate({
        soulseek: {
          download_timeout: 900,
          min_observed_download_speed_kbps: 0,
          search_min_delay_seconds: 3,
          cleanup_scope: 'all',
        },
      }),
    );
    expect(v('soulseek-download-timeout')).toBe('15');
    expect(v('soulseek-min-observed-download-speed')).toBe('0');
    expect(v('soulseek-search-min-delay-seconds')).toBe('3');
    expect(v('soulseek-cleanup-scope')).toBe('all');
  });

  it('saves each key the way the collector parsed it', async () => {
    act(() => store.hydrate({ soulseek: {} }));
    render(<SoulseekPanel store={store} />);
    fireEvent.change(input('soulseek-url'), { target: { value: 'http://slskd:5030' } });
    fireEvent.change(input('soulseek-search-timeout'), { target: { value: '' } });
    fireEvent.change(input('soulseek-download-timeout'), { target: { value: '12' } });
    fireEvent.change(input('soulseek-min-observed-download-speed'), { target: { value: '' } });
    fireEvent.change(input('soulseek-min-peer-speed'), { target: { value: '5' } });
    fireEvent.click(input('soulseek-auto-clear-searches'));
    fireEvent.change(input('soulseek-cleanup-scope'), { target: { value: 'all' } });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({
      soulseek: {
        slskd_url: 'http://slskd:5030',
        search_timeout: 60,
        download_timeout: 720,
        min_observed_download_speed_kbps: 250,
        min_peer_upload_speed: 5,
        auto_clear_searches: false,
        cleanup_scope: 'all',
      },
    });
    // what was typed stays on screen
    expect(input('soulseek-search-timeout').value).toBe('');
  });

  it('auto-detect fills the url once per click', async () => {
    shell.showToast = vi.fn();
    shell.showLoadingOverlay = vi.fn();
    shell.hideLoadingOverlay = vi.fn();
    const order: string[] = [];
    shell.testConnection = vi.fn(() => {
      order.push('test');
      return Promise.resolve();
    });
    routes['POST /api/detect-soulseek'] = () => ({ success: true, found_url: 'http://found:5030' });
    const slow = createSettingsStore({
      post: () => {
        order.push('save');
        return Promise.resolve({ success: true });
      },
      delay: 60_000,
    });
    act(() => slow.hydrate({ soulseek: {} }));
    render(<SoulseekPanel store={slow} />);
    fireEvent.click(document.querySelector('.detect-button')!);
    await waitFor(() => expect(input('soulseek-url').value).toBe('http://found:5030'));
    expect(calls.filter((c) => c.url === '/api/detect-soulseek')).toHaveLength(1);
    expect(shell.showToast).toHaveBeenCalledWith(
      'Soulseek server detected: http://found:5030',
      'success',
    );
    expect(shell.hideLoadingOverlay).toHaveBeenCalledOnce();
    // (test-after-save ordering uses the page's own store: checked in the browser)
    expect(order).toEqual([]);
    for (const k of ['showToast', 'showLoadingOverlay', 'hideLoadingOverlay', 'testConnection'])
      delete shell[k];
  });
});

describe('HiFi', () => {
  const instances = () => [
    { url: 'https://a.example', enabled: true },
    { url: 'https://b.example', enabled: false },
  ];

  it('is the markup it replaced; the list loads with the settings', async () => {
    routes['GET /api/hifi/instances/list'] = () => ({ instances: instances() });
    const { container } = render(
      <SourcePanels store={store} part="hifi" opened={0} active={false} />,
    );
    const got = shape(container);
    const want = shape(vanilla('panel-hifi.vanilla.html'));
    expect(got.elements).toEqual(want.elements);
    expect(got.text).toBe(want.text);
    expect(calls).toHaveLength(0);
    act(() => store.hydrate({}));
    await waitFor(() => expect(document.querySelectorAll('.hifi-instance-item')).toHaveLength(2));
    const rows = [...document.querySelectorAll<HTMLElement>('.hifi-instance-item')];
    expect(rows.map((r) => r.className)).toEqual([
      'hifi-instance-item',
      'hifi-instance-item disabled',
    ]);
    expect(rows[0].querySelector('.hifi-instance-toggle')!.className).toBe(
      'hifi-instance-toggle on',
    );
    expect(rows[0].querySelector('.hifi-instance-toggle')!.getAttribute('title')).toBe(
      'Click to disable',
    );
    expect(rows[1].querySelector('.hifi-instance-toggle')!.textContent).toBe('\u2718');
  });

  it('adds, toggles, removes and restores through the server, then rereads the list', async () => {
    const alert = vi.spyOn(window, 'alert').mockImplementation(() => undefined);
    shell.showToast = vi.fn();
    routes['GET /api/hifi/instances/list'] = () => ({ instances: instances() });
    routes['POST /api/hifi/instances'] = () => ({ success: false, error: 'already there' });
    routes['POST /api/hifi/instances/toggle'] = () => ({ success: true });
    routes['DELETE /api/hifi/instances?url=https%3A%2F%2Fb.example'] = () => ({ success: true });
    routes['POST /api/hifi/instances/reset'] = () => ({ success: true, restored: 1 });
    render(<HifiPanel store={store} model={model} />);
    act(() => store.hydrate({}));
    await waitFor(() => expect(document.querySelectorAll('.hifi-instance-item')).toHaveLength(2));
    fireEvent.change(input('hifi-new-instance'), { target: { value: 'ftp://x' } });
    fireEvent.click(document.querySelector('#hifi-new-instance + button')!);
    expect(alert).toHaveBeenCalledWith('URL must start with http:// or https://');
    fireEvent.change(input('hifi-new-instance'), { target: { value: ' https://c.example ' } });
    fireEvent.click(document.querySelector('#hifi-new-instance + button')!);
    await waitFor(() => expect(alert).toHaveBeenCalledWith('already there'));
    expect(calls.find((c) => c.url === '/api/hifi/instances' && c.method === 'POST')!.body).toEqual(
      { url: 'https://c.example' },
    );
    const lists = () => calls.filter((c) => c.url === '/api/hifi/instances/list').length;
    const before = lists();
    fireEvent.click(document.querySelectorAll('.hifi-instance-toggle')[0]);
    await waitFor(() => expect(lists()).toBe(before + 1));
    expect(calls.find((c) => c.url === '/api/hifi/instances/toggle')!.body).toEqual({
      url: 'https://a.example',
      enabled: false,
    });
    fireEvent.click(document.querySelectorAll('.hifi-instance-remove')[1]);
    await waitFor(() => expect(lists()).toBe(before + 2));
    fireEvent.click(document.getElementById('hifi-instances-restore-btn')!);
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith('Restored 1 default instance', 'success'),
    );
    expect(document.getElementById('hifi-instances-restore-btn')!.textContent).toBe(
      'Restore Defaults',
    );
    alert.mockRestore();
    delete shell.showToast;
  });

  it('drag reorders and saves the order; Check All lists each instance and the active one', async () => {
    routes['GET /api/hifi/instances/list'] = () => ({ instances: instances() });
    routes['POST /api/hifi/instances/reorder'] = () => ({ success: true });
    routes['GET /api/hifi/instances'] = () => ({
      active: 'https://a.example',
      instances: [
        { url: 'https://a.example', version: '2.1', can_download: true },
        { url: 'https://b.example', status: 'ssl_error' },
      ],
    });
    render(<HifiPanel store={store} model={model} />);
    act(() => store.hydrate({}));
    await waitFor(() => expect(document.querySelectorAll('.hifi-instance-item')).toHaveLength(2));
    const rows = document.querySelectorAll('.hifi-instance-item');
    fireEvent.dragStart(rows[1], { dataTransfer: { effectAllowed: '' } });
    fireEvent.drop(rows[0], { dataTransfer: {} });
    expect([...document.querySelectorAll('.hifi-instance-url')].map((r) => r.textContent)).toEqual([
      'https://b.example',
      'https://a.example',
    ]);
    await waitFor(() =>
      expect(calls.find((c) => c.url === '/api/hifi/instances/reorder')?.body).toEqual({
        urls: ['https://b.example', 'https://a.example'],
      }),
    );
    fireEvent.click(document.getElementById('hifi-instances-check-btn')!);
    await waitFor(() =>
      expect(document.getElementById('hifi-instances-status-panel')!.textContent).toContain(
        'SSL error',
      ),
    );
    const report = document.getElementById('hifi-instances-status-panel')!;
    expect(report.style.display).toBe('block');
    expect(report.textContent).toBe(
      'https://a.example v2.1 (ACTIVE)● Downloadhttps://b.example● SSL error',
    );
  });

  it('checks itself when its modal opens', async () => {
    routes['GET /api/hifi/status'] = () => ({ available: true, version: '3' });
    document.body.insertAdjacentHTML('beforeend', '<div id="source-config-home" hidden></div>');
    render(
      <>
        <SourceModal model={model} />
        <HifiPanel store={store} model={model} />
      </>,
    );
    act(() => model.openModal('hifi'));
    await waitFor(() =>
      expect(status('hifi-connection-status').textContent).toBe('Connected (v3)'),
    );
    act(() => model.closeModal());
    document.getElementById('source-config-home')?.remove();
  });
});

describe('Tidal', () => {
  it('checks itself when opened, and links an account by device code', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    shell.showToast = vi.fn();
    document.body.insertAdjacentHTML('beforeend', '<div id="source-config-home" hidden></div>');
    routes['GET /api/tidal/download/auth/status'] = () => ({ authenticated: false });
    routes['POST /api/tidal/download/auth/start'] = () => ({
      success: true,
      verification_uri: 'https://link.tidal.com/ABCDE',
      user_code: 'ABCDE',
    });
    let state = 'pending';
    routes['GET /api/tidal/download/auth/check'] = () => ({ status: state });
    render(
      <>
        <SourceModal model={model} />
        <TidalPanel model={model} />
      </>,
    );
    act(() => model.openModal('tidal'));
    await waitFor(() =>
      expect(status('tidal-download-auth-status').textContent).toBe('Not authenticated'),
    );
    expect(status('tidal-download-auth-status').style.color).toBe('rgb(255, 152, 0)');
    fireEvent.click(document.getElementById('tidal-download-auth-btn')!);
    await waitFor(() =>
      expect(document.getElementById('tidal-download-auth-btn')!.textContent).toBe(
        'Waiting for approval...',
      ),
    );
    const code = document.getElementById('tidal-download-auth-code')!;
    expect(code.style.display).toBe('block');
    expect(code.textContent).toBe('Go to https://link.tidal.com/ABCDE and enter code: ABCDE');
    expect(code.querySelector('a')!.getAttribute('href')).toBe('https://link.tidal.com/ABCDE');
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(code.style.display).toBe('block');
    state = 'completed';
    await act(() => vi.advanceTimersByTimeAsync(3000));
    await waitFor(() =>
      expect(status('tidal-download-auth-status').textContent).toBe('Authenticated'),
    );
    expect(code.style.display).toBe('none');
    expect(document.getElementById('tidal-download-auth-btn')!.textContent).toBe(
      'Re-link Tidal Account',
    );
    expect(shell.showToast).toHaveBeenCalledWith(
      'Tidal download account linked successfully',
      'success',
    );
    const checks = calls.filter((c) => c.url === '/api/tidal/download/auth/check').length;
    await act(() => vi.advanceTimersByTimeAsync(9000));
    expect(calls.filter((c) => c.url === '/api/tidal/download/auth/check').length).toBe(checks);
    vi.useRealTimers();
    act(() => model.closeModal());
    document.getElementById('source-config-home')?.remove();
    delete shell.showToast;
  });

  it('a start that fails says so and resets the button', async () => {
    shell.showToast = vi.fn();
    routes['POST /api/tidal/download/auth/start'] = () => ({
      success: false,
      error: 'no client id',
    });
    render(<TidalPanel model={model} />);
    fireEvent.click(document.getElementById('tidal-download-auth-btn')!);
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith(
        'Failed to start Tidal auth: no client id',
        'error',
      ),
    );
    expect(document.getElementById('tidal-download-auth-btn')!.textContent).toBe(
      'Link Tidal Account',
    );
    delete shell.showToast;
  });
});

describe('Qobuz', () => {
  it('shows what checkQobuzAuthStatus heard, and its sign-ins refresh it', async () => {
    shell.showToast = vi.fn();
    shell.checkQobuzAuthStatus = vi.fn();
    routes['POST /api/qobuz/auth/login'] = () => ({ success: false, error: 'captcha' });
    routes['POST /api/qobuz/auth/token'] = () => ({ success: true });
    render(<QobuzPanel />);
    expect(document.getElementById('qobuz-auth-logged-in')!.style.display).toBe('none');
    // empty fields are refused before anything is sent
    fireEvent.click(document.getElementById('qobuz-login-btn')!);
    expect(shell.showToast).toHaveBeenCalledWith(
      'Please enter your Qobuz email and password',
      'warning',
    );
    fireEvent.change(input('qobuz-email'), { target: { value: ' me@x.com ' } });
    fireEvent.change(input('qobuz-password'), { target: { value: 'pw' } });
    fireEvent.click(document.getElementById('qobuz-login-btn')!);
    await waitFor(() => expect(status('qobuz-auth-status').textContent).toBe('captcha'));
    expect(calls.find((c) => c.url === '/api/qobuz/auth/login')!.body).toEqual({
      email: 'me@x.com',
      password: 'pw',
    });
    expect(status('qobuz-auth-status').style.color).toBe('rgb(255, 85, 85)');
    expect(shell.showToast).toHaveBeenCalledWith('captcha', 'error');
    fireEvent.change(input('qobuz-download-token'), { target: { value: ' tok ' } });
    fireEvent.click(document.getElementById('qobuz-download-token-btn')!);
    await waitFor(() => expect(shell.checkQobuzAuthStatus).toHaveBeenCalledOnce());
    expect(calls.find((c) => c.url === '/api/qobuz/auth/token')!.body).toEqual({ token: 'tok' });
    expect(input('qobuz-download-token').value).toBe('');
    // settings.js heard the server: signed in
    act(() => {
      window.dispatchEvent(
        new CustomEvent('soulsync:qobuz-auth', {
          detail: {
            authenticated: true,
            user: { display_name: 'Boulder', subscription: 'Studio' },
          },
        }),
      );
    });
    expect(document.getElementById('qobuz-auth-logged-in')!.style.display).toBe('flex');
    expect(document.getElementById('qobuz-auth-form')!.style.display).toBe('none');
    expect(status('qobuz-auth-user-info').textContent).toBe('Connected: Boulder (Studio)');
    delete shell.showToast;
    delete shell.checkQobuzAuthStatus;
  });
});

describe('Deezer', () => {
  it('saves the ARL and mirrors it into the Connections card, both ways', async () => {
    document.body.insertAdjacentHTML(
      'beforeend',
      '<input type="password" id="deezer-connection-arl">',
    );
    act(() => store.hydrate({ deezer_download: { arl: 'old' } }));
    render(<DeezerPanel store={store} />);
    expect(input('deezer-download-arl').value).toBe('old');
    fireEvent.change(input('deezer-download-arl'), { target: { value: 'fresh' } });
    expect(input('deezer-connection-arl').value).toBe('fresh');
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({ deezer_download: { arl: 'fresh' } });
    // the Connections card writes the same key through SoulSyncSettings.set
    act(() => store.set('deezer_download', 'arl', 'from-connections'));
    expect(input('deezer-download-arl').value).toBe('from-connections');
  });

  it('a masked token: focus empties it, leaving it untouched brings the mask back and saves nothing', async () => {
    act(() => store.hydrate({ deezer_download: { arl: REDACTED_SECRET_SENTINEL } }));
    render(<DeezerPanel store={store} />);
    const field = input('deezer-download-arl');
    expect(field.value).toBe(REDACTED_SECRET_SENTINEL);
    fireEvent.focus(field);
    expect(field.value).toBe('');
    fireEvent.blur(field);
    expect(field.value).toBe(REDACTED_SECRET_SENTINEL);
    expect(store.hasPending()).toBe(false);
    // typed then emptied is a real clear
    fireEvent.focus(field);
    fireEvent.change(field, { target: { value: 'x' } });
    fireEvent.change(field, { target: { value: '' } });
    fireEvent.blur(field);
    expect(field.value).toBe('');
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({ deezer_download: { arl: '' } });
  });

  it('Test sends the token on screen, or empty for the mask so the saved one is tested', async () => {
    routes['POST /api/deezer-download/test'] = () => ({
      success: true,
      user: 'Boulder',
      tier: 'Premium',
    });
    act(() => store.hydrate({ deezer_download: { arl: REDACTED_SECRET_SENTINEL } }));
    render(<DeezerPanel store={store} />);
    fireEvent.click(document.getElementById('deezer-download-test-btn')!);
    await waitFor(() =>
      expect(status('deezer-download-status').textContent).toBe('Connected as Boulder (Premium)'),
    );
    expect(calls[0].body).toEqual({ arl: '' });
    expect(status('deezer-download-status').style.color).toBe('rgb(76, 175, 80)');
    routes['POST /api/deezer-download/test'] = () => ({ success: false, error: 'bad arl' });
    fireEvent.change(input('deezer-download-arl'), { target: { value: 'abc' } });
    fireEvent.click(document.getElementById('deezer-download-test-btn')!);
    await waitFor(() => expect(status('deezer-download-status').textContent).toBe('bad arl'));
    expect(calls[1].body).toEqual({ arl: 'abc' });
  });
});

describe('SoundCloud', () => {
  it('reports the three states and checks itself when its modal opens', async () => {
    document.body.insertAdjacentHTML('beforeend', '<div id="source-config-home" hidden></div>');
    const reply = { available: true, reachable: false };
    routes['GET /api/soundcloud/status'] = () => reply;
    render(
      <>
        <SourceModal model={model} />
        <SoundcloudPanel model={model} />
      </>,
    );
    expect(calls).toHaveLength(0);
    act(() => model.openModal('soundcloud'));
    // the open panel portals into the modal body
    expect(
      document.querySelector('#src-modal-body #soundcloud-download-settings-container'),
    ).not.toBeNull();
    await waitFor(() =>
      expect(status('soundcloud-connection-status').textContent).toBe(
        'Reachable check failed — try again',
      ),
    );
    reply.reachable = true;
    fireEvent.click(document.getElementById('soundcloud-test-btn')!);
    await waitFor(() =>
      expect(status('soundcloud-connection-status').textContent).toBe('Connected (anonymous)'),
    );
    routes['GET /api/soundcloud/status'] = () => ({ available: false, error: 'no yt-dlp' });
    fireEvent.click(document.getElementById('soundcloud-test-btn')!);
    await waitFor(() =>
      expect(status('soundcloud-connection-status').textContent).toBe('no yt-dlp'),
    );
    act(() => model.closeModal());
    expect(
      document.querySelector('#src-modal-body #soundcloud-download-settings-container'),
    ).toBeNull();
    expect(document.getElementById('soundcloud-download-settings-container')).not.toBeNull();
    document.getElementById('source-config-home')?.remove();
  });
});

describe('Lidarr', () => {
  it('saves its URL and key, and saves the page before it tests', async () => {
    const order: string[] = [];
    shell.saveSettings = vi.fn(() => {
      order.push('save');
      return Promise.resolve();
    });
    routes['POST /api/test-connection'] = () => {
      order.push('test');
      return { success: false, error: 'unauthorised' };
    };
    act(() =>
      store.hydrate({
        lidarr_download: { url: 'http://l:8686', api_key: REDACTED_SECRET_SENTINEL },
      }),
    );
    render(<LidarrPanel store={store} />);
    expect(input('lidarr-url').value).toBe('http://l:8686');
    fireEvent.change(input('lidarr-url'), { target: { value: 'http://lidarr:8686' } });
    fireEvent.click(document.getElementById('lidarr-test-btn')!);
    await waitFor(() =>
      expect(status('lidarr-connection-status').textContent).toBe('unauthorised'),
    );
    expect(order).toEqual(['save', 'test']);
    expect(calls.find((c) => c.url === '/api/test-connection')!.body).toEqual({
      service: 'lidarr',
    });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({ lidarr_download: { url: 'http://lidarr:8686' } });
  });
});
