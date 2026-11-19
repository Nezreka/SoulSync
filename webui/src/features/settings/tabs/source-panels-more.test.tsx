/**
 * The YouTube, Prowlarr, torrent and usenet source panels on the settings
 * parts: their load and save rules, the "For music" and "For video" groups
 * (the video blocks borrowed without remounting), Prowlarr's own Test, the
 * torrent and usenet lights following the sheet's Test, and the yt-dlp
 * updater. The frozen vanilla markup comparisons went with the markup; the
 * key guard (guard/) holds every id, key and button.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { REDACTED_SECRET_SENTINEL } from './parts';
import {
  MoreSourcePanels,
  ProwlarrPanel,
  TorrentPanel,
  UsenetPanel,
  YoutubePanel,
} from './source-panels-more';
import { createSourcesModel, sourcesModel } from './sources-model';
import { SourceModal } from './sources-tab';
import { videoDownloads } from './video-downloads';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

let store: SettingsStore;
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
  shell.showToast = vi.fn();
  shell.saveSettings = vi.fn(() => Promise.resolve());
});

afterEach(async () => {
  await act(() => store.flush());
  vi.unstubAllGlobals();
  delete shell.showToast;
  delete shell.saveSettings;
  document.getElementById('video-source-blocks')?.remove();
  document.getElementById('source-config-home')?.remove();
});

const input = (id: string) => document.getElementById(id) as HTMLInputElement;
/** the sheet group a control sits under: the nearest group title above it */
function groupOf(el: Element): string {
  const panel = el.closest('[data-react-panel]')!;
  let title = '';
  for (const node of panel.querySelectorAll('h4, [data-row], [data-field], [id]')) {
    if (node.tagName === 'H4') title = node.textContent ?? '';
    if (node === el) return title;
  }
  return title;
}
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}

describe("the video side's blocks", () => {
  it('show the video downloads config, keep it through the modal, and save it', async () => {
    videoDownloads.reset();
    routes['GET /api/video/downloads/config'] = () => ({
      seed_ratio_goal: 1.5,
      seed_mode: 'client',
      usenet_remove_completed: 'files',
      redownload_failed: false,
    });
    routes['POST /api/video/downloads/config'] = () => ({ success: true });
    document.body.insertAdjacentHTML('beforeend', '<div id="source-config-home" hidden></div>');
    render(
      <>
        <SourceModal />
        <TorrentPanel store={store} />
        <UsenetPanel store={store} />
      </>,
    );
    await act(() => videoDownloads.load());
    expect(input('video-seed-ratio').value).toBe('1.5');
    expect((document.getElementById('video-seed-mode') as HTMLSelectElement).value).toBe('client');
    act(() => sourcesModel.openModal('torrent'));
    const moved = document.querySelector('#src-modal-body #video-seed-ratio') as HTMLInputElement;
    expect(moved.value).toBe('1.5');
    fireEvent.input(moved, { target: { value: '2' } });
    moved.dispatchEvent(new Event('change', { bubbles: true }));
    await waitFor(() =>
      expect(calls.filter((c) => c.method === 'POST').at(-1)?.body).toMatchObject({
        seed_ratio_goal: 2,
        seed_mode: 'client',
        usenet_remove_completed: 'files',
        redownload_failed: false,
      }),
    );
    act(() => sourcesModel.closeModal());
    expect(input('video-seed-ratio').value).toBe('2');
    // usenet's video rule is under "For video", after the music one under
    // "For music"; the next-best release rule is on Downloads > Behaviour now
    // (downloads.test.tsx)
    const usenet = document.getElementById('usenet-client-settings-container')!;
    const music = usenet.querySelector('#music-usenet-remove-completed-group')!;
    const video = usenet.querySelector('#video-usenet-remove-completed-group')!;
    expect(groupOf(music)).toBe('For music');
    expect(groupOf(video)).toBe('For video');
    expect(document.getElementById('video-redownload-failed')).toBeNull();
  });
});

