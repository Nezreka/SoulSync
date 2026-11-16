/**
 * Library > Folders (SETTINGS_PORT.md, step 8a): the markup it replaced, the
 * parked vanilla panels in their old places, the tabs, the folder rules and
 * the two folder lists.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { shape, vanilla } from './__fixtures__/shape';
import { FoldersCard, isDocker, pathsSummary, podcastsPath } from './library-folders';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

let store: SettingsStore;
let posted: SettingsPayload[];

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
  shell.SoulSyncFolderCheck = { refresh: vi.fn() };
  shell.showToast = vi.fn();
});

afterEach(async () => {
  await act(() => store.flush());
  vi.unstubAllGlobals();
  delete shell.SoulSyncFolderCheck;
  delete shell.showToast;
});

const card = () => render(<FoldersCard store={store} part="folders" opened={0} active={false} />);
const input = (id: string) => document.getElementById(id) as HTMLInputElement;
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}

describe('the Folders card is the markup it replaced', () => {
  it('with the React Audiobooks and Video panels', () => {
    const { container } = card();
    const want = shape(vanilla('library-folders.vanilla.html', { afterInit: true }));
    const got = shape(container);
    expect(got.elements).toEqual(want.elements);
    expect(got.text).toBe(want.text);
  });
});

describe('the rules', () => {
  it('reads Docker and the default folders as loadSettingsData did', () => {
    expect(isDocker({ soulseek: { transfer_path: '/app/Transfer' } })).toBe(true);
    expect(isDocker({ _environment: { docker: true } })).toBe(true);
    expect(isDocker({})).toBe(false);
    expect(
      podcastsPath({ _environment: { docker: true }, podcasts: { download_path: './podcasts' } }),
    ).toBe('/app/podcasts');
    expect(podcastsPath({ library: { podcasts_path: '/pods' } })).toBe('/pods');
    expect(pathsSummary(['/data/downloads/', './Transfer'])).toBe('downloads +1 more');
    expect(pathsSummary(['', ''])).toBe('no folders set yet');
  });
});

describe('the card', () => {
  it('the tabs show one panel, the vanilla ones included, and the arrows move', () => {
    card();
    const tab = (k: string) => document.getElementById(`folders-${k}-tab`)!;
    const panel = (k: string) => document.getElementById(`folders-${k}-panel`)!;
    expect(panel('music').hidden).toBe(false);
    expect(panel('audiobooks').hidden).toBe(true);
    fireEvent.click(tab('video'));
    expect(panel('video').hidden).toBe(false);
    expect(panel('music').hidden).toBe(true);
    expect(tab('video').getAttribute('aria-selected')).toBe('true');
    fireEvent.keyDown(tab('video'), { key: 'ArrowRight' });
    expect(panel('music').hidden).toBe(false);
    fireEvent.keyDown(tab('music'), { key: 'End' });
    expect(panel('video').hidden).toBe(false);
  });

  it('the guide follows the deployment; the default library folder warns on bare metal', () => {
    act(() =>
      store.hydrate({ _environment: { docker: false }, soulseek: { transfer_path: './Transfer' } }),
    );
    card();
    const guide = (k: string) =>
      document.querySelector(`[data-paths-guide="${k}"]`)!.classList.contains('hidden');
    const warn = () =>
      document.querySelector('[data-paths-default-warning]')!.classList.contains('hidden');
    expect([guide('docker'), guide('native'), warn()]).toEqual([true, false, false]);
    act(() =>
      store.hydrate({ _environment: { docker: true }, soulseek: { transfer_path: './Transfer' } }),
    );
    expect([guide('docker'), guide('native'), warn()]).toEqual([false, true, true]);
  });

  it('the video panel guides follow the deployment too', () => {
    act(() => store.hydrate({ _environment: { docker: false } }));
    card();
    const guide = (k: string) =>
      document
        .querySelector(`#folders-video-panel [data-paths-guide="${k}"]`)!
        .classList.contains('hidden');
    expect([guide('docker'), guide('native')]).toEqual([true, false]);
    act(() => store.hydrate({ _environment: { docker: true } }));
    expect([guide('docker'), guide('native')]).toEqual([false, true]);
  });

  it('an emptied folder stays empty while typed, and a load shows the stored one', async () => {
    act(() => store.hydrate({ soulseek: { transfer_path: '/music' } }));
    card();
    const transfer = input('transfer-path');
    fireEvent.click(transfer.nextElementSibling!);
    fireEvent.change(transfer, { target: { value: '' } });
    // not the default filled straight back in
    expect(transfer.value).toBe('');
    fireEvent.change(transfer, { target: { value: '/mnt' } });
    expect(transfer.value).toBe('/mnt');
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved().soulseek).toEqual({ transfer_path: '/mnt' });
    // left empty, empty is saved (as the collector did) and the next load
    // shows the default, as loadSettingsData filled it
    fireEvent.change(transfer, { target: { value: '' } });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(posted.at(-1)).toEqual({ soulseek: { transfer_path: '' } });
    act(() => store.hydrate({ soulseek: { transfer_path: '' } }));
    expect(transfer.value).toBe('./Transfer');
  });

  it('locked fields unlock to type, and each folder saves its key', async () => {
    act(() => store.hydrate({ soulseek: { transfer_path: '/music' } }));
    card();
    const transfer = input('transfer-path');
    expect(transfer.readOnly).toBe(true);
    expect(input('download-path').readOnly).toBe(true);
    const button = transfer.nextElementSibling as HTMLButtonElement;
    fireEvent.click(button);
    expect(transfer.readOnly).toBe(false);
    expect(button.textContent).toBe('Lock');
    fireEvent.change(transfer, { target: { value: '/mnt/music' } });
    fireEvent.change(input('min-free-disk-gb'), { target: { value: '' } });
    fireEvent.click(input('m3u-relative-paths'));
    fireEvent.change(document.getElementById('playlists-materialize-mode')!, {
      target: { value: 'copy' },
    });
    fireEvent.click(document.getElementById('folders-podcasts-tab')!);
    fireEvent.click(input('podcasts-path').nextElementSibling!);
    fireEvent.change(input('podcasts-path'), { target: { value: '/pods' } });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({
      soulseek: { transfer_path: '/mnt/music', min_free_disk_gb: 0 },
      m3u_export: { relative_paths: true },
      playlists: { materialize_mode: 'copy' },
      podcasts: { download_path: '/pods' },
      library: { podcasts_path: '/pods' },
    });
    // the live folder checks run once the values are in
    expect(
      (shell.SoulSyncFolderCheck as { refresh: ReturnType<typeof vi.fn> }).refresh,
    ).toHaveBeenCalledWith('music');
  });

  it('the folder lists: add, type, remove with its status line; samples get a default row', async () => {
    act(() => store.hydrate({ library: { music_paths: ['/music2'] } }));
    card();
    await waitFor(() => expect(document.querySelectorAll('.music-path-input')).toHaveLength(1));
    // folder-check.js put a status line after the row
    const row = document.querySelector('.music-path-row')!;
    row.insertAdjacentHTML('afterend', '<div class="folder-check">Ready</div>');
    expect(document.querySelectorAll('.sample-path-input')[0]).toHaveProperty('value', './samples');
    expect(document.querySelector('#sample-paths-list')!.textContent).toContain('default');
    const add = document.querySelector('#music-paths-list + .form-actions button')!;
    fireEvent.click(add);
    const inputs = () => document.querySelectorAll<HTMLInputElement>('.music-path-input');
    expect(inputs()).toHaveLength(2);
    fireEvent.change(inputs()[1], { target: { value: ' /archive ' } });
    fireEvent.click(document.querySelector('.music-path-row button')!);
    expect(inputs()).toHaveLength(1);
    expect(document.querySelector('#music-paths-list .folder-check')).toBeNull();
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved().library).toEqual({ music_paths: ['/archive'] });
  });

  it('rebuild playlist folders reports what it did', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              success: true,
              count: 2,
              results: [{ linked: 3, copied: 1 }, { removed_stale: 2 }],
            }),
        } as Response),
      ),
    );
    card();
    fireEvent.click(document.getElementById('playlists-rebuild-btn')!);
    await waitFor(() =>
      expect(document.getElementById('playlists-rebuild-status')!.textContent).toBe(
        'Rebuilt 2 playlist folders — 3 linked, 1 copied, 2 stale removed.',
      ),
    );
    expect(shell.showToast).toHaveBeenCalledWith('Playlist folders rebuilt', 'success');
    expect(document.querySelector('#playlists-rebuild-status a')).toBeNull();
  });

  it('with nothing to rebuild, says where Organize by playlist is and links there (#2112)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ success: true, count: 0, results: [] }),
        } as Response),
      ),
    );
    const nav = vi.fn();
    shell.navigateToPage = nav;
    card();
    fireEvent.click(document.getElementById('playlists-rebuild-btn')!);
    const status = document.getElementById('playlists-rebuild-status')!;
    await waitFor(() => expect(status.textContent).toContain('Turn it on per playlist'));
    expect(status.textContent).toBe(
      "No playlists have Organize by playlist on yet. Turn it on per playlist: Playlists, open a playlist's ⋯ menu, Settings.",
    );
    const link = status.querySelector('a')!;
    expect(link.getAttribute('href')).toBe('/sync');
    fireEvent.click(link);
    expect(nav).toHaveBeenCalledWith('sync');
    delete shell.navigateToPage;
  });
});
