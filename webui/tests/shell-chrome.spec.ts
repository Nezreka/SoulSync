import { expect, test, type Page } from '@playwright/test';

// Characterization of the floating shell chrome (global search bar, aura,
// notification bell, helper FAB) and the two full-screen surfaces that must
// suppress it while open: the now-playing modal (body.np-modal-open) and the
// download-missing modal (#1007). These behaviors live in the legacy shell
// today; the assertions are user-visible contracts that must survive the
// React migration unchanged.

const chromeSelectors = ['#gsearch-bar', '#gsearch-aura', '#notif-bell-btn', '#helper-float-btn'];

const viewports = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 375, height: 667 },
] as const;

// Canned metadata responses so the search → album → download-missing-modal
// flow runs the app's real code paths without depending on outside services.
const specAlbum = {
  id: 'chrome-spec-album',
  name: 'Characterization Album',
  artist: 'Spec Artist',
  album_type: 'album',
  release_date: '2024-01-01',
  total_tracks: 2,
  image_url: '',
};

const searchResponse = {
  primary_source: 'deezer',
  db_artists: [],
  spotify_artists: [],
  spotify_albums: [specAlbum],
  spotify_tracks: [],
};

const albumResponse = {
  ...specAlbum,
  images: [],
  artists: [{ id: 'chrome-spec-artist', name: 'Spec Artist' }],
  tracks: [
    {
      id: 'chrome-spec-t1',
      name: 'Track One',
      artists: [{ name: 'Spec Artist' }],
      duration_ms: 120_000,
      track_number: 1,
    },
    {
      id: 'chrome-spec-t2',
      name: 'Track Two',
      artists: [{ name: 'Spec Artist' }],
      duration_ms: 90_000,
      track_number: 2,
    },
  ],
};

async function selectProfile(page: Page, baseURL: string, profileId = 1) {
  const response = await page.request.post(new URL('/api/profiles/select', baseURL).toString(), {
    data: { profile_id: profileId },
  });

  expect(response.ok()).toBe(true);
}

async function openDashboard(page: Page, baseURL: string) {
  await selectProfile(page, baseURL);
  await page.goto(new URL('/dashboard', baseURL).toString(), { waitUntil: 'domcontentloaded' });
  await expect
    .poll(async () => page.evaluate(() => document.querySelector('.page.active')?.id ?? ''))
    .toBe('dashboard-page');
}

async function expectChromeHidden(page: Page) {
  for (const selector of chromeSelectors) {
    await expect(page.locator(selector), `${selector} should be suppressed`).toBeHidden();
  }
}

async function expectChromeVisible(page: Page) {
  for (const selector of chromeSelectors) {
    await expect(page.locator(selector), `${selector} should be back`).toBeVisible();
  }
}