describe('Prowlarr', () => {
  it('reads and saves with the old rules; Test saves first and lists the indexers', async () => {
    routes['POST /api/test-connection'] = () => ({ success: true, message: 'Prowlarr 1.2 ok' });
    routes['GET /api/prowlarr/indexers'] = () => ({
      success: true,
      indexers: [
        { id: 3, name: 'Tracker', protocol: 'torrent', privacy: 'private', enable: true },
        { id: 7, name: 'NZB', protocol: 'usenet', enable: false },
      ],
    });
    act(() => store.hydrate({ prowlarr: { search_min_gap_seconds: 0 } }));
    render(<ProwlarrPanel store={store} />);
    expect(input('prowlarr-search-min-gap').value).toBe('0');
    expect(input('prowlarr-max-searches-per-minute').value).toBe('20');
    fireEvent.change(input('prowlarr-search-min-gap'), { target: { value: '' } });
    fireEvent.change(input('prowlarr-max-searches-per-minute'), { target: { value: '5' } });
    fireEvent.click(document.getElementById('prowlarr-test-btn')!);
    const cards = () => [
      ...document.querySelectorAll<HTMLElement>('#prowlarr-indexer-list [data-indexer]'),
    ];
    await waitFor(() => expect(cards()).toHaveLength(2));
    expect(shell.saveSettings).toHaveBeenCalledOnce();
    expect(document.getElementById('prowlarr-connection-status')!.textContent).toBe(
      'Prowlarr 1.2 ok',
    );
    const dot = document.getElementById('prowlarr-status-dot')!;
    expect(dot.className).toBe('ind-status-dot ind-status-dot-connected');
    expect(dot.title).toBe('Connected');
    // a disabled indexer is dimmed; each says its id, name, privacy and kind
    expect(cards()[1].className).toMatch(/itemOff/);
    expect(cards()[0].className).not.toMatch(/itemOff/);
    expect(cards()[0].textContent).toBe('#3TrackerprivateTorrent');
    expect(screen.getByRole('button', { name: 'Test and load indexers' }).id).toBe(
      'prowlarr-test-btn',
    );
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({
      prowlarr: { search_min_gap_seconds: 2, max_searches_per_minute: 5 },
    });
  });
});

describe('the torrent client', () => {
  it('reads stored seconds as minutes and saves the old fallbacks', async () => {
    act(() =>
      store.hydrate({
        torrent_client: { password: REDACTED_SECRET_SENTINEL, seed_remove_data: false },
        download_source: { torrent_stall_timeout_seconds: 900, torrent_min_seeders: 0 },
      }),
    );
    render(<TorrentPanel store={store} />);
    expect(input('torrent-stall-timeout').value).toBe('15');
    expect(input('torrent-min-seeders').value).toBe('0');
    expect(input('torrent-client-category').value).toBe('soulsync');
    expect(input('music-seed-remove-data').checked).toBe(false);
    fireEvent.change(input('torrent-stall-timeout'), { target: { value: '' } });
    fireEvent.change(input('torrent-min-seeders'), { target: { value: '-3' } });
    fireEvent.change(input('torrent-client-category'), { target: { value: '' } });
    // emptied, it stays empty on screen (the next word is typed into it);
    // the default is what is saved
    expect(input('torrent-client-category').value).toBe('');
    fireEvent.change(input('music-seed-ratio'), { target: { value: '1.5' } });
    fireEvent.change(document.getElementById('torrent-stall-action')!, {
      target: { value: 'pause' },
    });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({
      download_source: {
        torrent_stall_timeout_seconds: 600,
        torrent_min_seeders: 1,
        torrent_stall_action: 'pause',
      },
      torrent_client: { category: 'soulsync', seed_ratio_goal: 1.5 },
    });
  });

  it("has no Test of its own: the sheet's Test is the same request, and lights the dot", async () => {
    routes['POST /api/test-connection'] = () => ({ success: false, error: 'login refused' });
    const model = createSourcesModel({ store });
    render(<TorrentPanel store={store} model={model} />);
    expect(document.getElementById('torrent-client-test-btn')).toBeNull();
    expect(document.getElementById('torrent-client-connection-status')).toBeNull();
    const dot = document.getElementById('torrent-client-status-dot')!;
    expect(dot.className).toBe('ind-status-dot ind-status-dot-unknown');
    await act(() => model.testOne('torrent'));
    expect(calls[0].body).toEqual({ service: 'torrent_client' });
    expect(dot.className).toBe('ind-status-dot ind-status-dot-error');
    expect(model.getState().said.torrent_client).toBe('login refused');
  });
});

describe('the usenet client', () => {
  it('shows the key for SABnzbd and the login for NZBGet', async () => {
    act(() => store.hydrate({ usenet_client: {} }));
    render(<UsenetPanel store={store} />);
    const shown = (id: string) => !document.getElementById(id)!.hidden;
    expect([
      shown('usenet-apikey-group'),
      shown('usenet-username-group'),
      shown('usenet-password-group'),
    ]).toEqual([true, false, false]);
    fireEvent.change(document.getElementById('usenet-client-type')!, {
      target: { value: 'nzbget' },
    });
    expect([
      shown('usenet-apikey-group'),
      shown('usenet-username-group'),
      shown('usenet-password-group'),
    ]).toEqual([false, true, true]);
    fireEvent.change(document.getElementById('music-usenet-remove-completed')!, {
      target: { value: 'files' },
    });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({ usenet_client: { type: 'nzbget', remove_completed: 'files' } });
  });
});

