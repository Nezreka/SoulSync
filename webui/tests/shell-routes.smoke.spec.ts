import { expect, test, type Page } from '@playwright/test';

import {
  getShellRouteByPageId,
  resolveShellNavPage,
  shellRouteManifest,
  type ShellPageId,
} from '../src/platform/shell/route-manifest';

async function selectProfile(page: Page, baseURL: string, profileId = 1) {
  const response = await page.request.post(new URL('/api/profiles/select', baseURL).toString(), {
    data: { profile_id: profileId },
  });

  expect(response.ok()).toBe(true);
}

async function waitForShellRoute(page: Page, pageId: string) {
  const route = getShellRouteByPageId(pageId as ShellPageId);

  if (route?.kind === 'react') {
    await expect
      .poll(async () => page.evaluate(() => document.querySelector('.page.active')?.id ?? ''), {
        timeout: 15000,
      })
      .toBe('webui-react-root');
    return;
  }

  await expect
    .poll(async () => page.evaluate(() => document.querySelector('.page.active')?.id ?? ''))
    .toBe(`${pageId}-page`);
}

function getExpectedNavPage(pageId: ShellPageId): string {
  return resolveShellNavPage(pageId);
}

async function expectNavHighlight(page: Page, pageId: ShellPageId) {
  const navPage = getExpectedNavPage(pageId);
  const activeNavPage = await page.evaluate(() => {
    return document.querySelector('.nav-button.active')?.getAttribute('data-page') ?? '';
  });

  expect(activeNavPage).toBe(navPage);
}

async function verifyIssuesRoute(page: Page) {
  const appRoot = page.locator('#webui-react-root');
  await expect(appRoot).toBeVisible();
  await expect(page.getByTestId('issues-board')).toContainText('Issues');
}

function expectedUrlPattern(path: string, pageId: ShellPageId): RegExp {
  if (pageId === 'issues') {
    return /\/issues(?:\?status=open&category=all)?$/;
  }

  if (pageId === 'stats') {
    return /\/stats(?:\?range=7d)?$/;
  }

  if (pageId === 'import') {
    return /\/import\/album$/;
  }

  return new RegExp(`${path.replace('/', '\\/')}$`);
}

test('direct load activates all known shell routes', async ({ page, baseURL }) => {
  if (!baseURL) {
    test.skip();
    return;
  }

  await selectProfile(page, baseURL);

  for (const route of shellRouteManifest) {
    const routePage = await page.context().newPage();
    try {
      await routePage.goto(new URL(route.path, baseURL).toString(), {
        waitUntil: 'domcontentloaded',
      });
      await waitForShellRoute(routePage, route.pageId);
      await expect(routePage).toHaveURL(expectedUrlPattern(route.path, route.pageId));
      await expectNavHighlight(routePage, route.pageId);

      if (route.pageId === 'issues') {
        await verifyIssuesRoute(routePage);
      }
    } finally {
      await routePage.close();
    }
  }
});

const invariantViewports = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 375, height: 667 },
] as const;

// Known-noisy failures that predate this suite and don't indicate a broken
// page. Keep each entry narrow and commented; an empty list is the goal.
const allowedConsoleErrors: RegExp[] = [];
const allowedFailedRequests: RegExp[] = [];

interface RouteProblems {
  consoleErrors: string[];
  pageErrors: string[];
  failedRequests: string[];
}

// Network assertions are same-origin only: third-party fetches (brand icons,
// cover art) depend on outside network state and ORB policy, so they'd make
// the suite flaky without saying anything about the app. Console assertions
// skip "Failed to load resource" reports for the same reason — JS-emitted
// errors are what that channel is for, and same-origin request failures are
// already caught by the network listeners.
function watchForProblems(page: Page, baseURL: string): RouteProblems {
  const problems: RouteProblems = { consoleErrors: [], pageErrors: [], failedRequests: [] };
  const origin = new URL(baseURL).origin;
  const sameOrigin = (url: string) => URL.parse(url)?.origin === origin;

  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    const text = message.text();
    if (text.startsWith('Failed to load resource')) return;
    if (allowedConsoleErrors.some((pattern) => pattern.test(text))) return;
    const location = message.location();
    problems.consoleErrors.push(`${text} (${location.url}:${location.lineNumber})`);
  });

  page.on('pageerror', (error) => {
    problems.pageErrors.push(error.message);
  });

  page.on('response', async (response) => {
    if (response.status() < 500 || !sameOrigin(response.url())) return;
    const body = await response.text().catch(() => '<body unavailable>');
    const label = `${response.status()} ${response.request().method()} ${response.url()} — ${body.slice(0, 300)}`;
    if (allowedFailedRequests.some((pattern) => pattern.test(label))) return;
    problems.failedRequests.push(label);
  });

  page.on('requestfailed', (request) => {
    if (!sameOrigin(request.url())) return;
    const label = `FAILED ${request.method()} ${request.url()} (${request.failure()?.errorText})`;
    if (allowedFailedRequests.some((pattern) => pattern.test(label))) return;
    problems.failedRequests.push(label);
  });

  return problems;
}

