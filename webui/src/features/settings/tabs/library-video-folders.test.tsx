/**
 * The Folders card's Video panel (SETTINGS_PORT.md, video pass V2a): the
 * markup it replaced, the load (once, the first time the Library tab or the
 * video settings page is shown, with a retry when it fails), each folder
 * saved whole into the video downloads config when it is left, the extra
 * folders, the 4K switches and their locks, and the Docker lines. The cases
 * settings-video-library-load.test.ts ran against video-settings.js.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { shape, vanilla } from './__fixtures__/shape';
import {
  VideoFoldersPanel,
  composeLines,
  drivePrefix,
  videoFoldersSummary,
} from './library-video-folders';
import { videoDownloads } from './video-downloads';

const CONFIG = {
  download_path: '/downloads',
  movies_path: '/movies',
  tv_path: '/tv',
  anime_path: '/anime',
  movies_additional_paths: ['/movies2'],
  tv_additional_paths: ['/tv2'],
  youtube_path: '/youtube',
  movies_4k_enabled: true,
  movies_4k_path: '/movies-4k',
  tv_4k_enabled: false,
  tv_4k_path: '/tv-4k',
  seed_ratio_goal: 1.5,
  seed_overrides: { '4': { ratio: 2 } },
  usenet_remove_completed: 'history',
};

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;
let calls: Array<{ url: string; method: string; body?: Record<string, unknown> }>;
let answers: Record<string, unknown>;

beforeEach(() => {
  videoDownloads.reset();
  calls = [];
  answers = { 'GET /api/video/downloads/config': CONFIG };
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method || 'GET';
      const body =
        typeof init?.body === 'string'
          ? (JSON.parse(init.body) as Record<string, unknown>)
          : undefined;
      calls.push({ url, method, body });
      const key = `${method} ${url}`;
      if (key in answers && answers[key] === 503)
        return Promise.resolve({
          ok: false,
          status: 503,
          json: () => Promise.resolve(null),
        } as Response);
      const data = key in answers ? answers[key] : { success: true };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data) } as Response);
    }),
  );
  shell.showToast = vi.fn();
  shell.SoulSyncFolderCheck = {
    refresh: vi.fn(),
    environment: () => Promise.resolve({ docker: true }),
  };
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete shell.showToast;
  delete shell.SoulSyncFolderCheck;
});

const el = (id: string) => document.getElementById(id) as HTMLInputElement;
const posts = () =>
  calls
    .filter((c) => c.method === 'POST' && c.url === '/api/video/downloads/config')
    .map((c) => c.body!);
/** the browser's change event: a field left after typing in it */
function typeAndLeave(input: HTMLInputElement, value: string) {
  fireEvent.input(input, { target: { value } });
  input.dispatchEvent(new Event('change', { bubbles: true }));
}
const panel = () => render(<VideoFoldersPanel hidden={false} docker={null} settingsLoaded />);
async function shown() {
  act(() => {
    document.dispatchEvent(new CustomEvent('soulsync:library-settings-shown'));
  });
  await waitFor(() => expect(el('video-movies-path').value).toBe('/movies'));
  await waitFor(() => expect(el('video-movies-path').disabled).toBe(false));
}

describe('the panel is the markup it replaced', () => {
  it('before the video folders load', () => {
    const { container } = render(<VideoFoldersPanel hidden={false} docker={null} />);
    const old = vanilla('video-folders.vanilla.html');
    expect(shape(container)).toEqual(shape(old));
  });
});

