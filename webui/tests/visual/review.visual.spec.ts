import { expect, test } from '@playwright/test';

import { ambiguousReview, reviewFixture } from '../fixtures/review';
import { installShell, ORIGIN } from './harness';

// Separate desktop/tablet/phone fixtures protect decision hierarchy as well as
// the shared shell. All data is synthetic; no library mutation is performed.
for (const width of [1800, 768, 390]) {
  for (const state of ['ready', 'uncertain', 'release'] as const) {
    test(`review ${state} ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 1050 });
      await page.clock.setFixedTime(new Date('2025-01-13T10:00:00Z'));
      await page.addInitScript(() => {
        localStorage.setItem('soulsync_setup_welcome_dismissed', '1');
        localStorage.setItem('soulsync_helper_discovered', '1');
      });
      const missing = await installShell(
        page,
        reviewFixture(state === 'ready', state === 'uncertain' ? ambiguousReview : undefined),
      );
      await page.goto(`${ORIGIN}/review?mode=all`);
      await expect(page.getByRole('heading', { name: 'Album', exact: true })).toBeVisible();
      await expect(page.locator('#helper-float-btn')).toHaveClass(/\bhas-badge\b/);
      if (state === 'uncertain') {
        await page.getByRole('tab', { name: /^Tracks/ }).click();
        await page.locator('[data-review-assignment]').first().click();
        await page.getByText('Why this needs review', { exact: true }).scrollIntoViewIfNeeded();
      } else if (state === 'release') {
        await page.getByRole('tab', { name: 'Release', exact: true }).click();
        await page.getByRole('complementary', { name: 'release context' }).scrollIntoViewIfNeeded();
      }
      await page.waitForLoadState('networkidle');
      await page.evaluate(async () => {
        await document.fonts.ready;
        for (const a of document.getAnimations()) {
          if (a.effect?.getComputedTiming().iterations === Infinity) a.cancel();
          else a.finish();
        }
      });
      expect(missing).toEqual([]);
      await expect(page).toHaveScreenshot(['review', `${state}-${width}.png`]);
    });
  }
}

// MetadataComparison is also used by Library's real tag preview modal.
for (const width of [1440, 390]) {
  test(`shared Library tag comparison ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1050 });
    await page.addInitScript(() => {
      localStorage.setItem('soulsync-library-view-mode:1', 'enhanced');
      localStorage.setItem('soulsync_setup_welcome_dismissed', '1');
      localStorage.setItem('soulsync_helper_discovered', '1');
    });
    const { http, HttpResponse } = await import('msw');
    const { routeHandlers, shellHandlers } = await import('./fixtures');
    await installShell(page, [
      ...shellHandlers,
      ...routeHandlers['artist-detail'],
      http.get('*/api/library/artist/42/enhanced', () =>
        HttpResponse.json({
          success: true,
          artist: { id: 42, name: 'Aphex Twin' },
          albums: [
            {
              id: 1,
              title: 'Selected Ambient Works',
              record_type: 'album',
              tracks: [
                {
                  id: 9,
                  title: 'Xtal',
                  track_number: 1,
                  duration: 200000,
                  file_path: '/music/01 - Xtal.flac',
                },
              ],
            },
          ],
        }),
      ),
      http.get('*/api/library/track/9/tag-preview', () =>
        HttpResponse.json({
          success: true,
          has_changes: true,
          server_type: 'navidrome',
          diff: [
            { field: 'Title', file_value: 'XTAL', db_value: 'Xtal', changed: true },
            {
              field: 'Album',
              file_value: '',
              db_value: 'Selected Ambient Works 85-92',
              changed: true,
            },
            { field: 'Artist', file_value: 'Aphex Twin', db_value: 'Aphex Twin', changed: false },
          ],
        }),
      ),
    ]);
    await page.goto(`${ORIGIN}/artist-detail/library/42`);
    await page.locator('#enhanced-album-row-1').click();
    const actions = page.getByRole('button', {
      name: width < 600 ? 'Actions for Xtal' : 'More actions for Xtal',
    });
    await actions.scrollIntoViewIfNeeded();
    await page.waitForLoadState('networkidle');
    await actions.click();
    if (width < 600) await page.getByRole('button', { name: /Write Tags$/ }).click();
    else await page.getByRole('menuitem', { name: 'Write tags to file' }).click();
    const modal = page.locator('#tag-preview-overlay');
    await expect(modal.getByText('DB Value', { exact: true })).toBeVisible({
      visible: width > 520,
    });
    await expect(modal.getByRole('cell', { name: 'Unchanged', exact: true })).toBeVisible();
    const cells = modal.getByRole('row', { name: /Title/ }).getByRole('cell');
    const current = await cells.nth(0).boundingBox();
    const planned = await cells.nth(2).boundingBox();
    expect(Math.abs(current!.y - planned!.y)).toBeLessThan(1);
    expect(planned!.x).toBeGreaterThan(current!.x + current!.width);
    await expect(page.locator('#helper-float-btn')).toHaveClass(/\bhas-badge\b/);
    await page.evaluate(() => document.fonts.ready);
    await expect(modal).toHaveScreenshot(['review', `shared-library-tags-${width}.png`]);
    await modal.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(modal).toHaveCount(0);
  });
}
