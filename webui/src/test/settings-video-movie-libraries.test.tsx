import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { VideoServerConnection } from '@/features/settings/tabs/connections-video-server';
import { VideoFoldersPanel } from '@/features/settings/tabs/library-video-folders';
import { videoDownloads } from '@/features/settings/tabs/video-downloads';
import { videoServer } from '@/features/settings/tabs/video-server';

/**
 * more than one movie library: besides the main movies pick, every other
 * movie library on the server gets a Scan switch (no Anime switch), saved as
 * movies_more. and the folders tab's extra folders section, named for both
 * setups, with a hint that follows the server or Standalone.
 */

function setup(selected: Record<string, unknown>, movies = ['Movies', '4K Movies', 'Kids Movies']) {
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/video/libraries' && !init?.method) {
      return new Response(
        JSON.stringify({
          server: 'plex',
          movies: movies.map((title) => ({ title })),
          tv: [{ title: 'TV Shows' }],
          selected,
        }),
      );
    }
    return new Response(JSON.stringify({ status: 'saved' }));
  });
  vi.stubGlobal('fetch', fetcher);
  render(<VideoServerConnection />);
  act(() => {
    document.dispatchEvent(
      new CustomEvent('soulsync:video-page-shown', { detail: 'video-settings' }),
    );
  });
  const posts = () =>
    (fetcher.mock.calls as unknown as [string, RequestInit][])
      .filter(([u, i]) => u === '/api/video/libraries' && i?.method === 'POST')
      .map(([, i]) => JSON.parse(i.body as string));
  return { posts };
}

const scan = (title: string) =>
  document.querySelector(`[data-video-lib-movies-scan="${title}"]`) as HTMLInputElement;
const row = () => document.querySelector('[data-video-lib-movies-more-row]') as HTMLElement;

beforeEach(() => {
  videoServer.reset();
  (window as Window & { showToast?: unknown }).showToast = vi.fn();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe('other movie libraries', () => {
  it('lists every movie library but the main pick, with its saved Scan and no Anime', async () => {
    setup({ movies: 'Movies', movies_more: ['4K Movies'], tv: 'TV Shows' });
    await waitFor(() => expect(scan('4K Movies')).not.toBeNull());
    expect(row().hidden).toBe(false);
    expect(scan('Movies')).toBeNull();
    expect(scan('4K Movies').checked).toBe(true);
    expect(scan('Kids Movies').checked).toBe(false);
    expect(row().querySelector('[data-video-lib-tv-anime]')).toBeNull();
    expect(row().textContent).toContain('Another movie library on this server.');
  });

  it('a Scan switch saves the list at once, with the TV picks as they were', async () => {
    const { posts } = setup({
      movies: 'Movies',
      movies_more: [],
      tv: 'TV Shows',
      tv_more: [],
      anime: [],
    });
    await waitFor(() => expect(scan('4K Movies')).not.toBeNull());
    fireEvent.click(scan('4K Movies'));
    await waitFor(() =>
      expect(posts().at(-1)).toEqual({
        movies: 'Movies',
        movies_more: ['4K Movies'],
        tv: 'TV Shows',
        tv_more: [],
        anime: [],
      }),
    );
    expect(scan('4K Movies').checked).toBe(true);
    fireEvent.click(scan('4K Movies'));
    await waitFor(() => expect(posts().at(-1)).toMatchObject({ movies_more: [] }));
  });

  it('a saved list from an older server reads as none', async () => {
    setup({ movies: 'Movies', tv: 'TV Shows' });
    await waitFor(() => expect(scan('4K Movies')).not.toBeNull());
    expect(scan('4K Movies').checked).toBe(false);
  });

  it('changing the main pick moves it out of the list and drops its own tick', async () => {
    const { posts } = setup({ movies: 'Movies', movies_more: ['4K Movies'], tv: 'TV Shows' });
    await waitFor(() => expect(scan('Kids Movies')).not.toBeNull());
    fireEvent.change(document.querySelector('[data-video-lib-select="movies"]')!, {
      target: { value: '4K Movies' },
    });
    expect(scan('4K Movies')).toBeNull();
    expect(scan('Movies')).not.toBeNull();
    await waitFor(() =>
      expect(posts().at(-1)).toMatchObject({ movies: '4K Movies', movies_more: [] }),
    );
  });

  it('stays hidden when the server has one movie library', async () => {
    setup({ movies: 'Movies' }, ['Movies']);
    await waitFor(() =>
      expect(
        (document.querySelector('[data-video-lib-select="movies"]') as HTMLSelectElement).options
          .length,
      ).toBe(2),
    );
    expect(row().hidden).toBe(true);
  });
});

describe('extra folders', () => {
  function folders(server: string | null) {
    videoDownloads.reset();
    const fetcher = vi.fn(async (url: string) => {
      if (url === '/api/video/server') return new Response(JSON.stringify({ server }));
      if (url === '/api/video/downloads/config')
        return new Response(JSON.stringify({ movies_path: '/movies', tv_path: '/tv' }));
      return new Response(JSON.stringify({ success: true }));
    });
    vi.stubGlobal('fetch', fetcher);
    (window as unknown as Record<string, unknown>).SoulSyncFolderCheck = {
      refresh: vi.fn(),
      environment: () => Promise.resolve({ docker: false }),
    };
    render(<VideoFoldersPanel hidden={false} docker={null} />);
    act(() => {
      document.dispatchEvent(new CustomEvent('soulsync:library-settings-shown'));
    });
    return fetcher;
  }
  const card = () =>
    [...document.querySelectorAll('h3, h4, [class*="sectionTitle"]')].find(
      (h) => h.textContent === 'Extra folders',
    );

  it('is named for both setups and, with a server, speaks of its libraries', async () => {
    folders('plex');
    await waitFor(() => expect(videoServer.get().active).toBe('plex'));
    expect(document.body.textContent).not.toContain("Folders your server doesn't list");
    expect(card()).toBeTruthy();
    expect(document.body.textContent).toContain(
      "Folders SoulSync should also manage that the libraries it scans from your server don't include. New downloads never go here.",
    );
    expect(document.querySelector('[data-video-extra-kind="movies"]')).not.toBeNull();
  });

  it('on Standalone, speaks of more folders to scan', async () => {
    folders('soulsync');
    await waitFor(() =>
      expect(document.body.textContent).toContain(
        'More movie and TV folders for SoulSync to scan, like a second drive. New downloads never go here.',
      ),
    );
    expect(card()).toBeTruthy();
    expect(document.body.textContent).not.toContain('the libraries it scans from your server');
  });

  it('asks for the server once, and not when the page already knows it', async () => {
    const fetcher = folders('jellyfin');
    await waitFor(() => expect(videoServer.get().active).toBe('jellyfin'));
    const asked = () =>
      (fetcher.mock.calls as unknown as [string][]).filter(([u]) => u === '/api/video/server')
        .length;
    expect(asked()).toBe(1);
    videoServer.ensureServer();
    expect(asked()).toBe(1);
  });
});
