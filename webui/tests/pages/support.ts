import { expect, type Page } from '@playwright/test';

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
  // Which element hosts the page depends on the route's manifest kind: a
  // legacy page activates its own `#<pageId>-page` div, a React one activates
  // the shared `#webui-react-root`. Read the kind from the live manifest so
  // this helper stays true as pages flip (hardcoding `${pageId}-page` broke
  // every ported page's spec).
  await expect
    .poll(
      async () =>
        page.evaluate((id) => {
          const route = window.SoulSyncWebRouter?.routeManifest.find((r) => r.pageId === id);
          const active = document.querySelector('.page.active')?.id ?? '';
          const expected = route?.kind === 'react' ? 'webui-react-root' : `${id}-page`;
          return active === expected ? 'ok' : `active=${active} expected=${expected}`;
        }, pageId as never),
      { timeout: 15000 },
    )
    .toBe('ok');
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
