/**
 * Downloads > Sources (tabs/downloads/sources-view.tsx) and the sources model:
 * the order per kind, in words; every way of changing it saving what the old
 * chain saved; needs setup; the sheet; Test all sources. (The frozen markup
 * comparisons went with the markup; the key guard, guard/, holds the chain's
 * requests through its adapters.)
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { SourceSheet, SourcesView, useSourcesAttention } from './downloads/sources-view';
import {
  type SourcesModel,
  chainUses,
  createSourcesModel,
  legacyPair,
  musicFromSettings,
} from './sources-model';
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
  // the audiobook chain is a settings key (step 8c): a book chain of usenet
  // then torrent, set to torrent alone
  store.hydrate({
    audiobooks: { download_source: { mode: 'torrent', hybrid_order: ['usenet', 'torrent'] } },
  });
  shell.showToast = vi.fn();
  shell.openSourceModal = vi.fn();
});

afterEach(async () => {
  await act(() => store.flush());
  vi.unstubAllGlobals();
  delete shell.openSourceModal;
  document.body.removeAttribute('data-side');
  document.body.classList.remove('src-modal-open');
});

const view = (props: Partial<{ opened: number; active: boolean }> = {}) =>
  render(<SourcesView store={store} model={model} opened={0} active={false} {...props} />);
const widget = () => document.getElementById('download-chain-widget')!;
const rows = () =>
  [...widget().querySelectorAll<HTMLElement>('ul[aria-label] > li[data-id]')].map(
    (row) => row.dataset.id,
  );
const row = (id: string) =>
  widget().querySelector<HTMLElement>(`ul[aria-label] > li[data-id="${id}"]`)!;
const pool = () =>
  [...widget().querySelectorAll<HTMLElement>('[data-pool] > li[data-id]')].map((r) => r.dataset.id);
const poolRow = (id: string) =>
  widget().querySelector<HTMLElement>(`[data-pool] > li[data-id="${id}"]`)!;
const hero = () => document.querySelector('p')!.textContent;
/** pick an entry of a row's ⋯ menu */
function menu(name: string, entry: RegExp) {
  fireEvent.click(screen.getByRole('button', { name: `More for ${name}` }));
  fireEvent.click(within(screen.getByRole('menu')).getByRole('menuitem', { name: entry }));
}
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}

/** the music order of the mockup: soulseek, youtube, tidal */
function music() {
  model.load({
    download_source: { mode: 'hybrid', hybrid_order: ['soulseek', 'youtube', 'tidal'] },
  });
}

