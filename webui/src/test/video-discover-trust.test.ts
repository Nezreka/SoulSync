import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * Discover batch 1: the video half of "restore basic trust".
 *
 * Three things the page did wrong, all of them invisible in a screenshot:
 *
 * * a slow filter response appended its rows into the grid of the filter after
 *   it, so Browse could show a mix of two queries;
 * * a failed request rendered as "Nothing here", which is a lie about the
 *   catalogue;
 * * "Not interested" removed the card before the save was acknowledged, kept
 *   only the tile you clicked, and had no way back.
 *
 * These run the REAL functions out of video-discover.js with their closure
 * bound to stubs, rather than asserting on the source text.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/video/video-discover.js'), 'utf8');
const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');
const HTML = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');

afterEach(() => {
  document.body.innerHTML = '';
  vi.useRealTimers();
});

// ── the grid ────────────────────────────────────────────────────────────────

type Grid = {
  loadGrid: (reset: boolean) => void;
  state: Record<string, never> & { mode: string; cat: Record<string, unknown>; catGen: number };
  answer: (url: string, value: unknown) => void;
  html: () => string;
};

function gridHarness(): Grid {
  document.body.innerHTML = `
    <div data-vdsc-grid></div>
    <div class="hidden" data-vdsc-grid-empty></div>
    <div class="hidden" data-vdsc-grid-error></div>
    <div class="hidden" data-vdsc-grid-loading></div>
    <div class="hidden" data-vdsc-more-loading></div>
    <button data-vdsc-more>Load more</button>`;

  const pending: Record<string, (value: unknown) => void> = {};
  const cachedFetch = (url: string) =>
    new Promise((res) => {
      pending[url] = res;
    });

  const state = {
    mode: 'grid',
    catGen: 0,
    cat: {} as Record<string, unknown>,
  };
  const $ = (sel: string) => document.querySelector(sel);
  const card = (it: { title: string }) => `<a class="vsr-card">${it.title}</a>`;

  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const loadGrid = new Function(
    'state',
    '$',
    'cachedFetch',
    'LIST_URL',
    'card',
    'hydrateGet',
    'maybeAutoLoad',
    `${extractFunction('loadGrid', JS)}; return loadGrid;`,
  )(
    state,
    $,
    cachedFetch,
    '/list',
    card,
    () => {},
    () => {},
  ) as (reset: boolean) => void;

  return {
    loadGrid,
    state: state as never,
    answer: (url, value) => pending[url]?.(value),
    html: () => (document.querySelector('[data-vdsc-grid]') as HTMLElement).innerHTML,
  };
}

/** what openCategory does to state.cat, in one line. */
function openCat(h: Grid, q: string) {
  h.state.catGen += 1;
  h.state.cat = {
    title: q,
    q,
    page: 1,
    nextPage: 2,
    browse: true,
    gen: h.state.catGen,
    paginates: true,
    busy: false,
    hasMore: false,
  };
  (document.querySelector('[data-vdsc-grid]') as HTMLElement).innerHTML = '';
}

