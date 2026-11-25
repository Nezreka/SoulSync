/**
 * Connections > Services on the settings parts: the Music / Video switch, the
 * tiles with their status from what SoulSync knows (never from a text box
 * having something in it), and the side sheet a tile opens: a card moved in
 * by its wrapper (and still heard), put back in page order; one Test in the
 * sheet's foot that calls the card's own, and a sign-in as its main action.
 */

import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { settingsStore } from '../settings-store';
import { statusFrames } from './connections';
import { testConnection, testResults } from './connections-servers';
import { serviceStatus, servicesModel, ServicesPart } from './connections-services';
import { ConnectionsTab } from './connections-tab';
import { videoShown } from './video-shown';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

let config: Record<string, unknown> = {};
let calls: string[] = [];

beforeEach(() => {
  config = {};
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      calls.push(
        `${init?.method || 'GET'} ${url} ${typeof init?.body === 'string' ? init.body : ''}`,
      );
      const body = url === '/api/settings/config-status' ? config : { success: true };
      return Promise.resolve(
        new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } }),
      );
    }),
  );
  testResults.reset();
  shell.landPendingSettingsSave = vi.fn();
  servicesModel.reset();
  document.body.removeAttribute('data-side');
});

afterEach(() => {
  // unmount first: a portal's wrapper must still be in the page when it goes
  cleanup();
  document.body.innerHTML = '';
  act(() => servicesModel.reset());
  vi.unstubAllGlobals();
  delete shell.landPendingSettingsSave;
});

/** the page around the parts: the settings page and the hidden groups the
 *  music and video cards live in */
function page() {
  const root = document.createElement('div');
  root.id = 'settings-page';
  root.innerHTML = `
    <div data-stg-react="connections" data-stg-part="services" style="display: contents"></div>
    <div data-stg-react="connections" data-stg-part="api-music" style="display: contents"></div>
    <div data-stg-react="connections" data-stg-part="api-video" style="display: contents"></div>`;
  document.body.append(root);
  const slot = (part: string) => root.querySelector<HTMLElement>(`[data-stg-part="${part}"]`)!;
  render(<ServicesPart />, { container: slot('services') });
  render(<ConnectionsTab store={settingsStore} part="api-music" opened={0} active={false} />, {
    container: slot('api-music'),
  });
  render(<ConnectionsTab store={settingsStore} part="api-video" opened={0} active={false} />, {
    container: slot('api-video'),
  });
  return root;
}

const tile = (id: string) => document.querySelector<HTMLElement>(`[data-service-tile="${id}"]`)!;

const status = (id: string) => tile(id).querySelector('[data-tone]')!.textContent;
const tone = (id: string) => tile(id).querySelector('[data-tone]')!.getAttribute('data-tone');
const switchTo = (kind: string) =>
  fireEvent.click(document.querySelector<HTMLElement>(`#svc-tabs [data-value="${kind}"]`)!);
const picked = () => document.querySelector('#svc-tabs [aria-checked="true"]')?.textContent;

