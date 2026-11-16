/**
 * Connections > Services (SETTINGS_PORT.md, step 10a): the tabs, the tiles
 * and the modal, a React card moved in by its wrapper (and still heard), a
 * vanilla card lifted in and put back where it was.
 */

import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { settingsStore } from '../settings-store';
import { shape, vanilla } from './__fixtures__/shape';
import { servicesModel, ServicesPart } from './connections-services';
import { ConnectionsTab } from './connections-tab';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ success: true }), {
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    ),
  );
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

/** the page around the parts: the settings page, the hidden group the cards
 *  live in, a vanilla video card and the two server sections */
function page() {
  const root = document.createElement('div');
  root.id = 'settings-page';
  root.innerHTML = `
    <div data-stg-react="connections" data-stg-part="services" style="display: contents"></div>
    <div data-stg-react="connections" data-stg-part="api-music" style="display: contents"></div>
    <div class="settings-group" data-stg="connections" data-svc-legacy>
      <div class="api-service-frame stg-service" data-video-service="omdb">
        <div class="stg-service-header"><span class="stg-service-dot" style="color: #f5c518;"></span>
          <h4 class="service-title">OMDb (Ratings)</h4></div>
        <div class="stg-service-body"><input type="text" id="omdb-key" value=""></div>
      </div>
      <span id="after-omdb"></span>
    </div>
    <div class="settings-group" data-svc-side="music"></div>
    <div class="settings-group" data-svc-side="video"></div>`;
  document.body.append(root);
  const slot = (part: string) => root.querySelector<HTMLElement>(`[data-stg-part="${part}"]`)!;
  render(<ServicesPart />, { container: slot('services') });
  render(<ConnectionsTab store={settingsStore} part="api-music" opened={0} active={false} />, {
    container: slot('api-music'),
  });
  return root;
}

const tile = (id: string) => document.querySelector<HTMLElement>(`[data-service-tile="${id}"]`)!;

describe('the tiles', () => {
  it('nothing is drawn before the first build, as the page had it', () => {
    const slot = document.createElement('div');
    document.body.append(slot);
    render(<ServicesPart />, { container: slot });
    expect(shape(slot)).toEqual(shape(vanilla('connections-services.vanilla.html')));
  });

  it('each build reads the cards: the name, the brand colour, filled in or not', async () => {
    page();
    act(() => settingsStore.hydrate({ discogs: { token: 'tok' }, spotify: {} }));
    await waitFor(() => expect(tile('discogs')).not.toBeNull());
    const groups = [...document.querySelectorAll('.src-group-label')].map((g) => g.textContent);
    expect(groups).toEqual(['Metadata', 'Scrobbling', 'Extras']);
    expect([...document.querySelectorAll('.src-group-count')].map((g) => g.textContent)).toEqual([
      '8',
      '2',
      '5',
    ]);
    // the order the groups list, not the page's
    expect(
      [...document.querySelectorAll('.src-group')][0]
        .querySelectorAll('[data-service-tile]')[0]
        .getAttribute('data-service-tile'),
    ).toBe('musicbrainz');
    expect(tile('discogs').className).toBe('svc-tile is-active');
    expect(tile('discogs').querySelector('.src-tile-chip')!.textContent).toBe('configured');
    // the redirect field holds its default, so Spotify reads as set up (as before)
    expect(tile('spotify').className).toBe('svc-tile is-active');
    expect(tile('qobuz').className).toBe('svc-tile');
    expect(tile('qobuz').querySelector('.src-tile-chip')!.textContent).toBe('not set up');
    expect(tile('itunes').querySelector('.src-tile-chip')!.textContent).toBe('no key needed');
    expect(tile('spotify').style.getPropertyValue('--svc-brand')).toBe('rgb(29, 185, 84)');
    expect(tile('deezer').querySelector('.svc-tile-name')!.textContent).toBe('Deezer');
    expect(tile('deezer').title).toBe('Configure Deezer');
    expect(tile('tidal').querySelector('img')!.className).toBe('svc-mark is-inverted');
    // the music tab is on, and the music server section with it
    expect(document.querySelector('#svc-tabs .active')!.textContent).toBe('Music');
    expect(document.querySelector('[data-svc-side="music"]')!.className).toContain(
      'svc-side-active',
    );
  });

  it('follow the side the page is on, until a tab is picked', () => {
    page();
    document.body.setAttribute('data-side', 'video');
    act(() => servicesModel.rebuild());
    expect(document.querySelector('#svc-tabs .active')!.textContent).toBe('Video');
    expect(tile('omdb').querySelector('.svc-glyph')!.textContent).toBe('★');
    expect(document.querySelector('[data-svc-side="video"]')!.className).toContain(
      'svc-side-active',
    );
    fireEvent.click(document.querySelectorAll<HTMLElement>('#svc-tabs button')[0]);
    act(() => servicesModel.rebuild());
    expect(document.querySelector('#svc-tabs .active')!.textContent).toBe('Music');
  });
});