describe('the browse grid', () => {
  it('the latest query owns the grid, whatever order the answers arrive in', async () => {
    // The live shape: pick filter A, it is slow, pick filter B, B answers
    // first. A's rows used to land on top of B's, because loadGrid closed over
    // the old cat object and then wrote into the shared grid regardless.
    const h = gridHarness();

    openCat(h, 'genre=A');
    h.loadGrid(true);
    openCat(h, 'genre=B');
    h.loadGrid(true);

    h.answer('/list?genre=B&page=1', { items: [{ title: 'B1' }], has_more: false });
    await Promise.resolve();
    await Promise.resolve();
    h.answer('/list?genre=A&page=1', { items: [{ title: 'A1' }], has_more: false });
    await Promise.resolve();
    await Promise.resolve();

    expect(h.html()).toContain('B1');
    expect(h.html()).not.toContain('A1');
  });

  it('a page 2 still in flight cannot land in the next query', async () => {
    const h = gridHarness();
    openCat(h, 'genre=A');
    h.loadGrid(true);
    h.answer('/list?genre=A&page=1', { items: [{ title: 'A1' }], has_more: true, next_page: 2 });
    await Promise.resolve();
    await Promise.resolve();
    h.loadGrid(false); // page 2 of A goes out

    openCat(h, 'genre=B'); // user changes the filter
    h.loadGrid(true);
    h.answer('/list?genre=B&page=1', { items: [{ title: 'B1' }], has_more: false });
    await Promise.resolve();
    await Promise.resolve();
    h.answer('/list?genre=A&page=2', { items: [{ title: 'A2' }], has_more: false });
    await Promise.resolve();
    await Promise.resolve();

    expect(h.html()).toContain('B1');
    expect(h.html()).not.toContain('A2');
  });

  it('closing the category discards whatever was still coming', async () => {
    const h = gridHarness();
    openCat(h, 'genre=A');
    h.loadGrid(true);
    h.state.catGen += 1; // closeCategory bumps the generation
    h.state.mode = 'shelves';
    h.answer('/list?genre=A&page=1', { items: [{ title: 'A1' }], has_more: false });
    await Promise.resolve();
    await Promise.resolve();
    expect(h.html()).toBe('');
  });

  it('a failed request is not "Nothing here"', async () => {
    // cachedFetch answers null for any non-OK response, so a 500 and an empty
    // page were indistinguishable and both rendered the empty state.
    const h = gridHarness();
    openCat(h, 'genre=A');
    h.loadGrid(true);
    h.answer('/list?genre=A&page=1', null);
    await Promise.resolve();
    await Promise.resolve();

    expect(document.querySelector('[data-vdsc-grid-error]')!.className).not.toContain('hidden');
    expect(document.querySelector('[data-vdsc-grid-empty]')!.className).toContain('hidden');
    expect((document.querySelector('[data-vdsc-more]') as HTMLButtonElement).textContent).toBe(
      'Try again',
    );
  });

  it('an honestly empty result still says nothing found', async () => {
    const h = gridHarness();
    openCat(h, 'genre=A');
    h.loadGrid(true);
    h.answer('/list?genre=A&page=1', { items: [], has_more: false });
    await Promise.resolve();
    await Promise.resolve();

    expect(document.querySelector('[data-vdsc-grid-empty]')!.className).not.toContain('hidden');
    expect(document.querySelector('[data-vdsc-grid-error]')!.className).toContain('hidden');
  });

  it('a failed page 2 keeps the rows already on screen', async () => {
    const h = gridHarness();
    openCat(h, 'genre=A');
    h.loadGrid(true);
    h.answer('/list?genre=A&page=1', { items: [{ title: 'A1' }], has_more: true, next_page: 2 });
    await Promise.resolve();
    await Promise.resolve();
    h.loadGrid(false);
    h.answer('/list?genre=A&page=2', null);
    await Promise.resolve();
    await Promise.resolve();

    expect(h.html()).toContain('A1');
    // The cursor did not advance past the page that failed.
    expect(h.state.cat.nextPage).toBe(2);
  });

  it('Retry after a failed first page requests page one again', async () => {
    const h = gridHarness();
    openCat(h, 'genre=A');
    h.loadGrid(true);
    h.answer('/list?genre=A&page=1', null);
    await Promise.resolve();
    await Promise.resolve();
    document.querySelector('[data-vdsc-more]')!.addEventListener('click', () => h.loadGrid(false));
    (document.querySelector('[data-vdsc-more]') as HTMLButtonElement).click();
    h.answer('/list?genre=A&page=1', {
      items: [{ title: 'Recovered first page' }],
      has_more: true,
      next_page: 2,
    });
    await Promise.resolve();
    await Promise.resolve();
    expect(h.html()).toContain('Recovered first page');
    expect(h.state.cat.nextPage).toBe(2);
  });

  it('the error banner exists in the markup', () => {
    expect(HTML).toContain('data-vdsc-grid-error');
  });
});

// ── not interested ──────────────────────────────────────────────────────────

function notIntHarness(saveResult: unknown, undoResult: unknown = { success: true }) {
  // Its own document. wireNotInterested binds a listener to `document` and
  // never lets go, so sharing jsdom's one document across tests leaves the
  // first test's handler running over the second test's cards.
  const doc = document.implementation.createHTMLDocument('t');
  doc.body.innerHTML = `
    <div class="vsr-ranked">
      <span class="vsr-rank">1</span>
      <a class="vsr-card" id="card-a">
        <button class="vsr-notint" data-ig-kind="movie" data-ig-id="42" data-ig-title="Dune"></button>
      </a>
    </div>
    <a class="vsr-card" id="card-b">
      <button class="vsr-notint" data-ig-kind="movie" data-ig-id="42" data-ig-title="Dune"></button>
    </a>
    <a class="vsr-card" id="card-c">
      <button class="vsr-notint" data-ig-kind="movie" data-ig-id="99" data-ig-title="Other"></button>
    </a>`;

  const posts: Record<string, unknown>[] = [];
  const postIgnore = (payload: Record<string, unknown>) => {
    posts.push(payload);
    return Promise.resolve(payload.action === 'add' ? saveResult : undoResult);
  };
  const esc = (v: unknown) => String(v ?? '');
  const toasts: string[] = [];
  const state: Record<string, unknown> = {};

  const src = [
    extractFunction('occurrencesOf', JS),
    extractFunction('showUndoBar', JS),
    extractFunction('wireNotInterested', JS),
  ].join('\n');
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  new Function(
    'state',
    'postIgnore',
    'esc',
    'toast',
    'document',
    `var _undoTimer=null; ${src}; wireNotInterested();`,
  )(state, postIgnore, esc, (m: string) => toasts.push(m), doc);

  return {
    posts,
    toasts,
    doc,
    click: (id: string) =>
      doc
        .querySelector(`#${id} .vsr-notint`)!
        .dispatchEvent(new MouseEvent('click', { bubbles: true })),
    visible: (id: string) => {
      const el = doc.getElementById(id);
      if (!el) return false;
      const box = el.closest('.vsr-ranked') || el;
      return !box.classList.contains('vdsc-card-hiding');
    },
    present: (id: string) => Boolean(doc.getElementById(id)),
  };
}

