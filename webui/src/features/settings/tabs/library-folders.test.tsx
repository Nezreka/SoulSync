/**
 * Library > Folders, Music and Podcasts, and the M3U options Discovery draws
 * (settings-bic phase 4): the folder rules, the locks, the install-default
 * warning on its field, the folder check drawn by the field, the two folder
 * lists and Rebuild playlist folders.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { folderChecks } from './library-card';
import {
  M3uExportOptions,
  MusicFolders,
  MusicFoldersHero,
  PodcastFolders,
  isDocker,
  pathsSummary,
  podcastsPath,
} from './library-folders';

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

const card = () =>
  render(
    <div>
      <MusicFoldersHero store={store} />
      <MusicFolders store={store} />
      <PodcastFolders store={store} />
      <M3uExportOptions store={store} />
    </div>,
  );
const input = (id: string) => document.getElementById(id) as HTMLInputElement;
/** a path's lock: the button inside its field */
const lock = (id: string) =>
  input(id).parentElement!.querySelector<HTMLButtonElement>('[data-lock]')!;
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}

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
  it('the guide follows the deployment; the install default warns on its own field, on bare metal', () => {
    act(() =>
      store.hydrate({ _environment: { docker: false }, soulseek: { transfer_path: './Transfer' } }),
    );
    card();
    const guide = (k: string) =>
      (document.querySelector(`[data-paths-guide="${k}"]`) as HTMLElement).hidden;
    const line = () =>
      input('transfer-path').closest('[data-row]')!.querySelector('[data-folder-line]');
    expect([guide('docker'), guide('native')]).toEqual([true, false]);
    expect(line()!.getAttribute('data-tone')).toBe('warn');
    expect(line()!.textContent).toContain('Still the install folder default');
    // and the hero says it too
    expect(document.body.textContent).toContain(
      'Your music library is still the install folder default.',
    );
    act(() =>
      store.hydrate({ _environment: { docker: true }, soulseek: { transfer_path: './Transfer' } }),
    );
    expect([guide('docker'), guide('native')]).toEqual([false, true]);
    expect(line()).toBeNull();
  });

  it("the folder check's answer is drawn under its field, and the hero counts it", () => {
    act(() => store.hydrate({ soulseek: { download_path: '/dl' } }));
    card();
    const dl = input('download-path');
    // folder-check.js leaves a field that draws its own line alone and tells it
    expect(dl.hasAttribute('data-folder-own')).toBe(true);
    expect(dl.getAttribute('data-folder-check')).toBe('music');
    expect(dl.getAttribute('data-folder-role')).toBe('input');
    act(() => {
      dl.dispatchEvent(
        new CustomEvent('ss:folder-check', { detail: { tone: 'ok', text: 'Ready · 412 GB free' } }),
      );
    });
    const line = dl.closest('[data-row]')!.querySelector('[data-folder-line]')!;
    expect(line.textContent).toBe('Ready · 412 GB free');
    expect(line.getAttribute('data-tone')).toBe('ok');
    expect(document.body.textContent).toContain('412 GB');
    act(() => {
      dl.dispatchEvent(
        new CustomEvent('ss:folder-check', {
          detail: { tone: 'info', text: "Doesn't exist yet. SoulSync creates it on first use" },
        }),
      );
    });
    expect(
      dl.closest('[data-row]')!.querySelector('[data-folder-line]')!.getAttribute('data-tone'),
    ).toBe('idle');
    folderChecks.reset();
  });

  it('an emptied folder stays empty while typed, and a load shows the stored one', async () => {
    act(() => store.hydrate({ soulseek: { transfer_path: '/music' } }));
    card();
    const transfer = input('transfer-path');
    fireEvent.click(lock('transfer-path'));
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
    const button = lock('transfer-path');
    fireEvent.click(button);
    expect(transfer.readOnly).toBe(false);
    expect(button.textContent).toBe('Lock');
    fireEvent.change(transfer, { target: { value: '/mnt/music' } });
    fireEvent.change(input('min-free-disk-gb'), { target: { value: '' } });
    fireEvent.click(input('m3u-relative-paths'));
    fireEvent.click(document.querySelector('#playlists-materialize-mode [data-value="copy"]')!);
    fireEvent.click(lock('podcasts-path'));
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

  it('the folder lists: add, type, remove; samples get a default row', async () => {
    act(() => store.hydrate({ library: { music_paths: ['/music2'] } }));
    card();
    const inputs = () =>
      document.querySelectorAll<HTMLInputElement>('#music-paths-list input[type="text"]');
    await waitFor(() => expect(inputs()).toHaveLength(1));
    // each row is checked as an existing folder, and draws its own line
    expect(inputs()[0].getAttribute('data-folder-role')).toBe('existing');
    expect(inputs()[0].closest('[data-folder-row]')).not.toBeNull();
    const samples = document.querySelectorAll<HTMLInputElement>('#sample-paths-list input');
    expect(samples[0].value).toBe('./samples');
    // the Sample Studio folders are not checked
    expect(samples[0].hasAttribute('data-folder-check')).toBe(false);
    expect(document.querySelector('#sample-paths-list')!.textContent).toContain('default');
    const list = document.getElementById('music-paths-list')!.parentElement!;
    fireEvent.click(list.querySelector('button:not([aria-label])')!);
    expect(inputs()).toHaveLength(2);
    fireEvent.change(inputs()[1], { target: { value: ' /archive ' } });
    fireEvent.click(
      document.querySelector('#music-paths-list button[aria-label="Remove /music2"]')!,
    );
    expect(inputs()).toHaveLength(1);
    expect(inputs()[0].value).toBe(' /archive ');
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
        'Rebuilt 2 playlist folders: 3 linked, 1 copied, 2 stale removed.',
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
