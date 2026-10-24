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
    if (width > 520) {
      const current = dialog.getByRole('columnheader', { name: 'Current', exact: true });
      await expect(current).toBeVisible();
      expect((await current.boundingBox())!.width).toBeGreaterThan(100);
    } else await expect(dialog.locator('td[data-label=Current]').first()).toBeVisible();
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
    await expect(dialog.getByText('File paths', { exact: true })).toHaveCount(0);
    expect(await dialog.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(1);
    const box = await dialog.boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    await page.screenshot({ path: info.outputPath('apply-preview.png') });
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    expect(api.unexpected).toEqual([]);
  });

  test(`${width}px pairing evidence stays readable in narrow panels`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 1000 });
    await installShell(
      page,
      reviewFixture(false, (review) => {
        review.items[0].details = {
          ...review.items[0].details,
          pairing_checks: [
            {
              check: 'Embedded recording ID',
              local: '20d96d4f-02ba-4181-a8ce-433e17719d2e',
              release: '9fed8281-d85a-4e53-a168-34251b0aaa28',
              result: 'conflicts',
              detail: 'Different recording IDs need a decision.',
            },
            {
              check: 'Audio length',
              local: '162.19 seconds',
              release: '156.00 seconds',
              result: 'conflicts',
              detail: '6.19 seconds difference',
            },
          ],
        };
      }),
    );
    await page.goto(`${ORIGIN}/review?mode=all`);
    await page.getByRole('tab', { name: /^Tracks/ }).click();
    await page.locator('[data-review-assignment]').first().click();
    const evidence = page.getByRole('region', { name: 'Why this track was paired' });
    await evidence.scrollIntoViewIfNeeded();
    const heading = evidence.locator('summary').filter({ hasText: 'Embedded recording ID' });
    expect((await heading.boundingBox())!.height).toBeLessThan(45);
    expect(await evidence.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(1);
    await page.screenshot({ path: info.outputPath('pairing-evidence.png') });
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
    const footer = await evidence
      .getByRole('button', { name: 'Show 3 more recordings' })
      .boundingBox();
    const card = await evidence.boundingBox();
    expect(Math.abs(footer!.x + footer!.width / 2 - card!.x - card!.width / 2)).toBeLessThan(2);
    expect(footer!.y + footer!.height).toBeGreaterThan(card!.y + card!.height);
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
  await expect(page.getByRole('button', { name: 'Accept changes', exact: true })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Leave as-is', exact: true })).toBeEnabled();
});

for (const width of [1800, 390]) {
  test(`${width}px track actions and assignment rows have room for their content`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height: 1000 });
    await installShell(
      page,
      reviewFixture(false, (review) => {
        ambiguousReview(review);
        review.items[0].details = {
          ...review.items[0].details,
          expected_title: 'Reflections',
          file_duration_ms: 162190,
          expected_duration_ms: 156000,
          replacement_track: { name: 'Reflections', source_track_id: 'reflections' },
          assignment_options: Array.from({ length: 20 }, (_, i) => ({
            key: `slot-${i}`,
            title: `Long release track title ${i}`,
            artist: 'Daft Punk & another credited artist',
            duration_ms: 156000,
            track_number: i + 1,
            disc_number: 1,
          })),
        };
      }),
    );
    await page.goto(`${ORIGIN}/review?mode=all`);
    await page.getByRole('tab', { name: /^Tracks/ }).click();
    await page.locator('[data-review-assignment]').first().click();
    await page.getByRole('button', { name: 'Replace file…' }).click();
    const replacement = page.getByRole('dialog', { name: 'Replace file', exact: true });
    await expect(replacement.getByRole('radio', { name: /Find another copy/ })).toBeChecked();
    await expect(replacement).toContainText('recoverable quarantine when you apply the plan');
    await page.screenshot({ path: info.outputPath('replacement.png') });
    await replacement.getByRole('button', { name: 'Cancel', exact: true }).click();
    await page.getByRole('button', { name: 'Change match…' }).click();
    const assignment = page.getByRole('dialog', { name: 'Change match' });
    const rows = assignment.locator('[aria-label="Release tracks"] > button');
    await expect(rows).toHaveCount(20);
    const first = await rows.first().boundingBox(),
      second = await rows.nth(1).boundingBox();
    expect(first!.height).toBeGreaterThanOrEqual(56);
    expect(second!.y).toBeGreaterThanOrEqual(first!.y + first!.height);
    expect(await assignment.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(
      1,
    );
    await page.screenshot({ path: info.outputPath('assignments.png') });
  });
}

test('clearing queue filters preserves the selected queue mode', async ({ page }) => {
  await page.setViewportSize({ width: 1800, height: 1000 });
  await installShell(page, reviewFixture(false));
  await page.goto(`${ORIGIN}/review?mode=ready`);
  await page.getByLabel('Find album or track').fill('Northbound');
  await page.getByLabel('Filter by type').selectOption('metadata');
  await page.getByRole('button', { name: 'Clear filters', exact: true }).click();
  await expect(page.getByLabel('Find album or track')).toHaveValue('');
  await expect(page.getByLabel('Filter by type')).toHaveValue('');
  await expect(
    page
      .getByRole('navigation', { name: 'Queue view' })
      .getByRole('button', { name: /Ready to apply/ }),
  ).toHaveAttribute('aria-current', 'page');
});
