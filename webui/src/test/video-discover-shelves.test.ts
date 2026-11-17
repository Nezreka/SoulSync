import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * #2158: video Discover rows sat on their skeleton forever.
 *
 * In Theaters Now and Coming Soon showed one grey poster box and a big blank
 * area because the list call behind them never came back, and nothing on the
 * page had a deadline or a retry. These run the REAL fillShelf / cachedFetch out
 * of video-discover.js: every row ends as cards, gone (an honest empty row), or
 * a short "couldn't load" line with Try again. Never a skeleton for good.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/video/video-discover.js'), 'utf8');

afterEach(() => {
  document.body.innerHTML = '';
  vi.useRealTimers();
});

type Answer = (value: unknown) => void;

function shelfHarness() {
  document.body.innerHTML = `
    <section class="vdsc-group" data-group="new">
      <div data-group-body="new">
        <section class="vdsc-shelf" data-vdsc-q="key=now_playing" data-vdsc-title="In Theaters Now">
          <div class="vdsc-rail" data-vdsc-rail aria-busy="true"><div class="vdsc-skel"></div></div>
        </section>
      </div>
    </section>`;
  const calls: { url: string; ok: Answer; fail: Answer }[] = [];
  const cachedFetch = (url: string) =>
    new Promise((ok, fail) => {
      calls.push({ url, ok, fail });
    });
  const $ = (s: string, r?: ParentNode) => (r || document).querySelector(s);
  const card = (it: { title: string }) => `<a class="vsr-card">${it.title}</a>`;
  const pruned: Element[] = [];
  const fns = ['skeletonHtml', 'fillShelf', 'shelfFailed', 'retryShelf']
    .map((n) => extractFunction(n, JS))
    .join('\n');
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const api = new Function(
    '$',
    'cachedFetch',
    'LIST_URL',
    'card',
    'rankedCard',
    'stagger',
    'hydrateGet',
    'isHideOwned',
    'pruneGroup',
    'SHELF_RETRY_MS',
    `${fns}; return { fillShelf, retryShelf };`,
  )(
    $,
    cachedFetch,
    '/list',
    card,
    card,
    () => {},
    () => {},
    () => false,
    (g: Element) => pruned.push(g),
    1500,
  ) as { fillShelf: (s: Element) => void; retryShelf: (s: Element) => void };
  const shelf = () => document.querySelector('.vdsc-shelf') as HTMLElement | null;
  return { api, calls, shelf, pruned };
}

const flush = async () => {
  for (let i = 0; i < 4; i++) await Promise.resolve();
};

describe('a discover row always settles (#2158)', () => {
  it('fills with cards and drops the busy state', async () => {
    const h = shelfHarness();
    h.api.fillShelf(h.shelf()!);
    h.calls[0].ok({ items: [{ title: 'Hope' }] });
    await flush();
    expect(h.shelf()!.innerHTML).toContain('Hope');
    expect(h.shelf()!.querySelector('.vdsc-skel')).toBeNull();
    expect(h.shelf()!.querySelector('[aria-busy]')).toBeNull();
  });

  it('an honestly empty row goes away, and its group is checked', async () => {
    const h = shelfHarness();
    h.api.fillShelf(h.shelf()!);
    h.calls[0].ok({ items: [] });
    await flush();
    expect(h.shelf()).toBeNull();
    expect(h.pruned).toHaveLength(1);
  });

  it('a failure retries once quietly, then shows Try again instead of the skeleton', async () => {
    vi.useFakeTimers();
    const h = shelfHarness();
    h.api.fillShelf(h.shelf()!);
    h.calls[0].fail(new Error('timeout'));
    await flush();
    // the quiet retry: still loading, no error yet
    expect(h.shelf()!.querySelector('.vdsc-skel')).not.toBeNull();
    vi.advanceTimersByTime(1500);
    expect(h.calls).toHaveLength(2);
    h.calls[1].ok(null); // a 502 reads as null
    await flush();
    const s = h.shelf()!;
    expect(s.querySelector('.vdsc-skel')).toBeNull();
    expect(s.classList.contains('vdsc-shelf--failed')).toBe(true);
    expect(s.textContent).toContain('load this row');
    expect(s.querySelector('[data-vdsc-retry]')).not.toBeNull();
  });

  it('Try again brings the skeleton back and loads for real', async () => {
    vi.useFakeTimers();
    const h = shelfHarness();
    h.api.fillShelf(h.shelf()!);
    h.calls[0].ok(null);
    await flush();
    vi.advanceTimersByTime(1500);
    h.calls[1].ok(null);
    await flush();
    h.api.retryShelf(h.shelf()!);
    expect(h.shelf()!.querySelector('.vdsc-skel')).not.toBeNull();
    expect(h.calls).toHaveLength(3);
    h.calls[2].ok({ items: [{ title: 'Coming' }] });
    await flush();
    expect(h.shelf()!.classList.contains('vdsc-shelf--failed')).toBe(false);
    expect(h.shelf()!.innerHTML).toContain('Coming');
  });
});

describe('the list call has a deadline (#2158)', () => {
  function fetchHarness(impl: () => Promise<unknown>) {
    const listCache: Record<string, unknown> = {};
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const cachedFetch = new Function(
      'listCache',
      'LIST_TIMEOUT_MS',
      'fetch',
      `${extractFunction('cachedFetch', JS)}; return cachedFetch;`,
    )(listCache, 25000, impl) as (url: string) => Promise<unknown>;
    return { cachedFetch, listCache };
  }

  it('a call that never answers rejects instead of hanging', async () => {
    vi.useFakeTimers();
    const h = fetchHarness(() => new Promise(() => {}));
    let failed = false;
    h.cachedFetch('/list?key=now_playing').catch(() => {
      failed = true;
    });
    vi.advanceTimersByTime(25000);
    await flush();
    expect(failed).toBe(true);
  });

  it('a good answer is cached, a failed one is not', async () => {
    const good = fetchHarness(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({ items: [1] }) }),
    );
    await good.cachedFetch('/a');
    expect(good.listCache['/a']).toEqual({ items: [1] });
    const bad = fetchHarness(() => Promise.resolve({ ok: false, json: () => Promise.resolve({}) }));
    expect(await bad.cachedFetch('/b')).toBeNull();
    expect(bad.listCache['/b']).toBeUndefined();
  });
});
