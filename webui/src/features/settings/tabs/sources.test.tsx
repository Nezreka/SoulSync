/**
 * The download chain, the Sources tiles and the sources model, ported from the
 * vanilla Settings page (SETTINGS_PORT.md, step 7a): the same markup as what
 * settings.js drew (frozen from its own renderers), and the same saves, tests
 * and toasts.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { shape, vanilla } from './__fixtures__/shape';
import {
  type SourcesModel,
  chainUses,
  createSourcesModel,
  legacyPair,
  musicFromSettings,
} from './sources-model';
import { ChainWidget, SourceModal, SourceTiles } from './sources-tab';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

let store: SettingsStore;
let model: SourcesModel;
let posted: SettingsPayload[];
let calls: Array<{ url: string; method: string; body: unknown }>;
let routes: Record<string, (body: unknown) => unknown>;
let audiobook: string[];

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
  model = createSourcesModel({ store, autoTestDelay: 0 });
  calls = [];
  routes = {
    'GET /api/video/downloads/config': () => ({ download_mode: 'usenet' }),
    'POST /api/video/downloads/config': () => ({ success: true }),
    'POST /api/test-connection': () => ({ success: true }),
    'GET /api/qobuz/auth/status': () => ({ authenticated: true }),
    'GET /api/hifi/status': () => ({ available: true }),
    'POST /api/deezer-download/test': () => ({ success: true }),
    'GET /api/soundcloud/status': () => ({ available: true, reachable: true }),
    'GET /api/settings/config-status': () => ({}),
  };
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method || 'GET';
      const body = init?.body ? JSON.parse(init.body as string) : undefined;
      calls.push({ url, method, body });
      const route = routes[`${method} ${url}`];
      const data = route ? route(body) : { success: false, error: `no route ${method} ${url}` };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data) } as Response);
    }),
  );
  audiobook = ['usenet', 'torrent'];
  shell.getAudiobookChain = () => audiobook.slice();
  shell.getAudiobookActiveSources = () => ['torrent'];
  shell.setAudiobookChain = vi.fn((order: string[]) => {
    audiobook = order.slice();
  });
  shell.showToast = vi.fn();
  shell.openSourceModal = vi.fn();
});

afterEach(async () => {
  await act(() => store.flush());
  vi.unstubAllGlobals();
  for (const k of [
    'getAudiobookChain',
    'getAudiobookActiveSources',
    'setAudiobookChain',
    'openSourceModal',
  ])
    delete shell[k];
  document.body.removeAttribute('data-side');
});

const chain = (props: Partial<{ opened: number; active: boolean; loads: number }> = {}) =>
  render(<ChainWidget model={model} opened={0} active={false} loads={0} {...props} />);
const widget = () => document.getElementById('download-chain-widget')!;
const steps = () =>
  [...document.querySelectorAll('.dlchain-step')].map((n) => n.getAttribute('data-src'));
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}
function wrap(node: Element) {
  const host = document.createElement('div');
  host.append(node.cloneNode(true));
  return host;
}

/** the music chain of the frozen fixtures: soulseek, youtube, tidal */
function music() {
  model.load({
    download_source: { mode: 'hybrid', hybrid_order: ['soulseek', 'youtube', 'tidal'] },
  });
}

describe('the chain is the markup settings.js drew', () => {
  it('Source Settings, with the music chain', async () => {
    music();
    const { container } = chain();
    await act(() => model.loadChain());
    // the old block, without its hidden transport (the mode select and the
    // legacy list with its two selects: the model holds those values now),
    // and with the widget as renderDownloadChain filled it
    const want = vanilla('chain.vanilla.html', { afterInit: true });
    want.querySelector('#download-source-mode')!.closest('.form-group')!.remove();
    want.querySelector('#hybrid-settings-container')!.remove();
    want
      .querySelector('#download-chain-widget')!
      .replaceWith(vanilla('chain-music.vanilla.html').querySelector('#download-chain-widget')!);
    expect(shape(container)).toEqual(shape(want));
  });

  it.each([
    ['video', 'chain-video.vanilla.html'],
    ['audiobooks', 'chain-audiobooks.vanilla.html'],
  ])('the %s chain', async (kind, fixture) => {
    chain();
    act(() => model.switchChain(kind));
    await waitFor(() => expect(steps().length).toBeGreaterThan(0));
    expect(shape(wrap(widget()))).toEqual(shape(vanilla(fixture)));
  });

  it('an empty chain', async () => {
    model.load({ download_source: { hybrid_order: ['soulseek'] } });
    chain();
    await act(() => model.loadChain());
    act(() => model.commit([]));
    expect(shape(wrap(widget()))).toEqual(shape(vanilla('chain-empty.vanilla.html')));
  });
});