describe('the load', () => {
  it('loads once, the first time the Library tab is shown, and keeps what was typed', async () => {
    panel();
    expect(calls).toEqual([]);
    act(() => {
      document.dispatchEvent(new CustomEvent('soulsync:library-settings-shown'));
      document.dispatchEvent(new CustomEvent('soulsync:library-settings-shown'));
    });
    expect(el('video-movies-path').disabled).toBe(true);
    expect(document.querySelector('.stg-library-load-status')!.textContent).toBe(
      'Loading saved video settings...',
    );
    await shown();
    expect(document.querySelector('.stg-library-load-status')).toBeNull();
    expect(el('video-download-path').value).toBe('/downloads');
    expect(document.querySelector('#folders-video-panel')!.getAttribute('aria-busy')).toBe('false');
    expect(calls.filter((c) => c.url === '/api/video/downloads/config')).toHaveLength(1);
    // the libraries, the quality profiles for their defaults, the drive map
    expect(calls.map((c) => c.url)).toEqual(
      expect.arrayContaining([
        '/api/video/libraries/folders',
        '/api/video/downloads/quality/profiles',
        '/api/video/libraries/drives',
      ]),
    );
    fireEvent.input(el('video-movies-path'), { target: { value: '/unsaved' } });
    act(() => {
      document.dispatchEvent(
        new CustomEvent('soulsync:video-page-shown', { detail: 'video-settings' }),
      );
    });
    expect(calls.filter((c) => c.url === '/api/video/downloads/config')).toHaveLength(1);
    expect(el('video-movies-path').value).toBe('/unsaved');
    expect(document.querySelector('[data-stg-summary]')!.textContent).toBe(
      '3 of 3 libraries set · 2 additional',
    );
  });

  it('a failed load keeps the controls off and offers a retry', async () => {
    answers['GET /api/video/downloads/config'] = 503;
    panel();
    act(() => {
      document.dispatchEvent(new CustomEvent('soulsync:library-settings-shown'));
    });
    await waitFor(() => expect(document.querySelector('.stg-library-retry')).not.toBeNull());
    expect(document.querySelector('.stg-library-load-status')!.textContent).toBe(
      'Could not load saved video settings. Retry',
    );
    expect(el('video-movies-path').disabled).toBe(true);
    answers['GET /api/video/downloads/config'] = CONFIG;
    fireEvent.click(document.querySelector('.stg-library-retry')!);
    await waitFor(() => expect(el('video-movies-path').disabled).toBe(false));
    expect(el('video-movies-path').value).toBe('/movies');
  });
});