describe('the order, read back', () => {
  it('says the music order in a sentence, with its figures, and lists it', async () => {
    music();
    view();
    await act(() => model.loadChain());
    expect(hero()).toBe('Music tries Soulseek first, then YouTube, then Tidal.');
    expect(rows()).toEqual(['soulseek', 'youtube', 'tidal']);
    expect(row('soulseek').textContent).toContain('Tried first');
    expect(row('youtube').textContent).toContain('Tried when Soulseek finds nothing');
    // not used, grouped by what kind of source each is
    expect(pool()).toEqual([
      'qobuz',
      'deezer_dl',
      'soundcloud',
      'hifi',
      'torrent',
      'usenet',
      'lidarr',
    ]);
    const stats = [...document.querySelectorAll('b')].map((b) => b.textContent);
    expect(stats.slice(0, 3)).toEqual(['3', '0', '7']);
    // no on/off switch per source: a source is on by being in the order
    expect(widget().querySelector('input[type="checkbox"]')).toBeNull();
  });

  it('a source the server will skip is said so in the order, with the one button that fixes it', async () => {
    model.load({
      download_source: { mode: 'hybrid', hybrid_order: ['soulseek', 'qobuz'] },
      _source_status: { qobuz: false },
    });
    view();
    await act(() => model.loadChain());
    expect(hero()).toBe(
      "Music tries Soulseek first, then Qobuz. Qobuz isn't set up yet, so it is skipped for now.",
    );
    expect(row('qobuz').textContent).toContain('Skipped until it is set up');
    expect(row('qobuz').textContent).toContain('Needs setup');
    fireEvent.click(screen.getByRole('button', { name: 'Set up Qobuz' }));
    expect(model.getState().open).toBe('qobuz');
    // it keeps its place: the server keeps it and skips it
    expect(rows()).toEqual(['soulseek', 'qobuz']);
    // and the tab and the pill carry the count
    const Count = () => <i data-count="">{useSourcesAttention(model)}</i>;
    render(<Count />);
    expect(document.querySelector('[data-count]')!.textContent).toBe('1');
  });

  it('a row shows what its last test said, in words', async () => {
    model.load({ download_source: { mode: 'hybrid', hybrid_order: ['youtube', 'soulseek'] } });
    routes['POST /api/test-connection'] = (body) =>
      (body as { service: string }).service === 'youtube'
        ? { success: true, message: '⚠ the cookie file is old' }
        : { success: false, error: 'slskd is not answering' };
    await act(() => model.testAll({ silent: true }));
    view();
    await act(() => model.loadChain());
    expect(model.getState().status.youtube).toBe('warn');
    expect(row('youtube').textContent).toContain('the cookie file is old');
    expect(row('youtube').textContent).not.toContain('⚠');
    expect(row('soulseek').textContent).toContain('slskd is not answering');
    // a source in no order says where else it is used, or its own state
    expect(poolRow('usenet').textContent).toContain('Through your indexers · Video #1');
    expect(poolRow('tidal').textContent).toContain('Streaming service · Not tested yet');
  });

  it('video and audiobooks have their own order and their own words', async () => {
    view();
    act(() => model.switchChain('video'));
    await waitFor(() => expect(rows()).toEqual(['usenet']));
    expect(hero()).toBe('Movies and TV download from Usenet only.');
    expect(row('usenet').textContent).toContain('The only source for video');
    expect(pool()).toEqual(['soulseek', 'torrent', 'extto']);
    // External has no settings and no test here, as before
    expect(within(poolRow('extto')).queryByRole('button', { name: /settings/i })).toBeNull();
    act(() => model.switchChain('audiobooks'));
    await waitFor(() => expect(rows()).toEqual(['usenet', 'torrent']));
    expect(hero()).toBe('Books ask Usenet first, then Torrent.');
    expect(row('torrent').textContent).toContain('Asked after Usenet');
    // the kind switch is a tablist over the order
    const tabs = within(document.getElementById('dlchain-tabs')!).getAllByRole('tab');
    expect(tabs.map((tab) => tab.getAttribute('aria-selected'))).toEqual([
      'false',
      'false',
      'true',
    ]);
    expect(tabs[0].getAttribute('aria-controls')).toBe('dlchain-panel');
  });

  it('the video side opens on the video order until a kind is picked', async () => {
    document.body.setAttribute('data-side', 'video');
    view();
    await act(() => model.loadChain());
    const selected = () =>
      within(document.getElementById('dlchain-tabs')!)
        .getAllByRole('tab')
        .find((tab) => tab.getAttribute('aria-selected') === 'true')?.textContent;
    expect(selected()).toBe('Video');
    act(() => model.switchChain('music'));
    await act(() => model.loadChain());
    expect(selected()).toBe('Music');
  });
});

