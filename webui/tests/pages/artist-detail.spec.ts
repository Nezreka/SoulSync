import { expect, test, type Page } from '@playwright/test';

import { gotoShellPage, selectProfile, viewports } from './support';

// Characterization of artist-detail deep links. The URL is already owned by
// the React router (src/routes/artist-detail/$source/$id.tsx) which hands
// off to the legacy page body — the next step of upstream's own migration
// sequence is porting that body, so pin the handoff contract now: a direct
// deep-link load renders the artist, and history moves cleanly between the
// library and the detail page.

async function firstLibraryArtist(page: Page, baseURL: string) {
  const response = await page.request.get(
    new URL('/api/library/artists?limit=1', baseURL).toString(),
  );
  expect(response.ok()).toBe(true);
  const data = await response.json();
  const artist = data.artists?.[0];
  test.skip(!artist, 'library has no artists to deep-link to');
  return artist as { id: string; name: string };
}

async function waitForArtistDetail(page: Page, artistName: string) {
  await expect
    .poll(async () => page.evaluate(() => document.querySelector('.page.active')?.id ?? ''), {
      timeout: 15000,
    })
    .toBe('artist-detail-page');
  await expect(page.locator('#artist-detail-name')).toHaveText(artistName, { timeout: 15000 });
}

for (const viewport of viewports) {
  test.describe(`artist-detail deep link at ${viewport.name} (${viewport.width}px)`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    test.beforeEach(({ baseURL }) => {
      test.skip(!baseURL, 'needs a live server');
    });

    test('direct load renders the artist page body', async ({ page, baseURL }) => {
      const artist = await firstLibraryArtist(page, baseURL!);

      await selectProfile(page, baseURL!);
      await page.goto(
        new URL(
          `/artist-detail/library/${artist.id}?name=${encodeURIComponent(artist.name)}`,
          baseURL!,
        ).toString(),
        { waitUntil: 'domcontentloaded' },
      );

      await waitForArtistDetail(page, artist.name);
      await expect(page).toHaveURL(new RegExp(`/artist-detail/library/${artist.id}`));
    });

    test('history moves between library and artist detail', async ({ page, baseURL }) => {
      const artist = await firstLibraryArtist(page, baseURL!);

      await gotoShellPage(page, baseURL!, '/library', 'library');
      await page.goto(
        new URL(
          `/artist-detail/library/${artist.id}?name=${encodeURIComponent(artist.name)}`,
          baseURL!,
        ).toString(),
        { waitUntil: 'domcontentloaded' },
      );
      await waitForArtistDetail(page, artist.name);

      await page.goBack();
      await expect
        .poll(async () => page.evaluate(() => document.querySelector('.page.active')?.id ?? ''))
        .toBe('library-page');

      await page.goForward();
      await waitForArtistDetail(page, artist.name);
    });
  });
}
