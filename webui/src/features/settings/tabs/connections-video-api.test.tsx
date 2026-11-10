/**
 * The video side's API Configuration (SETTINGS_PORT.md, video pass V3a): the
 * markup it replaced, the keys loaded each time the video settings page is
 * shown, a key saved when left and a switch at once (all of them, as
 * saveKeys posted), the Test buttons, and a card opened from its tile (moved
 * in by its wrapper and still heard there).
 */

import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { shape, vanilla } from './__fixtures__/shape';
import { servicesModel, ServicesPart } from './connections-services';
import { VideoApiGroup } from './connections-video-api';
import { testVideoServices, videoKeys } from './video-keys';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;
let calls: Array<{ url: string; method: string; body?: Record<string, unknown> }>;
let answers: Record<string, unknown>;

beforeEach(() => {
  videoKeys.reset();
  servicesModel.reset();
  calls = [];
  answers = {
    'GET /api/video/enrichment/config': {
      tmdb_api_key: 'tmdb-k',
      tvdb_api_key: 'tvdb-k',
      anilist_enabled: false,
      billboard_autoplay: true,
    },
  };
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method || 'GET';
      const body =
        typeof init?.body === 'string'
          ? (JSON.parse(init.body) as Record<string, unknown>)
          : undefined;
      calls.push({ url, method, body });
      const data = answers[`${method} ${url}`] ?? { success: true };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data) } as Response);
    }),
  );
  shell.showToast = vi.fn();
  shell.landPendingSettingsSave = vi.fn();
});

afterEach(() => {
  cleanup();
  document.body.innerHTML = '';
  act(() => servicesModel.reset());
  vi.unstubAllGlobals();
  delete shell.showToast;
  delete shell.landPendingSettingsSave;
});

const el = (id: string) => document.getElementById(id) as HTMLInputElement;
const posts = () =>
  calls
    .filter((c) => c.method === 'POST' && c.url === '/api/video/enrichment/config')
    .map((c) => c.body!);
function shown() {
  act(() => {
    document.dispatchEvent(
      new CustomEvent('soulsync:video-page-shown', { detail: 'video-settings' }),
    );
  });
}