describe('the folders', () => {
  it('a folder saves the whole config when it is left', async () => {
    panel();
    await shown();
    typeAndLeave(el('video-anime-path'), '/media/anime');
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]).toEqual({
      download_path: '/downloads',
      movies_path: '/movies',
      tv_path: '/tv',
      anime_path: '/media/anime',
      movies_additional_paths: ['/movies2'],
      tv_additional_paths: ['/tv2'],
      youtube_path: '/youtube',
      movies_4k_enabled: true,
      movies_4k_path: '/movies-4k',
      tv_4k_enabled: false,
      tv_4k_path: '/tv-4k',
      seed_ratio_goal: 1.5,
      seed_time_goal_hours: 0,
      seed_remove_data: true,
      seed_mode: 'soulsync',
      seed_overrides: { '4': { ratio: 2 } },
      usenet_remove_completed: 'history',
      redownload_failed: true,
    });
  });

  it('extra folders: edit, add and remove', async () => {
    panel();
    await shown();
    const movies = () =>
      document.querySelectorAll<HTMLInputElement>('#video-movies-additional-paths input');
    expect(movies()[0].value).toBe('/movies2');
    expect(
      document.querySelector<HTMLInputElement>('#video-tv-additional-paths input')!.value,
    ).toBe('/tv2');
    fireEvent.click(document.querySelector('[data-video-add-path="movies"]')!);
    expect(movies()).toHaveLength(2);
    typeAndLeave(movies()[1], '/movies3');
    await waitFor(() =>
      expect(posts().at(-1)?.movies_additional_paths).toEqual(['/movies2', '/movies3']),
    );
    expect(posts().at(-1)?.tv_additional_paths).toEqual(['/tv2']);
    fireEvent.click(document.querySelector('#video-movies-additional-paths button')!);
    await waitFor(() => expect(posts().at(-1)?.movies_additional_paths).toEqual(['/movies3']));
  });

  it("removing a folder takes its own row and status line, not the last one's", async () => {
    answers['GET /api/video/downloads/config'] = {
      ...CONFIG,
      movies_additional_paths: ['/a', '/b', '/c'],
    };
    panel();
    await shown();
    const list = document.getElementById('video-movies-additional-paths')!;
    // folder-check.js puts a status line after each row
    for (const row of list.querySelectorAll('[data-folder-row]')) {
      const line = document.createElement('div');
      line.className = 'folder-check';
      line.textContent = `status ${(row.querySelector('input') as HTMLInputElement).value}`;
      row.after(line);
    }
    fireEvent.click(list.querySelector('[data-folder-row] button')!);
    await waitFor(() => expect(list.querySelectorAll('[data-folder-row]')).toHaveLength(2));
    // each row is still followed by its own status line
    expect(
      [...list.querySelectorAll('[data-folder-row]')].map((row) => [
        (row.querySelector('input') as HTMLInputElement).value,
        row.nextElementSibling?.textContent,
      ]),
    ).toEqual([
      ['/b', 'status /b'],
      ['/c', 'status /c'],
    ]);
    expect([...list.querySelectorAll('input')].map((i) => i.value)).toEqual(['/b', '/c']);
  });

  it('a 4K folder shows only while its switch is on; switched off it keeps its path', async () => {
    panel();
    await shown();
    const row = (k: string) => document.querySelector<HTMLElement>(`[data-video-4k-row="${k}"]`)!;
    expect(row('movies').hidden).toBe(false);
    expect(row('tv').hidden).toBe(true);
    fireEvent.click(el('video-tv-4k-enabled'));
    expect(row('tv').hidden).toBe(false);
    await waitFor(() =>
      expect(posts().at(-1)).toMatchObject({ tv_4k_enabled: true, movies_4k_enabled: true }),
    );
    // a folder already set stays locked
    expect(el('video-tv-4k-path').readOnly).toBe(true);
    fireEvent.click(el('video-movies-4k-enabled'));
    expect(row('movies').hidden).toBe(true);
    await waitFor(() =>
      expect(posts().at(-1)).toMatchObject({
        movies_4k_enabled: false,
        movies_4k_path: '/movies-4k',
      }),
    );
  });

  it('every folder is locked; a 4K switch with no folder yet opens its lock', async () => {
    answers['GET /api/video/downloads/config'] = { ...CONFIG, tv_4k_path: '' };
    panel();
    for (const id of ['download', 'movies', 'tv', 'anime', 'youtube', 'movies-4k', 'tv-4k']) {
      expect(el(`video-${id}-path`).readOnly).toBe(true);
      expect(
        el(`video-${id}-path`).closest('.path-input-group')!.querySelector('button')!.textContent,
      ).toBe('Unlock');
    }
    await shown();
    fireEvent.click(el('video-tv-4k-enabled'));
    expect(el('video-tv-4k-path').readOnly).toBe(false);
    expect(
      el('video-tv-4k-path').closest('.path-input-group')!.querySelector('button')!.textContent,
    ).toBe('Lock');
  });

  it('lists a Docker volume line for each folder that is set', async () => {
    panel();
    await shown();
    const pre = el('video-compose-lines');
    await waitFor(() => expect(pre.textContent).toContain('/movies-4k'));
    expect(document.querySelector<HTMLElement>('[data-video-compose]')!.hidden).toBe(false);
    expect(pre.textContent).toContain('      - /path/on/your/machine/movies:/movies');
    expect(pre.textContent).toContain('/path/on/your/machine/downloads:/downloads');
    expect(pre.textContent).toContain('/path/on/your/machine/movies2:/movies2');
    // the TV 4K folder is off, so it has no line
    expect(pre.textContent).not.toContain('tv-4k');
  });
});

describe('the rules', () => {
  it("the summary, the Docker lines and a drive's neighbours", () => {
    expect(videoFoldersSummary({})).toBe('no libraries set yet');
    expect(videoFoldersSummary({ movies_path: '/m', tv_additional_paths: ['/t2', ' '] })).toBe(
      '1 of 3 libraries set · 1 additional',
    );
    expect(composeLines(['/a/', '/a', ''])).toBe(
      '    volumes:\n      - /path/on/your/machine/a:/a',
    );
    expect(composeLines([])).toBe('Set a folder above to see its line.');
    expect(
      drivePrefix('/mnt/easystore3/PLEX/TV SHOWS', '\\\\nas\\easystore3\\PLEX\\TV SHOWS'),
    ).toEqual({
      server: '/mnt/easystore3',
      local: '\\\\nas\\easystore3',
      sep: '\\',
    });
    expect(drivePrefix('/a/b', '/x/y')).toBeNull();
  });
});
