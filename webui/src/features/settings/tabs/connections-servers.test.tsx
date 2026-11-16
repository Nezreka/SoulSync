/**
 * Connections > Server Connections (SETTINGS_PORT.md, step 10c): the markup
 * it replaced, the toggles and the active server, Plex's setup and PIN link,
 * the library pickers and their endpoints, the playlist account, Music
 * Assistant's source, and testConnection.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { shape, vanilla } from './__fixtures__/shape';
import { ServerConnections, testConnection } from './connections-servers';

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
      const answer = answers[url] ?? answers[url.split('?')[0]] ?? { success: true };
      const data = typeof answer === 'function' ? (answer as () => unknown)() : answer;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data) } as Response);
    }),
  );
  for (const k of [
    'showToast',
    'showLoadingOverlay',
    'hideLoadingOverlay',
    'landPendingSettingsSave',
  ])
    shell[k] = vi.fn();
  document.body.removeAttribute('data-side');
});

afterEach(async () => {
  await act(() => store.flush());
  vi.unstubAllGlobals();
  vi.useRealTimers();
  for (const k of [
    'showToast',
    'showLoadingOverlay',
    'hideLoadingOverlay',
    'landPendingSettingsSave',
  ])
    delete shell[k];
});

const section = () => render(<ServerConnections store={store} />);
const el = <T extends HTMLElement = HTMLInputElement>(id: string) =>
  document.getElementById(id) as T;
const shown = (id: string) => el(id).style.display !== 'none';
const sent = (url: string) => calls.filter((c) => c.url === url);
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}
const loaded = (settings: Record<string, unknown>) => act(() => store.hydrate(settings));

describe('the section is the markup it replaced', () => {
  it('before the settings load', () => {
    const { container } = section();
    expect(shape(container)).toEqual(shape(vanilla('connections-servers.vanilla.html')));
  });
});

describe('the servers', () => {
  it('the saved server is shown and its pickers load; Jellyfin: the user, then the libraries', async () => {
    answers['/api/jellyfin/users'] = {
      success: true,
      users: [{ name: 'ann' }, { name: 'bob' }],
      current: 'bob',
    };
    answers['/api/jellyfin/music-libraries'] = {
      success: true,
      libraries: [{ title: 'Music' }, { title: 'Live' }],
      selected: 'Live',
    };
    section();
    loaded({
      active_media_server: 'jellyfin',
      jellyfin: { base_url: 'http://jf', api_timeout: 90 },
    });
    expect(el('jellyfin-toggle').className).toBe('server-toggle-btn active');
    expect(el('plex-container').className).toBe('server-config-container hidden');
    expect(el('jellyfin-container').className).toBe('server-config-container');
    expect(el('jellyfin-url').value).toBe('http://jf');
    expect(el('jellyfin-timeout').value).toBe('90');
    await waitFor(() => expect(el<HTMLSelectElement>('jellyfin-music-library').value).toBe('Live'));
    expect(el<HTMLSelectElement>('jellyfin-user').value).toBe('bob');
    expect(shown('jellyfin-user-selector-container')).toBe(true);
    // a new user, then that user's libraries
    fireEvent.change(el('jellyfin-user'), { target: { value: 'ann' } });
    await waitFor(() => expect(sent('/api/jellyfin/music-libraries')).toHaveLength(2));
    expect(sent('/api/jellyfin/select-user')[0].body).toEqual({ username: 'ann' });
  });

  it('a toggle shows its server and saves it as the active one, but not from the video side', async () => {
    section();
    loaded({ active_media_server: 'plex' });
    fireEvent.click(el('navidrome-toggle'));
    expect(el('navidrome-container').className).toBe('server-config-container');
    await waitFor(() =>
      expect(sent('/api/settings')).toContainEqual(
        expect.objectContaining({ body: { active_media_server: 'navidrome' } }),
      ),
    );
    document.body.setAttribute('data-side', 'video');
    fireEvent.click(el('soulsync-toggle'));
    expect(el('soulsync-container').className).toBe('server-config-container');
    await act(() => store.flush());
    expect(sent('/api/settings')).toHaveLength(1);
  });

  it('each connection field saves its key; Jellyfin timeout as a number, else 30', async () => {
    section();
    loaded({ active_media_server: 'plex' });
    fireEvent.change(el('jellyfin-timeout'), { target: { value: 'x' } });
    fireEvent.change(el('navidrome-username'), { target: { value: 'nd' } });
    fireEvent.change(el('navidrome-password'), { target: { value: 'pw' } });
    fireEvent.change(el('music_assistant-url'), { target: { value: ' http://ma ' } });
    fireEvent.change(el('music_assistant-source-folder'), { target: { value: '/m ' } });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({
      jellyfin: { api_timeout: 30 },
      navidrome: { username: 'nd', password: 'pw' },
      music_assistant: { base_url: 'http://ma', source_folder: '/m' },
    });
    // a url field trims what it shows itself (the browser does, as before)
    expect(el('music_assistant-source-folder').value).toBe('/m ');
  });
});

describe('Plex', () => {
  it('configured: the setup offers View Configuration, which opens the fields', async () => {
    answers['/api/plex/music-libraries'] = {
      success: true,
      libraries: [{ title: 'Music' }],
      current: 'Music',
    };
    section();
    loaded({
      active_media_server: 'plex',
      plex: { base_url: 'http://plex', token: '__redacted_unchanged__' },
    });
    expect(shown('plex-configuration')).toBe(false);
    expect(shown('plex-setup')).toBe(true);
    expect(shown('plex-view-config-button')).toBe(true);
    expect(shown('plex-link-to-plex-button')).toBe(false);
    fireEvent.click(el('plex-view-config-button'));
    expect(shown('plex-configuration')).toBe(true);
    expect(el('plex-config-action-button').textContent).toBe('Clear Configuration');
    expect(el('plex-config-action-button').title).toBe('Clear saved Plex configuration');
    expect(shown('plex-url-actions')).toBe(false);
    await waitFor(() => expect(shown('plex-library-selector-container')).toBe(true));
    fireEvent.change(el('plex-music-library'), { target: { value: 'Music' } });
    expect(sent('/api/plex/select-music-library')[0].body).toEqual({ library_name: 'Music' });
  });

  it('not configured: manual setup can be cancelled; Clear empties the fields and saves', async () => {
    section();
    loaded({ active_media_server: 'plex', plex: { base_url: '', token: '' } });
    expect(shown('plex-link-to-plex-button')).toBe(true);
    expect(shown('plex-view-config-button')).toBe(false);
    fireEvent.click(el('plex-manual-config-button'));
    expect(el('plex-config-action-button').textContent).toBe('Cancel');
    expect(shown('plex-url-actions')).toBe(true);
    fireEvent.click(el('plex-config-action-button'));
    expect(shown('plex-setup')).toBe(true);
    expect(shown('plex-configuration')).toBe(false);
    loaded({ plex: { base_url: 'http://p', token: 't' } });
    fireEvent.click(el('plex-view-config-button'));
    fireEvent.click(el('plex-config-action-button'));
    await waitFor(() => expect(saved().plex).toEqual({ base_url: '', token: '' }));
    expect(sent('/api/plex/clear-library')).toHaveLength(1);
    expect(shell.showToast).toHaveBeenCalledWith(
      'Plex configuration cleared',
      'success',
      undefined,
    );
  });

  it('Link to Plex: the code, then the polling; a yes saves the address and token, locked, and tests', async () => {
    vi.useFakeTimers();
    let polls = 0;
    answers['/api/plex/pin/start'] = {
      success: true,
      request_id: 'r1',
      code: 'ABCD',
      expires_in: 600,
    };
    answers['/api/plex/pin/status'] = () =>
      ++polls < 2
        ? { status: 'Waiting...' }
        : { success: true, token: 'tok', found_url: 'http://found' };
    answers['/api/test-connection'] = { success: true, message: 'Plex ok' };
    section();
    loaded({ active_media_server: 'plex', plex: {} });
    fireEvent.click(el('plex-link-to-plex-button'));
    expect(shown('plex-setup-buttons')).toBe(false);
    expect(shown('plex-pin-auth-flow')).toBe(true);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(el('plex-pin-code').textContent).toBe('ABCD');
    expect(el('plex-pin-status').textContent).toBe('Waiting...');
    await act(() => vi.advanceTimersByTimeAsync(5000));
    expect(saved().plex).toEqual({ base_url: 'http://found', token: 'tok' });
    expect(shown('plex-configuration')).toBe(true);
    expect(el('plex-url').disabled).toBe(true);
    expect(el('plex-url').value).toBe('http://found');
    expect(sent('/api/test-connection')[0].body).toEqual({ service: 'plex' });
    // no more polling once it said yes
    await act(() => vi.advanceTimersByTimeAsync(10000));
    expect(polls).toBe(2);
  });

  it('a re-link keeps the address and changes nothing when the new token cannot reach it', async () => {
    answers['/api/plex/pin/start'] = { success: true, request_id: 'r2', code: 'WXYZ' };
    answers['/api/plex/pin/status'] = {
      success: true,
      token: 'new',
      found_url: 'http://elsewhere',
    };
    answers['/api/plex/verify-token'] = { success: false, error: 'Unauthorized' };
    section();
    loaded({ active_media_server: 'plex', plex: { base_url: 'http://mine', token: 'old' } });
    fireEvent.click(el('plex-view-config-button'));
    fireEvent.click(el('plex-relink-button'));
    await waitFor(() =>
      expect(el('plex-pin-status').textContent).toBe('Unauthorized. Nothing was changed.'),
    );
    expect(sent('/api/plex/verify-token')[0].body).toEqual({ url: 'http://mine', token: 'new' });
    expect(posted).toEqual([]);
    // Cancel goes back to the configured view
    fireEvent.click(el('plex-pin-auth-flow').querySelector('.test-button')!);
    expect(shown('plex-setup')).toBe(false);
    expect(shown('plex-configuration')).toBe(true);
  });
});

describe('Navidrome and Music Assistant', () => {
  it('the playlist account loads, saves through its endpoint and clears', async () => {
    answers['/api/profiles/me/server-library'] = { success: true, navidrome_username: 'me' };
    answers['/api/navidrome/music-folders'] = {
      success: true,
      folders: [{ title: 'A' }],
      selected: 'A',
    };
    section();
    loaded({ active_media_server: 'navidrome' });
    await waitFor(() =>
      expect(el('navidrome-playlist-status').textContent).toBe('Your synced playlists go to me.'),
    );
    expect(el('navidrome-playlist-username').value).toBe('me');
    expect(shown('navidrome-playlist-clear')).toBe(true);
    await waitFor(() => expect(el<HTMLSelectElement>('navidrome-music-folder').value).toBe('A'));
    fireEvent.change(el('navidrome-playlist-username'), { target: { value: ' you ' } });
    fireEvent.click(el('navidrome-playlist-login').querySelector('.test-button')!);
    expect(shell.showToast).toHaveBeenCalledWith(
      'Enter the playlist account username and password',
      'warning',
      undefined,
    );
    fireEvent.change(el('navidrome-playlist-password'), { target: { value: 'pw' } });
    answers['/api/profiles/me/navidrome-login'] = { success: true, username: 'you' };
    fireEvent.click(el('navidrome-playlist-login').querySelector('.test-button')!);
    await waitFor(() =>
      expect(el('navidrome-playlist-status').textContent).toBe('Your synced playlists go to you.'),
    );
    expect(sent('/api/profiles/me/navidrome-login')[0].body).toEqual({
      username: 'you',
      password: 'pw',
    });
    expect(el('navidrome-playlist-password').value).toBe('');
    fireEvent.click(el('navidrome-playlist-clear'));
    await waitFor(() => expect(shown('navidrome-playlist-clear')).toBe(false));
    expect(sent('/api/profiles/me/navidrome-login')[1].method).toBe('DELETE');
  });

  it("Music Assistant's sources load quietly when it is set up; a pick is sent and saved", async () => {
    answers['/api/music-assistant/sources'] = {
      success: true,
      selected: 'b',
      sources: [
        { key: 'a', title: 'Local', domain: 'filesystem' },
        { key: 'b', title: 'NAS', domain: 'smb' },
      ],
    };
    section();
    loaded({
      active_media_server: 'music_assistant',
      music_assistant: { base_url: 'http://ma', token: 't' },
    });
    await waitFor(() => expect(el<HTMLSelectElement>('music_assistant-source').value).toBe('b'));
    expect(shown('music_assistant-source-selector-container')).toBe(true);
    fireEvent.change(el('music_assistant-source'), { target: { value: 'a' } });
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith(
        'Music source set to Local (filesystem)',
        'success',
        undefined,
      ),
    );
    expect(sent('/api/music-assistant/select-source')[0].body).toEqual({ instance_id: 'a' });
    await waitFor(() => expect(saved().music_assistant).toEqual({ source_instance: 'a' }));
  });
});

describe('testConnection', () => {
  it('saves what is waiting first, says what the server said, and a good server test reloads its picker', async () => {
    answers['/api/test-connection'] = { success: true, message: '⚠ slow but ok' };
    answers['/api/navidrome/music-folders'] = { success: true, folders: [{ title: 'A' }] };
    section();
    loaded({ active_media_server: 'navidrome' });
    await waitFor(() => expect(sent('/api/navidrome/music-folders')).toHaveLength(1));
    store.set('genius', 'access_token', 'g');
    await act(() => testConnection('navidrome', store));
    expect(shell.landPendingSettingsSave).toHaveBeenCalled();
    expect(saved().genius).toEqual({ access_token: 'g' });
    expect(shell.showToast).toHaveBeenCalledWith('⚠ slow but ok', 'warning', undefined);
    await waitFor(() => expect(sent('/api/navidrome/music-folders')).toHaveLength(2));
    answers['/api/test-connection'] = { success: false, error: 'nope' };
    await act(() => testConnection('lastfm', store));
    expect(shell.showToast).toHaveBeenCalledWith(
      'lastfm connection failed: nope',
      'error',
      'gs-connecting',
    );
  });
});

describe('the library pickers (were static/settings.js, before that beatport-ui.js)', () => {
  const plex = () => el<HTMLSelectElement>('plex-music-library');
  async function plexWith(answer: unknown) {
    answers['/api/plex/music-libraries'] = answer;
    section();
    loaded({ active_media_server: 'plex' });
    await act(() => Promise.resolve());
    await act(() => Promise.resolve());
  }

  it('the saved library is picked, not the first one', async () => {
    // not first, on purpose: a select defaults to its first option
    await plexWith({
      success: true,
      selected: 'Music',
      libraries: [{ title: 'Podcasts' }, { title: 'Music' }],
    });
    await waitFor(() => expect(plex().value).toBe('Music'));
    expect([...plex().options].map((o) => o.textContent)).toEqual(['Podcasts', 'Music']);
    expect(el('plex-library-selector-container').style.display).toBe('block');
  });

  it('prefers value over title, for the All Libraries sentinel', async () => {
    await plexWith({
      success: true,
      selected: '__all__',
      libraries: [{ title: 'Music' }, { title: 'All Libraries', value: '__all__' }],
    });
    await waitFor(() => expect(plex().value).toBe('__all__'));
    expect(plex().options[1].textContent).toBe('All Libraries');
  });

  it('hides the picker when the server has nothing, or does not answer', async () => {
    await plexWith({ success: true, libraries: [] });
    expect(el('plex-library-selector-container').style.display).toBe('none');
    answers['/api/plex/music-libraries'] = () => {
      throw new Error('plex down');
    };
    await act(() => testConnection('plex', store));
    expect(el('plex-library-selector-container').style.display).toBe('none');
  });

  it('a failed switch is a toast, never an alert', async () => {
    const alert = vi.spyOn(window, 'alert').mockImplementation(() => {});
    await plexWith({ success: true, libraries: [{ title: 'Music' }, { title: 'Vinyl' }] });
    await waitFor(() => expect(plex().options).toHaveLength(2));
    answers['/api/plex/select-music-library'] = { success: false, error: 'no such library' };
    fireEvent.change(plex(), { target: { value: 'Vinyl' } });
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith(
        'Failed to switch library: no such library',
        'error',
        'set-media',
      ),
    );
    expect(sent('/api/plex/select-music-library')[0].body).toEqual({ library_name: 'Vinyl' });
    expect(alert).not.toHaveBeenCalled();
    alert.mockRestore();
  });
});
