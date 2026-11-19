/**
 * Connections, step 10a (SETTINGS_PORT.md): the markup each part replaced,
 * the metadata source picker's rules, the Experimental switches and their
 * confirm, the MusicBrainz check, Hydrabase, and the keys each card saves.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { shape, vanilla } from './__fixtures__/shape';
import { hydrabaseLink, musicbrainzKeys, musicbrainzRate } from './connections';
import {
  ExperimentalSection,
  musicbrainzError,
  syncMetadataSourceSelection,
} from './connections-metadata';
import { serviceActions } from './connections-services';
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
  it('the metadata source: two rows, named without a colon, the long help behind the "i"', () => {
    const { container } = part('metadata');
    const rows = [...container.querySelectorAll<HTMLElement>('[data-row]')];
    expect(rows.map((r) => r.querySelector('label')!.textContent)).toEqual([
      'Primary metadata source',
      'Library discography source',
    ]);
    expect(rows[0].querySelector('#metadata-fallback-source')).not.toBeNull();
    expect(rows[1].id).toBe('library-discography-source-group');
    expect(rows[1].querySelector('#library-discography-source')).not.toBeNull();
    // one line of help; the source list is behind the "i", closed
    expect(rows[0].textContent).toContain('Spotify and Discogs need their account connected');
    const info = rows[0].querySelector<HTMLButtonElement>(
      '[aria-label="More about this setting"]',
    )!;
    const rest = document.getElementById(info.getAttribute('aria-controls')!)!;
    expect(rest.hidden).toBe(true);
    expect(rest.textContent).toContain('Spotify (no auth)');
    expect(rest.textContent).not.toContain('—');
    // music only, as it was
    expect(container.querySelector('[data-music-only]')).not.toBeNull();
    expect(
      [...container.querySelectorAll<HTMLOptionElement>('#library-discography-source option')].map(
        (o) => o.value,
      ),
    ).toEqual(['primary', 'automatic', 'itunes', 'deezer', 'musicbrainz', 'spotify']);
  });

  it('the music API Configuration group: every card on the parts, its Test for the sheet, no emoji, no colon', () => {
    const { container } = part('api-music');
    const cards = [...unwrapped(container).querySelectorAll<HTMLElement>('[data-service]')];
    expect(cards.map((c) => c.getAttribute('data-service'))).toEqual([
      'spotify',
      'itunes',
      'deezer',
      'discogs',
      'tidal',
      'qobuz',
      'concerts',
      'lastfm',
      'genius',
      'pollinations',
      'artist-fanart',
      'musicbrainz',
      'acoustid',
      'listenbrainz',
      'hydrabase',
    ]);
    for (const id of [
      'spotify-client-id',
      'spotify-client-secret',
      'spotify-redirect-uri',
      'spotify-disconnect-btn',
      'metadata-spotify-free-enrichment',
      'itunes-country',
      'deezer-app-id',
      'deezer-app-secret',
      'deezer-redirect-uri',
      'deezer-connection-arl',
      'discogs-token',
      'tidal-client-id',
      'tidal-client-secret',
      'tidal-redirect-uri',
      'lastfm-api-key',
      'lastfm-username',
      'genius-access-token',
      'listenbrainz-token',
      'listenbrainz-username',
    ])
      expect(document.getElementById(id), id).not.toBeNull();
    // every card but Hydrabase (its Connect is its check) gives the sheet a Test;
    // none keeps a button of its own for it
    const tested = cards
      .map((c) => c.getAttribute('data-service')!)
      .filter((id) => serviceActions.get(`music:${id}`)?.test);
    expect(tested).toHaveLength(14);
    expect(serviceActions.get('music:hydrabase')?.test).toBeUndefined();
    expect(container.querySelector('[data-svc-test], .test-button, .form-group')).toBeNull();
    // the old page's look is gone: every card's fields are the parts'
    for (const c of cards) expect(c.querySelector('[data-ui]'), c.dataset.service).not.toBeNull();
    for (const b of container.querySelectorAll('button'))
      expect(b.textContent, b.textContent ?? '').not.toMatch(/[\u{1F300}-\u{1FAFF}]/u);
    for (const l of container.querySelectorAll('label'))
      expect(l.textContent, l.textContent ?? '').not.toMatch(/:\s*$/);
  });

  it("Spotify's Disconnect shows while /status says it is signed in (it never could before)", () => {
    part('api-music');
    const button = () => document.getElementById('spotify-disconnect-btn') as HTMLButtonElement;
    expect(button().hidden).toBe(true);
    status({ spotify: { authenticated: true } });
    expect(button().hidden).toBe(false);
    expect(button().textContent).toBe('Disconnect');
    status({ spotify: { authenticated: false } });
    expect(button().hidden).toBe(true);
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
  it('each field saves its key; the redirect uri is shown once', async () => {
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
    // the address is on screen once: the field, no copy line under it
    const sheet = el('spotify-redirect-uri').closest<HTMLElement>('[data-field]')!.parentElement!;
    expect(sheet.textContent!.split('http://127.0.0.1:8888/callback').length).toBe(1);
    expect(document.querySelector('[data-copy]')).toBeNull();
    // emptied, it stays empty while the new one is typed
    fireEvent.change(el('spotify-redirect-uri'), { target: { value: '' } });
    expect(el('spotify-redirect-uri').value).toBe('');
    fireEvent.change(el('spotify-redirect-uri'), { target: { value: 'https://me.example/cb' } });
    expect(el('spotify-redirect-uri').value).toBe('https://me.example/cb');
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

  it('sign-in is the sheet’s main action: it saves first, then opens the sign-in', async () => {
    part('api-music');
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    for (const [id, path] of [
      ['spotify', '/auth/spotify'],
      ['tidal', '/auth/tidal'],
      ['deezer', '/auth/deezer'],
    ]) {
      const primary = serviceActions.get(`music:${id}`)!.primary!;
      expect(primary.label).toBe('Authenticate');
      await act(async () => {
        await primary.onClick();
      });
      expect(open).toHaveBeenLastCalledWith(path, '_blank');
    }
    expect(shell.saveSettings).toHaveBeenCalledTimes(3);
    // signed in, Spotify's says so
    status({ spotify: { authenticated: true } });
    expect(serviceActions.get('music:spotify')!.primary!.label).toBe('Sign in again');
    open.mockRestore();
  });

  it('the redirect uri field has its own copy button, copying what is typed', async () => {
    part('api-music');
    loaded({ spotify: {}, deezer: {}, tidal: {} });
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const copyOf = (id: string) =>
      el(id).parentElement!.querySelector<HTMLButtonElement>('[data-copy-btn]')!;
    fireEvent.click(copyOf('tidal-redirect-uri'));
    expect(writeText).toHaveBeenCalledWith('http://127.0.0.1:8889/tidal/callback');
    await waitFor(() =>
      expect(copyOf('tidal-redirect-uri').getAttribute('aria-label')).toBe('Copied'),
    );
    // emptied, it copies the default the server falls back to
    fireEvent.change(el('deezer-redirect-uri'), { target: { value: 'https://me.example/dz' } });
    fireEvent.click(copyOf('deezer-redirect-uri'));
    expect(writeText).toHaveBeenLastCalledWith('https://me.example/dz');
    fireEvent.change(el('spotify-redirect-uri'), { target: { value: '' } });
    fireEvent.click(copyOf('spotify-redirect-uri'));
    expect(writeText).toHaveBeenLastCalledWith('http://127.0.0.1:8888/callback');
  });

  it('a test saves what is waiting first', async () => {
    part('api-music');
    loaded({ discogs: {} });
    store.set('discogs', 'token', 'x');
    void serviceActions.get('music:discogs')!.test!();
    await waitFor(() => expect(shell.testConnection).toHaveBeenCalledWith('discogs'));
    expect(saved().discogs).toEqual({ token: 'x' });
  });

  it('Deezer with an ARL tests both logins and says both', async () => {
    part('api-music');
    loaded({ deezer_download: { arl: '__redacted_unchanged__' } });
    answers['/api/test-connection'] = { success: true, message: 'Deezer ok' };
    answers['/api/deezer-download/test'] = { success: true, user: 'me', tier: 'Premium' };
    void serviceActions.get('music:deezer')!.test!();
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
    expect(el('qobuz-connection-logged-in').hidden).toBe(false);
    expect(el('qobuz-connection-form').hidden).toBe(true);
    expect(el('qobuz-connection-user-info').textContent).toBe('Connected: Ann (Studio)');
    act(() => {
      window.dispatchEvent(new CustomEvent('soulsync:qobuz-auth', { detail: {} }));
    });
    expect(el('qobuz-connection-form').hidden).toBe(false);
    answers['/api/qobuz/auth/token'] = { success: false, error: 'bad token' };
    fireEvent.change(el('qobuz-connection-token'), { target: { value: ' tk ' } });
    fireEvent.click(el('qobuz-token-login-btn'));
    await waitFor(() => expect(el('qobuz-token-status').textContent).toBe('bad token'));
    expect(calls.find((c) => c.url === '/api/qobuz/auth/token')?.body).toEqual({ token: 'tk' });
    expect(el('qobuz-token-status').getAttribute('data-tone')).toBe('bad');
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