describe('the tiles are the markup settings.js drew', () => {
  it('in use (in any chain), available, indexers; dots, chips and their words', async () => {
    music();
    model.load({
      download_source: { mode: 'hybrid', hybrid_order: ['soulseek', 'youtube', 'tidal'] },
      _source_status: { tidal: false },
    });
    await act(() => model.loadVideoChain());
    act(() => {
      model.setStatus('soulseek', 'ok');
      model.setStatus('youtube', 'warn');
      model.setStatus('prowlarr', 'ok');
      model.setStatus('usenet', 'fail');
    });
    // what the server said, through a test. youtube's message is keyed by
    // its own id; usenet's test is 'usenet_client', so only a thrown error
    // puts words on its tile (as on the old page)
    routes['POST /api/test-connection'] = (body) => {
      if ((body as { service: string }).service === 'youtube')
        return { success: true, message: '⚠ cookies are stale' };
      throw new Error('no client');
    };
    await act(async () => {
      await model.testOne('youtube');
      await model.testOne('usenet');
    });
    const { container } = render(<SourceTiles model={model} opened={0} active={false} />);
    expect(shape(container)).toEqual(shape(vanilla('chain-tiles.vanilla.html')));
    // a tile opens its source's modal
    fireEvent.click(container.querySelector('[data-source-id="tidal"]')!);
    expect(model.getState().open).toBe('tidal');
  });
});

describe('the model', () => {
  it('loads the chain as loadHybridSourceOrder did', () => {
    expect(
      musicFromSettings({ download_source: { hybrid_order: ['tidal', 'amazon', 'youtube'] } }),
    ).toMatchObject({
      music: ['tidal', 'youtube'],
      cleaned: true,
    });
    // no saved order: the legacy primary + secondary
    expect(musicFromSettings({ download_source: { hybrid_primary: 'qobuz' } }).music).toEqual([
      'qobuz',
      'youtube',
    ]);
    expect(musicFromSettings({}).music).toEqual(['soulseek', 'youtube']);
    // a retired mode falls back to soulseek
    expect(
      musicFromSettings({ download_source: { mode: 'amazon', hybrid_order: ['soulseek'] } }),
    ).toMatchObject({
      mode: 'soulseek',
      cleaned: true,
    });
    // unready sources stay in the chain, marked
    expect(
      musicFromSettings({
        download_source: { hybrid_order: ['usenet'] },
        _source_status: { usenet: false, tidal: false },
      }).unready,
    ).toEqual({ usenet: true });
  });

  it('a retired source dropped on load goes out with the next save, not on its own', async () => {
    shell._suppressSettingsAutoSave = true;
    try {
      model.load({
        download_source: { mode: 'hybrid', hybrid_order: ['amazon', 'tidal', 'soulseek'] },
      });
      await new Promise((r) => setTimeout(r, 10));
      expect(posted).toEqual([]);
      expect(store.hasPending()).toBe(true);
    } finally {
      delete shell._suppressSettingsAutoSave;
    }
    await act(() => store.flush());
    expect(saved().download_source).toEqual({
      mode: 'hybrid',
      hybrid_primary: 'tidal',
      hybrid_secondary: 'soulseek',
      hybrid_order: ['tidal', 'soulseek'],
    });
  });

  it('the hidden selects only knew seven sources', () => {
    expect(legacyPair(['lidarr', 'usenet'], 'soulseek')).toEqual({ primary: '', secondary: '' });
    expect(legacyPair(['tidal'], 'qobuz')).toEqual({ primary: 'tidal', secondary: 'qobuz' });
  });

  it('finds where a source sits in every chain', () => {
    expect(
      chainUses('usenet', [
        { label: 'Music', order: ['soulseek', 'usenet'] },
        { label: 'Video', order: ['usenet'] },
      ]),
    ).toEqual([
      { label: 'Music', pos: 2 },
      { label: 'Video', pos: 1 },
    ]);
  });
});