describe('the tiles', () => {
  it('nothing is drawn before the first build, as the page had it', () => {
    const slot = document.createElement('div');
    document.body.append(slot);
    render(<ServicesPart />, { container: slot });
    expect(slot.querySelector('[data-service-tile]')).toBeNull();
    expect(slot.querySelector('#svc-tabs')).toBeNull();
    expect(slot.querySelector('#service-tile-grid')!.textContent).toBe('');
  });

  it('groups the cards by what they do, in the order the groups list', async () => {
    page();
    act(() => settingsStore.hydrate({ spotify: {} }));
    await waitFor(() => expect(tile('discogs')).not.toBeNull());
    const groups = [...document.querySelectorAll<HTMLElement>('[data-svc-group]')];
    expect(groups.map((g) => g.dataset.svcGroup)).toEqual(['Metadata', 'Scrobbling', 'Extras']);
    expect(groups.map((g) => g.querySelectorAll('[data-service-tile]').length)).toEqual([8, 2, 5]);
    expect(groups[0].querySelector('[data-service-tile]')!.getAttribute('data-service-tile')).toBe(
      'musicbrainz',
    );
    // a tile names its service and says what opening it is for
    expect(tile('deezer').getAttribute('aria-label')).toMatch(/^Deezer: .+\. Settings$/);
    // a dark logo is drawn white
    expect(tile('tidal').querySelector('[class*="markInv"]')).not.toBeNull();
    expect(picked()).toBe('Music');
  });

  it('says what SoulSync knows: the config check, the sign-in, the last Test; never a filled-in box', async () => {
    config = {
      discogs: { configured: true },
      spotify: { configured: true },
      genius: { configured: false },
    };
    page();
    // the Redirect URI fields hold their default: that used to read as set up
    act(() => settingsStore.hydrate({ spotify: {}, deezer: {} }));
    await waitFor(() => expect(status('discogs')).toBe('Set up'));
    expect(tone('discogs')).toBe('ok');
    expect(status('genius')).toBe('Not set up');
    // credentials but no sign-in yet
    expect(status('spotify')).toBe('Not signed in');
    expect(tone('spotify')).toBe('warn');
    // nothing to judge by yet: said honestly
    expect(status('qobuz')).toBe('Not tested yet');
    expect(status('itunes')).toBe('No key needed');
    expect(status('deezer')).toBe('No key needed');
    // /status says Spotify is signed in
    act(() => {
      window.dispatchEvent(
        new CustomEvent('ss:service-status', { detail: { spotify: { authenticated: true } } }),
      );
    });
    await waitFor(() => expect(status('spotify')).toBe('Signed in'));
    // the Qobuz sign-in, as checkQobuzAuthStatus hears it
    act(() => {
      window.dispatchEvent(
        new CustomEvent('soulsync:qobuz-auth', {
          detail: { authenticated: true, user: { display_name: 'Ann' } },
        }),
      );
    });
    await waitFor(() => expect(status('qobuz')).toBe('Signed in as Ann'));
    // a Test this visit says what the server said
    act(() => testResults.set('genius', { tone: 'bad', text: 'Invalid token' }));
    expect(status('genius')).toBe('Invalid token');
    expect(tone('genius')).toBe('bad');
    void statusFrames;
  });

  it('the primary metadata source carries the accent', async () => {
    page();
    act(() => settingsStore.hydrate({ metadata: { fallback_source: 'deezer' } }));
    await waitFor(() => expect(tile('deezer')).not.toBeNull());
    expect(tile('deezer').className).toMatch(/tileOn/);
    expect(tile('spotify').className).not.toMatch(/tileOn/);
  });

  it('follow the side the page is on, until a kind is picked', () => {
    page();
    document.body.setAttribute('data-side', 'video');
    act(() => servicesModel.rebuild());
    expect(picked()).toBe('Video');
    expect(tile('omdb')).not.toBeNull();
    // the required keys are said where the video tiles are
    expect(document.querySelector('.svc-required-note')!.textContent).toContain(
      'TMDB and TVDB are required.',
    );
    switchTo('music');
    act(() => servicesModel.rebuild());
    expect(picked()).toBe('Music');
    expect(document.querySelector('.svc-required-note')).toBeNull();
  });

  it('picking Video on the music side loads the video sections, as the video side does', () => {
    page();
    act(() => servicesModel.rebuild());
    const before = videoShown.get();
    switchTo('video');
    expect(picked()).toBe('Video');
    // the server section's libraries sat at Loading... until the video side opened
    expect(videoShown.get()).toBe(before + 1);
    // the video side loads them itself when its page is shown: not twice
    switchTo('music');
    document.body.setAttribute('data-side', 'video');
    const onVideo = videoShown.get();
    switchTo('video');
    expect(videoShown.get()).toBe(onVideo);
  });
});

