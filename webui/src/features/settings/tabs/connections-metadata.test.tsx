/**
 * Connections, step 10a (SETTINGS_PORT.md): the markup each part replaced,
 * the metadata source picker's rules, the Experimental switches and their
 * confirm, the MusicBrainz check, Hydrabase, and the keys each card saves.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { shape, vanilla, withHelpLines } from './__fixtures__/shape';
import { hydrabaseLink, musicbrainzKeys, musicbrainzRate } from './connections';
import {
  ExperimentalSection,
  musicbrainzError,
  syncMetadataSourceSelection,
} from './connections-metadata';
import { ConnectionsTab } from './connections-tab';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

let store: SettingsStore;
let posted: SettingsPayload[];
let calls: Array<{ url: string; method: string; body?: unknown }>;
let answers: Record<string, unknown>;

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
  answers = {};
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method || 'GET';
      const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined;
      calls.push({ url, method, body });
      const data = answers[url] ?? { success: true };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data) } as Response);
    }),
  );
  shell.showToast = vi.fn();
  shell.testConnection = vi.fn(() => Promise.resolve());
  shell.saveSettings = vi.fn(() => Promise.resolve());
  shell.buildServiceTiles = vi.fn();
  hydrabaseLink.set(false);
});

afterEach(async () => {
  await act(() => store.flush());
  vi.unstubAllGlobals();
  for (const k of ['showToast', 'testConnection', 'saveSettings', 'buildServiceTiles'])
    delete shell[k];
});

/** a part in a slot of its own, as the page has it: the service cards
 *  portal into wrappers kept in their slot */
function part(name: string) {
  const slot = document.createElement('div');
  slot.dataset.stgReact = 'connections';
  slot.dataset.stgPart = name;
  slot.style.display = 'contents';
  document.body.append(slot);
  return render(<ConnectionsTab store={store} part={name} opened={0} active={false} />, {
    container: slot,
  });
}
/** the slot as it reads without the cards' wrappers */
function unwrapped(container: HTMLElement) {
  const copy = container.cloneNode(true) as HTMLElement;
  for (const wrap of copy.querySelectorAll('[data-svc-wrap]')) wrap.replaceWith(...wrap.childNodes);
  return copy;
}
const el = <T extends HTMLElement = HTMLInputElement>(id: string) =>
  document.getElementById(id) as T;
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}
function status(frame: unknown) {
  act(() => {
    window.dispatchEvent(new CustomEvent('ss:service-status', { detail: frame }));
  });
}
const loaded = (settings: SettingsPayload) => act(() => store.hydrate(settings));

describe('each part is the markup it replaced', () => {
  it('the metadata source group, with the discography source under the picker', () => {
    const { container } = part('metadata');
    const old = vanilla('connections-metadata.vanilla.html', { afterInit: true });
    // library-discography-source.ts slipped this in after the picker's row
    old
      .querySelector('#metadata-fallback-source')!
      .closest('.form-group')!
      .insertAdjacentHTML(
        'afterend',
        '<div id="library-discography-source-group" class="form-group"><label for="library-discography-source">Library discography source:</label><select id="library-discography-source" class="form-select"><option value="primary">Use primary metadata source</option><option value="automatic">Automatic commercial catalogue</option><option value="itunes">iTunes</option><option value="deezer">Deezer</option><option value="musicbrainz">MusicBrainz</option><option value="spotify">Spotify</option></select><small class="settings-hint">Controls album, EP and single catalogues shown in Library artist views. Automatic tries iTunes, then Deezer.</small></div>',
      );
    expect(shape(container)).toEqual(shape(old));
  });

  it('the music API Configuration group, every card in it', () => {
    const { container } = part('api-music');
    expect(shape(unwrapped(container))).toEqual(
      shape(withHelpLines(vanilla('connections-api-music.vanilla.html'))),
    );
  });

  it('Experimental, and its confirm in the page body', () => {
    const { container } = render(<ExperimentalSection store={store} />);
    expect(shape(container)).toEqual(
      shape(vanilla('connections-experimental.vanilla.html', { afterInit: true })),
    );
    const modal = document.createElement('div');
    modal.append(el('experimental-enable-modal-overlay').cloneNode(true));
    expect(shape(modal)).toEqual(shape(vanilla('connections-experimental-modal.vanilla.html')));
  });
});

