/**
 * DashboardHeader — the artefact differential against the RECORDED vanilla
 * region (dash-vanilla-fixture.html's .dashboard-header) plus the click seams.
 *
 * The differential walks both trees and compares tag, id, class list, the
 * attributes that matter (src/alt/title/aria-hidden/style.display) and
 * whitespace-normalized text. It is the structure check no behaviour test can
 * replace — the tools arc's nested-stat-ids and findings-badge misses were
 * both caught only by this. The source converted from live index.html to the
 * fixture at P9, when the vanilla markup was deleted.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { vanillaDashboardHtml } from './dash-artefact';
import { DashboardHeader } from './dashboard-header';

function extractVanillaHeader(): string {
  const html = vanillaDashboardHtml();
  const start = html.indexOf('<div class="dashboard-header">');
  expect(start).toBeGreaterThan(-1);
  const re = /<div\b|<\/div>/g;
  re.lastIndex = start;
  let depth = 0;
  for (let m = re.exec(html); m; m = re.exec(html)) {
    depth += m[0] === '<div' ? 1 : -1;
    if (depth === 0) return html.slice(start, m.index + '</div>'.length);
  }
  throw new Error('unbalanced dashboard-header region');
}

const normalize = (text: string | null) => (text ?? '').replace(/\s+/g, ' ').trim();

/** An element's OWN text (direct text nodes only), normalized. Comparing
 *  textContent would trip on the vanilla's inter-element indentation, which
 *  JSX output legitimately lacks. */
function ownText(el: Element): string {
  return normalize(
    Array.from(el.childNodes)
      .filter((node) => node.nodeType === 3 /* TEXT_NODE */)
      .map((node) => node.textContent ?? '')
      .join(' '),
  );
}

/** The attributes the differential compares, beyond id/class. `onclick` is
 *  deliberately not among them — React binds listeners instead. */
const ATTRS = ['src', 'alt', 'title', 'aria-hidden'] as const;

function compareTrees(vanilla: Element, ported: Element, path: string) {
  // CARVE-OUT: the title block became the hello strip (greeting + live stats,
  // the Aug 2026 header redesign) — the ONE region of this header that
  // intentionally diverged from the vanilla fixture. Its behaviour is covered
  // by -dash.hello.test.ts and the hello-strip cases below; everything else,
  // above all the orb containers worker-orbs.js reads every frame, stays
  // pinned 1:1.
  if (vanilla.classList.contains('header-text')) return;
  expect(`${path} tag:${ported.tagName}`).toBe(`${path} tag:${vanilla.tagName}`);
  expect(`${path} id:${ported.id}`).toBe(`${path} id:${vanilla.id}`);
  expect(`${path} class:${Array.from(ported.classList).join('.')}`).toBe(
    `${path} class:${Array.from(vanilla.classList).join('.')}`,
  );
  for (const attr of ATTRS) {
    expect(`${path} ${attr}:${ported.getAttribute(attr) ?? ''}`).toBe(
      `${path} ${attr}:${vanilla.getAttribute(attr) ?? ''}`,
    );
  }
  const vStyle = (vanilla as HTMLElement).style?.display ?? '';
  const pStyle = (ported as HTMLElement).style?.display ?? '';
  expect(`${path} display:${pStyle}`).toBe(`${path} display:${vStyle}`);
  expect(`${path} text:${ownText(ported)}`).toBe(`${path} text:${ownText(vanilla)}`);

  const vKids = Array.from(vanilla.children);
  const pKids = Array.from(ported.children);
  expect(`${path} children:${pKids.map((kid) => kid.tagName).join(',')}`).toBe(
    `${path} children:${vKids.map((kid) => kid.tagName).join(',')}`,
  );
  vKids.forEach((vKid, index) => {
    const label = vKid.id || Array.from(vKid.classList).join('.') || vKid.tagName;
    compareTrees(vKid, pKids[index], `${path}>${label}`);
  });
}

const fetchMock = vi.fn((..._args: unknown[]) => Promise.reject(new Error('down')));

beforeEach(() => {
  fetchMock.mockClear();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete window.openEnrichmentManager;
  delete window.openRepairModal;
  delete window.openWishlistFromHero;
  delete window.isJiosaavnExperimentalEnabled;
  delete window.SoulSyncWebRouter;
  delete window.navigateToPage;
});

async function mountHeader() {
  let view: ReturnType<typeof render>;
  await act(async () => {
    view = render(<DashboardHeader />);
  });
  return view!;
}