describe('the modal', () => {
  it('a React card moves in by its wrapper, is still heard, and goes home in page order', async () => {
    const root = page();
    act(() => settingsStore.hydrate({ spotify: {} }));
    await waitFor(() => expect(tile('deezer')).not.toBeNull());
    fireEvent.click(tile('deezer'));
    const body = document.getElementById('svc-modal-body')!;
    expect(body.querySelector('[data-service="deezer"]')).not.toBeNull();
    expect(document.getElementById('service-config-modal')!.hidden).toBe(false);
    expect(document.getElementById('svc-modal-title')!.textContent).toBe('Deezer');
    expect(document.getElementById('svc-modal-sub')!.textContent).toBe('Favorites & Playlists');
    expect(document.body.classList.contains('src-modal-open')).toBe(true);
    // the body shows as the content, not a collapsed accordion
    expect(body.querySelector<HTMLElement>('.stg-service-body')!.style.display).toBe('block');
    // typing in it still saves (the bug a moved React node had)
    fireEvent.change(document.getElementById('deezer-app-id')!, { target: { value: 'dz' } });
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(document.getElementById('service-config-modal')!.hidden).toBe(true);
    expect(shell.landPendingSettingsSave).toHaveBeenCalled();
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
    // and the test row stays last
    expect(home.lastElementChild!.className).toBe('api-test-buttons');
  });

  it('a vanilla card is lifted in and put back exactly where it was', () => {
    page();
    act(() => servicesModel.switchKind('video'));
    fireEvent.click(tile('omdb'));
    const frame = document.querySelector<HTMLElement>('[data-video-service="omdb"]')!;
    expect(frame.parentElement!.id).toBe('svc-modal-body');
    expect(frame.querySelector<HTMLElement>('.stg-service-body')!.style.display).toBe('block');
    expect(document.getElementById('svc-modal-title')!.textContent).toBe('OMDb');
    expect(document.querySelector('#svc-modal-icon .svc-glyph')!.textContent).toBe('★');
    fireEvent.click(document.querySelector('#service-config-modal .src-modal-close')!);
    expect(frame.nextElementSibling!.id).toBe('after-omdb');
    expect(frame.querySelector<HTMLElement>('.stg-service-body')!.style.display).toBe('');
    expect(document.body.classList.contains('src-modal-open')).toBe(false);
  });

  it('a click on the backdrop closes it, a click inside does not', () => {
    page();
    act(() => servicesModel.switchKind('video'));
    fireEvent.click(tile('omdb'));
    fireEvent.click(document.querySelector('#service-config-modal .src-modal')!);
    expect(document.getElementById('service-config-modal')!.hidden).toBe(false);
    fireEvent.click(document.getElementById('service-config-modal')!);
    expect(document.getElementById('service-config-modal')!.hidden).toBe(true);
  });
});