describe('the metadata source picker', () => {
  it('shows the saved source, Spotify Free as its own choice', () => {
    part('metadata');
    loaded({ metadata: { fallback_source: 'spotify', spotify_free: true } });
    expect(el<HTMLSelectElement>('metadata-fallback-source').value).toBe('spotify_free');
  });

  it('locks Spotify and Discogs once /status speaks, and a locked pick warns and stays put', async () => {
    part('metadata');
    loaded({ metadata: { fallback_source: 'itunes' }, discogs: { token: '' } });
    const options = () =>
      [...el<HTMLSelectElement>('metadata-fallback-source').options].map((o) => o.textContent);
    expect(options()).toContain('Spotify');
    status({ spotify: { authenticated: false, free_installed: true } });
    expect(options()).toEqual([
      '🔒 Spotify',
      'Spotify (no auth)',
      'iTunes / Apple Music',
      'Deezer',
      '🔒 Discogs',
      'MusicBrainz',
    ]);
    const spotify = el<HTMLSelectElement>('metadata-fallback-source').options[0];
    expect(spotify.title).toBe(
      'Spotify authentication is required before this source can be selected.',
    );
    expect(spotify.dataset.unavailable).toBe('true');
    fireEvent.change(el('metadata-fallback-source'), { target: { value: 'spotify' } });
    expect(shell.showToast).toHaveBeenCalledWith(
      'Spotify must be authenticated before it can be selected as the primary metadata source.',
      'warning',
      undefined,
    );
    expect(el<HTMLSelectElement>('metadata-fallback-source').value).toBe('itunes');
    fireEvent.change(el('metadata-fallback-source'), { target: { value: 'spotify_free' } });
    await waitFor(() =>
      expect(saved().metadata).toEqual({ fallback_source: 'spotify', spotify_free: true }),
    );
  });

  it('a source that stops being pickable goes back to Deezer, and that is saved', async () => {
    part('metadata');
    loaded({ metadata: { fallback_source: 'spotify' } });
    status({ spotify: { authenticated: true } });
    expect(posted).toEqual([]);
    status({ spotify: { authenticated: false } });
    expect(el<HTMLSelectElement>('metadata-fallback-source').value).toBe('deezer');
    await waitFor(() =>
      expect(saved().metadata).toEqual({ fallback_source: 'deezer', spotify_free: false }),
    );
  });

  it('a disconnect the server already answered is shown, not saved again', () => {
    part('metadata');
    loaded({ metadata: { fallback_source: 'spotify' } });
    act(() => syncMetadataSourceSelection(store, 'itunes'));
    expect(el<HTMLSelectElement>('metadata-fallback-source').value).toBe('itunes');
    expect(store.hasPending()).toBe(false);
  });

  it('JioSaavn sits after MusicBrainz when switched on; Hydrabase last while connected', () => {
    part('metadata');
    loaded({
      metadata: { fallback_source: 'hydrabase' },
      experimental: { jiosaavn_enabled: true },
    });
    // a saved source whose option is not there yet: nothing picked
    expect(el<HTMLSelectElement>('metadata-fallback-source').selectedIndex).toBe(-1);
    act(() => hydrabaseLink.set(true));
    const values = [...el<HTMLSelectElement>('metadata-fallback-source').options].map(
      (o) => o.value,
    );
    expect(values.slice(-3)).toEqual(['musicbrainz', 'jiosaavn', 'hydrabase']);
    expect(el<HTMLSelectElement>('metadata-fallback-source').value).toBe('hydrabase');
  });

  it('the Library discography source saves at once, on its own', async () => {
    part('metadata');
    loaded({ metadata: { fallback_source: 'deezer', library_discography_source: 'itunes' } });
    expect(el<HTMLSelectElement>('library-discography-source').value).toBe('itunes');
    fireEvent.change(el('library-discography-source'), { target: { value: 'automatic' } });
    expect(el<HTMLSelectElement>('library-discography-source').disabled).toBe(true);
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith(
        'Library discography source saved',
        'success',
        undefined,
      ),
    );
    expect(calls.find((c) => c.url === '/api/settings')?.body).toEqual({
      metadata: { library_discography_source: 'automatic' },
    });
    expect(el<HTMLSelectElement>('library-discography-source').value).toBe('automatic');
    expect(el<HTMLSelectElement>('library-discography-source').disabled).toBe(false);
    expect(store.hasPending()).toBe(false);
  });
});

