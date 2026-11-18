import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * The member's ask (#2168), static/video/video-requests.js, run for real in
 * jsdom. Only the qualities the server says this profile may pick for the
 * kind show up: one is a quiet line ("Comes in X"), two or more a short radio
 * list with the usual one picked and "Asks first" on the ones that need an
 * OK. The button and the lines under it follow the pick: Get it starts a
 * download, Ask for it files a request, and the quota line says what's left.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/video/video-requests.js'), 'utf8');

type Win = Window & Record<string, unknown>;
const win = window as unknown as Win;
let posts: Array<Record<string, unknown>>;
let options: Record<string, unknown>;
let modes: Record<string, string>;
const toasts: Array<[string, string]> = [];

function json(data: unknown, status = 200) {
  return Promise.resolve({
    ok: status < 400,
    status,
    json: () => Promise.resolve(data),
  } as Response);
}

beforeEach(() => {
  document.body.innerHTML = '';
  posts = [];
  toasts.length = 0;
  modes = { movie: 'ask', show: 'download', youtube: 'download' };
  options = {};
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method || 'GET';
      if (method === 'POST' && url === '/api/video/requests') {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        posts.push(body);
        return json(
          body.get ? { success: true, got: true } : { success: true, id: 9, asked: true },
        );
      }
      if (url === '/api/video/requests') return json({ success: true, requests: [], quota: null });
      if (url === '/api/video/requests/options') return json({ success: true, modes });
      if (url.startsWith('/api/video/requests/options?kind=')) return json(options);
      if (url === '/api/video/requests/counts') return json({ success: true, pending: 0 });
      return json(null, 404);
    }),
  );
  (globalThis as Record<string, unknown>).currentProfile = { id: 5, is_admin: false };
  win.showToast = (m: string, t?: string) => {
    toasts.push([m, t || '']);
  };
  // requestAnimationFrame runs the sheet's fade-in
  vi.stubGlobal('requestAnimationFrame', (cb: () => void) => setTimeout(cb, 0));
  new Function(JS)();
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete (globalThis as Record<string, unknown>).currentProfile;
  delete win.showToast;
});

const sheet = () => document.querySelector('.vreq-sheet--ask') as HTMLElement;
const go = () => sheet().querySelector('[data-vreq-sheet-go]') as HTMLButtonElement;
const radios = () => [...sheet().querySelectorAll<HTMLElement>('[role="radio"]')];
const settle = () => new Promise((r) => setTimeout(r, 0));
async function opened() {
  await vi.waitFor(() => expect(sheet()).not.toBeNull());
}
const vr = () =>
  win.VideoRequests as { request: (i: unknown) => Promise<unknown>; [k: string]: unknown };

describe('one allowed quality', () => {
  it('has no menu: "Comes in X", asks first, the quota line and Ask for it', async () => {
    options = {
      success: true,
      kind: 'movie',
      mode: 'ask',
      asks_who: 'Sam',
      qualities: [{ id: 2, name: 'Movie 1080p Quality', tags: [], default: true, asks: true }],
      quota: { limit: 5, days: 7, used: 2, remaining: 3 },
    };
    const done = vr().request({
      kind: 'movie',
      tmdb_id: 693134,
      title: 'Dune: Part Two',
      year: 2024,
    });
    await opened();
    expect(radios()).toHaveLength(0);
    expect(sheet().querySelector('[data-vreq-ask-quality]')?.textContent).toBe(
      'Comes in Movie 1080p Quality',
    );
    expect(sheet().querySelector('[data-vreq-askline]')?.textContent).toContain(
      'Sam gets asked first.',
    );
    expect(sheet().querySelector('[data-vreq-quota]')?.textContent).toBe(
      '3 of 5 asks left this week',
    );
    expect(go().textContent).toBe('Ask for it');
    go().click();
    await done;
    expect(posts).toEqual([
      expect.objectContaining({ kind: 'movie', tmdb_id: 693134, quality_profile_id: 2 }),
    ]);
    expect(posts[0].get).toBeUndefined();
  });
});

