import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * the video requests page, the shared request flow and the card ribbons.
 *
 * an approved request used to say "On the way" until the whole title landed,
 * even when half a show was already here or every search had given up. the
 * backend now stamps approved rows with progress + state, and these helpers
 * turn that into the words on the row.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/video/video-requests.js'), 'utf8');
const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');

type Row = Record<string, unknown>;
type Helpers = {
  requestStatus: (r: Row) => { text: string; tone: string };
  progressPct: (r: Row) => number | null;
  bucketOf: (r: Row) => string;
  canSearchAgain: (r: Row) => boolean;
  qualityName: (profiles: { id: number; name: string }[], id: unknown) => string;
  cardStates: (rows: Row[]) => Record<string, string>;
  quotaSpent: (q: unknown) => boolean;
};

function helpers(): Helpers {
  const names = [
    'progressOf',
    'bucketOf',
    'requestStatus',
    'progressPct',
    'canSearchAgain',
    'qualityName',
    'cardStates',
    'quotaSpent',
  ];
  const body = names.map((n) => extractFunction(n, JS)).join('\n');
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(`${body}\nreturn { ${names.join(', ')} };`)() as Helpers;
}

const approved = (kind: string, state: string, p: Partial<Record<string, number>>): Row => ({
  status: 'approved',
  kind,
  state,
  progress: { owned: 0, wanted: 0, failed: 0, total: 0, ...p },
});

describe('the status words', () => {
  const h = helpers();

  it('says where a show is at, episode by episode', () => {
    expect(
      h.requestStatus(approved('show', 'partial', { owned: 3, wanted: 7, total: 10 })),
    ).toEqual({
      text: '3 of 10 episodes',
      tone: 'partial',
    });
    expect(
      h.requestStatus(approved('show', 'partial', { owned: 3, wanted: 6, failed: 1, total: 10 }))
        .text,
    ).toBe('3 of 10 episodes · 1 failed');
  });

  it('says partly here once nothing is left in flight', () => {
    expect(
      h.requestStatus(approved('show', 'partial', { owned: 9, failed: 1, total: 10 })).text,
    ).toBe('Partly here · 1 failed');
  });

  it('owns up when the searches gave up', () => {
    expect(h.requestStatus(approved('movie', 'failed', { failed: 1, total: 1 }))).toEqual({
      text: 'Couldn’t find it',
      tone: 'failed',
    });
  });

  it('keeps the plain states', () => {
    expect(h.requestStatus(approved('movie', 'on_the_way', { wanted: 1, total: 1 })).text).toBe(
      'On the way',
    );
    expect(h.requestStatus(approved('show', 'available', { owned: 10, total: 10 })).text).toBe(
      'In your library',
    );
    expect(h.requestStatus({ status: 'pending' }).text).toBe('Waiting');
    expect(h.requestStatus({ status: 'denied' }).text).toBe('Declined');
  });

  it('falls back to in_library when a row has no state', () => {
    expect(h.requestStatus({ status: 'approved', in_library: true }).text).toBe('In your library');
    expect(h.requestStatus({ status: 'approved' }).text).toBe('On the way');
  });

  it('puts only fully arrived titles in the available tab', () => {
    expect(h.bucketOf(approved('show', 'available', { owned: 1, total: 1 }))).toBe('available');
    // in_library is true for a show with one episode; the state knows better
    expect(
      h.bucketOf({ ...approved('show', 'partial', { owned: 1, wanted: 4 }), in_library: true }),
    ).toBe('onway');
    expect(h.bucketOf(approved('movie', 'failed', { failed: 1 }))).toBe('onway');
  });
});

describe('the progress bar', () => {
  const h = helpers();

  it('draws for a show still arriving', () => {
    expect(h.progressPct(approved('show', 'partial', { owned: 3, wanted: 7, total: 10 }))).toBe(30);
    expect(h.progressPct(approved('show', 'on_the_way', { wanted: 10, total: 10 }))).toBe(0);
  });

  it('stays away when it would say nothing', () => {
    expect(h.progressPct(approved('movie', 'on_the_way', { wanted: 1, total: 1 }))).toBeNull();
    expect(h.progressPct(approved('show', 'available', { owned: 10, total: 10 }))).toBeNull();
    expect(h.progressPct(approved('show', 'on_the_way', { total: 0 }))).toBeNull();
    expect(h.progressPct({ status: 'pending', kind: 'show' })).toBeNull();
  });

  it('is styled', () => {
    expect(CSS).toContain('.vreq-progress');
    expect(CSS).toContain('.vreq-status--failed');
    expect(CSS).toContain('.vreq-status--partial');
  });
});