describe('Experimental', () => {
  it('JioSaavn waits for the confirm; turning it off takes the picker off it', async () => {
    render(
      <>
        <ExperimentalSection store={store} />
        <ConnectionsTab store={store} part="metadata" opened={0} active={false} />
      </>,
    );
    loaded({ metadata: { fallback_source: 'deezer' }, experimental: {} });
    const heard = vi.fn();
    window.addEventListener('ss:jiosaavn-experimental', heard);
    fireEvent.click(el('experimental-jiosaavn-enabled'));
    expect(el('experimental-jiosaavn-enabled').checked).toBe(false);
    expect(el('experimental-enable-modal-overlay').className).toBe('modal-overlay');
    expect(el('experimental-enable-modal-title').textContent).toBe('Enable JioSaavn');
    expect(el<HTMLButtonElement>('experimental-enable-confirm-btn').disabled).toBe(true);
    fireEvent.click(el('experimental-enable-ack-checkbox'));
    fireEvent.click(el('experimental-enable-confirm-btn'));
    await waitFor(() => expect(el('experimental-jiosaavn-enabled').checked).toBe(true));
    expect(el('experimental-enable-modal-overlay').className).toBe('modal-overlay hidden');
    expect(heard).toHaveBeenLastCalledWith(expect.objectContaining({ detail: { enabled: true } }));
    fireEvent.change(el('metadata-fallback-source'), { target: { value: 'jiosaavn' } });
    fireEvent.click(el('experimental-jiosaavn-enabled'));
    expect(el<HTMLSelectElement>('metadata-fallback-source').value).toBe('deezer');
    await waitFor(() =>
      expect(saved()).toEqual({
        experimental: { jiosaavn_enabled: false },
        metadata: { fallback_source: 'deezer', spotify_free: false },
      }),
    );
    window.removeEventListener('ss:jiosaavn-experimental', heard);
  });

  it('Cancel leaves Bandcamp off; switching off needs no confirm', async () => {
    render(<ExperimentalSection store={store} />);
    loaded({ experimental: { bandcamp_enabled: false } });
    fireEvent.click(el('experimental-bandcamp-enabled'));
    expect(el('experimental-enable-modal-title').textContent).toBe('Enable Bandcamp');
    fireEvent.click(
      el('experimental-enable-modal-overlay').querySelector('.modal-button--secondary')!,
    );
    await act(() => Promise.resolve());
    expect(el('experimental-bandcamp-enabled').checked).toBe(false);
    expect(store.hasPending()).toBe(false);
    loaded({ experimental: { bandcamp_enabled: true } });
    fireEvent.click(el('experimental-bandcamp-enabled'));
    await waitFor(() => expect(saved().experimental).toEqual({ bandcamp_enabled: false }));
  });
});

