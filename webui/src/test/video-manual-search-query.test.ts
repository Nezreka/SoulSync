import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * #2121: manual video search opens with its query in an editable box, like
 * Prowlarr's own search. Left as prefilled, the usual search runs (ids,
 * structured and scene queries). Edited, the typed words go to every source.
 *
 * Runs the real video-download-view.js modal against a stubbed fetch.
 */

const VIEW = readFileSync(resolve(process.cwd(), 'static/video/video-download-view.js'), 'utf8');

type Posted = { url: string; body: Record<string, unknown> };
let posted: Posted[] = [];

function load() {
  // the file is an IIFE that hangs VideoDownload off window
  new Function(VIEW)();
  return (window as unknown as { VideoDownload: { manualSearch: (o: object) => void } })
    .VideoDownload;
}

const flush = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  posted = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: { body?: string }) => {
      if (init?.body) posted.push({ url, body: JSON.parse(init.body) });
      const json = String(url).includes('/downloads/config')
        ? { download_mode: 'torrent' }
        : String(url).includes('/search/start')
          ? { id: null, complete: true, live: true, results: [] }
          : null;
      return Promise.resolve({ ok: json !== null, status: 200, json: () => Promise.resolve(json) });
    }),
  );
});

afterEach(() => {
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

const starts = () => posted.filter((p) => p.url.includes('/search/start')).map((p) => p.body);

describe('manual search query box (#2121)', () => {
  it('opens prefilled with the scene query and runs the usual search', async () => {
    load().manualSearch({ title: 'Primal', scope: 'episode', season: 2, episode: 3 });
    const input = document.querySelector('[data-vms-query]') as HTMLInputElement;
    expect(input.value).toBe('Primal S02E03');
    await flush();
    await flush();
    expect(starts().length).toBe(1);
    expect(starts()[0].query).toBeUndefined();
  });

  it('searches exactly what was typed after an edit', async () => {
    load().manualSearch({ title: 'Primal', scope: 'episode', season: 2, episode: 3 });
    await flush();
    await flush();
    const input = document.querySelector('[data-vms-query]') as HTMLInputElement;
    input.value = 'Primal 2019 S02 VOSTFR';
    const form = document.querySelector('[data-vms-query-form]') as HTMLFormElement;
    form.dispatchEvent(new Event('submit', { cancelable: true }));
    await flush();
    await flush();
    const last = starts().at(-1)!;
    expect(last.query).toBe('Primal 2019 S02 VOSTFR');
    expect(last.scope).toBe('episode');
  });

  it('prefills a movie with its year, a season with its number', () => {
    const vd = load();
    vd.manualSearch({ title: 'Dune', scope: 'movie', year: 2021 });
    expect((document.querySelector('[data-vms-query]') as HTMLInputElement).value).toBe(
      'Dune 2021',
    );
    document.body.innerHTML = '';
    vd.manualSearch({ title: 'Primal', scope: 'season', season: 1 });
    expect((document.querySelector('[data-vms-query]') as HTMLInputElement).value).toBe(
      'Primal S01',
    );
  });
});