describe('the order, drawn', () => {
  // what the old chain markup tests were for: the place number on each row
  // (the "then try" of the old arrows), and a logo that reads the same in the
  // order and in the list under it
  it('each row carries its place, the first is marked, and a dark logo is drawn white in both lists', async () => {
    music();
    view();
    await act(() => model.loadChain());
    // the grip's icon is aria-hidden too, so read the rank span by its number
    expect(
      rows().map((id) =>
        [...row(id!).querySelectorAll('span[aria-hidden="true"]')]
          .map((el) => el.textContent)
          .find((text) => /^\d+$/.test(text ?? '')),
      ),
    ).toEqual(['1', '2', '3']);
    expect(row('soulseek').hasAttribute('data-first')).toBe(true);
    expect(row('youtube').hasAttribute('data-first')).toBe(false);
    // tidal is in the order, qobuz is not: both are dark marks
    const inverted = (el: HTMLElement) => el.querySelector('[class*="markInv"]') !== null;
    expect(inverted(row('tidal'))).toBe(true);
    expect(inverted(poolRow('qobuz'))).toBe(true);
    expect(inverted(row('soulseek'))).toBe(false);
  });

  it('a logo keeps a well of its own, never shrunk into the text line', async () => {
    const { readFileSync } = await import('node:fs');
    const css = readFileSync('src/features/settings/ui/settings.module.css', 'utf-8');
    const rules = [...css.matchAll(/(?:^|\n)\s*([^{}\n]*\.mark)\s*\{([^}]*)\}/g)];
    expect(rules.length).toBeGreaterThan(0);
    for (const [, sel, body] of rules) {
      for (const prop of ['width', 'height']) {
        const px = Number(new RegExp(`(?:^|[^-])${prop}:\\s*(\\d+)px`).exec(body)?.[1] ?? 40);
        expect(px, `${sel} ${prop}`).toBeGreaterThanOrEqual(28);
      }
    }
    const base = rules.find(([, sel]) => sel.trim() === '.mark')![2];
    expect(base).toMatch(/border-radius:/);
    expect(base).toMatch(/background:/);
    expect(base).toMatch(/flex: none/);
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

describe('changing the order', () => {
  it('music: add, move, remove save the four download_source keys, with the page', async () => {
    music();
    view();
    await act(() => model.loadChain());
    fireEvent.click(screen.getByRole('button', { name: 'Use Qobuz' }));
    expect(rows()).toEqual(['soulseek', 'youtube', 'tidal', 'qobuz']);
    menu('Qobuz', /Move up/);
    expect(rows()).toEqual(['soulseek', 'youtube', 'qobuz', 'tidal']);
    menu('Soulseek', /Stop using for music/);
    expect(rows()).toEqual(['youtube', 'qobuz', 'tidal']);
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved().download_source).toEqual({
      mode: 'hybrid',
      hybrid_primary: 'youtube',
      hybrid_secondary: 'qobuz',
      hybrid_order: ['youtube', 'qobuz', 'tidal'],
    });
  });

  it('the keyboard moves a row too, and saves once, on the drop', async () => {
    music();
    view();
    await act(() => model.loadChain());
    const grip = screen.getByRole('button', { name: 'Reorder Tidal, 3 of 3' });
    grip.focus();
    fireEvent.keyDown(grip, { key: ' ' });
    fireEvent.keyDown(grip, { key: 'ArrowUp' });
    fireEvent.keyDown(grip, { key: 'ArrowUp' });
    expect(store.hasPending()).toBe(false);
    fireEvent.keyDown(grip, { key: ' ' });
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].download_source?.hybrid_order).toEqual(['tidal', 'soulseek', 'youtube']);
    expect(document.querySelector('[data-live]')!.textContent).toBe(
      'Dropped. Music tries Tidal first, then Soulseek, then YouTube.',
    );
  });

  it('says what changed and how it saves, and Undo puts the order back through the same save', async () => {
    music();
    view();
    await act(() => model.loadChain());
    menu('YouTube', /Move up/);
    const [message, type, , opts] = (shell.showToast as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(message).toBe('YouTube is now tried first for music. It saves with your other changes.');
    expect(type).toBe('success');
    expect(opts.action.label).toBe('Undo');
    act(() => opts.action.run());
    expect(rows()).toEqual(['soulseek', 'youtube', 'tidal']);
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved().download_source?.hybrid_order).toEqual(['soulseek', 'youtube', 'tidal']);
  });

  it('down to one source is single-source mode; the last one cannot go, and says why', async () => {
    model.load({ download_source: { mode: 'hybrid', hybrid_order: ['soulseek', 'tidal'] } });
    view();
    await act(() => model.loadChain());
    menu('Tidal', /Stop using for music/);
    expect(rows()).toEqual(['soulseek']);
    expect(hero()).toBe('Music downloads from Soulseek only.');
    fireEvent.click(screen.getByRole('button', { name: 'More for Soulseek' }));
    const last = screen.getByRole('menuitem', { name: /Stop using for music/ });
    expect(last).toHaveProperty('disabled', true);
    expect(last.textContent).toContain('Keep at least one source');
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved().download_source).toMatchObject({ mode: 'soulseek', hybrid_order: ['soulseek'] });
  });

  it('a source this page does not know stays in the saved order', async () => {
    model.load({
      download_source: { mode: 'hybrid', hybrid_order: ['soulseek', 'mystery', 'youtube'] },
    });
    view();
    await act(() => model.loadChain());
    expect(rows()).toEqual(['soulseek', 'youtube']);
    menu('YouTube', /Move up/);
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved().download_source?.hybrid_order).toEqual(['youtube', 'soulseek', 'mystery']);
  });

  it('video saves itself at once and says so; audiobooks save through the store', async () => {
    view();
    act(() => model.switchChain('video'));
    await waitFor(() => expect(rows()).toEqual(['usenet']));
    fireEvent.click(screen.getByRole('button', { name: 'Use Torrent' }));
    await waitFor(() =>
      expect(
        calls.find((c) => c.method === 'POST' && c.url === '/api/video/downloads/config')?.body,
      ).toEqual({
        download_mode: 'hybrid',
        hybrid_order: ['usenet', 'torrent'],
      }),
    );
    expect(model.getState().video).toEqual(['usenet', 'torrent']);
    expect(shell.showToast).toHaveBeenCalledWith(
      'Torrent is now used for video, tried second. The video order saves at once.',
      'success',
      null,
      expect.anything(),
    );
    expect(posted).toEqual([]);
    act(() => model.switchChain('audiobooks'));
    await waitFor(() => expect(rows()).toEqual(['usenet', 'torrent']));
    menu('Torrent', /Move up/);
    // through the store, one key each of download_source
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toEqual({
      audiobooks: {
        'download_source.mode': 'hybrid',
        'download_source.hybrid_order': ['torrent', 'usenet'],
      },
    });
  });

  it('a failed video save says so', async () => {
    routes['POST /api/video/downloads/config'] = () => {
      throw new Error('down');
    };
    view();
    act(() => model.switchChain('video'));
    await waitFor(() => expect(rows()).toEqual(['usenet']));
    fireEvent.click(screen.getByRole('button', { name: 'Use Torrent' }));
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith('Could not save the download chain', 'error'),
    );
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

  it('the Indexers row redraws from the same run: Prowlarr is in no order, so nothing else would', async () => {
    model.load({ download_source: { mode: 'soulseek' } });
    view();
    await act(() => model.loadChain());
    const prowlarr = () =>
      screen.getByRole('button', { name: 'Prowlarr settings' }).parentElement!.textContent;
    expect(prowlarr()).toContain('Not tested yet');
    routes['POST /api/test-connection'] = (body) =>
      (body as { service: string }).service === 'prowlarr'
        ? { success: false, error: 'Prowlarr said 401' }
        : { success: true };
    await act(() => model.testAll({ silent: true }));
    expect(prowlarr()).toContain('Prowlarr said 401');
  });

  it('the quiet run when the pill opens: no toast, no connections, once', async () => {
    model.load({ download_source: { mode: 'soulseek' } });
    view({ opened: 1, active: true });
    await waitFor(() => expect(model.getState().status.soulseek).toBe('ok'));
    expect(shell.showToast).not.toHaveBeenCalled();
    expect(calls.some((c) => c.url === '/api/settings/config-status')).toBe(false);
    const before = calls.length;
    model.autoTestOnce();
    await new Promise((r) => setTimeout(r, 20));
    expect(calls.filter((c) => c.url === '/api/test-connection').length).toBe(
      calls.slice(0, before).filter((c) => c.url === '/api/test-connection').length,
    );
    // and the row now says so
    expect(row('soulseek').textContent).toContain('Connected');
  });
});

