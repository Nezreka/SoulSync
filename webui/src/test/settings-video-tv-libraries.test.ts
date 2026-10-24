import { waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

/**
 * the video library picker: besides the main TV pick, every other TV library
 * gets a Scan tick (read it as well) and an Anime tick (its shows' new
 * episodes are filed in the Anime folder). both save on change.
 */

const source = readFileSync(resolve(process.cwd(), 'static/video/video-settings.js'), 'utf8');
const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');
const PAGE_ID = /PAGE_ID = '([^']+)'/.exec(source)![1];

function setup(selected: Record<string, unknown>) {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/video/libraries' && !init?.method) {
      return new Response(
        JSON.stringify({
          server: 'plex',
          movies: [{ title: 'Movies' }],
          tv: [{ title: 'TV Shows' }, { title: 'Anime' }, { title: 'Kids' }],
          selected,
        }),
      );
    }
    return new Response(JSON.stringify({ status: 'saved' }));
  });
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  new Function('document', 'window', 'fetch', source)(doc, {}, fetcher);
  doc.dispatchEvent(new Event('DOMContentLoaded'));
  doc.dispatchEvent(new CustomEvent('soulsync:video-page-shown', { detail: PAGE_ID }));
  const posts = () =>
    (fetcher.mock.calls as unknown as [string, RequestInit][])
      .filter(([u, i]) => u === '/api/video/libraries' && i?.method === 'POST')
      .map(([, i]) => JSON.parse(i.body as string));
  return { doc, posts };
}

const box = (doc: Document, kind: string, title: string) =>
  doc.querySelector(`[data-video-lib-tv-${kind}="${title}"]`) as HTMLInputElement;

describe('other TV libraries', () => {
  it('lists every TV library but the main pick, with its saved ticks', async () => {
    const { doc } = setup({
      movies: 'Movies',
      tv: 'TV Shows',
      tv_more: ['Anime'],
      anime: ['Anime'],
    });
    await waitFor(() => expect(box(doc, 'scan', 'Anime')).not.toBeNull());
    const row = doc.querySelector('[data-video-lib-tv-more-row]') as HTMLElement;
    expect(row.hidden).toBe(false);
    expect(box(doc, 'scan', 'TV Shows')).toBeNull();
    expect(box(doc, 'scan', 'Anime').checked).toBe(true);
    expect(box(doc, 'anime', 'Anime').checked).toBe(true);
    expect(box(doc, 'scan', 'Kids').checked).toBe(false);
    expect(box(doc, 'anime', 'Kids').checked).toBe(false);
  });

  it('ticking Anime also ticks Scan, and both lists are saved', async () => {
    const { doc, posts } = setup({ movies: 'Movies', tv: 'TV Shows', tv_more: [], anime: [] });
    await waitFor(() => expect(box(doc, 'anime', 'Anime')).not.toBeNull());
    const anime = box(doc, 'anime', 'Anime');
    anime.checked = true;
    anime.dispatchEvent(new Event('change'));
    expect(box(doc, 'scan', 'Anime').checked).toBe(true);
    expect(posts().at(-1)).toEqual({
      movies: 'Movies',
      tv: 'TV Shows',
      tv_more: ['Anime'],
      anime: ['Anime'],
    });
  });

  it('changing the main pick moves it out of the list', async () => {
    const { doc, posts } = setup({ movies: 'Movies', tv: 'TV Shows', tv_more: [], anime: [] });
    await waitFor(() => expect(box(doc, 'scan', 'Kids')).not.toBeNull());
    const tv = doc.querySelector('[data-video-lib-select="tv"]') as HTMLSelectElement;
    tv.value = 'Kids';
    tv.dispatchEvent(new Event('change'));
    expect(box(doc, 'scan', 'Kids')).toBeNull();
    expect(box(doc, 'scan', 'TV Shows')).not.toBeNull();
    expect(posts().at(-1)).toMatchObject({ tv: 'Kids', tv_more: [], anime: [] });
  });

  it('stays hidden when the server has one TV library', async () => {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const fetcher = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            movies: [],
            tv: [{ title: 'TV Shows' }],
            selected: { tv: 'TV Shows' },
          }),
        ),
    );
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    new Function('document', 'window', 'fetch', source)(doc, {}, fetcher);
    doc.dispatchEvent(new Event('DOMContentLoaded'));
    doc.dispatchEvent(new CustomEvent('soulsync:video-page-shown', { detail: PAGE_ID }));
    await waitFor(() =>
      expect(
        (doc.querySelector('[data-video-lib-select="tv"]') as HTMLSelectElement).options.length,
      ).toBe(2),
    );
    expect((doc.querySelector('[data-video-lib-tv-more-row]') as HTMLElement).hidden).toBe(true);
  });
});