describe('search again', () => {
  const h = helpers();
  it('is offered only when something failed', () => {
    expect(h.canSearchAgain(approved('movie', 'failed', { failed: 1 }))).toBe(true);
    expect(h.canSearchAgain(approved('show', 'partial', { owned: 2, failed: 1 }))).toBe(true);
    expect(h.canSearchAgain(approved('show', 'partial', { owned: 2, wanted: 3 }))).toBe(false);
    expect(h.canSearchAgain(approved('show', 'on_the_way', { wanted: 3 }))).toBe(false);
    expect(h.canSearchAgain({ status: 'pending' })).toBe(false);
  });

  it('reuses the wishlist retry endpoint', () => {
    expect(extractFunction('searchAgain', JS)).toContain('/api/video/wishlist/retry');
  });
});

describe('quality names and card states', () => {
  const h = helpers();
  const profiles = [
    { id: 0, name: 'Default' },
    { id: 4, name: '4K' },
  ];

  it('names a chosen profile and stays quiet for the default', () => {
    expect(h.qualityName(profiles, 4)).toBe('4K');
    expect(h.qualityName(profiles, 0)).toBe('');
    expect(h.qualityName(profiles, null)).toBe('');
    expect(h.qualityName(profiles, 99)).toBe('');
  });

  it('keys card states by kind and tmdb id', () => {
    const s = h.cardStates([
      { kind: 'movie', tmdb_id: 1, status: 'pending' },
      { kind: 'show', tmdb_id: 2, status: 'approved', state: 'available' },
      { kind: 'show', tmdb_id: 3, status: 'approved', state: 'partial' },
      { kind: 'movie', tmdb_id: 4, status: 'denied' },
    ]);
    expect(s).toEqual({ 'movie:1': 'requested', 'show:2': 'available', 'show:3': 'requested' });
  });

  it('knows a spent quota', () => {
    expect(h.quotaSpent({ limit: 3, remaining: 0 })).toBe(true);
    expect(h.quotaSpent({ limit: 3, remaining: 1 })).toBe(false);
    expect(h.quotaSpent(null)).toBe(false);
    expect(h.quotaSpent({ limit: 0, remaining: 0 })).toBe(false);
  });
});

// ── the live module in jsdom ────────────────────────────────────────────────
type Api = {
  request: (item: Row) => Promise<unknown>;
  cardButton: (o: Row) => string;
  hydrate: (root?: ParentNode) => void;
};
type Sheet = { pickRequest: (o: Row) => Promise<unknown> };

