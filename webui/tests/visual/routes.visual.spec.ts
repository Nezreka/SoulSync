import type { RequestHandler } from 'msw';

import { expect, test, type Page } from '@playwright/test';

import { routeHandlers, shellHandlers, stateHandlers } from './fixtures';
import { installShell, ORIGIN } from './harness';

/**
 * Screenshot baselines for the main routes, so CSS refactors can prove they
 * changed nothing. Run with `npm run test:visual`; see tests/visual/README.md
 * for when to update the baselines.
 */

interface Shot {
  name: string;
  path: string;
  /** API handlers on top of `shellHandlers`; defaults to `routeHandlers[name]`. */
  handlers?: RequestHandler[];
  /** Runs before the page loads (localStorage, say). */
  init?: (page: Page) => Promise<unknown>;
  /** Runs once the page has settled, before the shot (open a modal, say). */
  act?: (page: Page) => Promise<unknown>;
}

const ROUTES: Shot[] = [
  { name: 'dashboard', path: '/dashboard' },
  { name: 'discover', path: '/discover' },
  {
    name: 'search',
    path: '/search',
    // The query only comes from the input; Enter skips the debounce.
    act: async (page) => {
      const input = page.locator('#enhanced-search-input');
      await input.fill('aphex');
      await input.press('Enter');
      await expect(page.locator('#enhanced-dropdown')).not.toHaveClass(/\bhidden\b/);
      await page.waitForLoadState('networkidle');
    },
  },
  { name: 'library', path: '/library' },
  { name: 'artist-detail', path: '/artist-detail/library/42' },
  { name: 'watchlist', path: '/watchlist' },
  { name: 'wishlist', path: '/wishlist' },
  {
    name: 'settings',
    path: '/settings',
    // Opening /settings runs loadPageData('settings') twice (activatePage,
    // then navigateToPage's idle callback). Each load calls toggleServer(),
    // which schedules a full autosave unless the shared boolean
    // _suppressSettingsAutoSave is up. When one slips through, POST
    // /api/settings gets `{}` 2s later and the shot catches a "Failed to save
    // settings" toast. Nothing here edits a field, so pin the flag on.
    init: (page) =>
      page.addInitScript(() =>
        Object.defineProperty(window, '_suppressSettingsAutoSave', {
          configurable: true,
          get: () => true,
          set: () => {},
        }),
      ),
  },
  { name: 'podcasts', path: '/podcasts' },
  { name: 'audiobooks', path: '/audiobooks' },
  { name: 'issues', path: '/issues' },
  { name: 'stats', path: '/stats' },
  { name: 'chat', path: '/chat' },
  { name: 'automations', path: '/automations' },
];

/** Overlays and states a plain route load doesn't reach. Desktop only. */
const STATES: Shot[] = [
  {
    name: 'issue-detail-modal',
    path: '/issues?issueId=7',
    handlers: [...stateHandlers.issueDetail, ...routeHandlers.issues],
    act: (page) => expect(page.getByRole('dialog')).toBeVisible(),
  },
  {
    name: 'podcast-rss-modal',
    path: '/podcasts',
    handlers: routeHandlers.podcasts,
    act: async (page) => {
      await page.getByTitle('Add custom or private podcast RSS feed URL').click();
      // Match the heading, not role=dialog: older builds render this modal without dialog semantics.
      await expect(page.getByRole('heading', { name: 'Add Podcast by RSS Feed' })).toBeVisible();
    },
  },
  {
    name: 'sidebar-collapsed',
    path: '/dashboard',
    handlers: routeHandlers.dashboard ?? [],
    init: (page) => page.addInitScript(() => localStorage.setItem('sidebarCollapsed', '1')),
  },
  { name: 'issues-empty', path: '/issues', handlers: stateHandlers.issuesEmpty },
  // `{}` for every call is the error state: the list never arrives.
  { name: 'issues-error', path: '/issues', handlers: [] },
  { name: 'stats-error', path: '/stats', handlers: stateHandlers.statsError },
];

/** The key routes also checked at the 768px breakpoint. */
const MOBILE_ROUTES = ['dashboard', 'discover', 'library', 'watchlist', 'settings'];

/**
 * Long pages also get a `<route>-full.png` of everything below the fold. The
 * app scrolls inside `.main-content`, so `fullPage` would miss it; instead the
 * viewport grows until that container no longer scrolls. Scrolling it and
 * shooting each screen was tried and is not repeatable: a scrolled composited
 * layer rasterises text and borders at fractional offsets slightly differently
 * from run to run (seen on discover, about half the runs).
 */
/**
 * Panels masked out of a full-content shot. On discover, the adventurousness
 * dial and the artist map / web hubs draw their text one pixel higher or lower
 * from run to run, at the same layout. Layout rects are identical in both
 * states, and it survives cancelling every animation, dropping will-change and
 * backdrop-filter, and single-threaded raster, so it looks like how Chromium
 * snaps text at fractional offsets rather than anything in the page. The rest
 * of the page is still compared; those two panels aren't, below the fold.
 * The hubs are masked by their section, not `.artmap-hub`: the hub's bottom
 * border lands one row past its own box in some runs, just outside a mask
 * cut to that box.
 */
const FULL_CONTENT_MASKS: Record<string, string[]> = {
  'desktop/discover': ['#adv-wave', '.discovery-zone-section--map-tools'],
  'mobile/discover': ['#adv-wave', '.discovery-zone-section--map-tools'],
};

