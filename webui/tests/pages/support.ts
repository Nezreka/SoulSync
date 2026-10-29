import { expect, type Page } from '@playwright/test';

import { getShellRouteByPageId, type ShellPageId } from '../../src/platform/shell/route-manifest';

export const viewports = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 375, height: 667 },
] as const;

export async function selectProfile(page: Page, baseURL: string, profileId = 1) {
  const response = await page.request.post(new URL('/api/profiles/select', baseURL).toString(), {
    data: { profile_id: profileId },
  });

  expect(response.ok()).toBe(true);
}

export async function gotoShellPage(page: Page, baseURL: string, path: string, pageId: string) {
  await selectProfile(page, baseURL);
  await page.goto(new URL(path, baseURL).toString(), { waitUntil: 'domcontentloaded' });

  const route = getShellRouteByPageId(pageId as ShellPageId);
  const expectedHost = route?.kind === 'react' ? 'webui-react-root' : `${pageId}-page`;
  await expect
    .poll(async () => page.evaluate(() => document.querySelector('.page.active')?.id ?? ''), {
      timeout: 15000,
    })
    .toBe(expectedHost);
}

export async function expectNoHorizontalOverflow(page: Page, label: string) {
  const overflow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(overflow.scrollWidth, `${label} overflows horizontally`).toBeLessThanOrEqual(
    overflow.clientWidth + 1,
  );
}