describe('editing a chain', () => {
  it('music: add, move, remove save the four download_source keys', async () => {
    music();
    chain();
    await act(() => model.loadChain());
    fireEvent.click(screen.getByTitle('Add Qobuz to the chain'));
    expect(steps()).toEqual(['soulseek', 'youtube', 'tidal', 'qobuz']);
    fireEvent.click(screen.getByTitle('Move Qobuz up'));
    expect(steps()).toEqual(['soulseek', 'youtube', 'qobuz', 'tidal']);
    fireEvent.click(screen.getByTitle('Remove Soulseek from the chain'));
    expect(steps()).toEqual(['youtube', 'qobuz', 'tidal']);
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved().download_source).toEqual({
      mode: 'hybrid',
      hybrid_primary: 'youtube',
      hybrid_secondary: 'qobuz',
      hybrid_order: ['youtube', 'qobuz', 'tidal'],
    });
    expect(document.getElementById('dlchain-mode-hint')!.textContent).toBe('hybrid — 3 sources');
  });

  it('down to one source is single-source mode; the last one cannot go', async () => {
    model.load({ download_source: { mode: 'hybrid', hybrid_order: ['soulseek', 'tidal'] } });
    chain();
    await act(() => model.loadChain());
    fireEvent.click(screen.getByTitle('Remove Tidal from the chain'));
    fireEvent.click(screen.getByTitle('Remove Soulseek from the chain'));
    expect(shell.showToast).toHaveBeenCalledWith(
      'Keep at least one source — downloads need somewhere to go',
      'info',
    );
    expect(steps()).toEqual(['soulseek']);
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved().download_source).toMatchObject({ mode: 'soulseek', hybrid_order: ['soulseek'] });
    expect(document.getElementById('dlchain-mode-hint')!.textContent).toBe('single source');
  });

  it('drag: onto a step puts it before; onto the pool removes it; onto the slot appends', async () => {
    music();
    chain();
    await act(() => model.loadChain());
    const data = new Map<string, string>();
    const dataTransfer = {
      setData: (k: string, v: string) => data.set(k, v),
      getData: (k: string) => data.get(k) ?? '',
      effectAllowed: '',
      dropEffect: '',
    };
    const step = (id: string) => document.querySelector(`.dlchain-step[data-src="${id}"]`)!;
    fireEvent.dragStart(step('tidal'), { dataTransfer });
    fireEvent.drop(step('soulseek'), { dataTransfer });
    expect(steps()).toEqual(['tidal', 'soulseek', 'youtube']);
    fireEvent.dragStart(document.querySelector('.dlchain-tile[data-src="qobuz"]')!, {
      dataTransfer,
    });
    fireEvent.drop(document.getElementById('dlchain-slot')!, { dataTransfer });
    expect(steps()).toEqual(['tidal', 'soulseek', 'youtube', 'qobuz']);
    fireEvent.dragStart(step('youtube'), { dataTransfer });
    fireEvent.drop(document.getElementById('dlchain-pool')!, { dataTransfer });
    expect(steps()).toEqual(['tidal', 'soulseek', 'qobuz']);
  });

  it('video saves itself straight away and shows on the tiles; audiobooks go to the Library tab', async () => {
    chain();
    act(() => model.switchChain('video'));
    await waitFor(() => expect(steps()).toEqual(['usenet']));
    fireEvent.click(screen.getByTitle('Add Torrent to the chain'));
    await waitFor(() =>
      expect(
        calls.find((c) => c.method === 'POST' && c.url === '/api/video/downloads/config')?.body,
      ).toEqual({
        download_mode: 'hybrid',
        hybrid_order: ['usenet', 'torrent'],
      }),
    );
    expect(model.getState().video).toEqual(['usenet', 'torrent']);
    act(() => model.switchChain('audiobooks'));
    await waitFor(() => expect(steps()).toEqual(['usenet', 'torrent']));
    fireEvent.click(screen.getByTitle('Move Torrent up'));
    expect(shell.setAudiobookChain).toHaveBeenCalledWith(['torrent', 'usenet']);
    expect(posted).toEqual([]);
  });

  it('a failed video save says so', async () => {
    routes['POST /api/video/downloads/config'] = () => {
      throw new Error('down');
    };
    chain();
    act(() => model.switchChain('video'));
    await waitFor(() => expect(steps()).toEqual(['usenet']));
    fireEvent.click(screen.getByTitle('Add Torrent to the chain'));
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith('Could not save the download chain', 'error'),
    );
  });

  it('the video side opens on the video chain until a tab is picked', async () => {
    document.body.setAttribute('data-side', 'video');
    chain();
    await act(() => model.loadChain());
    expect(document.querySelector('.dlchain-tab.active')!.textContent).toBe('Video');
    act(() => model.switchChain('music'));
    await act(() => model.loadChain());
    expect(document.querySelector('.dlchain-tab.active')!.textContent).toBe('Music');
  });

  it('the filter hides tiles and empty groups, and says when nothing matches', async () => {
    music();
    chain();
    await act(() => model.loadChain());
    fireEvent.change(document.getElementById('dlchain-filter')!, { target: { value: 'qob' } });
    const visible = [...document.querySelectorAll<HTMLElement>('.dlchain-tile')].filter(
      (t) => !t.hidden,
    );
    expect(visible.map((t) => t.dataset.src)).toEqual(['qobuz']);
    expect(
      [...document.querySelectorAll<HTMLElement>('.dlchain-group')].filter((g) => !g.hidden),
    ).toHaveLength(1);
    fireEvent.change(document.getElementById('dlchain-filter')!, { target: { value: 'zzz' } });
    expect(document.getElementById('dlchain-no-match')!.hidden).toBe(false);
  });
});

