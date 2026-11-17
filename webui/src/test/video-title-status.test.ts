import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * #2160: a member browsing the video side couldn't tell whether a title was
 * in the library, partly there, asked for, approved, downloading or failed.
 * every poster now wears one status badge from one batched lookup, and the
 * detail page shows who asked (names for admins only), progress and seasons.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/video/video-title-status.js'), 'utf8');
const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');
const REQ_JS = readFileSync(resolve(process.cwd(), 'static/video/video-requests.js'), 'utf8');

type Status = Record<string, unknown>;
type Api = {
  hydrate: (root?: ParentNode) => Promise<void>;
  blockHtml: (s: Status | null) => string | null;
  badgeFor: (s: Status | null) => { variant: string; label: string } | null;
  cardAttrs: (kind: string, tmdbId: unknown, owned?: boolean) => string;
  invalidate: () => void;
};

let listeners: [string, EventListenerOrEventListenerObject][] = [];
let timers: ReturnType<typeof setTimeout>[] = [];
const realAdd = document.addEventListener.bind(document);
const realSetTimeout = globalThis.setTimeout;
let statuses: Record<string, Status>;
let posts: Record<string, number[]>[];

beforeEach(() => {
  vi.spyOn(document, 'addEventListener').mockImplementation(
    (
      type: string,
      fn: EventListenerOrEventListenerObject,
      opts?: boolean | AddEventListenerOptions,
    ) => {
      listeners.push([type, fn]);
      realAdd(type, fn, opts);
    },
  );
  vi.stubGlobal('setTimeout', (fn: () => void, ms?: number) => {
    const id = realSetTimeout(fn, ms);
    timers.push(id);
    return id;
  });
  statuses = {};
  posts = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      if (url.startsWith('/api/video/requests')) {
        const body = {
          success: true,
          requests: [{ kind: 'movie', tmdb_id: 1, status: 'pending' }],
        };
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }
      if (url === '/api/video/title-status') {
        posts.push(JSON.parse(typeof init?.body === 'string' ? init.body : '{}'));
        return Promise.resolve(
          new Response(JSON.stringify({ success: true, statuses }), { status: 200 }),
        );
      }
      return Promise.resolve(new Response('{}', { status: 404 }));
    }),
  );
  document.body.innerHTML = '';
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  new Function(JS)();
});