describe('the service cards', () => {
  it('each field saves its key; the redirect line follows the field', async () => {
    part('api-music');
    loaded({
      spotify: { client_id: 'a', client_secret: '__redacted_unchanged__' },
      deezer: {},
      deezer_download: { arl: '' },
      itunes: {},
      discogs: {},
      tidal: {},
    });
    expect(el('spotify-redirect-uri').value).toBe('http://127.0.0.1:8888/callback');
    expect(el('spotify-callback-display').textContent).toBe('http://127.0.0.1:8888/callback');
    // emptied, it stays empty while the new one is typed; the shown callback
    // falls back to the default, as #spotify-callback-display did
    fireEvent.change(el('spotify-redirect-uri'), { target: { value: '' } });
    expect(el('spotify-redirect-uri').value).toBe('');
    expect(el('spotify-callback-display').textContent).toBe('http://127.0.0.1:8888/callback');
    fireEvent.change(el('spotify-redirect-uri'), { target: { value: 'https://me.example/cb' } });
    expect(el('spotify-redirect-uri').value).toBe('https://me.example/cb');
    expect(el('spotify-callback-display').textContent).toBe('https://me.example/cb');
    fireEvent.change(el('spotify-client-id'), { target: { value: 'b' } });
    fireEvent.click(el('metadata-spotify-free-enrichment'));
    fireEvent.change(el('itunes-country'), { target: { value: 'GB' } });
    fireEvent.change(el('deezer-connection-arl'), { target: { value: 'arl1' } });
    fireEvent.change(el('discogs-token'), { target: { value: 'tok' } });
    fireEvent.change(el('tidal-client-id'), { target: { value: 't' } });
    fireEvent.change(el('deezer-app-id'), { target: { value: 'd' } });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({
      spotify: { redirect_uri: 'https://me.example/cb', client_id: 'b' },
      metadata: { spotify_free_enrichment: false },
      itunes: { country: 'GB' },
      deezer_download: { arl: 'arl1' },
      discogs: { token: 'tok' },
      tidal: { client_id: 't' },
      deezer: { app_id: 'd' },
    });
  });

  it('the Spotify dot turns amber when it needs attention (the tiles read it)', () => {
    part('api-music');
    const dot = () =>
      document.querySelector<HTMLElement>('[data-service="spotify"] .stg-service-dot')!;
    expect(dot().style.color).toBe('rgb(29, 185, 84)');
    status({ spotify: { authenticated: true, post_ban_cooldown: 30 } });
    expect(dot().style.color).toBe('rgb(241, 196, 15)');
    status({ spotify: { authenticated: true } });
    expect(dot().style.color).toBe('rgb(29, 185, 84)');
  });

  it('a test saves what is waiting first', async () => {
    part('api-music');
    loaded({ discogs: {} });
    store.set('discogs', 'token', 'x');
    fireEvent.click(
      document.querySelector('[data-service="discogs"] .test-button') as HTMLButtonElement,
    );
    await waitFor(() => expect(shell.testConnection).toHaveBeenCalledWith('discogs'));
    expect(saved().discogs).toEqual({ token: 'x' });
  });

  it('Deezer with an ARL tests both logins and says both', async () => {
    part('api-music');
    loaded({ deezer_download: { arl: '__redacted_unchanged__' } });
    answers['/api/test-connection'] = { success: true, message: 'Deezer ok' };
    answers['/api/deezer-download/test'] = { success: true, user: 'me', tier: 'Premium' };
    fireEvent.click(
      document.querySelector('[data-service="deezer"] .test-button') as HTMLButtonElement,
    );
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith(
        'Deezer ok · ARL: me (Premium)',
        'success',
        undefined,
      ),
    );
    // the mask is never sent: empty tests the saved token
    expect(calls.find((c) => c.url === '/api/deezer-download/test')?.body).toEqual({ arl: '' });
  });

  it('Qobuz follows its sign-in, and signs in by token', async () => {
    part('api-music');
    act(() => {
      window.dispatchEvent(
        new CustomEvent('soulsync:qobuz-auth', {
          detail: { authenticated: true, user: { display_name: 'Ann', subscription: 'Studio' } },
        }),
      );
    });
    expect(el('qobuz-connection-logged-in').style.display).toBe('flex');
    expect(el('qobuz-connection-form').style.display).toBe('none');
    expect(el('qobuz-connection-user-info').textContent).toBe('Connected: Ann (Studio)');
    act(() => {
      window.dispatchEvent(new CustomEvent('soulsync:qobuz-auth', { detail: {} }));
    });
    expect(el('qobuz-connection-form').style.display).toBe('block');
    answers['/api/qobuz/auth/token'] = { success: false, error: 'bad token' };
    fireEvent.change(el('qobuz-connection-token'), { target: { value: ' tk ' } });
    fireEvent.click(el('qobuz-token-login-btn'));
    await waitFor(() => expect(el('qobuz-token-status').textContent).toBe('bad token'));
    expect(calls.find((c) => c.url === '/api/qobuz/auth/token')?.body).toEqual({ token: 'tk' });
    expect(el('qobuz-token-status').style.color).toBe('rgb(255, 85, 85)');
  });
});

