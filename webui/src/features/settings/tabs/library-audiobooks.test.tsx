/**
 * Library > Audiobooks (SETTINGS_PORT.md, step 8c): both panels are the markup
 * they replaced, read the way loadSettingsData filled them, and save each key
 * the way the page save collected it.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { shape, vanilla } from './__fixtures__/shape';
import {
  AudiobookFoldersPanel,
  AudiobookOrganizationPanel,
  audiobooksPath,
  qualitySwitches,
} from './library-audiobooks';
import { OrganizationCard } from './library-organization';

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
const folders = () => render(<AudiobookFoldersPanel store={store} hidden={false} />);
const organization = () =>
  render(<OrganizationCard store={store} part="organization" opened={0} active={false} />);
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}

describe('the panels are the markup they replaced', () => {
  it('Folders > Audiobooks', () => {
    const { container } = render(<AudiobookFoldersPanel store={store} hidden />);
    const want = shape(vanilla('library-audiobook-folders.vanilla.html', { afterInit: true }));
    expect(shape(container)).toEqual(want);
  });

  it('Organization > Audiobooks, its words and tick boxes with their spaces', () => {
    const { container } = render(
      <AudiobookOrganizationPanel
        store={store}
        hidden
        template={{ value: '', onChange: () => {} }}
      />,
    );
    const old = vanilla('library-audiobook-organization.vanilla.html', { afterInit: true });
    expect(shape(container)).toEqual(shape(old));
    const words = (root: Element) =>
      [...root.querySelectorAll('.setting-help-body, .stg-help-text, .settings-hint, label')].map(
        (el) => (el.textContent || '').replace(/\s+/g, ' ').trim(),
      );
    expect(words(container)).toEqual(words(old));
    const ticks = (root: Element) =>
      [...root.querySelectorAll('label:has(> input[type="checkbox"][id])')].map((el) =>
        (el.textContent || '').replace(/\s+/g, ' ').trimEnd(),
      );
    expect(ticks(old)).toHaveLength(9);
    expect(ticks(old).every((t) => t.startsWith(' '))).toBe(true);
    expect(ticks(container)).toEqual(ticks(old));
  });
});

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
    fireEvent.click(input('audiobooks-path').nextElementSibling!);
    fireEvent.change(input('audiobooks-path'), { target: { value: '' } });
    expect(document.querySelectorAll('.audiobook-extra-input')).toHaveLength(1);
    document
      .querySelector('.audiobook-extra-row')!
      .insertAdjacentHTML('afterend', '<div class="folder-check">ok</div>');
    fireEvent.click(document.querySelector('#audiobooks-extra-paths + button')!);
    const rows = () => document.querySelectorAll<HTMLInputElement>('.audiobook-extra-input');
    expect(rows()).toHaveLength(2);
    await waitFor(() => expect(document.activeElement).toBe(rows()[1]));
    fireEvent.change(rows()[1], { target: { value: ' /mnt/more ' } });
    fireEvent.click(document.querySelector('.audiobook-extra-row button')!);
    expect(rows()).toHaveLength(1);
    expect(document.querySelector('.folder-check')).toBeNull();
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
    await waitFor(() => expect(document.querySelector('.abr-row span')!.textContent).toBe('Bluey'));
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
    await waitFor(() =>
      expect(document.querySelector('.abr-empty')!.textContent).toBe(
        'No artists routed. Use “Treat as audiobook” on an artist’s page.',
      ),
    );
    expect(
      calls.some((c) => c.method === 'DELETE' && c.url === '/api/audiobook-routes/bluey'),
    ).toBe(true);
  });
});

describe('Organization > Audiobooks', () => {
  it('loads every field with its default', () => {
    act(() => store.hydrate({ audiobooks: {} }));
    organization();
    expect(input('template-audiobook-path').value).toBe('$author/$series/$seriespos - $title');
    expect(input('audiobook-recycle-deletes').checked).toBe(true);
    expect(input('audiobook-recycle-keep-days').value).toBe('7');
    expect(input('audiobook-format-first').value).toBe('m4b');
    expect(
      [...document.querySelectorAll<HTMLInputElement>('#audiobook-allowed-formats input')].every(
        (b) => b.checked,
      ),
    ).toBe(true);
    expect(input('audiobook-min-bitrate').value).toBe('0');
    expect(input('audiobook-marketplace').value).toBe('us');
    expect(input('audiobook-download-mode').value).toBe('hybrid');
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
    fireEvent.change(input('audiobook-format-first'), { target: { value: 'mp3' } });
    const format = (v: string) =>
      document.querySelector<HTMLInputElement>(`#audiobook-allowed-formats input[value="${v}"]`)!;
    fireEvent.click(format('flac'));
    fireEvent.click(format('ogg'));
    const layout = (v: string) =>
      document.querySelector<HTMLInputElement>(`#audiobook-file-layout input[value="${v}"]`)!;
    fireEvent.click(layout('multiple'));
    fireEvent.change(input('audiobook-max-bitrate'), { target: { value: '-5' } });
    fireEvent.click(input('audiobook-allow-dramatized'));
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
        'quality.format_order': ['mp3', 'm4b', 'm4a', 'opus', 'ogg', 'flac'],
        'quality.allowed_formats': ['m4b', 'm4a', 'mp3', 'opus'],
        'quality.file_layout': 'single',
        'quality.max_bitrate_kbps': 0,
        'quality.allow_dramatized': false,
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

  it('every format switched off stays off on screen, and saves as all allowed', async () => {
    act(() => store.hydrate({ audiobooks: { quality: { allowed_formats: ['mp3'] } } }));
    organization();
    const mp3 = document.querySelector<HTMLInputElement>(
      '#audiobook-allowed-formats input[value="mp3"]',
    )!;
    fireEvent.click(mp3);
    expect(
      [...document.querySelectorAll<HTMLInputElement>('#audiobook-allowed-formats input')].some(
        (b) => b.checked,
      ),
    ).toBe(false);
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved().audiobooks).toEqual({ 'quality.allowed_formats': [] });
  });
});
