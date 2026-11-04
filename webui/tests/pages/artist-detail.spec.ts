import { expect, test, type Page } from '@playwright/test';

import { selectProfile, viewports } from './support';

async function firstLibraryArtist(page: Page, baseURL: string) {
  const response = await page.request.get(
    new URL('/api/library/v2/artists?page=1&sort=name&monitored=all', baseURL).toString(),
  );
  expect(response.ok()).toBe(true);
  const artist = (await response.json()).artists?.[0];
  test.skip(!artist, 'library has no artists to deep-link to');
  return artist as { id: number; name: string };
}

for (const viewport of viewports) {
  test.describe(`Library v2 artist links at ${viewport.name} (${viewport.width}px)`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    test.beforeEach(({ baseURL }) => test.skip(!baseURL, 'needs a live server'));

    test('opens an owned artist through ?artist=', async ({ page, baseURL }) => {
      const artist = await firstLibraryArtist(page, baseURL!);
      await selectProfile(page, baseURL!);
      await page.goto(new URL(`/library?artist=${artist.id}`, baseURL!).toString(), {
        waitUntil: 'domcontentloaded',
      });

      await expect(page).toHaveURL(new RegExp(`/library\\?artist=${artist.id}`));
      await expect(page.getByRole('heading', { name: artist.name })).toBeVisible();
    });

    test('redirects provider artist links into ?discover=', async ({ page, baseURL }) => {
      await selectProfile(page, baseURL!);
      await page.route('**/api/artist-detail/provider-id*', (route) =>
        route.fulfill({
          json: {
            success: true,
            artist: { id: 'provider-id', name: 'Provider Artist', image_url: '', genres: [] },
            discography: { albums: [], eps: [], singles: [] },
          },
        }),
      );
      await page.goto(
        new URL('/artist-detail/spotify/provider-id?name=Provider%20Artist', baseURL!).toString(),
        { waitUntil: 'domcontentloaded' },
      );

      await expect(page).toHaveURL(/\/library\?.*discover=.*spotify.*provider-id/);
      await expect(page.getByRole('heading', { name: 'Provider Artist' })).toBeVisible();
    });

    test('browser history restores the owned artist view', async ({ page, baseURL }) => {
      const artist = await firstLibraryArtist(page, baseURL!);
      await selectProfile(page, baseURL!);
      await page.goto(new URL('/library', baseURL!).toString(), { waitUntil: 'domcontentloaded' });
      await page.goto(new URL(`/library?artist=${artist.id}`, baseURL!).toString(), {
        waitUntil: 'domcontentloaded',
      });
      await expect(page.getByRole('heading', { name: artist.name })).toBeVisible();

      await page.goBack();
      await expect(page).toHaveURL(/\/library$/);
      await expect(page.getByRole('heading', { name: 'Library' })).toBeVisible();

      await page.goForward();
      await expect(page).toHaveURL(new RegExp(`artist=${artist.id}`));
      await expect(page.getByRole('heading', { name: artist.name })).toBeVisible();
    });
  });
}
