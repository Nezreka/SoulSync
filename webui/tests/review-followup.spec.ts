import { expect, test } from '@playwright/test';
import { http, HttpResponse } from 'msw';

import { ambiguousReview, reviewFixture } from './fixtures/review';
import { acceptanceApi } from './fixtures/review-acceptance';
import { installShell, ORIGIN } from './visual/harness';

for (const width of [1800, 390]) {
  test(`${width}px grouped apply preview and quarantine details`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 1000 });
    const api = acceptanceApi('duplicate_saved');
    await installShell(page, api.handlers);
    await page.goto(`${ORIGIN}/review?mode=all`);
    await page.getByRole('button', { name: /^Apply 2 ready changes$/ }).click();
    const dialog = page.getByRole('dialog', { name: 'Apply ready album plans' });
    await expect(dialog).toContainText('2 planned actions');
    await dialog.locator('summary').filter({ hasText: 'Metadata fills' }).click();
    await expect(dialog.getByRole('columnheader', { name: 'Current', exact: true })).toBeVisible();
    await expect(
      dialog.getByRole('columnheader', { name: 'Quarantine', exact: true }),
    ).toBeVisible();
    const helpButton = dialog.getByRole('button', { name: 'About recoverable quarantine' });
    expect(
      await helpButton.evaluate((el) => ({
        width: getComputedStyle(el).width,
        height: getComputedStyle(el).height,
      })),
    ).toEqual({ width: '18px', height: '18px' });
    await dialog.getByText('File paths', { exact: true }).click();
    await expect(
      dialog.getByText('/music/Acceptance Artist/Northbound/01 - Home.copy.flac', { exact: true }),
    ).toBeVisible();
    expect(await dialog.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(1);
    const box = await dialog.boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    await page.screenshot({ path: info.outputPath('apply-preview.png') });
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    expect(api.unexpected).toEqual([]);
  });

  test(`${width}px bounded audio evidence and opaque accessible help`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 1000 });
    await installShell(
      page,
      reviewFixture(false, (review) => {
        ambiguousReview(review);
        review.items[0].details = {
          ...review.items[0].details,
          ambiguity_reasons: ['Another local file fits this release track'],
          recording_mbid: 'selected',
          competitors: [
            {
              kind: 'local_file',
              file_path: '/music/Album/15 - Home [DLX].flac',
              assigned_title: 'Home [DLX]',
            },
          ],
          acoustid: {
            status: 'ok',
            recordings: Array.from({ length: 8 }, (_, i) => ({
              title: `Home version ${i}`,
              artist: 'Artist',
              score: 0.99,
              mbid: i === 7 ? 'selected' : `id-${i}`,
            })),
          },
        };
      }),
    );
    await page.goto(`${ORIGIN}/review?mode=all`);
    await page.getByRole('tab', { name: /^Tracks/ }).click();
    await page.locator('[data-review-assignment]').first().click();
    const evidence = page.getByRole('region', { name: 'Audio identity', exact: true });
    await expect(evidence.getByRole('link', { name: /recording/i })).toHaveCount(5);
    await expect(evidence.getByRole('link', { name: /Selected recording/ })).toBeVisible();
    await expect(evidence.getByText('15 - Home [DLX].flac', { exact: true })).toBeVisible();
    await evidence.getByRole('button', { name: 'Show 3 more recordings' }).click();
    await expect(evidence.getByRole('link', { name: /recording/i })).toHaveCount(8);
    await evidence.getByRole('button', { name: 'Show fewer recordings' }).click();
    await evidence.getByRole('button', { name: 'About AcoustID audio evidence' }).click();
    const help = page.getByRole('dialog', { name: 'About AcoustID audio evidence' });
    await expect(help).toBeVisible();
    const style = await help.evaluate((el) => ({
      background: getComputedStyle(el).backgroundColor,
      opacity: getComputedStyle(el).opacity,
    }));
    expect(style.background).toBe('rgb(24, 24, 29)');
    expect(style.opacity).toBe('1');
    await page.screenshot({ path: info.outputPath('audio-help.png') });
    await page.keyboard.press('Escape');
    await page.getByRole('tab', { name: /^Release/ }).click();
    const badge = page.locator('[data-slot="badge"]').filter({ hasText: /^CD$/ }).first();
    await badge.hover();
    await expect(page.getByRole('tooltip')).toContainText('Release format');
    expect(await badge.evaluate((el) => getComputedStyle(el).cursor)).toBe('default');
    expect(
      await page.getByRole('tooltip').evaluate((el) => getComputedStyle(el).backgroundColor),
    ).toBe('rgb(24, 24, 29)');
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
    ).toBeLessThanOrEqual(1);
  });
}

test('another album stays editable while an apply operation is running', async ({ page }) => {
  await installShell(page, [
    http.get('*/api/repair/bulk-fix/status', () =>
      HttpResponse.json({
        running: true,
        operation_id: 'other-operation',
        review_approved: true,
        review_case_ids: ['album:other'],
        review_scope: 'album',
        phase: 'organizing',
        total: 40,
        done: 40,
      }),
    ),
    ...reviewFixture(false),
  ]);
  await page.goto(`${ORIGIN}/review?mode=all`);
  await expect(page.getByRole('button', { name: 'Reanalyze album', exact: true })).toBeEnabled();
  await page.getByRole('tab', { name: /^Tracks/ }).click();
  await page.locator('[data-review-assignment]').first().click();
  await expect(
    page.getByRole('button', { name: 'Use proposed change', exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole('button', { name: 'Keep current values', exact: true }),
  ).toBeEnabled();
});
