import { waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
const source = readFileSync(resolve(process.cwd(), 'static/video/video-settings.js'), 'utf8');
const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');
function setup() {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const fetcher = vi.fn(
    async (url: string) =>
      new Response(
        JSON.stringify(
          url.includes('/organization')
            ? { movie_template: '$title ($year)/$title', episode_template: '$series/$episodetitle' }
            : {
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
              },
        ),
      ),
  );
  new Function('document', 'window', 'fetch', source)(doc, {}, fetcher);
  doc.dispatchEvent(new Event('DOMContentLoaded'));
  return {
    doc,
    fetcher,
    show: () => doc.dispatchEvent(new CustomEvent('soulsync:library-settings-shown')),
  };
}
describe('shared video Library initialization', () => {
  it('loads folders and organization on a fresh music-side Library visit', async () => {
    const { doc, fetcher, show } = setup();
    expect(fetcher).not.toHaveBeenCalled();
    show();
    show();
    expect((doc.getElementById('video-movies-path') as HTMLInputElement).disabled).toBe(true);
    await waitFor(() =>
      expect((doc.getElementById('video-movies-path') as HTMLInputElement).disabled).toBe(false),
    );
    expect((doc.getElementById('video-movies-path') as HTMLInputElement).value).toBe('/movies');
    expect((doc.getElementById('vo-movie-template') as HTMLInputElement).value).toBe(
      '$title ($year)/$title',
    );
    expect(fetcher).toHaveBeenCalledTimes(3);
    (doc.getElementById('video-movies-path') as HTMLInputElement).value = '/unsaved';
    show();
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect((doc.getElementById('video-movies-path') as HTMLInputElement).value).toBe('/unsaved');
  });
  it('keeps blank controls disabled after a failed load and allows retry', async () => {
    const { doc, fetcher, show } = setup();
    fetcher.mockResolvedValueOnce(new Response('', { status: 503 }));
    show();
    await waitFor(() => expect(doc.querySelector('.stg-library-retry')).not.toBeNull());
    expect((doc.getElementById('video-movies-path') as HTMLInputElement).disabled).toBe(true);
    (doc.querySelector('.stg-library-retry') as HTMLButtonElement).click();
    await waitFor(() =>
      expect((doc.getElementById('video-movies-path') as HTMLInputElement).disabled).toBe(false),
    );
    expect((doc.getElementById('video-movies-path') as HTMLInputElement).value).toBe('/movies');
  });
});

it('loads, edits, adds and removes additional video library paths', async () => {
  const { doc, fetcher, show } = setup();
  show();
  await waitFor(() =>
    expect(doc.querySelector('#video-movies-additional-paths input')).not.toBeNull(),
  );
  const movie = doc.querySelector('#video-movies-additional-paths input') as HTMLInputElement;
  await waitFor(() => expect(movie.disabled).toBe(false));
  expect(movie.value).toBe('/movies2');
  const tv = doc.querySelector('#video-tv-additional-paths input') as HTMLInputElement;
  expect(tv.value).toBe('/tv2');
  (doc.querySelector('[data-video-add-path="movies"]') as HTMLButtonElement).click();
  const inputs = doc.querySelectorAll('#video-movies-additional-paths input');
  expect(inputs.length).toBe(2);
  (inputs[1] as HTMLInputElement).value = '/movies3';
  inputs[1].dispatchEvent(new Event('change'));
  const calls = fetcher.mock.calls as unknown as [string, RequestInit][];
  let body = JSON.parse(calls.at(-1)![1].body as string);
  expect(body.movies_additional_paths).toEqual(['/movies2', '/movies3']);
  expect(body.tv_additional_paths).toEqual(['/tv2']);
  (doc.querySelector('#video-movies-additional-paths button') as HTMLButtonElement).click();
  body = JSON.parse(calls.at(-1)![1].body as string);
  expect(body.movies_additional_paths).toEqual(['/movies3']);
});

it('loads the optional anime folder and saves it on change', async () => {
  const { doc, fetcher, show } = setup();
  show();
  const anime = doc.getElementById('video-anime-path') as HTMLInputElement;
  await waitFor(() => expect(anime.disabled).toBe(false));
  expect(anime.value).toBe('/anime');
  anime.value = '/media/anime';
  anime.dispatchEvent(new Event('change'));
  const calls = fetcher.mock.calls as unknown as [string, RequestInit][];
  const body = JSON.parse(calls.at(-1)![1].body as string);
  expect(body.anime_path).toBe('/media/anime');
  expect(body.tv_path).toBe('/tv');
});

it('shows a 4K folder only while its switch is on and saves both', async () => {
  const { doc, fetcher, show } = setup();
  show();
  const movies4k = doc.getElementById('video-movies-4k-path') as HTMLInputElement;
  await waitFor(() => expect(movies4k.value).toBe('/movies-4k'));
  const moviesRow = doc.querySelector('[data-video-4k-row="movies"]') as HTMLElement;
  const tvRow = doc.querySelector('[data-video-4k-row="tv"]') as HTMLElement;
  expect(moviesRow.hidden).toBe(false);
  expect(tvRow.hidden).toBe(true);
  const tvBox = doc.getElementById('video-tv-4k-enabled') as HTMLInputElement;
  await waitFor(() => expect(tvBox.disabled).toBe(false));
  tvBox.checked = true;
  tvBox.dispatchEvent(new Event('change'));
  expect(tvRow.hidden).toBe(false);
  const calls = fetcher.mock.calls as unknown as [string, RequestInit][];
  let body = JSON.parse(calls.at(-1)![1].body as string);
  expect(body.tv_4k_enabled).toBe(true);
  expect(body.movies_4k_enabled).toBe(true);
  expect(body.movies_4k_path).toBe('/movies-4k');
  // switching off hides the folder but keeps its path for next time
  const moviesBox = doc.getElementById('video-movies-4k-enabled') as HTMLInputElement;
  moviesBox.checked = false;
  moviesBox.dispatchEvent(new Event('change'));
  expect(moviesRow.hidden).toBe(true);
  body = JSON.parse(calls.at(-1)![1].body as string);
  expect(body.movies_4k_enabled).toBe(false);
  expect(body.movies_4k_path).toBe('/movies-4k');
});

it('lists a compose volume line for each folder that is set', async () => {
  const { doc, show } = setup();
  show();
  const pre = doc.getElementById('video-compose-lines') as HTMLElement;
  await waitFor(() => expect(pre.textContent).toContain('/movies-4k'));
  const text = pre.textContent ?? '';
  expect(text).toContain('      - /path/on/your/machine/movies:/movies');
  expect(text).toContain('/path/on/your/machine/downloads:/downloads');
  expect(text).toContain('/path/on/your/machine/movies2:/movies2');
  // the TV 4K folder is off, so it has no line
  expect(text).not.toContain('tv-4k');
});

it('locks the shared download folder on the video tab like the music one', () => {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const input = doc.getElementById('video-download-path') as HTMLInputElement;
  expect(input.readOnly).toBe(true);
  const lock = input.closest('.path-input-group')?.querySelector('button');
  expect(lock?.getAttribute('onclick')).toBe("togglePathLock('video-download', this)");
  expect(doc.getElementById('download-path')?.hasAttribute('readonly')).toBe(true);
});

it('locks every video folder, and a 4K switch with no folder yet opens its lock', async () => {
  const { doc, show } = setup();
  for (const id of ['movies', 'tv', 'anime', 'youtube', 'movies-4k', 'tv-4k']) {
    const input = doc.getElementById(`video-${id}-path`) as HTMLInputElement;
    expect(input.readOnly).toBe(true);
    expect(
      input.closest('.path-input-group')?.querySelector('button')?.getAttribute('onclick'),
    ).toBe(`togglePathLock('video-${id}', this)`);
  }
  show();
  const tvBox = doc.getElementById('video-tv-4k-enabled') as HTMLInputElement;
  const tvPath = doc.getElementById('video-tv-4k-path') as HTMLInputElement;
  await waitFor(() => expect(tvPath.value).toBe('/tv-4k'));
  await waitFor(() => expect(tvBox.disabled).toBe(false));
  // a folder already set stays locked
  tvBox.checked = true;
  tvBox.dispatchEvent(new Event('change'));
  expect(tvPath.readOnly).toBe(true);
  tvBox.checked = false;
  tvBox.dispatchEvent(new Event('change'));
  tvPath.value = '';
  tvBox.checked = true;
  tvBox.dispatchEvent(new Event('change'));
  expect(tvPath.readOnly).toBe(false);
  expect(tvPath.parentElement?.querySelector('button')?.textContent).toBe('Lock');
});