describe("a source's sheet", () => {
  function sheet(ids: string[] = ['soulseek-settings-container']) {
    const home = document.createElement('div');
    home.id = 'source-config-home';
    for (const id of ids) {
      const panel = document.createElement('div');
      panel.id = id;
      panel.className = 'src-config-panel';
      panel.innerHTML = '<input id="probe-field" />';
      home.append(panel);
    }
    document.body.append(home);
    const out = render(<SourceSheet store={store} model={model} />);
    return { home, ...out };
  }
  const dialog = () => screen.getByRole('dialog', { hidden: true });
  afterEach(() => document.getElementById('source-config-home')?.remove());

  it('is closed until a source is opened, then names it, where it is used and how it is doing', async () => {
    music();
    sheet();
    expect(dialog().hidden).toBe(true);
    const probe = vi.fn();
    shell.probeSourceOnOpen = probe;
    act(() => model.openModal('soulseek'));
    expect(dialog().hidden).toBe(false);
    expect(document.getElementById('src-modal-title')!.textContent).toBe('Soulseek');
    expect(document.getElementById('src-modal-sub')!.textContent).toBe('Music #1');
    expect(document.getElementById('src-modal-meta')!.textContent).toBe('Not tested yet');
    expect(probe).toHaveBeenCalledWith('soulseek');
    expect(document.body.classList.contains('src-modal-open')).toBe(true);
    delete shell.probeSourceOnOpen;
  });

  it('Prowlarr is named, not titled by its id', () => {
    sheet(['prowlarr-settings-container']);
    act(() => model.openModal('prowlarr'));
    expect(document.getElementById('src-modal-title')!.textContent).toBe('Prowlarr');
    expect(document.getElementById('src-modal-sub')!.textContent).toBe(
      'Searched by Torrent and Usenet',
    );
  });

  it('Test saves what is typed first, then shows what the server said beside it', async () => {
    music();
    sheet();
    act(() => model.openModal('soulseek'));
    store.set('soulseek', 'slskd_url', 'http://new:5030');
    routes['POST /api/test-connection'] = () => {
      // the edit is on the server before the test runs
      expect(posted).toEqual([{ soulseek: { slskd_url: 'http://new:5030' } }]);
      return { success: false, error: 'slskd said no' };
    };
    await act(async () => fireEvent.click(document.getElementById('src-modal-test')!));
    await waitFor(() =>
      expect(document.getElementById('src-modal-result')!.textContent).toBe('slskd said no'),
    );
    expect(document.getElementById('src-modal-result')!.dataset.tone).toBe('bad');
    expect(model.getState().status.soulseek).toBe('fail');
    routes['POST /api/test-connection'] = () => ({ success: true });
    await act(async () => fireEvent.click(document.getElementById('src-modal-test')!));
    await waitFor(() =>
      expect(document.getElementById('src-modal-result')!.textContent).toBe('Connected.'),
    );
  });

  it('closing lands an edit still waiting, and asks the server again what is set up', async () => {
    model.load({
      download_source: { mode: 'hybrid', hybrid_order: ['soulseek', 'qobuz'] },
      _source_status: { qobuz: false },
    });
    sheet(['qobuz-settings-container']);
    act(() => model.openModal('qobuz'));
    expect(model.getState().unready.qobuz).toBe(true);
    store.set('qobuz', 'anything', 'typed');
    // signed in while the sheet was open
    routes['GET /api/settings'] = () => ({ _source_status: { qobuz: true } });
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(model.getState().open).toBeNull();
    await waitFor(() => expect(posted).toEqual([{ qobuz: { anything: 'typed' } }]));
    // "Needs setup" goes without a reload
    await waitFor(() => expect(model.getState().unready.qobuz).toBeUndefined());
  });

  it('with autosave off, closing leaves the edit for the save button', async () => {
    shell._settingsAutosaveOff = true;
    try {
      sheet();
      act(() => model.openModal('soulseek'));
      store.set('soulseek', 'slskd_url', 'http://kept:5030');
      fireEvent.click(screen.getByRole('button', { name: 'Close' }));
      await new Promise((r) => setTimeout(r, 10));
      expect(posted).toEqual([]);
      expect(store.hasPending()).toBe(true);
    } finally {
      delete shell._settingsAutosaveOff;
    }
  });

  it('a source without a panel on the page does not open', () => {
    sheet([]);
    act(() => model.openModal('soulseek'));
    expect(model.getState().open).toBeNull();
    expect(dialog().hidden).toBe(true);
  });
});
