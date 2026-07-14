import { expect, test } from '@playwright/test';

import { gotoShellPage, viewports } from './support';

// Characterization of the legacy Search page (/search) ahead of its React
// port — the first warm-up port target. Asserts user-visible behavior only,
// with the metadata endpoint mocked so the app's real handler chain runs
// deterministically. The basic (Soulseek) section's live-stream flow is out
// of scope here; only its default hidden/shown state is pinned.

const searchResponse = {
  primary_source: 'deezer',
  db_artists: [],
  spotify_artists: [{ id: 'warmup-artist', name: 'Warmup Artist', image_url: '' }],
  spotify_albums: [
    {
      id: 'warmup-album',
      name: 'Warmup Album',
      artist: 'Warmup Artist',
      album_type: 'album',
      release_date: '2024-01-01',
      total_tracks: 2,
      image_url: '',
    },
  ],
  spotify_tracks: [
    {
      id: 'warmup-track',
      name: 'Warmup Track',
      artist: 'Warmup Artist',
      album: 'Warmup Album',
      duration_ms: 120_000,
      image_url: '',
    },
  ],
};

for (const viewport of viewports) {
  test.describe(`search page at ${viewport.name} (${viewport.width}px)`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    test.beforeEach(async ({ page, baseURL }) => {
      test.skip(!baseURL, 'needs a live server');
      await gotoShellPage(page, baseURL!, '/search', 'search');
    });

    test('enhanced search is the default section and renders results', async ({ page }) => {
      await expect(page.locator('#enhanced-search-section')).toBeVisible();
      await expect(page.locator('#basic-search-section')).toBeHidden();

      await page.route('**/api/enhanced-search', (route) =>
        route.fulfill({ json: searchResponse }),
      );

      // The source-icon row appears once the search controller finishes its
      // async init; submitting before that risks the active source being
      // re-picked out from under the request (same race as the global bar).
      await expect(page.locator('#enh-source-row *').first()).toBeVisible();

      await page.locator('#enhanced-search-input').fill('warmup album');
      await page.keyboard.press('Enter');

      await expect(page.locator('#enh-albums-section')).toBeVisible();
      await expect(page.locator('#enh-albums-list')).toContainText('Warmup Album');
      await expect(page.locator('#enh-tracks-section')).toContainText('Warmup Track');
    });

    test('clicking an album result opens the download-missing modal', async ({ page }) => {
      await page.route('**/api/enhanced-search', (route) =>
        route.fulfill({ json: searchResponse }),
      );
      await page.route('**/api/spotify/album/warmup-album*', (route) =>
        route.fulfill({
          json: {
            id: 'warmup-album',
            name: 'Warmup Album',
            album_type: 'album',
            images: [],
            release_date: '2024-01-01',
            total_tracks: 1,
            artists: [{ id: 'warmup-artist', name: 'Warmup Artist' }],
            tracks: [
              {
                id: 'warmup-t1',
                name: 'Warmup Track One',
                artists: [{ name: 'Warmup Artist' }],
                duration_ms: 120_000,
                track_number: 1,
              },
            ],
          },
        }),
      );

      await expect(page.locator('#enh-source-row *').first()).toBeVisible();
      await page.locator('#enhanced-search-input').fill('warmup album');
      await page.keyboard.press('Enter');

      await page
        .locator('#enh-albums-list .enh-compact-item', { hasText: 'Warmup Album' })
        .click();

      const modal = page.locator('.download-missing-modal');
      await expect(modal).toBeVisible();
      await expect(modal).toContainText('Warmup Track One');

      await modal.getByRole('button', { name: 'Close', exact: true }).click();
      await expect(modal).toBeHidden();
    });
  });
}