describe('the artefact differential', () => {
  it('renders the vanilla region 1:1 in its initial state', async () => {
    const parser = new DOMParser();
    const vanillaDoc = parser.parseFromString(extractVanillaHeader(), 'text/html');
    const vanilla = vanillaDoc.body.firstElementChild!;

    const view = await mountHeader();
    const ported = view.container.firstElementChild!;

    compareTrees(vanilla, ported, 'dashboard-header');
  });
});

describe('the hello strip', () => {
  it('greets by the mirrored profile name, and plainly without one', async () => {
    window._currentProfileName = 'BoulderBadgeDad';
    const view = await mountHeader();
    const greeting = view.container.querySelector('.hello-greeting span')!;
    expect(greeting.textContent).toMatch(/^(good (morning|afternoon|evening)|up late\?), BoulderBadgeDad$/);
    view.unmount();

    delete window._currentProfileName;
    const bare = await mountHeader();
    expect(bare.container.querySelector('.hello-greeting span')!.textContent).toMatch(
      /^(good (morning|afternoon|evening)|up late\?)$/,
    );
  });
});
describe('state rendering', () => {
  it('writes the pill state class onto the button and the strings into the tooltip', async () => {
    const view = await mountHeader();
    act(() => {
      window.dispatchEvent(
        new CustomEvent('ss:enrich-status', {
          detail: {
            id: 'musicbrainz',
            data: {
              running: true,
              paused: false,
              current_item: { type: 'artist', name: 'BYLT' },
              progress: { artists: { matched: 3, total: 10, percent: 30 } },
            },
          },
        }),
      );
    });
    const button = view.container.querySelector('#musicbrainz-button')!;
    expect(button.className).toBe('musicbrainz-button active');
    expect(view.container.querySelector('#mb-tooltip-status')!.textContent).toBe('Running');
    expect(view.container.querySelector('#mb-tooltip-current')!.textContent).toBe('Artist: "BYLT"');
    expect(view.container.querySelector('#mb-tooltip-progress')!.textContent).toBe(
      'Artists: 3 / 10 (30%)',
    );
  });

  it('shows the repair badge only when findings are pending', async () => {
    const view = await mountHeader();
    const badge = () => view.container.querySelector<HTMLElement>('#repair-findings-badge')!;
    expect(badge().style.display).toBe('none');
    act(() => {
      window.dispatchEvent(
        new CustomEvent('ss:repair-status', { detail: { enabled: true, findings_pending: 7 } }),
      );
    });
    expect(badge().style.display).toBe('');
    expect(badge().textContent).toBe('7');
  });

  it('shows/hides the JioSaavn and Hydrabase containers by inline display, nodes staying mounted', async () => {
    const view = await mountHeader();
    const jiosaavn = () => view.container.querySelector<HTMLElement>('.jiosaavn-button-container')!;
    const hydrabase = () =>
      view.container.querySelector<HTMLElement>('#hydrabase-button-container')!;
    expect(jiosaavn().style.display).toBe('none');
    expect(hydrabase().style.display).toBe('none');
    const jiosaavnNode = jiosaavn();
    act(() => {
      window.dispatchEvent(
        new CustomEvent('ss:jiosaavn-experimental', { detail: { enabled: true } }),
      );
      window.dispatchEvent(new CustomEvent('ss:dev-mode', { detail: { enabled: true } }));
    });
    expect(jiosaavn().style.display).toBe('');
    expect(hydrabase().style.display).toBe('');
    // The worker-orbs layer holds node references — the SAME element must
    // survive the visibility flip.
    expect(jiosaavn()).toBe(jiosaavnNode);
  });

  it('applies the Hydrabase inline status color', async () => {
    const view = await mountHeader();
    act(() => {
      window.dispatchEvent(
        new CustomEvent('ss:enrich-status', {
          detail: { id: 'hydrabase', data: { running: false, paused: true } },
        }),
      );
    });
    const status = view.container.querySelector<HTMLElement>('#hydrabase-tooltip-status')!;
    expect(status.textContent).toBe('Paused');
    expect(status.style.color).toBe('rgb(255, 193, 7)');
  });

  it('renders the quick-nav counts, classes and countdown title', async () => {
    const view = await mountHeader();
    act(() => {
      window.dispatchEvent(
        new CustomEvent('ss:watchlist-count', {
          detail: { success: true, count: 4, next_run_in_seconds: 90 },
        }),
      );
      window.dispatchEvent(
        new CustomEvent('ss:dashboard-wishlist-count', { detail: { count: 0 } }),
      );
    });
    const watchlistButton = view.container.querySelector<HTMLElement>('#watchlist-button')!;
    expect(watchlistButton.title).toBe('Next auto-scan in 1m 30s');
    expect(view.container.querySelector('#watchlist-badge')!.className).toBe(
      'hero-btn-badge has-items',
    );
    expect(view.container.querySelector('#watchlist-badge')!.textContent).toBe('4');
    const wishlistButton = view.container.querySelector<HTMLElement>('#wishlist-button')!;
    expect(wishlistButton.className).toBe('header-button wishlist-button wishlist-inactive');
    act(() => {
      window.dispatchEvent(
        new CustomEvent('ss:dashboard-wishlist-count', { detail: { count: 6 } }),
      );
    });
    expect(wishlistButton.className).toBe('header-button wishlist-button wishlist-active');
    expect(view.container.querySelector('#wishlist-badge')!.textContent).toBe('6');
  });
});

