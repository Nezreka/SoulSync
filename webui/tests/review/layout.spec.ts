import { expect, test } from '@playwright/test';
import { http, HttpResponse } from 'msw';

import type { ReviewCase } from '../../src/routes/review/-review.api';

import { ambiguousReview, reviewFixture } from '../fixtures/review';
import { installShell, ORIGIN } from '../visual/harness';

for (const width of [1800, 1280, 768, 390, 320]) {
  test(`${width}px uncertainty, comparisons and catalogue provenance`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.addInitScript(() => localStorage.setItem('tutorialShown', 'true'));
    await installShell(page, reviewFixture(false, ambiguousReview));
    await page.goto(`${ORIGIN}/review?mode=all`);
    await expect(page.getByRole('heading', { name: 'Album', exact: true })).toBeVisible();
    const header = page.locator('[class*="headerTools"]');
    expect(await header.evaluate((e) => e.scrollWidth - e.clientWidth)).toBeLessThanOrEqual(1);
    if (width <= 1450) {
      await expect(page.getByLabel('Find album or track')).toBeHidden();
      await page.getByRole('button', { name: /Browse albums/ }).click();
      await expect(page.getByLabel('Find album or track')).toBeVisible();
      await page
        .locator('#review-album-browser')
        .getByRole('button', { name: /Album Artist/ })
        .click();
      await expect(page.getByRole('button', { name: /Browse albums/ })).toBeFocused();
      await expect(page.getByLabel('Find album or track')).toBeHidden();
    }
    await page.getByRole('tab', { name: /^Tracks/ }).click();
    const row = page.locator('[data-review-assignment]').first();
    await row.click();
    await expect(row).toHaveAttribute('data-status', 'uncertain');
    const detail = page.getByRole('complementary', { name: 'Selected track details' });
    const reason = detail.getByText('Two recording identities have equal fingerprint scores');
    await expect(reason).toBeVisible();
    expect(
      await reason.evaluate((e) =>
        Boolean(
          e.compareDocumentPosition(
            document.querySelector('[aria-label="Selected track details"] table')!,
          ) & Node.DOCUMENT_POSITION_FOLLOWING,
        ),
      ),
    ).toBeTruthy();
    const table = detail.getByRole('table');
    const cells = table.locator('tbody tr').first().locator('td');
    const current = await cells.nth(0).boundingBox();
    const planned = await cells.nth(2).boundingBox();
    expect(Math.abs(current!.y - planned!.y)).toBeLessThan(1);
    expect(planned!.x).toBeGreaterThan(current!.x + current!.width);
    expect(await table.evaluate((e) => e.scrollWidth - e.clientWidth)).toBeLessThanOrEqual(1);
    await expect(detail.getByText('Catalogue Album', { exact: true })).toBeVisible();
    await expect(detail.getByText('Japanese edition', { exact: true })).toBeVisible();
    await expect(detail.getByText('Local album (deluxe)', { exact: true })).toBeVisible();
    await detail.scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath('review-detail.png') });
    if (width <= 1280) {
      await page.getByRole('button', { name: /Back to tracks/ }).click();
      await expect(row).toBeFocused();
    }
  });
}

test('compact queue retains filters and paging when browsing is closed', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 1000 });
  let base!: ReviewCase;
  const handlers = reviewFixture(false, (c) => {
    base = c;
  });
  const cases = Array.from({ length: 32 }, (_, i) => ({
    ...base,
    case_id: `album:${i}`,
    album: `Album ${i}`,
  }));
  await installShell(page, [
    http.get('*/api/repair/review-queue/summary', () =>
      HttpResponse.json({
        all: cases.length,
        review: cases.length,
        ready: 0,
        files: 0,
        waiting: 0,
        ready_total: 0,
        ready_albums_total: 0,
      }),
    ),
    http.get('*/api/repair/review-queue', ({ request }) => {
      const url = new URL(request.url);
      const pageIndex = Number(url.searchParams.get('page') || 0);
      const filtered = cases.filter((c) => c.album.includes(url.searchParams.get('q') || ''));
      return HttpResponse.json({
        items: filtered.slice(pageIndex * 30, (pageIndex + 1) * 30),
        total: filtered.length,
        page: pageIndex,
        limit: 30,
      });
    }),
    http.get('*/api/repair/review-queue/:id', ({ params }) =>
      HttpResponse.json(cases.find((c) => c.case_id === params.id)),
    ),
    ...handlers,
  ]);
  await page.goto(`${ORIGIN}/review?mode=all`);
  await page.getByRole('button', { name: /Browse albums/ }).click();
  const browser = page.locator('#review-album-browser');
  await browser.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(browser.getByText('Page 2 of 2')).toBeVisible();
  await browser.getByRole('button', { name: /Album 31 Artist/ }).click();
  await expect(page.getByRole('heading', { name: 'Album 31', exact: true })).toBeVisible();
  await page.getByRole('button', { name: /Previous album/ }).click();
  await expect(page.getByRole('heading', { name: 'Album 30', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /Browse albums/ })).not.toBeFocused();
  await page.getByRole('button', { name: /Browse albums/ }).click();
  await expect(browser.getByText('Page 2 of 2')).toBeVisible();
  await page.getByLabel('Find album or track').fill('Album 3');
  await expect(browser.getByText('Page 1 of 1')).toBeVisible();
  await browser.getByRole('button', { name: /Album 3 Artist/ }).click();
  await page.getByRole('button', { name: /Browse albums/ }).click();
  await expect(page.getByLabel('Find album or track')).toHaveValue('Album 3');
  await expect(browser.getByRole('button', { name: /Album \d+ Artist/ })).toHaveCount(3);
});

test('album metadata decisions lead to their affected tracks before approval', async ({ page }) => {
  await installShell(page, reviewFixture(false));
  await page.goto(`${ORIGIN}/review?mode=all`);
  const board = page.getByLabel('Album decisions', { exact: true });
  await board.getByRole('button', { name: /Metadata changes/ }).click();
  await expect(page.getByRole('tab', { name: /^Tracks/ })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByLabel('Show tracks')).toHaveValue('decision:metadata');
  await expect(page.locator('[data-review-assignment]')).toHaveCount(1);
  await page.locator('[data-review-assignment]').click();
  await expect(page.getByText('Recording ID needs review', { exact: true })).toBeVisible();
});

test('plans awaiting acquisition still allow independent approved edits', async ({
  page,
}) => {
  await installShell(
    page,
    reviewFixture(true, (review) => {
      review.plan!.status = 'ready';
      review.plan!.can_apply_ready = true;
      review.decisions!.push({
        id: 'waiting',
        category: 'pending_fulfillment',
        label: 'Waiting',
        risk: 'blocked',
        finding_ids: [],
        action_count: 1,
        summary: 'Waiting for a missing track',
      });
      review.album_plan!.assignments.push({
        status: 'missing',
        finding_id: null,
        slot_title: 'Bonus track',
        slot_track_number: 2,
        slot_disc_number: 1,
        fulfillment_state: 'wishlisted',
      });
    }),
  );
  await page.goto(`${ORIGIN}/review?mode=all`);
  await expect(page.getByText('Waiting for missing tracks', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Apply 1 ready change', exact: true }),
  ).toHaveCount(1);
  await expect(page.getByLabel('Ready change summary', { exact: true })).toHaveCount(1);
  await expect(page.getByLabel('Proposed metadata by field', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Decisions complete' })).toHaveCount(0);
});