const FULL_CONTENT = new Set([
  'desktop/dashboard',
  'desktop/discover',
  'desktop/library',
  'desktop/artist-detail',
  'desktop/settings',
  'mobile/dashboard',
  'mobile/discover',
  'mobile/library',
  'mobile/settings',
]);

const VIEWPORTS = [
  { name: 'desktop', width: 1440, height: 900, routes: ROUTES, states: STATES },
  {
    name: 'mobile',
    width: 768,
    height: 1024,
    routes: ROUTES.filter((r) => MOBILE_ROUTES.includes(r.name)),
    states: [],
  },
];

/** Monday 13 January 2025, 10:00 UTC: greetings and dates never move. */
const FROZEN_NOW = new Date('2025-01-13T10:00:00Z');

/** Freeze everything that would make two runs of the same page differ. */
async function stabilise(page: Page) {
  await page.clock.setFixedTime(FROZEN_NOW);
  await page.addInitScript(() => {
    // A returning user: the first-launch help tip pops in on a timer.
    localStorage.setItem('soulsync_setup_welcome_dismissed', '1');
    localStorage.setItem('soulsync_helper_discovered', '1');
    // Seeded Math.random, so shuffled art and random picks repeat.
    let seed = 0x2f6b1d3a;
    Math.random = () => {
      seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  });
}

/**
 * Stop every CSS animation the way the screenshot does (infinite ones
 * cancelled, finite ones finished), but before it, and give the compositor
 * two frames to drop their layers. Reduced motion doesn't stop them all: the
 * sidebar orbs, the discover hero and the artist-map dots keep running, and
 * while they run they hold composited layers that land neighbouring text a
 * pixel up or down depending on timing.
 */
async function freezeAnimations(page: Page) {
  await page.evaluate(async () => {
    for (const animation of document.getAnimations()) {
      if (animation.effect?.getComputedTiming().iterations === Infinity) animation.cancel();
      else animation.finish();
    }
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  });
}

async function settle(page: Page) {
  await expect
    .poll(() => page.evaluate(() => document.querySelector('.page.active')?.id ?? ''), {
      timeout: 15_000,
    })
    .not.toBe('');
  // helper.js badges the help button 2.5s after load; wait it out so the
  // badge is in every shot rather than in some.
  // The generous timeout covers a loaded machine running every shot at once.
  await expect(page.locator('#helper-float-btn')).toHaveClass(/\bhas-badge\b/, {
    timeout: 15_000,
  });
  await page.waitForLoadState('networkidle');
  await page.evaluate(() => document.fonts.ready);
  await freezeAnimations(page);
}

/**
 * Resolve once no request has started or been in flight for `quietMs`.
 * `waitForLoadState('networkidle')` only covers the first load; this also
 * covers fetches a resize or scroll sets off later.
 */
async function networkQuiet(page: Page, quietMs = 500) {
  let inFlight = 0;
  let last = Date.now();
  const start = () => {
    inFlight++;
    last = Date.now();
  };
  const end = () => {
    inFlight--;
    last = Date.now();
  };
  page.on('request', start);
  page.on('requestfinished', end);
  page.on('requestfailed', end);
  try {
    await expect
      .poll(() => inFlight <= 0 && Date.now() - last >= quietMs, {
        intervals: [100],
        timeout: 15_000,
      })
      .toBe(true);
  } finally {
    page.off('request', start);
    page.off('requestfinished', end);
    page.off('requestfailed', end);
  }
}

/** Grow the viewport to the full height of `.main-content`, then shoot it. */
async function shootFullContent(page: Page, key: string) {
  const main = page.locator('.main-content');
  const { width } = page.viewportSize()!;
  // Growing the page can grow the content: min-heights in vh, and shelves
  // that only load once they're in view. Grow, let it load, repeat until it fits.
  for (let attempt = 0; attempt < 8; attempt++) {
    await networkQuiet(page);
    await page.evaluate(
      () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))),
    );
    const overflow = await main.evaluate((el) => el.scrollHeight - el.clientHeight);
    if (overflow <= 0) break;
    await page.setViewportSize({ width, height: page.viewportSize()!.height + overflow });
  }
  expect(
    await main.evaluate((el) => el.scrollHeight - el.clientHeight),
    `${key} still scrolls`,
  ).toBe(0);
  await page.evaluate(() => document.fonts.ready);
  await freezeAnimations(page);
  // A tall page takes longer to capture twice and compare.
  await expect(page).toHaveScreenshot(`${key}-full.png`.split('/'), {
    timeout: 20_000,
    mask: (FULL_CONTENT_MASKS[key] ?? []).map((selector) => page.locator(selector)),
  });
}

async function shoot(page: Page, viewport: string, shot: Shot) {
  await stabilise(page);
  await shot.init?.(page);
  const missing = await installShell(page, [
    ...(shot.handlers ?? routeHandlers[shot.name] ?? []),
    ...shellHandlers,
  ]);
  await page.goto(`${ORIGIN}${shot.path}`);
  await settle(page);
  await shot.act?.(page);
  await freezeAnimations(page);
  expect(missing, 'static files missing; run `npm run build`').toEqual([]);
  // Soft, so a difference above the fold still lets the full shot run and
  // report what changed below it.
  await expect.soft(page).toHaveScreenshot([viewport, `${shot.name}.png`]);
  const key = `${viewport}/${shot.name}`;
  if (FULL_CONTENT.has(key)) await shootFullContent(page, key);
}

for (const viewport of VIEWPORTS) {
  test.describe(`${viewport.name} (${viewport.width}px)`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    for (const shot of [...viewport.routes, ...viewport.states]) {
      test(shot.name, ({ page }) => shoot(page, viewport.name, shot));
    }
  });
}