describe('MusicBrainz', () => {
  it('shows a saved interval as a rate, adaptive as no limit', () => {
    expect(musicbrainzRate({ musicbrainz: { request_interval: 0.1 } })).toEqual({
      mode: 'fixed',
      rate: '10',
    });
    expect(musicbrainzRate({ musicbrainz: { request_interval: 0 } })).toEqual({
      mode: 'adaptive',
      rate: '10',
    });
    const fresh = musicbrainzRate({});
    expect(musicbrainzKeys('', fresh.mode, fresh.rate)).toEqual({
      base_url: '',
      request_interval: expect.closeTo(1.05, 4) as number,
    });
  });

  it.each(['ftp://mirror', 'not a url', 'https://user:secret@mirror', 'https://mirror?x=1'])(
    'refuses the URL %s',
    (url) => expect(musicbrainzKeys(url, 'fixed', '1')).toHaveProperty('error'),
  );
  it.each(['-1', '0', 'NaN', 'Infinity', ''])('refuses the fixed rate %s', (rate) =>
    expect(musicbrainzKeys('http://mirror:5000', 'fixed', rate)).toHaveProperty('error'),
  );
  it('refuses no limit on public MusicBrainz', () =>
    expect(musicbrainzKeys('', 'adaptive', '1')).toEqual({
      error: 'No limit (adaptive) is only for a self-hosted MusicBrainz server.',
    }));

  it('a bad address holds every save back until it is put right', async () => {
    store.block(() => !!musicbrainzError());
    part('api-music');
    loaded({ musicbrainz: { base_url: '', request_interval: 1.05 } });
    expect(el('musicbrainz-request-rate').value).toBe('0.952381');
    fireEvent.change(el('musicbrainz-base-url'), { target: { value: 'mirror' } });
    store.set('genius', 'access_token', 'g');
    await act(() => store.flush());
    expect(posted).toEqual([]);
    expect(musicbrainzError()).toBe('MusicBrainz server URL must start with http:// or https://.');
    fireEvent.change(el('musicbrainz-base-url'), { target: { value: 'http://mirror:5000' } });
    fireEvent.change(el('musicbrainz-rate-mode'), { target: { value: 'adaptive' } });
    expect(el('musicbrainz-request-rate').disabled).toBe(true);
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({
      genius: { access_token: 'g' },
      musicbrainz: { base_url: 'http://mirror:5000', request_interval: 0 },
    });
  });
});

describe('Hydrabase', () => {
  it('a load that finds it connected says so, and the button disconnects it', async () => {
    answers['/api/hydrabase/status'] = { connected: true };
    part('api-music');
    loaded({ hydrabase: { url: 'ws://h', api_key: 'k', auto_connect: true } });
    await waitFor(() => expect(el('hydrabase-settings-status').textContent).toBe('Connected'));
    expect(el('hydrabase-connect-btn').textContent).toBe('Disconnect');
    expect(el('hydrabase-auto-connect').checked).toBe(true);
    fireEvent.click(el('hydrabase-connect-btn'));
    await waitFor(() => expect(el('hydrabase-settings-status').textContent).toBe('Disconnected'));
    expect(shell.saveSettings).toHaveBeenCalledWith(true);
    expect(calls.some((c) => c.url === '/api/hydrabase/disconnect')).toBe(true);
    expect(el('hydrabase-connect-btn').textContent).toBe('Connect');
    expect(hydrabaseLink.get()).toBe(false);
  });

  it('needs both fields to connect', () => {
    part('api-music');
    loaded({ hydrabase: { url: 'ws://h' } });
    fireEvent.click(el('hydrabase-connect-btn'));
    expect(el('hydrabase-settings-status').textContent).toBe('URL and API Key required');
    expect(shell.saveSettings).not.toHaveBeenCalled();
  });
});