describe('Test all sources', () => {
  it("tests every chain's sources, Prowlarr first when torrent or usenet is in one, and says what failed", async () => {
    model.load({ download_source: { mode: 'hybrid', hybrid_order: ['soulseek', 'qobuz'] } });
    routes['POST /api/test-connection'] = (body) => {
      const svc = (body as { service: string }).service;
      return svc === 'usenet_client'
        ? { success: false, message: 'SABnzbd refused' }
        : { success: true };
    };
    routes['GET /api/settings/config-status'] = () => ({ lastfm: { configured: true } });
    await act(() => model.testAll());
    const services = calls
      .filter((c) => c.url === '/api/test-connection')
      .map((c) => (c.body as { service: string }).service);
    // prowlarr (usenet is in the video chain), soulseek, torrent (audiobooks), usenet (video), then the connections
    expect(services).toEqual([
      'prowlarr',
      'soulseek',
      'torrent_client',
      'usenet_client',
      'server',
      'lastfm',
    ]);
    expect(model.getState().status).toMatchObject({
      soulseek: 'ok',
      qobuz: 'ok',
      usenet: 'fail',
      prowlarr: 'ok',
      torrent: 'ok',
    });
    expect(shell.showToast).toHaveBeenCalledWith(
      // usenet's words are under 'usenet_client', not 'usenet', so the old
      // page never named them here either (SETTINGS_PORT.md step 7a)
      'Tested sources 3✓ / 1✗, connections 2✓',
      'error',
    );
  });

  it('the quiet run when a tab opens: no toast, no connections, once', async () => {
    model.load({ download_source: { mode: 'soulseek' } });
    chain({ opened: 1, active: true });
    await waitFor(() => expect(model.getState().status.soulseek).toBe('ok'));
    expect(shell.showToast).not.toHaveBeenCalled();
    expect(calls.some((c) => c.url === '/api/settings/config-status')).toBe(false);
    const before = calls.length;
    model.autoTestOnce();
    await new Promise((r) => setTimeout(r, 20));
    expect(calls.filter((c) => c.url === '/api/test-connection').length).toBe(
      calls.slice(0, before).filter((c) => c.url === '/api/test-connection').length,
    );
  });

  it('a pass with a ⚠ message is a warning, with its words on the tile', async () => {
    model.load({ download_source: { mode: 'youtube' } });
    routes['POST /api/test-connection'] = () => ({
      success: true,
      message: '⚠ the cookie file is old',
    });
    await act(() => model.testAll({ silent: true }));
    expect(model.getState().status.youtube).toBe('warn');
    render(<SourceTiles model={model} opened={0} active={false} />);
    const tile = document.querySelector('[data-source-id="youtube"]')!;
    expect(tile.querySelector('.src-tile-dot')!.getAttribute('title')).toBe(
      '⚠ the cookie file is old',
    );
    expect(tile.querySelector('.src-tile-chip')!.textContent).toBe('needs setup');
  });
});

