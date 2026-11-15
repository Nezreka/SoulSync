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
  LidarrPanel,
  QobuzPanel,
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