describe('the sheet', () => {
  it('a React card moves in by its wrapper, is still heard, and goes home in page order', async () => {
    const root = page();
    act(() => settingsStore.hydrate({ spotify: {} }));
    await waitFor(() => expect(tile('deezer')).not.toBeNull());
    fireEvent.click(tile('deezer'));
    const body = document.getElementById('svc-modal-body')!;
    expect(body.querySelector('[data-service="deezer"]')).not.toBeNull();
    expect(document.getElementById('service-config-modal')!.hidden).toBe(false);
    expect(document.getElementById('service-config-modal')!.getAttribute('role')).toBe('dialog');
    expect(document.getElementById('svc-modal-title')!.textContent).toBe('Deezer');
    expect(document.getElementById('svc-modal-sub')!.textContent).toBe('Favorites & Playlists');
    expect(document.body.classList.contains('src-modal-open')).toBe(true);
    // the card is on the parts: fields, not the old page's form groups
    expect(body.querySelector('[data-svc-body][data-ui]')).not.toBeNull();
    expect(body.querySelector('.form-group')).toBeNull();
    // typing in it still saves (the bug a moved React node had)
    fireEvent.change(document.getElementById('deezer-app-id')!, { target: { value: 'dz' } });
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(document.getElementById('service-config-modal')!.hidden).toBe(true);
    expect(shell.landPendingSettingsSave).toHaveBeenCalled();
    // #2285: its tile is still there once the sheet is closed
    expect(tile('deezer')).not.toBeNull();
    await new Promise((r) => setTimeout(r, 20));
    expect(tile('deezer')).not.toBeNull();
    // heard: the store holds it, and closing sent it on its way
    expect(settingsStore.getState().settings.deezer).toEqual({ app_id: 'dz' });
    await waitFor(() => expect(settingsStore.hasPending()).toBe(false));
    const home = root.querySelector('[data-stg-part="api-music"] > [data-svc-legacy]')!;
    expect(
      [...home.querySelectorAll('[data-service]')].map((f) => f.getAttribute('data-service')),
    ).toEqual([
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
  });

  it('has one Test, in its foot: it calls the card’s own test, so it sends what it always sent', async () => {
    // the page's own testConnection, as host.tsx puts it on the window
    shell.testConnection = vi.fn((service: string) => testConnection(service));
    page();
    act(() => settingsStore.hydrate({ spotify: {} }));
    await waitFor(() => expect(tile('discogs')).not.toBeNull());
    fireEvent.click(tile('discogs'));
    const card = document.querySelector<HTMLElement>('#svc-modal-body [data-service="discogs"]')!;
    // no button of its own for it: one Test per sheet
    expect(card.querySelector('[data-svc-test], .test-button')).toBeNull();
    expect(
      [...document.querySelectorAll('#service-config-modal button')].filter((b) =>
        /test/i.test(b.textContent ?? ''),
      ),
    ).toHaveLength(1);
    fireEvent.click(document.getElementById('svc-modal-test')!);
    await waitFor(() => expect(shell.testConnection).toHaveBeenCalledWith('discogs'));
    await waitFor(() =>
      expect(calls.some((c) => c.includes('/api/test-connection') && c.includes('discogs'))).toBe(
        true,
      ),
    );
    // its answer is beside the button
    await waitFor(() =>
      expect(document.getElementById('svc-modal-result')!.textContent).toBe('Connected'),
    );
    delete shell.testConnection;
  });

  it('a card with a sign-in has it as the main action; the others have Done', async () => {
    page();
    act(() => settingsStore.hydrate({ spotify: {} }));
    await waitFor(() => expect(tile('spotify')).not.toBeNull());
    const foot = () =>
      [...document.querySelectorAll('#service-config-modal button')].map((b) => b.textContent);
    fireEvent.click(tile('spotify'));
    expect(foot()).toContain('Authenticate');
    expect(foot()).not.toContain('Done');
    fireEvent.keyDown(document, { key: 'Escape' });
    fireEvent.click(tile('genius'));
    expect(foot()).toContain('Done');
    expect(foot()).not.toContain('Authenticate');
  });

  it('a video card moves in, and goes home when the sheet closes', () => {
    page();
    act(() => servicesModel.switchKind('video'));
    fireEvent.click(tile('omdb'));
    const frame = document.querySelector<HTMLElement>('[data-video-service="omdb"]')!;
    expect(frame.closest('#svc-modal-body')).not.toBeNull();
    expect(document.getElementById('svc-modal-title')!.textContent).toBe('OMDb');
    expect(document.getElementById('svc-modal-sub')!.textContent).toBe('Ratings');
    fireEvent.click(document.querySelector('#service-config-modal button[aria-label="Close"]')!);
    expect(frame.closest('#svc-modal-body')).toBeNull();
    expect(frame.closest('[data-svc-legacy]')).not.toBeNull();
    expect(document.body.classList.contains('src-modal-open')).toBe(false);
  });

  it('a click on the scrim closes it, a click inside does not', () => {
    page();
    act(() => servicesModel.switchKind('video'));
    fireEvent.click(tile('omdb'));
    fireEvent.click(document.getElementById('svc-modal-body')!);
    expect(document.getElementById('service-config-modal')!.hidden).toBe(false);
    fireEvent.click(document.querySelector('[data-sheet-scrim]')!);
    expect(document.getElementById('service-config-modal')!.hidden).toBe(true);
  });
});

describe('serviceStatus', () => {
  const base = {
    config: null,
    frame: null,
    qobuz: null,
    hydrabase: false,
    settings: {},
    videoKeys: {},
    videoOn: {},
    tests: {},
  };
  it('video: a saved key, a required one missing, the keyless cards by their switches', () => {
    expect(serviceStatus('video', 'tmdb', { ...base, videoKeys: { tmdb: 'k' } }).text).toBe(
      'Key saved',
    );
    expect(serviceStatus('video', 'tvdb', base)).toMatchObject({
      tone: 'warn',
      text: 'Required: no key yet',
    });
    expect(serviceStatus('video', 'omdb', base).text).toBe('Not set up');
    expect(
      serviceStatus('video', 'nokey', { ...base, videoOn: { tvmaze: true, anilist: false } }).text,
    ).toBe('1 of 3 on');
  });
  it('music without a config check: its saved key, else not tested yet', () => {
    expect(
      serviceStatus('music', 'pollinations', {
        ...base,
        settings: { pollinations: { api_key: 'x' } },
      }).text,
    ).toBe('Key saved');
    expect(serviceStatus('music', 'lastfm', base).text).toBe('Not tested yet');
    expect(serviceStatus('music', 'hydrabase', { ...base, hydrabase: true }).text).toBe(
      'Connected',
    );
  });
});