for (const viewport of viewports) {
  test.describe(`shell chrome at ${viewport.name} (${viewport.width}px)`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    test.beforeEach(async ({ page, baseURL }) => {
      test.skip(!baseURL, 'needs a live server');
      await openDashboard(page, baseURL!);
    });

    test('global search activates, shows results, and Escape dismisses', async ({ page }) => {
      await page.route('**/api/enhanced-search', (route) =>
        route.fulfill({ json: searchResponse }),
      );

      await page.locator('#gsearch-input').click();
      await expect(page.locator('#gsearch-bar')).toHaveClass(/active/);
      await expect(page.locator('#gsearch-aura')).toHaveClass(/active/);

      // The source-icon row appears once the controller finishes its async
      // init — which may re-pick the active source. Submitting before that
      // settles loses the race: results render, then init's re-render blanks
      // them for the newly-picked source. A real user sees the icons first.
      await expect(page.locator('#gsearch-source-row *').first()).toBeVisible();

      await page.locator('#gsearch-input').fill('characterization album');
      await page.keyboard.press('Enter');

      const results = page.locator('#gsearch-results');
      await expect(results).toHaveClass(/visible/);
      await expect(results).toContainText('Characterization Album');

      await page.keyboard.press('Escape');
      await expect(page.locator('#gsearch-bar')).not.toHaveClass(/active/);
      await expect(page.locator('#gsearch-aura')).not.toHaveClass(/active/);
      await expect(results).not.toHaveClass(/visible/);
    });

    test('notification bell toggles its panel', async ({ page }) => {
      await page.locator('#notif-bell-btn').click();
      await expect(page.locator('#notif-panel')).toBeVisible();
      await expect(page.locator('#notif-panel')).toContainText('Notifications');

      await page.locator('#notif-bell-btn').click();
      await expect(page.locator('#notif-panel')).toHaveCount(0);
    });

    test('helper button toggles its menu', async ({ page }) => {
      await page.locator('#helper-float-btn').click();
      await expect(page.locator('.helper-menu')).toBeVisible();

      await page.locator('#helper-float-btn').click();
      await expect(page.locator('.helper-menu')).toHaveCount(0);
    });

    test('now-playing modal suppresses the floating chrome while open', async ({ page }) => {
      await expectChromeVisible(page);

      // The mini-player entry point only opens the modal during actual
      // playback, which would drag stream availability into the test.
      // Drive the same function it calls; the contract under test is the
      // modal + chrome behavior, not the playback trigger.
      await page.evaluate(() => {
        (window as typeof window & { openNowPlayingModal: () => void }).openNowPlayingModal();
      });

      await expect(page.locator('#np-modal-overlay')).toBeVisible();
      await expect(page.locator('body')).toHaveClass(/np-modal-open/);
      await expectChromeHidden(page);

      await page.locator('#np-close-btn').click();
      await expect(page.locator('#np-modal-overlay')).toBeHidden();
      await expectChromeVisible(page);
    });

    test('download-missing modal suppresses chrome and keeps footer actions tappable (#1007)', async ({
      page,
    }) => {
      await page.route('**/api/enhanced-search', (route) =>
        route.fulfill({ json: searchResponse }),
      );
      await page.route(`**/api/spotify/album/${specAlbum.id}*`, (route) =>
        route.fulfill({ json: albumResponse }),
      );

      await page.locator('#gsearch-input').click();
      await expect(page.locator('#gsearch-source-row *').first()).toBeVisible();
      await page.locator('#gsearch-input').fill('characterization album');
      await page.keyboard.press('Enter');
      await page
        .locator('#gsearch-results .gsearch-item', { hasText: 'Characterization Album' })
        .click();

      const modal = page.locator('.download-missing-modal');
      await expect(modal).toBeVisible();
      await expect(modal).toContainText('Track One');
      await expectChromeHidden(page);

      // The #1007 regression: floating chrome sat on top of these buttons at
      // mobile widths. Assert each footer action is the top element at its
      // own center point, i.e. a tap actually lands on it.
      const footerActions = ['Begin Analysis', 'Add to Wishlist', 'Export as M3U', 'Close'];
      for (const label of footerActions) {
        const button = modal
          .locator('.download-missing-modal-footer button', { hasText: label })
          .first();
        await button.scrollIntoViewIfNeeded();
        const covered = await button.evaluate((el) => {
          const rect = el.getBoundingClientRect();
          const hit = document.elementFromPoint(
            rect.left + rect.width / 2,
            rect.top + rect.height / 2,
          );
          if (!hit || el.contains(hit) || hit.contains(el)) return '';
          // Toasts deliberately float above modals and self-dismiss —
          // a transient toast over a button is not the #1007 bug.
          if (hit.closest('#toast-container, .toast-compact')) return '';
          return hit.outerHTML.slice(0, 120);
        });
        expect(covered, `"${label}" is covered by another element`).toBe('');
      }

      await modal.getByRole('button', { name: 'Close', exact: true }).click();
      await expect(modal).toBeHidden();
      await expectChromeVisible(page);
    });
  });
}