describe('the request flow', () => {
  let toasts: [string, string][];
  let posts: Row[];
  let quota: Row | null;
  let requests: Row[];
  let profiles: Row[];

  beforeEach(() => {
    toasts = [];
    posts = [];
    quota = null;
    requests = [];
    profiles = [{ id: 0, name: 'Default' }];
    document.body.innerHTML = '';
    vi.stubGlobal('showToast', (m: string, t: string) => toasts.push([m, t]));
    vi.stubGlobal('canDownload', () => false);
    // the badge poll isn't under test
    vi.stubGlobal('setInterval', () => 0);
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string, init?: RequestInit) => {
        let body: unknown = { success: true };
        if (url.startsWith('/api/video/downloads/quality/profiles')) body = { profiles };
        else if (url === '/api/video/requests' && init?.method === 'POST') {
          posts.push(JSON.parse(String(init.body)));
          body = { success: true, id: 1 };
        } else if (url.startsWith('/api/video/requests')) {
          body = { success: true, requests, quota, pending: 0 };
        }
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }),
    );
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    new Function(JS)();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const api = () => (window as unknown as { VideoRequests: Api }).VideoRequests;
  const sheet = () => (window as unknown as { VideoRequestSheet: Sheet }).VideoRequestSheet;
  const flush = () => new Promise((r) => setTimeout(r, 0));

  it('stops at a spent quota before any sheet opens', async () => {
    quota = { limit: 2, days: 7, used: 2, remaining: 0 };
    const res = await api().request({ kind: 'show', tmdb_id: 7, title: 'Severance' });
    expect(res).toBeNull();
    expect(document.querySelector('.vreq-sheet')).toBeNull();
    expect(posts).toEqual([]);
    expect(toasts[0]).toEqual(['You’ve used all 2 requests this week', 'warning']);
  });

  it('offers a quality select with the named profiles and sends the pick', async () => {
    profiles = [
      { id: 0, name: 'Default' },
      { id: 4, name: '4K' },
    ];
    const pending = api().request({ kind: 'movie', tmdb_id: 9, title: 'Heat' });
    for (let i = 0; i < 10 && !document.querySelector('[data-vreq-quality]'); i++) await flush();
    const sel = document.querySelector<HTMLSelectElement>('[data-vreq-quality]');
    expect(sel).not.toBeNull();
    expect([...sel!.options].map((o) => o.textContent)).toEqual(['Default', '4K']);
    // movies get no season choices
    expect(document.querySelector('[data-vreq-choice]')).toBeNull();
    sel!.value = '4';
    document.querySelector<HTMLElement>('[data-vreq-sheet-go]')!.click();
    await pending;
    expect(posts).toEqual([
      expect.objectContaining({ kind: 'movie', tmdb_id: 9, quality_profile_id: 4 }),
    ]);
  });

  it('skips the sheet for a movie when there is only the default', async () => {
    await api().request({ kind: 'movie', tmdb_id: 9, title: 'Heat' });
    expect(posts).toHaveLength(1);
    expect(posts[0]).not.toHaveProperty('quality_profile_id');
  });

  it('leaves the default out of the body', async () => {
    profiles = [
      { id: 0, name: 'Default' },
      { id: 4, name: '4K' },
    ];
    const res = sheet().pickRequest({ kind: 'show', title: 'Severance', profiles });
    document.querySelector<HTMLElement>('[data-vreq-choice="first_season"]')!.click();
    document.querySelector<HTMLElement>('[data-vreq-sheet-go]')!.click();
    expect(await res).toEqual({ monitor: 'first_season', quality_profile_id: 0 });
  });

  it('paints Requested over the preview ribbon and hides the card action', async () => {
    requests = [{ kind: 'movie', tmdb_id: 9, status: 'pending' }];
    document.body.innerHTML =
      '<a class="vsr-card" data-vsr-source="tmdb">' +
      '<span class="vcard-ctrls">' +
      api().cardButton({ kind: 'movie', tmdbId: 9, title: 'Heat' }) +
      '</span><div class="vsr-poster"><span class="vsr-ribbon vsr-ribbon--preview">Preview</span></div></a>' +
      '<a class="vsr-card" data-vsr-source="tmdb">' +
      api().cardButton({ kind: 'show', tmdbId: 5, title: 'Other' }) +
      '<span class="vsr-ribbon vsr-ribbon--preview">Preview</span></a>';
    api().hydrate(document);
    for (let i = 0; i < 10 && !document.querySelector('.vsr-ribbon--req'); i++) await flush();
    const [a, b] = document.querySelectorAll('.vsr-card');
    expect(a.querySelector('.vsr-ribbon')!.textContent).toBe('Requested');
    expect(a.querySelector<HTMLButtonElement>('[data-vreq-card]')!.hidden).toBe(true);
    expect(b.querySelector('.vsr-ribbon')!.textContent).toBe('Preview');
    expect(b.querySelector<HTMLButtonElement>('[data-vreq-card]')!.hidden).toBe(false);
  });

  it('never paints for a profile that can download', async () => {
    vi.stubGlobal('canDownload', () => true);
    requests = [{ kind: 'movie', tmdb_id: 9, status: 'pending' }];
    document.body.innerHTML =
      '<a class="vsr-card">' +
      api().cardButton({ kind: 'movie', tmdbId: 9 }) +
      '<span class="vsr-ribbon vsr-ribbon--preview">Preview</span></a>';
    api().hydrate(document);
    await flush();
    await flush();
    expect(document.querySelector('.vsr-ribbon')!.textContent).toBe('Preview');
  });
});
