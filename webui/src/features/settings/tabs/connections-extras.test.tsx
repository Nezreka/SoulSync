/**
 * Connections, step 10b (SETTINGS_PORT.md): the Scrobbling and Extras cards
 * and the rest of the music API Configuration group. Its markup is checked
 * with the whole group in connections-metadata.test.tsx.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { ConnectionsTab } from './connections-tab';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

let store: SettingsStore;
let posted: SettingsPayload[];
let calls: Array<{ url: string; method: string }>;
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
      calls.push({ url, method: init?.method || 'GET' });
      const data = answers[url] ?? { success: true };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data) } as Response);
    }),
  );
  for (const k of ['showToast', 'showLoadingOverlay', 'hideLoadingOverlay']) shell[k] = vi.fn();
  shell.testConnection = vi.fn(() => Promise.resolve());
  shell.saveSettings = vi.fn(() => Promise.resolve());
  shell.showConfirmDialog = vi.fn(() => Promise.resolve(true));
});

afterEach(async () => {
  await act(() => store.flush());
  vi.unstubAllGlobals();
  for (const k of [
    'showToast',
    'showLoadingOverlay',
    'hideLoadingOverlay',
    'testConnection',
    'saveSettings',
    'showConfirmDialog',
  ])
    delete shell[k];
});

function group() {
  const slot = document.createElement('div');
  document.body.append(slot);
  return render(<ConnectionsTab store={store} part="api-music" opened={0} active={false} />, {
    container: slot,
  });
}
const el = (id: string) => document.getElementById(id) as HTMLInputElement;
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}

describe('the Scrobbling and Extras cards', () => {
  it('show what is saved, Last.fm says whether it is authorized', () => {
    group();
    expect(el('lastfm-scrobble-status').textContent).toBe('');
    act(() =>
      store.hydrate({
        lastfm: { username: 'me', scrobble_enabled: true, session_key: 'abc' },
        listenbrainz: { base_url: 'http://lb', scrobble_enabled: false },
        acoustid: { enabled: true, api_key: '__redacted_unchanged__' },
        concerts: { country: 'GB' },
        fanart: { api_key: 'f' },
      }),
    );
    expect(el('lastfm-username').value).toBe('me');
    expect(el('lastfm-scrobble-enabled').checked).toBe(true);
    expect(el('lastfm-scrobble-status').textContent).toBe('Authorized');
    expect(el('listenbrainz-base-url').value).toBe('http://lb');
    expect(el('acoustid-enabled').checked).toBe(true);
    expect(el('acoustid-api-key').value).toBe('__redacted_unchanged__');
    expect(el('concerts-country').value).toBe('GB');
    expect(el('artist-fanart-api-key').value).toBe('f');
    act(() => store.hydrate({ lastfm: {} }));
    expect(el('lastfm-scrobble-status').textContent).toBe('Not authorized');
  });

  it('each field saves its key; the ones the page trimmed are trimmed', async () => {
    group();
    act(() => store.hydrate({ lastfm: {} }));
    fireEvent.change(el('lastfm-username'), { target: { value: ' me ' } });
    // shown as typed
    expect(el('lastfm-username').value).toBe(' me ');
    fireEvent.change(el('lastfm-api-key'), { target: { value: ' k ' } });
    fireEvent.click(el('lastfm-scrobble-enabled'));
    fireEvent.change(el('concerts-country'), { target: { value: 'gb ' } });
    fireEvent.change(el('concerts-ticketmaster-api-key'), { target: { value: ' tm' } });
    fireEvent.change(el('pollinations-api-key'), { target: { value: ' p ' } });
    fireEvent.change(el('artist-fanart-api-key'), { target: { value: 'f ' } });
    fireEvent.change(el('genius-access-token'), { target: { value: ' g ' } });
    fireEvent.click(el('acoustid-enabled'));
    fireEvent.change(el('listenbrainz-username'), { target: { value: ' u ' } });
    fireEvent.change(el('listenbrainz-token'), { target: { value: 't' } });
    fireEvent.click(el('listenbrainz-scrobble-enabled'));
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({
      lastfm: { username: 'me', api_key: ' k ', scrobble_enabled: true },
      concerts: { country: 'GB', ticketmaster_api_key: 'tm' },
      pollinations: { api_key: 'p' },
      fanart: { api_key: 'f' },
      genius: { access_token: ' g ' },
      acoustid: { enabled: true },
      listenbrainz: { username: 'u', token: 't', scrobble_enabled: true },
    });
  });

  it('Authorize Scrobbling saves first, then opens Last.fm', async () => {
    const opened = vi.spyOn(window, 'open').mockImplementation(() => null);
    answers['/api/lastfm/auth-url'] = { success: true, url: 'https://last.fm/auth' };
    group();
    fireEvent.click(document.querySelector('#lastfm-scrobble-actions button')!);
    await waitFor(() =>
      expect(opened).toHaveBeenCalledWith('https://last.fm/auth', '_blank', 'width=600,height=500'),
    );
    expect(shell.saveSettings).toHaveBeenCalledWith();
    opened.mockRestore();
  });

  it('Clear Quarantine asks, then clears', async () => {
    group();
    const button = [...document.querySelectorAll('[data-service="acoustid"] button')].find(
      (b) => b.textContent === 'Clear Quarantine',
    )!;
    fireEvent.click(button);
    await waitFor(() =>
      expect(calls).toContainEqual({ url: '/api/quarantine/clear', method: 'POST' }),
    );
    expect(shell.showConfirmDialog).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Clear Quarantine', destructive: true }),
    );
    expect(shell.showToast).toHaveBeenCalledWith('Quarantine cleared', 'success');
  });

  it('Last.fm says plainly when the key was refused (Pro only from 1 December 2026)', async () => {
    const pro =
      'Last.fm now requires a Pro subscription for API keys (from 1 December 2026). Your key was not accepted.';
    answers['/api/lastfm/configured'] = { configured: true, key_rejected: true, message: pro };
    group();
    await waitFor(() => expect(el('lastfm-key-rejected')?.textContent).toBe(pro));
    // ListenBrainz is the recommended scrobbler, on both cards
    expect(document.getElementById('listenbrainz-recommended')?.textContent).toMatch(/Recommended/);
    expect(document.getElementById('lastfm-pro-note')?.textContent).toMatch(/Pro subscription/);
  });

  it('a working key shows no refusal, and Test asks again', async () => {
    answers['/api/lastfm/configured'] = { configured: true, key_rejected: false, message: '' };
    group();
    await waitFor(() =>
      expect(calls.filter((c) => c.url === '/api/lastfm/configured')).toHaveLength(1),
    );
    expect(document.getElementById('lastfm-key-rejected')).toBeNull();
    const buttons = document.querySelectorAll<HTMLButtonElement>(
      '[data-service="lastfm"] .form-actions:last-child .test-button',
    );
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() =>
      expect(calls.filter((c) => c.url === '/api/lastfm/configured')).toHaveLength(2),
    );
  });

  it('the test row and each card test the service', async () => {
    group();
    const row = [...document.querySelectorAll<HTMLButtonElement>('.api-test-buttons button')];
    expect(row.map((b) => b.textContent)).toEqual([
      'Test Spotify',
      'Test Tidal',
      'Test ListenBrainz',
      'Test AcoustID',
      'Test Last.fm',
      'Test Genius',
    ]);
    fireEvent.click(row[2]);
    fireEvent.click(
      document.querySelector<HTMLButtonElement>('[data-service="concerts"] .test-button')!,
    );
    await waitFor(() => expect(shell.testConnection).toHaveBeenCalledTimes(2));
    expect(shell.testConnection).toHaveBeenNthCalledWith(1, 'listenbrainz');
    expect(shell.testConnection).toHaveBeenNthCalledWith(2, 'concerts');
  });

  it('Expand All opens every card, then shuts them', () => {
    const page = document.createElement('div');
    page.id = 'settings-page';
    document.body.append(page);
    render(<ConnectionsTab store={store} part="api-music" opened={0} active={false} />, {
      container: page,
    });
    const toggle = document.querySelector<HTMLButtonElement>('.stg-accordion-toggle')!;
    fireEvent.click(toggle);
    const cards = [...page.querySelectorAll('.stg-service')];
    expect(cards).toHaveLength(15);
    expect(cards.every((c) => c.classList.contains('expanded'))).toBe(true);
    expect(toggle.textContent).toBe('Collapse All');
    fireEvent.click(toggle);
    expect(cards.some((c) => c.classList.contains('expanded'))).toBe(false);
    expect(toggle.textContent).toBe('Expand All');
  });
});