describe('the source modal', () => {
  const home = () => {
    document.body.insertAdjacentHTML(
      'beforeend',
      `<div id="source-config-home" hidden>
        <div id="tidal-download-settings-container" class="src-config-panel"><input id="tidal-field"></div>
        <div id="prowlarr-settings-container" class="src-config-panel"><select id="prowlarr-field"></select></div>
      </div>`,
    );
  };
  afterEach(() => {
    document.getElementById('source-config-home')?.remove();
    document.body.classList.remove('src-modal-open');
    delete shell.probeSourceOnOpen;
    delete shell.landPendingSettingsSave;
    delete shell.loadYtdlpStatus;
  });

  it('closed, it is the markup it replaced', () => {
    const { container } = render(<SourceModal model={model} />);
    expect(shape(container)).toEqual(shape(vanilla('source-modal.vanilla.html')));
    expect(document.getElementById('source-config-modal')!.hidden).toBe(true);
  });

  it('moves the panel in and back out, never a copy, and probes the source', async () => {
    home();
    shell.probeSourceOnOpen = vi.fn();
    render(<SourceModal model={model} />);
    act(() => model.openModal('tidal'));
    const body = document.getElementById('src-modal-body')!;
    expect(body.querySelector('#tidal-download-settings-container')).not.toBeNull();
    expect(document.querySelectorAll('#tidal-download-settings-container')).toHaveLength(1);
    expect(document.getElementById('source-config-modal')!.hidden).toBe(false);
    expect(document.body.classList.contains('src-modal-open')).toBe(true);
    expect(document.getElementById('src-modal-title')!.textContent).toBe('Tidal');
    expect(document.getElementById('src-modal-sub')!.textContent).toBe(
      'Account and stream quality',
    );
    expect(shell.probeSourceOnOpen).toHaveBeenCalledWith('tidal');
    await waitFor(() => expect(document.activeElement?.id).toBe('tidal-field'));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(model.getState().open).toBeNull();
    expect(
      document
        .getElementById('source-config-home')!
        .querySelector('#tidal-download-settings-container'),
    ).not.toBeNull();
    expect(document.body.classList.contains('src-modal-open')).toBe(false);
  });

  it('Prowlarr is titled by its id, as before; a click outside closes and lands a pending save', () => {
    home();
    shell.landPendingSettingsSave = vi.fn();
    render(<SourceModal model={model} />);
    act(() => model.openModal('prowlarr'));
    expect(document.getElementById('src-modal-title')!.textContent).toBe('prowlarr');
    fireEvent.click(document.getElementById('source-config-modal')!);
    expect(model.getState().open).toBeNull();
    expect(shell.landPendingSettingsSave).toHaveBeenCalledOnce();
  });

  it('Test shows what the server said and colours the dot', async () => {
    home();
    routes['POST /api/test-connection'] = () => ({ success: false, message: 'token expired' });
    render(<SourceModal model={model} />);
    act(() => model.openModal('tidal'));
    fireEvent.click(document.getElementById('src-modal-test')!);
    await waitFor(() =>
      expect(document.getElementById('src-modal-result')!.textContent).toBe('token expired'),
    );
    expect(document.getElementById('src-modal-result')!.className).toBe('src-modal-result is-fail');
    expect(document.querySelector('#src-modal-meta .src-tile-dot')!.className).toBe(
      'src-tile-dot hss-fail',
    );
    // reopened, the last result is still there
    act(() => model.closeModal());
    act(() => model.openModal('tidal'));
    expect(document.getElementById('src-modal-result')!.textContent).toBe('token expired');
  });

  it('a source without a panel on the page does not open', () => {
    render(<SourceModal model={model} />);
    act(() => model.openModal('tidal'));
    expect(model.getState().open).toBeNull();
    act(() => model.openModal('nonsense'));
    expect(model.getState().open).toBeNull();
  });
});