describe('not interested', () => {
  it('hides every tile for that title, not just the one you clicked', async () => {
    const h = notIntHarness({ success: true });
    h.click('card-a');
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(h.visible('card-a')).toBe(false);
    expect(h.visible('card-b')).toBe(false);
    // A different title is untouched.
    expect(h.visible('card-c')).toBe(true);
  });

  it('takes the rank numeral with the card', async () => {
    const h = notIntHarness({ success: true });
    h.click('card-a');
    await Promise.resolve();
    expect(h.doc.querySelector('.vsr-ranked')!.classList.contains('vdsc-card-hiding')).toBe(true);
  });

  it('a rejected save puts the cards back and says so', async () => {
    // It used to fade the card out whatever the server answered, so a failed
    // save looked exactly like a successful one until the next reload.
    const h = notIntHarness(null);
    h.click('card-a');
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(h.visible('card-a')).toBe(true);
    expect(h.visible('card-b')).toBe(true);
    expect(h.toasts.join(' ')).toContain("Couldn't hide Dune");
  });

  it('offers an Undo that un-hides and reverses the save', async () => {
    const h = notIntHarness({ success: true });
    h.click('card-a');
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    const undo = h.doc.querySelector('#vdsc-undo .vdsc-undo-btn') as HTMLButtonElement;
    expect(undo).not.toBeNull();
    undo.click();
    await Promise.resolve();
    expect(h.visible('card-a')).toBe(true);
    expect(h.posts.map((p) => p.action)).toEqual(['add', 'remove']);
    await vi.waitFor(() => expect(h.doc.getElementById('vdsc-undo')).toBeNull());
  });

  it('failed Undo keeps every occurrence hidden and offers a retry', async () => {
    const h = notIntHarness({ success: true }, null);
    h.click('card-a');
    await Promise.resolve();
    await Promise.resolve();
    const undo = h.doc.querySelector('.vdsc-undo-btn') as HTMLButtonElement;
    undo.click();
    await vi.waitFor(() => expect(undo.textContent).toBe('Retry Undo'));
    expect(h.visible('card-a')).toBe(false);
    expect(h.visible('card-b')).toBe(false);
    expect(undo.disabled).toBe(false);
    expect(h.toasts.join(' ')).toContain("Couldn't un-hide");
  });

  it('the cards only really go once the undo window has closed', async () => {
    vi.useFakeTimers();
    const h = notIntHarness({ success: true });
    h.click('card-a');
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(h.present('card-a')).toBe(true); // hidden, still recoverable
    vi.advanceTimersByTime(10_000);
    expect(h.present('card-a')).toBe(false);
    expect(h.present('card-b')).toBe(false);
  });

  it('cannot be fired twice while the save is out', async () => {
    const h = notIntHarness({ success: true });
    h.click('card-a');
    h.click('card-a');
    await Promise.resolve();
    await Promise.resolve();
    expect(h.posts).toHaveLength(1);
  });
});

// ── hero controls ───────────────────────────────────────────────────────────

describe('the hero', () => {
  it('scopes its arrow keys to itself', () => {
    // It was a document-level handler that ran whenever the page was visible,
    // so every arrow key anywhere on Discover moved the billboard.
    expect(JS).not.toMatch(/document\.addEventListener\('keydown'[\s\S]{0,200}ArrowLeft/);
    expect(JS).toMatch(/heroEl\.addEventListener\('keydown'/);
  });

  it('pauses the rotation while something in it has focus', () => {
    expect(JS).toContain("heroEl.addEventListener('focusin', stopHeroTimer)");
    expect(CSS).toContain('.vdsc-hero:focus-within .vdsc-dot--on::after');
  });

  it('gives the dots their own strip and a finger-sized target', () => {
    // The dots sat bottom-right, on top of the Trailer button on a phone, and
    // mobile.css's blanket button min-height stretched each 9px dot into a
    // 38px lozenge.
    const dots = /\.vdsc-dots \{([^}]*)\}/.exec(CSS)![1];
    expect(dots).toContain('left: 0');
    expect(dots).toContain('right: 0');
    const dot = /\.vdsc-dot \{([^}]*)\}/.exec(CSS)![1];
    expect(dot).toMatch(/height: 40px/);
    expect(dot).toMatch(/min-height: 40px/);
    // The visible dot is a pseudo-element, so the button's size is the target
    // and nothing can stretch the dot itself.
    expect(CSS).toContain('.vdsc-dot::before');
  });
});