describe('click seams', () => {
  it('Manage Workers opens the enrichment manager', async () => {
    const openEnrichmentManager = vi.fn();
    window.openEnrichmentManager = openEnrichmentManager;
    const view = await mountHeader();
    fireEvent.click(view.container.querySelector('#manage-enrichment-btn')!);
    expect(openEnrichmentManager).toHaveBeenCalledTimes(1);
  });

  it('the repair orb navigates via openRepairModal', async () => {
    const openRepairModal = vi.fn();
    window.openRepairModal = openRepairModal;
    const view = await mountHeader();
    fireEvent.click(view.container.querySelector('#repair-button')!);
    expect(openRepairModal).toHaveBeenCalledTimes(1);
  });

  it('watchlist navigates through the global navigateToPage (the chrome-aware entry)', async () => {
    const navigateToPage = vi.fn(() => Promise.resolve(true));
    window.navigateToPage = navigateToPage as never;
    const view = await mountHeader();
    fireEvent.click(view.container.querySelector('#watchlist-button')!);
    expect(navigateToPage).toHaveBeenCalledWith('watchlist');
  });

  it('wishlist prefers the init.js fast/slow path and falls back to navigation', async () => {
    const openWishlistFromHero = vi.fn();
    const navigateToPage = vi.fn(() => Promise.resolve(true));
    window.navigateToPage = navigateToPage as never;
    window.openWishlistFromHero = openWishlistFromHero;
    const view = await mountHeader();
    fireEvent.click(view.container.querySelector('#wishlist-button')!);
    expect(openWishlistFromHero).toHaveBeenCalledTimes(1);
    expect(navigateToPage).not.toHaveBeenCalled();

    delete window.openWishlistFromHero;
    fireEvent.click(view.container.querySelector('#wishlist-button')!);
    expect(navigateToPage).toHaveBeenCalledWith('wishlist');
  });

  it('a provider orb click goes through the toggle (integration)', async () => {
    const view = await mountHeader();
    fetchMock.mockImplementation(
      () => Promise.resolve({ ok: true, status: 200, json: async () => ({}) }) as never,
    );
    fetchMock.mockClear();
    await act(async () => {
      fireEvent.click(view.container.querySelector('#deezer-button')!);
    });
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls[0]).toBe('/api/enrichment/deezer/resume');
  });
});

/**
 * Asserted as stylesheet text because jsdom has no layout engine: it cannot
 * measure a flex item, so it cannot notice one holding a row wider than the
 * viewport. This particular overflow is worth pinning because it does not stay
 * on the dashboard — an unclipped row wider than the screen makes the whole
 * PAGE scroll sideways, on every page that shares the shell.
 */
describe('the hello strip does not push the page sideways on a phone', () => {
  const css = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8').replace(
    /\/\*[\s\S]*?\*\//g,
    '',
  );
  const phoneBlocks = [...css.matchAll(/@media[^{]*max-width:\s*768px[^{]*\{([\s\S]*?)\n\}/g)].map(
    (m) => m[1],
  );

  it('lets the greeting text shrink instead of holding the row open', () => {
    // A flex item holding text keeps min-width:auto, which refuses to go below
    // its longest unbreakable word — so a long profile name sets the floor.
    const block = phoneBlocks.find((b) => b.includes('.hello-greeting > span'));
    expect(block, 'no phone rule shrinks .hello-greeting > span').toBeTruthy();
    expect(block).toMatch(/min-width:\s*0/);
  });

  it('breaks a long username that has no space to wrap at', () => {
    const block = phoneBlocks.find((b) => b.includes('.hello-greeting > span'));
    expect(block).toMatch(/overflow-wrap:\s*anywhere/);
  });

  it('shrinks the 176px hero illustration', () => {
    const block = phoneBlocks.find((b) => b.includes('.page-header-icon'));
    expect(block, 'no phone rule shrinks the dashboard hero').toBeTruthy();
  });
});
