import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { VideoServerConnection } from '@/features/settings/tabs/connections-video-server';
import { videoServer } from '@/features/settings/tabs/video-server';

/**
 * the video library picker: besides the main TV pick, every other TV library
 * gets a Scan tick (read it as well) and an Anime tick (its shows' new
 * episodes are filed in the Anime folder). both save on change. (React since
 * SETTINGS_PORT.md video pass V3b: connections-video-server.tsx)
 */

function setup(selected: Record<string, unknown>, tv = ['TV Shows', 'Anime', 'Kids']) {
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/video/libraries' && !init?.method) {
      return new Response(
        JSON.stringify({
          server: 'plex',
          movies: [{ title: 'Movies' }],
          tv: tv.map((title) => ({ title })),
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

const box = (kind: string, title: string) =>
  document.querySelector(`[data-video-lib-tv-${kind}="${title}"]`) as HTMLInputElement;

beforeEach(() => {
  videoServer.reset();
  (window as Window & { showToast?: unknown }).showToast = vi.fn();
});

describe('other TV libraries', () => {
  it('lists every TV library but the main pick, with its saved ticks', async () => {
    setup({ movies: 'Movies', tv: 'TV Shows', tv_more: ['Anime'], anime: ['Anime'] });
    await waitFor(() => expect(box('scan', 'Anime')).not.toBeNull());
    const row = document.querySelector('[data-video-lib-tv-more-row]') as HTMLElement;
    expect(row.hidden).toBe(false);
    expect(box('scan', 'TV Shows')).toBeNull();
    expect(box('scan', 'Anime').checked).toBe(true);
    expect(box('anime', 'Anime').checked).toBe(true);
    expect(box('scan', 'Kids').checked).toBe(false);
    expect(box('anime', 'Kids').checked).toBe(false);
  });

  it('ticking Anime also ticks Scan, and both lists are saved', async () => {
    const { posts } = setup({ movies: 'Movies', tv: 'TV Shows', tv_more: [], anime: [] });
    await waitFor(() => expect(box('anime', 'Anime')).not.toBeNull());
    fireEvent.click(box('anime', 'Anime'));
    expect(box('scan', 'Anime').checked).toBe(true);
    await waitFor(() =>
      expect(posts().at(-1)).toEqual({
        movies: 'Movies',
        tv: 'TV Shows',
        tv_more: ['Anime'],
        anime: ['Anime'],
      }),
    );
  });

  it('changing the main pick moves it out of the list', async () => {
    const { posts } = setup({ movies: 'Movies', tv: 'TV Shows', tv_more: [], anime: [] });
    await waitFor(() => expect(box('scan', 'Kids')).not.toBeNull());
    fireEvent.change(document.querySelector('[data-video-lib-select="tv"]')!, {
      target: { value: 'Kids' },
    });
    expect(box('scan', 'Kids')).toBeNull();
    expect(box('scan', 'TV Shows')).not.toBeNull();
    await waitFor(() =>
      expect(posts().at(-1)).toMatchObject({ tv: 'Kids', tv_more: [], anime: [] }),
    );
  });

  it('stays hidden when the server has one TV library', async () => {
    setup({ tv: 'TV Shows' }, ['TV Shows']);
    await waitFor(() =>
      expect(
        (document.querySelector('[data-video-lib-select="tv"]') as HTMLSelectElement).options
          .length,
      ).toBe(2),
    );
    expect((document.querySelector('[data-video-lib-tv-more-row]') as HTMLElement).hidden).toBe(
      true,
    );
  });
});