describe('YouTube', () => {
  it('marks the Chromium browsers on Windows and warns when one is picked', () => {
    act(() =>
      store.hydrate({ _environment: { windows: true }, youtube: { cookies_browser: 'chrome' } }),
    );
    render(<YoutubePanel store={store} opened={0} active={false} />);
    const labels = [...document.querySelectorAll('#youtube-cookies-browser option')].map(
      (o) => o.textContent,
    );
    expect(labels).toContain('Chrome (not supported on Windows)');
    expect(labels).toContain('Firefox');
    const warn = document.getElementById('youtube-cookie-abe-warning')!;
    expect(warn.hidden).toBe(false);
    expect(warn.textContent).toContain('Chrome seals its cookies with App-Bound Encryption');
    fireEvent.change(document.getElementById('youtube-cookies-browser')!, {
      target: { value: 'firefox' },
    });
    expect(warn.hidden).toBe(true);
  });

  it('the paste box shows for custom, says when a file is saved, and saves what is pasted', async () => {
    act(() =>
      store.hydrate({ youtube: { cookies_browser: 'custom', cookies_file: '/x/cookies.txt' } }),
    );
    render(<YoutubePanel store={store} opened={0} active={false} />);
    const group = document.getElementById('youtube-cookies-paste-group')!;
    expect(group.hidden).toBe(false);
    const box = document.getElementById('youtube-cookies-paste') as HTMLTextAreaElement;
    expect(box.placeholder).toBe(
      'A cookies.txt is saved. Paste again to replace it, or leave blank to keep it.',
    );
    fireEvent.change(box, { target: { value: '# Netscape HTTP Cookie File\nrow' } });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({ youtube: { cookies_paste: '# Netscape HTTP Cookie File\nrow' } });
  });

  it('transcode: options follow the switch, Opus greys out 320 and drops to 256', async () => {
    act(() => store.hydrate({ youtube: {} }));
    render(<YoutubePanel store={store} opened={0} active={false} />);
    const opts = document.getElementById('youtube-transcode-options')!;
    expect(opts.hidden).toBe(false);
    fireEvent.change(document.getElementById('youtube-transcode-codec')!, {
      target: { value: 'opus' },
    });
    expect(input('youtube-transcode-bitrate').value).toBe('256');
    fireEvent.click(input('youtube-transcode'));
    expect(opts.hidden).toBe(true);
    fireEvent.click(input('youtube-album-bundle'));
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({
      youtube: { transcode_codec: 'opus', transcode_bitrate: '256', transcode: false },
      download_source: { youtube_album_bundle: true },
    });
  });

  it('yt-dlp: read on arrival, a pending restart in amber, an update with its words and toast', async () => {
    let status: Record<string, unknown> = {
      installed: '2026.11.01',
      latest: '2026.11.10',
      behind: true,
    };
    routes['GET /api/ytdlp/status?channel=nightly'] = () => status;
    routes['GET /api/ytdlp/status?channel=stable'] = () => ({
      installed: '2026.10.01',
      latest: '2026.10.01',
    });
    routes['POST /api/ytdlp/update'] = () => ({
      success: true,
      restart_required: true,
      message: 'Installed 2026.11.10',
      detail: 'pip said ok',
    });
    render(<YoutubePanel store={store} opened={1} active />);
    await waitFor(() =>
      expect(document.getElementById('ytdlp-latest')!.textContent).toBe('2026.11.10'),
    );
    expect(document.getElementById('ytdlp-latest')!.dataset.tone).toBe('warn');
    const badge = document.getElementById('ytdlp-hint-badge')!;
    expect(badge.hidden).toBe(false);
    expect(badge.title).toBe('A newer yt-dlp is available');
    status = {
      installed: '2026.11.01',
      on_disk: '2026.11.10',
      restart_pending: true,
      latest: '2026.11.10',
    };
    fireEvent.click(document.getElementById('ytdlp-update-btn')!);
    await waitFor(() =>
      expect(document.getElementById('ytdlp-status')!.textContent).toBe('Installed 2026.11.10'),
    );
    expect(document.getElementById('ytdlp-detail')!.textContent).toBe('pip said ok');
    expect(shell.showToast).toHaveBeenCalledWith(
      'yt-dlp updated. Restart SoulSync to use it',
      'info',
    );
    await waitFor(() =>
      expect(document.getElementById('ytdlp-installed')!.textContent).toBe(
        '2026.11.01 running, 2026.11.10 installed. Restart to finish.',
      ),
    );
    fireEvent.change(document.getElementById('ytdlp-channel')!, { target: { value: 'stable' } });
    await waitFor(() =>
      expect(document.getElementById('ytdlp-installed')!.textContent).toBe('2026.10.01'),
    );
  });
});