for (const viewport of invariantViewports) {
  test(`every shell route loads clean at ${viewport.name} (${viewport.width}px)`, async ({
    page,
    baseURL,
  }) => {
    if (!baseURL) {
      test.skip();
      return;
    }

    await selectProfile(page, baseURL);

    for (const route of shellRouteManifest) {
      const routePage = await page.context().newPage();
      await routePage.setViewportSize({ width: viewport.width, height: viewport.height });
      const problems = watchForProblems(routePage, baseURL);

      try {
        await routePage.goto(new URL(route.path, baseURL).toString(), {
          waitUntil: 'domcontentloaded',
        });
        await waitForShellRoute(routePage, route.pageId);
        // Continuous status polling keeps some pages from ever reaching
        // networkidle, so give async content a bounded window and move on.
        await routePage.waitForLoadState('networkidle', { timeout: 4000 }).catch(() => {});

        const overflow = await routePage.evaluate(() => ({
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: document.documentElement.clientWidth,
        }));
        expect
          .soft(overflow.scrollWidth, `${route.path} overflows horizontally at ${viewport.name}`)
          .toBeLessThanOrEqual(overflow.clientWidth + 1);

        expect
          .soft(problems.pageErrors, `${route.path} threw at ${viewport.name}`)
          .toEqual([]);
        expect
          .soft(problems.consoleErrors, `${route.path} logged errors at ${viewport.name}`)
          .toEqual([]);
        expect
          .soft(problems.failedRequests, `${route.path} had failed requests at ${viewport.name}`)
          .toEqual([]);
      } finally {
        await routePage.close();
      }
    }
  });
}

test('browser history restores shell routes', async ({ page, baseURL }) => {
  if (!baseURL) {
    test.skip();
    return;
  }

  await selectProfile(page, baseURL);

  await page.goto(new URL('/discover', baseURL).toString(), { waitUntil: 'domcontentloaded' });
  await waitForShellRoute(page, 'discover');

  await page.evaluate(() => {
    (window as typeof window & { __spaNavMarker?: string }).__spaNavMarker = 'persist';
  });
  await page.getByRole('link', { name: 'Issues' }).click();
  await expect
    .poll(async () =>
      page.evaluate(
        () => (window as typeof window & { __spaNavMarker?: string }).__spaNavMarker ?? null,
      ),
    )
    .toBe('persist');
  await waitForShellRoute(page, 'issues');
  await expect(page).toHaveURL(/\/issues(?:\?status=open&category=all)?$/);

  await page.goBack();
  await waitForShellRoute(page, 'discover');
  await expect(page).toHaveURL(/\/discover$/);

  await page.goForward();
  await waitForShellRoute(page, 'issues');
  await expect(page).toHaveURL(/\/issues(?:\?status=open&category=all)?$/);
});

test('browser history leaves artist detail when going back to library', async ({
  page,
  baseURL,
}) => {
  if (!baseURL) {
    test.skip();
    return;
  }

  await selectProfile(page, baseURL);

  await page.goto(new URL('/library', baseURL).toString(), { waitUntil: 'domcontentloaded' });
  await waitForShellRoute(page, 'library');
  await expect.poll(async () => page.locator('.library-artist-card').count()).toBeGreaterThan(0);

  await page.locator('.library-artist-card').first().click();
  await waitForShellRoute(page, 'artist-detail');
  await expect(page).toHaveURL(/\/artist-detail\/library\/[^/]+$/);

  await page.goBack();
  await waitForShellRoute(page, 'library');
  await expect(page).toHaveURL(/\/library$/);
});
