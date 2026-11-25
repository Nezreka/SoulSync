/**
 * The source panels in source-panels.tsx (Soulseek, HiFi, Tidal, Qobuz,
 * Deezer, SoundCloud, Lidarr), on the settings parts: the same saves, the
 * masked-secret rules, each panel's own checks, and what the sheet shows of
 * each (every control id it had, the groups, help behind the "i", the one
 * Test in the sheet's foot). The frozen vanilla markup comparisons went with
 * the markup; the key guard (guard/) holds every id, key and button.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
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

describe('Soulseek', () => {
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
    expect(v('soulseek-login-wait-minutes')).toBe('45');
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
    fireEvent.change(input('soulseek-login-wait-minutes'), { target: { value: '0' } });
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
        login_wait_minutes: 0,
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
    fireEvent.click(screen.getByRole('button', { name: 'Auto-detect' }));
    await waitFor(() => expect(input('soulseek-url').value).toBe('http://found:5030'));
    expect(calls.filter((c) => c.url === '/api/detect-soulseek')).toHaveLength(1);
    expect(shell.showToast).toHaveBeenCalledWith(
      'Soulseek server detected: http://found:5030',
      'success',
    );
    expect(shell.hideLoadingOverlay).toHaveBeenCalledOnce();
    // (test-after-save ordering uses the page's own store: checked in the browser)
    expect(order).toEqual([]);
    for (const k of ['showToast', 'showLoadingOverlay', 'hideLoadingOverlay']) delete shell[k];
  });
});

describe('HiFi', () => {
  const instances = () => [
    { url: 'https://a.example', enabled: true },
    { url: 'https://b.example', enabled: false },
  ];

  const rows = () => [...document.querySelectorAll<HTMLElement>('#hifi-instances-list [data-url]')];

  it('the list loads with the settings; each row says on or off, with real buttons', async () => {
    routes['GET /api/hifi/instances/list'] = () => ({ instances: instances() });
    render(<SourcePanels store={store} part="hifi" opened={0} active={false} />);
    expect(calls).toHaveLength(0);
    act(() => store.hydrate({}));
    await waitFor(() => expect(rows()).toHaveLength(2));
    expect(rows().map((r) => r.dataset.url)).toEqual(['https://a.example', 'https://b.example']);
    const on = within(rows()[0]).getByRole('button', { name: 'Turn off https://a.example' });
    expect(on.getAttribute('aria-pressed')).toBe('true');
    expect(on.textContent).toBe('On');
    const off = within(rows()[1]).getByRole('button', { name: 'Turn on https://b.example' });
    expect(off.getAttribute('aria-pressed')).toBe('false');
    expect(
      within(rows()[1]).getByRole('button', { name: 'Remove https://b.example' }),
    ).toBeTruthy();
  });

  it('adds, toggles, removes and restores through the server, then rereads the list', async () => {
    // a refusal is a toast now, not window.alert
    shell.showToast = vi.fn();
    const alert = shell.showToast as ReturnType<typeof vi.fn>;
    routes['GET /api/hifi/instances/list'] = () => ({ instances: instances() });
    routes['POST /api/hifi/instances'] = () => ({ success: false, error: 'already there' });
    routes['POST /api/hifi/instances/toggle'] = () => ({ success: true });
    routes['DELETE /api/hifi/instances?url=https%3A%2F%2Fb.example'] = () => ({ success: true });
    routes['POST /api/hifi/instances/reset'] = () => ({ success: true, restored: 1 });
    render(<HifiPanel store={store} model={model} />);
    act(() => store.hydrate({}));
    await waitFor(() => expect(rows()).toHaveLength(2));
    const add = screen.getByRole('button', { name: 'Add' });
    fireEvent.change(input('hifi-new-instance'), { target: { value: 'ftp://x' } });
    fireEvent.click(add);
    expect(alert).toHaveBeenCalledWith('URL must start with http:// or https://', 'error');
    fireEvent.change(input('hifi-new-instance'), { target: { value: ' https://c.example ' } });
    fireEvent.click(add);
    await waitFor(() => expect(alert).toHaveBeenCalledWith('already there', 'error'));
    expect(calls.find((c) => c.url === '/api/hifi/instances' && c.method === 'POST')!.body).toEqual(
      { url: 'https://c.example' },
    );
    const lists = () => calls.filter((c) => c.url === '/api/hifi/instances/list').length;
    const before = lists();
    fireEvent.click(screen.getByRole('button', { name: 'Turn off https://a.example' }));
    await waitFor(() => expect(lists()).toBe(before + 1));
    expect(calls.find((c) => c.url === '/api/hifi/instances/toggle')!.body).toEqual({
      url: 'https://a.example',
      enabled: false,
    });
    fireEvent.click(screen.getByRole('button', { name: 'Remove https://b.example' }));
    await waitFor(() => expect(lists()).toBe(before + 2));
    fireEvent.click(document.getElementById('hifi-instances-restore-btn')!);
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith('Restored 1 default instance', 'success'),
    );
    expect(document.getElementById('hifi-instances-restore-btn')!.textContent).toBe(
      'Restore Defaults',
    );
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
    await waitFor(() => expect(rows()).toHaveLength(2));
    fireEvent.dragStart(rows()[1], { dataTransfer: { effectAllowed: '' } });
    fireEvent.drop(rows()[0], { dataTransfer: {} });
    expect(rows().map((r) => r.dataset.url)).toEqual(['https://b.example', 'https://a.example']);
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
    expect(report.hidden).toBe(false);
    const lines = [...report.querySelectorAll<HTMLElement>('[data-hifi-check]')];
    expect(lines.map((l) => l.textContent)).toEqual([
      'https://a.example v2.1ActiveDownload',
      'https://b.exampleSSL error',
    ]);
    // the state is words with a tone, never a coloured dot alone
    expect(lines.map((l) => l.querySelector('[data-tone]')!.getAttribute('data-tone'))).toEqual([
      'ok',
      'bad',
    ]);
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
    expect(status('tidal-download-auth-status').dataset.tone).toBe('warn');
    fireEvent.click(document.getElementById('tidal-download-auth-btn')!);
    await waitFor(() =>
      expect(document.getElementById('tidal-download-auth-btn')!.textContent).toBe(
        'Waiting for approval...',
      ),
    );
    const code = document.getElementById('tidal-download-auth-code')!;
    expect(code.hidden).toBe(false);
    expect(code.textContent).toBe('Go to https://link.tidal.com/ABCDE and enter code: ABCDE');
    expect(code.querySelector('a')!.getAttribute('href')).toBe('https://link.tidal.com/ABCDE');
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(code.hidden).toBe(false);
    state = 'completed';
    await act(() => vi.advanceTimersByTimeAsync(3000));
    await waitFor(() =>
      expect(status('tidal-download-auth-status').textContent).toBe('Authenticated'),
    );
    expect(code.hidden).toBe(true);
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

  it('a link still waiting keeps its button off when reopened, and its poll runs on after Settings is left', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    shell.showToast = vi.fn();
    document.body.insertAdjacentHTML('beforeend', '<div id="source-config-home" hidden></div>');
    routes['GET /api/tidal/download/auth/status'] = () => ({ authenticated: false });
    routes['POST /api/tidal/download/auth/start'] = () => ({
      success: true,
      verification_uri: 'https://link.tidal.com/X',
      user_code: 'X',
    });
    let state = 'pending';
    routes['GET /api/tidal/download/auth/check'] = () => ({ status: state });
    const view = render(
      <>
        <SourceModal model={model} />
        <TidalPanel model={model} />
      </>,
    );
    act(() => model.openModal('tidal'));
    await waitFor(() =>
      expect(status('tidal-download-auth-status').textContent).toBe('Not authenticated'),
    );
    const button = () => document.getElementById('tidal-download-auth-btn') as HTMLButtonElement;
    fireEvent.click(button());
    await waitFor(() => expect(button().textContent).toBe('Waiting for approval...'));
    act(() => model.closeModal());
    act(() => model.openModal('tidal'));
    await act(() => vi.advanceTimersByTimeAsync(10));
    await waitFor(() => expect(button().textContent).toBe('Link Tidal Account'));
    // the label follows the check, the button stays off
    expect(button().disabled).toBe(true);
    act(() => model.closeModal());
    // Settings is left: the link still lands, and says so
    view.unmount();
    state = 'completed';
    await act(() => vi.advanceTimersByTimeAsync(3000));
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith(
        'Tidal download account linked successfully',
        'success',
      ),
    );
    const checks = calls.filter((c) => c.url === '/api/tidal/download/auth/check').length;
    await act(() => vi.advanceTimersByTimeAsync(9000));
    expect(calls.filter((c) => c.url === '/api/tidal/download/auth/check').length).toBe(checks);
    vi.useRealTimers();
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
    routes['GET /api/qobuz/auth/status'] = () => ({});
    routes['POST /api/qobuz/auth/login'] = () => ({ success: false, error: 'captcha' });
    routes['POST /api/qobuz/auth/token'] = () => ({ success: true });
    render(<QobuzPanel />);
    expect(document.getElementById('qobuz-auth-logged-in')!.hidden).toBe(true);
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
    expect(status('qobuz-auth-status').dataset.tone).toBe('bad');
    expect(shell.showToast).toHaveBeenCalledWith('captcha', 'error');
    fireEvent.change(input('qobuz-download-token'), { target: { value: ' tok ' } });
    fireEvent.click(document.getElementById('qobuz-download-token-btn')!);
    // a sign-in asks the server again (connections.ts checkQobuzAuthStatus)
    await waitFor(() =>
      expect(calls.filter((c) => c.url === '/api/qobuz/auth/status')).toHaveLength(1),
    );
    expect(calls.find((c) => c.url === '/api/qobuz/auth/token')!.body).toEqual({ token: 'tok' });
    expect(input('qobuz-download-token').value).toBe('');
    // the server says: signed in
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
    expect(document.getElementById('qobuz-auth-logged-in')!.hidden).toBe(false);
    expect(document.getElementById('qobuz-auth-form')!.hidden).toBe(true);
    expect(status('qobuz-auth-user-info').textContent).toBe('Connected: Boulder (Studio)');
    delete shell.showToast;
  });
});

describe('Deezer', () => {
  it('saves the ARL, the same key the Connections card holds', async () => {
    act(() => store.hydrate({ deezer_download: { arl: 'old' } }));
    render(<DeezerPanel store={store} />);
    expect(input('deezer-download-arl').value).toBe('old');
    fireEvent.change(input('deezer-download-arl'), { target: { value: 'fresh' } });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({ deezer_download: { arl: 'fresh' } });
    // the Connections card (connections-metadata.tsx) writes the same key
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
    expect(status('deezer-download-status').dataset.tone).toBe('ok');
    routes['POST /api/deezer-download/test'] = () => ({ success: false, error: 'bad arl' });
    fireEvent.change(input('deezer-download-arl'), { target: { value: 'abc' } });
    fireEvent.click(document.getElementById('deezer-download-test-btn')!);
    await waitFor(() => expect(status('deezer-download-status').textContent).toBe('bad arl'));
    expect(calls[1].body).toEqual({ arl: 'abc' });
  });
});

describe('SoundCloud', () => {
  it('the OAuth token (#2116) loads and saves as its own key; a saved one stays masked', async () => {
    act(() => store.hydrate({ soundcloud_download: { oauth_token: '__redacted_unchanged__' } }));
    render(<SoundcloudPanel store={store} model={model} />);
    const token = document.getElementById('soundcloud-download-oauth-token') as HTMLInputElement;
    expect(token.type).toBe('password');
    expect(token.value).toBe('__redacted_unchanged__');
    fireEvent.focus(token);
    fireEvent.change(token, { target: { value: 'tok123' } });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(posted.at(-1)).toEqual({ soundcloud_download: { oauth_token: 'tok123' } });
  });

  it('reports the three states and checks itself when its modal opens', async () => {
    document.body.insertAdjacentHTML('beforeend', '<div id="source-config-home" hidden></div>');
    const reply = { available: true, reachable: false };
    routes['GET /api/soundcloud/status'] = () => reply;
    render(
      <>
        <SourceModal model={model} />
        <SoundcloudPanel store={store} model={model} />
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
        'Reachable check failed. Try again.',
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
  it("saves its URL and key; the sheet's Test is its test (no second button)", async () => {
    act(() =>
      store.hydrate({
        lidarr_download: { url: 'http://l:8686', api_key: REDACTED_SECRET_SENTINEL },
      }),
    );
    render(<LidarrPanel store={store} />);
    expect(input('lidarr-url').value).toBe('http://l:8686');
    // a saved key shows as dots and says why
    expect(document.getElementById('lidarr-download-settings-container')!.textContent).toContain(
      'Saved keys are never sent back to the browser',
    );
    fireEvent.change(input('lidarr-url'), { target: { value: 'http://lidarr:8686' } });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({ lidarr_download: { url: 'http://lidarr:8686' } });
    expect(document.getElementById('lidarr-test-btn')).toBeNull();
    // the stale "Use Lidarr Only" mode is gone from its words
    expect(document.body.textContent).not.toContain('Lidarr Only');
  });

  it('saves the whole album default and its two folders', async () => {
    act(() => store.hydrate({ lidarr_download: { url: 'http://l:8686' } }));
    render(<LidarrPanel store={store} />);
    expect(input('lidarr-whole-album-default').checked).toBe(true);
    fireEvent.click(input('lidarr-whole-album-default'));
    fireEvent.change(input('lidarr-transfer-path'), { target: { value: '/lidarr-imports' } });
    fireEvent.change(input('lidarr-download-path'), { target: { value: '/lidarr-dl' } });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({
      lidarr_download: {
        whole_album_default: false,
        transfer_path: '/lidarr-imports',
        download_path: '/lidarr-dl',
      },
    });
  });

  it('saves "Lidarr imports every download", off by default', async () => {
    act(() => store.hydrate({ lidarr_download: { url: 'http://l:8686' } }));
    render(<LidarrPanel store={store} />);
    expect(input('lidarr-import-through-lidarr').checked).toBe(false);
    fireEvent.click(input('lidarr-import-through-lidarr'));
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({ lidarr_download: { import_through_lidarr: true } });
  });
});
