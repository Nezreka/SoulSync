/**
 * The Audiobooks kind of the Library tab (settings-bic phase 4): Folders,
 * Naming, Import and files, Tags and artwork, and the download options drawn
 * on Downloads > Sources. Read the way loadSettingsData filled them, each key
 * saved the way the page save collected it.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import {
  AudiobookDownloadOptions,
  AudiobookFolders,
  AudiobookImport,
  AudiobookNaming,
  AudiobookTags,
  audiobooksPath,
  qualitySwitches,
  resetRoutes,
} from './library-audiobooks';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

let store: SettingsStore;
let posted: SettingsPayload[];
let routes: Array<{ label?: string; route_key: string }>;
let calls: Array<{ url: string; method: string; body?: unknown }>;

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
  routes = [];
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method || 'GET';
      const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined;
      calls.push({ url, method, body });
      let data: unknown = { success: true };
      if (url === '/api/audiobook-routes') data = { artists: routes };
      else if (url.endsWith('/move'))
        data = (body as { apply: boolean }).apply
          ? { success: true, moved: 2, failed: 0 }
          : {
              success: true,
              items: [{ action: 'move' }, { action: 'move' }, { action: 'exists' }],
            };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data) } as Response);
    }),
  );
  shell.SoulSyncFolderCheck = { refresh: vi.fn() };
  shell.showToast = vi.fn();
  shell.showConfirmDialog = vi.fn(() => Promise.resolve(true));
});

afterEach(async () => {
  await act(() => store.flush());
  vi.unstubAllGlobals();
  for (const k of ['SoulSyncFolderCheck', 'showToast', 'showConfirmDialog']) delete shell[k];
});

const input = (id: string) => document.getElementById(id) as HTMLInputElement;
const folders = () => {
  resetRoutes();
  return render(
    <div>
      <AudiobookFolders store={store} />
    </div>,
  );
};
const organization = () =>
  render(
    <div>
      <AudiobookNaming store={store} />
      <AudiobookImport store={store} />
      <AudiobookTags store={store} />
      <AudiobookDownloadOptions store={store} />
    </div>,
  );
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}

describe('the rules', () => {
  it('the folder and the switches read as loadSettingsData read them', () => {
    expect(audiobooksPath({ _environment: { docker: true } })).toBe('/app/audiobooks');
    expect(audiobooksPath({ library: { audiobooks_path: '/books' } })).toBe('/books');
    expect(
      audiobooksPath({
        soulseek: { transfer_path: '/app/x' },
        audiobooks: { download_path: './audiobooks' },
      }),
    ).toBe('/app/audiobooks');
    expect(qualitySwitches({})).toEqual({
      formats: ['m4b', 'm4a', 'mp3', 'opus', 'ogg', 'flac'],
      layouts: ['single', 'multiple'],
    });
    expect(qualitySwitches({ allowed_formats: ['mp3'], file_layout: 'single' })).toEqual({
      formats: ['mp3'],
      layouts: ['single'],
    });
  });
});

describe('Folders > Audiobooks', () => {
  it('the folder saves under both keys; more folders add, type and remove', async () => {
    act(() =>
      store.hydrate({
        audiobooks: { download_path: '/books' },
        library: { audiobooks_additional_paths: ['/old'] },
      }),
    );
    folders();
    expect(input('audiobooks-path').value).toBe('/books');
    expect(input('audiobooks-path').readOnly).toBe(true);
    fireEvent.click(input('audiobooks-path').parentElement!.querySelector('[data-lock]')!);
    fireEvent.change(input('audiobooks-path'), { target: { value: '' } });
    const rows = () =>
      document.querySelectorAll<HTMLInputElement>('#audiobooks-extra-paths input[type="text"]');
    expect(rows()).toHaveLength(1);
    expect(rows()[0].getAttribute('data-folder-check')).toBe('audiobooks');
    fireEvent.click(
      document
        .getElementById('audiobooks-extra-paths')!
        .parentElement!.querySelector('button:not([aria-label])')!,
    );
    expect(rows()).toHaveLength(2);
    await waitFor(() => expect(document.activeElement).toBe(rows()[1]));
    fireEvent.change(rows()[1], { target: { value: ' /mnt/more ' } });
    fireEvent.click(
      document.querySelector('#audiobooks-extra-paths button[aria-label="Remove /old"]')!,
    );
    expect(rows()).toHaveLength(1);
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({
      audiobooks: { download_path: './audiobooks' },
      library: { audiobooks_path: './audiobooks', audiobooks_additional_paths: ['/mnt/more'] },
    });
  });

  it('lists the spoken-word artists, removes one, and moves albums after asking', async () => {
    routes = [{ label: 'Bluey', route_key: 'bluey' }];
    folders();
    act(() => store.hydrate({}));
    await waitFor(() =>
      expect(document.querySelector('[data-route-row="bluey"]')!.textContent).toContain('Bluey'),
    );
    fireEvent.click(document.querySelector('[data-action="move"]')!);
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith('Moved 2 album(s)', 'success'),
    );
    expect(shell.showConfirmDialog).toHaveBeenCalledWith({
      title: 'Move albums',
      message:
        'Move 2 album folder(s) from the music library to the audiobook folder? 1 album(s) already exist there and stay as they are. Your media server needs a library scan afterwards.',
      confirmText: 'Move',
    });
    routes = [];
    fireEvent.click(document.querySelector('[data-action="remove"]')!);
    // taking one off asks first now (settings-bic AUDIT 33)
    await waitFor(() =>
      expect(shell.showConfirmDialog).toHaveBeenLastCalledWith(
        expect.objectContaining({ title: 'Stop filing Bluey as audiobooks?', destructive: true }),
      ),
    );
    await waitFor(() =>
      expect(document.getElementById('audiobook-routes-list')!.textContent).toBe(
        "No artists routed. Use “Treat as audiobook” on an artist's page.",
      ),
    );
    expect(
      calls.some((c) => c.method === 'DELETE' && c.url === '/api/audiobook-routes/bluey'),
    ).toBe(true);
  });
});

describe('Naming, Import and files, Tags and artwork, and the download options', () => {
  it('loads every field with its default', () => {
    act(() => store.hydrate({ audiobooks: {} }));
    organization();
    expect(input('template-audiobook-path').value).toBe('$author/$series/$seriespos - $title');
    expect(input('audiobook-recycle-deletes').checked).toBe(true);
    expect(input('audiobook-recycle-keep-days').value).toBe('7');
    // the quality fields are on Quality > Audiobooks now
    expect(input('audiobook-format-first')).toBeNull();
    expect(input('audiobook-marketplace').value).toBe('us');
    // the hidden download mode select went: the audiobook order writes that key
    expect(input('audiobook-download-mode')).toBeNull();
    expect(input('audiobook-torrent-category').value).toBe('audiobooks');
    expect(input('audiobook-prowlarr-categories').value).toBe('3030');
    expect(input('audiobook-completeness-tolerance').value).toBe('92');
    expect(input('audiobook-staging-days').value).toBe('7');
    expect(input('audiobook-merge-m4b').checked).toBe(false);
    expect(input('audiobook-remove-owned-from-wishlist').checked).toBe(false);
    expect(input('audiobook-write-nfo').checked).toBe(true);
  });

  it('each change saves its key as the page save collected it', async () => {
    act(() =>
      store.hydrate({
        audiobooks: { quality: { format_order: ['m4b', 'mp3'], min_bitrate_kbps: 32 } },
      }),
    );
    organization();
    fireEvent.change(input('template-audiobook-path'), { target: { value: '$author/$title' } });
    fireEvent.change(input('audiobook-recycle-keep-days'), { target: { value: '' } });
    fireEvent.change(input('audiobook-marketplace'), { target: { value: 'uk' } });
    fireEvent.change(input('audiobook-torrent-category'), { target: { value: '  ' } });
    expect(input('audiobook-torrent-category').value).toBe('  ');
    fireEvent.change(input('audiobook-prowlarr-categories'), {
      target: { value: '3030, x, 3040' },
    });
    fireEvent.change(input('audiobook-completeness-tolerance'), { target: { value: '5' } });
    fireEvent.change(input('audiobook-staging-days'), { target: { value: '200' } });
    fireEvent.click(input('audiobook-merge-m4b'));
    fireEvent.click(input('audiobook-embed-artwork'));
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({
      file_organization: { 'templates.audiobook_path': '$author/$title' },
      audiobooks: {
        recycle_keep_days: 0,
        marketplace: 'uk',
        torrent_category: 'audiobooks',
        usenet_category: 'audiobooks',
        prowlarr_categories: [3030, 3040],
        completeness_tolerance: 0.1,
        staging_days: 90,
        merge_m4b: true,
        embed_artwork: false,
      },
    });
    // the rest of the quality dict is the server's still
    expect(store.getState().settings.audiobooks?.quality).toMatchObject({ min_bitrate_kbps: 32 });
  });
});