afterEach(() => {
  for (const [type, fn] of listeners) document.removeEventListener(type, fn);
  for (const id of timers) clearTimeout(id);
  listeners = [];
  timers = [];
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const api = () => (window as unknown as { VideoTitleStatus: Api }).VideoTitleStatus;

function card(kind: string, tmdb: number, owned = false): string {
  const ribbon = owned
    ? '<span class="vsr-ribbon vsr-ribbon--owned">In Library</span>'
    : '<span class="vsr-ribbon vsr-ribbon--preview">Preview</span>';
  return (
    `<a class="vsr-card" data-tmdb-card="${tmdb}"${api().cardAttrs(kind, tmdb, owned)}>` +
    `<button type="button" data-vreq-card data-kind="${kind}" data-tmdb="${tmdb}"></button>` +
    `<div class="vsr-poster">${ribbon}</div></a>`
  );
}
const ribbonOf = (tmdb: number) =>
  document.querySelector(`[data-tmdb-card="${tmdb}"] .vsr-ribbon`) as HTMLElement;
const askOf = (tmdb: number) =>
  document.querySelector(`[data-tmdb-card="${tmdb}"] [data-vreq-card]`) as HTMLButtonElement;

describe('the poster badge', () => {
  it('has words for every state the issue lists', () => {
    const w = (s: Status) => api().badgeFor(s)?.label;
    expect(w({ status: 'available' })).toBe('In Library');
    expect(w({ status: 'partial' })).toBe('Partial');
    expect(w({ status: 'pending' })).toBe('Requested');
    expect(w({ status: 'processing', reason: 'approved' })).toBe('Approved');
    expect(w({ status: 'processing', reason: 'wished' })).toBe('Wishlisted');
    expect(w({ status: 'searching' })).toBe('Searching');
    expect(w({ status: 'downloading', progress: 42.4 })).toBe('Downloading 42%');
    expect(w({ status: 'failed' })).toBe('Failed');
    expect(api().badgeFor(null)).toBeNull();
  });

  it('paints a whole page from one request and hides asks that would be duplicates', async () => {
    statuses = {
      'movie:1': { status: 'pending', request: 'others' },
      'movie:2': { status: 'downloading', progress: 10, request: 'on_the_way' },
      'show:3': { status: 'partial', request: 'open' },
    };
    document.body.innerHTML =
      card('movie', 1) + card('movie', 2) + card('show', 3, true) + card('movie', 4);
    await api().hydrate(document);
    expect(posts).toEqual([{ movies: [1, 2, 4], shows: [3] }]);
    expect(ribbonOf(1).textContent).toBe('Requested');
    expect(ribbonOf(1).className).toContain('vsr-ribbon--req');
    expect(ribbonOf(2).textContent).toBe('Downloading 10%');
    expect(ribbonOf(3).textContent).toBe('Partial');
    expect(ribbonOf(4).textContent).toBe('Preview');
    expect(askOf(1).hidden).toBe(true);
    expect(askOf(2).hidden).toBe(true);
    expect(askOf(3).hidden).toBe(false);
    expect(askOf(4).hidden).toBe(false);
  });

  it('keeps an owned card In Library when there is nothing more to say', async () => {
    document.body.innerHTML = card('movie', 7, true);
    await api().hydrate(document);
    expect(ribbonOf(7).textContent).toBe('In Library');
  });

  it('answers again from its short cache, without a second request', async () => {
    statuses = { 'movie:1': { status: 'pending' } };
    document.body.innerHTML = card('movie', 1);
    await api().hydrate(document);
    document.body.innerHTML = card('movie', 1);
    await api().hydrate(document);
    expect(posts.length).toBe(1);
    expect(ribbonOf(1).textContent).toBe('Requested');
  });

  it('labels the detail page more-like-this chips the same way', async () => {
    statuses = { 'movie:9': { status: 'processing', reason: 'approved' } };
    document.body.innerHTML = '<a class="vd-sim-card" data-vd-sim="movie" data-vd-sim-id="9"></a>';
    await api().hydrate(document);
    const chip = document.querySelector('.vd-sim-wishchip')!;
    expect(chip.textContent).toBe('Approved');
    expect(chip.className).toContain('vd-sim-wishchip--wish');
  });

  it('is styled with tokens, quietly, for every variant', () => {
    for (const v of ['partial', 'active', 'failed']) expect(CSS).toContain(`.vsr-ribbon--${v} {`);
    expect(CSS).toContain('.vts-block {');
  });
});

describe('next to the request module', () => {
  it('lets the shared status own movie and show cards, whichever paints last', async () => {
    vi.stubGlobal('canDownload', () => false);
    vi.stubGlobal('setInterval', () => 0);
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    new Function(REQ_JS)();
    const req = (window as unknown as { VideoRequests: { hydrate: (r: ParentNode) => void } })
      .VideoRequests;
    statuses = { 'movie:1': { status: 'processing', reason: 'approved', request: 'on_the_way' } };
    document.body.innerHTML = card('movie', 1);
    await api().hydrate(document);
    req.hydrate(document);
    for (let i = 0; i < 5; i++) await new Promise((r) => realSetTimeout(r, 0));
    expect(ribbonOf(1).textContent).toBe('Approved');
    expect(askOf(1).hidden).toBe(true);
  });
});

describe('the detail page block', () => {
  it('names who asked for admins (the server only sends names to them)', () => {
    const html = api().blockHtml({
      status: 'pending',
      requests: [
        { mine: false, who: 'Kim', status: 'pending', created_at: '2020-01-01 00:00:00' },
        { mine: false, who: 'Tom', status: 'pending', created_at: '2020-01-02 00:00:00' },
      ],
    })!;
    expect(html).toContain('Requested, waiting for approval');
    expect(html).toContain('Requested by Kim and Tom');
  });

  it('shows a member their own ask, and nobody else by name', () => {
    const mine = api().blockHtml({
      status: 'pending',
      requests: [
        { mine: true, who: null, status: 'pending' },
        { mine: false, who: null, status: 'pending' },
      ],
    })!;
    expect(mine).toContain('Requested by you and others');
    expect(mine).toContain('data-vts-goto-requests');
    const theirs = api().blockHtml({
      status: 'pending',
      requests: [{ mine: false, who: null, status: 'pending' }],
    })!;
    expect(theirs).toContain('Requested by someone');
    expect(theirs).not.toContain('data-vts-goto-requests');
  });

  it('shows progress while it downloads', () => {
    const html = api().blockHtml({ status: 'downloading', progress: 61.6, requests: [] })!;
    expect(html).toContain('Downloading · 62%');
    expect(html).toContain('width:62%');
  });

  it('says what each season has on a partly owned show', () => {
    const html = api().blockHtml({
      status: 'partial',
      episodes: { aired: 20, owned: 12 },
      seasons: [
        { season: 1, status: 'available', aired: 10, owned: 10 },
        { season: 2, status: 'partial', aired: 10, owned: 2 },
        { season: 3, status: 'processing', aired: 0, owned: 0 },
      ],
      requests: [],
    })!;
    expect(html).toContain('12 of 20 aired episodes here');
    expect(html).toContain('vts-season--available');
    expect(html).toContain('2/10');
    expect(html).toContain('on the way');
  });

  it('stays out of the way on an owned movie nobody asked for', () => {
    expect(api().blockHtml({ status: 'available', requests: [] })).toBeNull();
    expect(api().blockHtml({ status: null, requests: [] })).toBeNull();
  });
});