describe('two or more allowed', () => {
  beforeEach(() => {
    options = {
      success: true,
      kind: 'show',
      mode: 'download',
      asks_who: 'Sam',
      qualities: [
        { id: 5, name: 'TV Show 720p/1080p', tags: [], default: true, asks: false },
        {
          id: 6,
          name: 'TV Show 720p/1080p EXTENDED',
          tags: ['extended'],
          default: false,
          asks: true,
        },
      ],
      quota: null,
    };
  });

  it('a short list, the usual one picked, Asks first tagged, Get it', async () => {
    const done = vr().request({ kind: 'show', tmdb_id: 95396, title: 'Severance' });
    await opened();
    expect(radios().map((r) => r.getAttribute('aria-checked'))).toEqual(['true', 'false']);
    expect(radios().map((r) => r.querySelector('.vreq-qtag')?.textContent)).toEqual([
      'Usual',
      'Asks first',
    ]);
    expect(sheet().querySelector('[data-vreq-askline]')?.textContent).toBe(
      'Starts downloading right away.',
    );
    expect(sheet().querySelector('[data-vreq-quota]')?.textContent).toBe(
      'No limit on what you get right away',
    );
    expect(go().textContent).toBe('Get it');
    go().click();
    await done;
    expect(posts[0]).toMatchObject({
      kind: 'show',
      quality_profile_id: 5,
      get: true,
      monitor: 'all',
    });
    expect(toasts.at(-1)).toEqual(['On its way. It starts downloading now', 'success']);
  });

  it('picking one that needs an OK turns it into an ask (mouse and arrow keys)', async () => {
    const done = vr().request({ kind: 'show', tmdb_id: 95396, title: 'Severance' });
    await opened();
    radios()[1].click();
    expect(radios()[1].getAttribute('aria-checked')).toBe('true');
    expect(go().textContent).toBe('Ask for it');
    expect(sheet().querySelector('[data-vreq-askline]')?.textContent).toContain(
      'Sam gets asked first.',
    );
    // back with the arrow keys, then forward again
    radios()[1].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    expect(go().textContent).toBe('Get it');
    radios()[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    expect(go().textContent).toBe('Ask for it');
    go().click();
    await done;
    expect(posts[0]).toMatchObject({ quality_profile_id: 6 });
    expect(posts[0].get).toBeUndefined();
  });

  it('Cancel sends nothing', async () => {
    const done = vr().request({ kind: 'show', tmdb_id: 95396, title: 'Severance' });
    await opened();
    (sheet().querySelector('[data-vreq-sheet-cancel]') as HTMLElement).click();
    expect(await done).toBeNull();
    expect(posts).toEqual([]);
  });
});

describe("a kind they can't request", () => {
  it('says so calmly and opens nothing', async () => {
    options = { success: true, kind: 'movie', mode: 'none', qualities: [] };
    expect(await vr().request({ kind: 'movie', tmdb_id: 1, title: 'X' })).toBeNull();
    expect(sheet()).toBeNull();
    expect(posts).toEqual([]);
    expect(toasts.at(-1)?.[0]).toContain('Movies aren’t part of your profile');
  });

  it('has no Request button on its cards, and the detail page reads the kind', async () => {
    modes = { movie: 'none', show: 'ask', youtube: 'none' };
    await (vr().access as (f: boolean) => Promise<unknown>)(true);
    await settle();
    const card = vr().cardButton as (o: unknown) => string;
    expect(card({ kind: 'movie', tmdbId: 1, title: 'X' })).toBe('');
    expect(card({ kind: 'show', tmdbId: 2, title: 'Y' })).toContain('data-vreq-card');
    const canGet = vr().canGet as (k: string) => boolean;
    const mayAsk = vr().mayAsk as (k: string) => boolean;
    expect([canGet('movie'), mayAsk('movie'), canGet('show'), mayAsk('show')]).toEqual([
      false,
      false,
      false,
      true,
    ]);
  });
});
