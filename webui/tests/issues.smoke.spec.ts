import { expect, test, type Page } from '@playwright/test';

import { shellRouteManifest, type ShellPageId } from '../src/platform/shell/route-manifest';

async function waitForShellRoute(page: Page, pageId: string) {
  if (pageId === 'issues') {
    await expect
      .poll(async () =>
        page.evaluate(() => document.querySelector('.page.active')?.id ?? ''),
      )
      .toBe('webui-react-root');
    return;
  }

  await expect
    .poll(async () =>
      page.evaluate(() => document.querySelector('.page.active')?.id ?? ''),
    )
    .toBe(`${pageId}-page`);
}

function getExpectedNavPage(pageId: ShellPageId): string {
  if (pageId === 'artist-detail') {
    return 'library';
  }

  return pageId;
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

function expectedUrlPattern(path: string): RegExp {
  if (path === '/issues') {
    return /\/issues(?:\?status=open&category=all)?$/;
  }

  return new RegExp(`${path.replace('/', '\\/')}$`);
}

test('direct load activates all known top-level routes', async ({ page, baseURL }) => {
  if (!baseURL) {
    test.skip();
    return;
  }

  for (const route of shellRouteManifest) {
    await page.goto(new URL(route.path, baseURL).toString(), { waitUntil: 'domcontentloaded' });
    await waitForShellRoute(page, route.pageId);
    await expect(page).toHaveURL(expectedUrlPattern(route.path));
    await expectNavHighlight(page, route.pageId);

    if (route.pageId === 'issues') {
      await verifyIssuesRoute(page);
    }
  }
});

test('browser history restores top-level routes', async ({ page, baseURL }) => {
  if (!baseURL) {
    test.skip();
    return;
  }

  await page.goto(new URL('/discover', baseURL).toString(), { waitUntil: 'domcontentloaded' });
  await waitForShellRoute(page, 'discover');

  await page.getByRole('button', { name: 'Issues' }).click();
  await waitForShellRoute(page, 'issues');
  await expect(page).toHaveURL(/\/issues(?:\?status=open&category=all)?$/);

  await page.goBack();
  await waitForShellRoute(page, 'discover');
  await expect(page).toHaveURL(/\/discover$/);

  await page.goForward();
  await waitForShellRoute(page, 'issues');
  await expect(page).toHaveURL(/\/issues(?:\?status=open&category=all)?$/);
});