describe('the video API Configuration', () => {
  it('is the markup it replaced', () => {
    const { container } = render(<VideoApiGroup />);
    // each card sits in its wrapper (display: contents), the one thing added
    const copy = container.cloneNode(true) as HTMLElement;
    for (const wrap of copy.querySelectorAll('[data-svc-wrap]'))
      wrap.replaceWith(...wrap.childNodes);
    expect(shape(copy)).toEqual(shape(vanilla('video-api.vanilla.html')));
  });

  it('loads each time the video settings page is shown', async () => {
    render(<VideoApiGroup />);
    expect(el('video-anilist-enabled').checked).toBe(true);
    shown();
    await waitFor(() => expect(el('tmdb-api-key').value).toBe('tmdb-k'));
    expect(el('tvdb-api-key').value).toBe('tvdb-k');
    expect(el('video-anilist-enabled').checked).toBe(false);
    // a key the config does not name keeps what the field held
    expect(el('video-ryd-enabled').checked).toBe(true);
    shown();
    await waitFor(() =>
      expect(calls.filter((c) => c.url === '/api/video/enrichment/config')).toHaveLength(2),
    );
  });

  it('a key saves when left, a switch at once, every key each time', async () => {
    render(<VideoApiGroup />);
    fireEvent.input(el('omdb-api-key'), { target: { value: 'om' } });
    expect(posts()).toHaveLength(0);
    el('omdb-api-key').dispatchEvent(new Event('change', { bubbles: true }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]).toEqual({
      tmdb_api_key: '',
      tvdb_api_key: '',
      omdb_api_key: 'om',
      fanart_api_key: '',
      opensubtitles_api_key: '',
      subdl_api_key: '',
      subsource_api_key: '',
      trakt_api_key: '',
      mdblist_api_key: '',
      tvmaze_enabled: true,
      anilist_enabled: true,
      wikidata_enabled: true,
      ryd_enabled: true,
      sponsorblock_enabled: true,
      dearrow_enabled: true,
    });
    expect(shell.showToast).toHaveBeenCalledWith('API keys saved', 'success');
    fireEvent.click(el('video-dearrow-enabled'));
    await waitFor(() => expect(posts()).toHaveLength(2));
    expect(posts()[1].dearrow_enabled).toBe(false);
  });

  it('Test saves quietly, then tests; a keyless card tests only what is on', async () => {
    answers['POST /api/video/enrichment/tmdb/test'] = { success: true, message: 'TMDB ok' };
    answers['POST /api/video/enrichment/wikidata/test'] = { success: false, error: 'down' };
    render(<VideoApiGroup />);
    fireEvent.click(document.querySelector('[data-video-test-service="tmdb"]')!);
    await waitFor(() => expect(shell.showToast).toHaveBeenCalledWith('TMDB ok', 'success'));
    expect(shell.showToast).not.toHaveBeenCalledWith('API keys saved', 'success');
    fireEvent.click(el('video-anilist-enabled'));
    await act(() => testVideoServices('tvmaze,anilist,wikidata'));
    const tested = calls.filter((c) => c.url.endsWith('/test')).map((c) => c.url);
    expect(tested).toEqual([
      '/api/video/enrichment/tmdb/test',
      '/api/video/enrichment/tvmaze/test',
      '/api/video/enrichment/wikidata/test',
    ]);
    expect(shell.showToast).toHaveBeenCalledWith('Failed: Wikidata: down', 'error');
    for (const k of ['ryd', 'sponsorblock', 'dearrow']) fireEvent.click(el(`video-${k}-enabled`));
    await act(() => testVideoServices('ryd,sponsorblock,dearrow'));
    expect(shell.showToast).toHaveBeenCalledWith(
      'Nothing to test, all of these are switched off',
      'info',
    );
  });

  it('the video side save button posts the keys quietly', async () => {
    render(<VideoApiGroup />);
    const button = document.createElement('button');
    button.id = 'save-settings';
    document.body.append(button);
    document.body.setAttribute('data-side', 'video');
    fireEvent.click(button);
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(shell.showToast).not.toHaveBeenCalledWith('API keys saved', 'success');
    document.body.removeAttribute('data-side');
  });

  it('a tile opens its card in the modal, still heard there, and puts it back', async () => {
    const root = document.createElement('div');
    root.id = 'settings-page';
    root.innerHTML = `
      <div data-stg-react="connections" data-stg-part="services" style="display: contents"></div>
      <div data-stg-react="connections" data-stg-part="api-video" style="display: contents"></div>`;
    document.body.append(root);
    render(<ServicesPart />, {
      container: root.querySelector<HTMLElement>('[data-stg-part="services"]')!,
    });
    render(<VideoApiGroup />, {
      container: root.querySelector<HTMLElement>('[data-stg-part="api-video"]')!,
    });
    act(() => servicesModel.switchKind('video'));
    expect(document.querySelector('[data-service-tile="tmdb"] .src-tile-chip')!.textContent).toBe(
      'required',
    );
    fireEvent.click(document.querySelector('[data-service-tile="trakt"]')!);
    const frame = document.querySelector<HTMLElement>('[data-video-service="trakt"]')!;
    expect(frame.closest('#svc-modal-body')).not.toBeNull();
    expect(frame.querySelector<HTMLElement>('.stg-service-body')!.style.display).toBe('block');
    fireEvent.input(el('trakt-api-key'), { target: { value: 'tr' } });
    el('trakt-api-key').dispatchEvent(new Event('change', { bubbles: true }));
    await waitFor(() => expect(posts().at(-1)?.trakt_api_key).toBe('tr'));
    fireEvent.click(document.querySelector('#service-config-modal .src-modal-close')!);
    const group = root.querySelector('[data-svc-legacy]')!;
    expect(frame.closest('[data-svc-legacy]')).toBe(group);
    // back in page order
    expect(
      [...group.querySelectorAll('[data-video-service]')].map((f) =>
        f.getAttribute('data-video-service'),
      ),
    ).toEqual([
      'tmdb',
      'tvdb',
      'omdb',
      'fanart',
      'opensubtitles',
      'subdl',
      'subsource',
      'trakt',
      'mdblist',
      'nokey',
      'ytextras',
    ]);
  });
});
