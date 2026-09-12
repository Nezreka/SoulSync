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
                movies_additional_paths: ['/movies2'],
                tv_additional_paths: ['/tv2'],
                youtube_path: '/youtube',
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
    expect(fetcher).toHaveBeenCalledTimes(2);
    (doc.getElementById('video-movies-path') as HTMLInputElement).value = '/unsaved';
    show();
    expect(fetcher).toHaveBeenCalledTimes(2);
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
