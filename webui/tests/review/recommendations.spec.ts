import { expect, test } from '@playwright/test';

import { reviewFixture } from '../fixtures/review';
import { installShell, ORIGIN } from '../visual/harness';

for (const width of [1800, 1280, 390]) {
  for (const ready of [false, true]) {
    test(`${width}px ${ready ? 'saved' : 'manual'} metadata decisions expose identity effects`, async ({
      page,
    }, info) => {
      await page.setViewportSize({ width, height: 1050 });
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await page.addInitScript(() => {
        localStorage.setItem('sidebarCollapsed', '1');
        localStorage.setItem('tutorialShown', 'true');
      });
      const missing = await installShell(page, reviewFixture(ready));
      await page.goto(`${ORIGIN}/review?mode=all`);
      await expect(page.getByRole('heading', { name: 'Album', exact: true })).toBeVisible();
      if (ready) {
        await expect(page.getByText(/All required decisions are planned/)).toBeVisible();
        await expect(
          page.getByText('Remove MusicBrainz recording IDs on 1 track').first(),
        ).toBeVisible();
        await expect(
          page.getByText(
            /passed the album and audio checks|supported by the album and audio evidence/,
          ),
        ).toHaveCount(0);
        await page.getByText(/All required decisions are planned/).scrollIntoViewIfNeeded();
        const summary = page.getByLabel('Ready change summary', { exact: true });
        expect(
          await summary.evaluate((element) => element.scrollWidth - element.clientWidth),
        ).toBeLessThanOrEqual(1);
        await page.screenshot({ path: info.outputPath('ready-plan.png') });
      }
      await page.getByRole('tab', { name: /^Tracks/ }).click();
      const row = page.locator('[data-review-assignment]').first();
      await expect(row).toContainText(ready ? 'Change planned' : 'Review metadata');
      await expect(row).toHaveAttribute('data-status', ready ? 'change' : 'uncertain');
      await expect(
        page.getByText(ready ? '1 proposed change' : '1 metadata decision', { exact: true }),
      ).toBeVisible();
      await expect(
        page.getByRole('button', {
          name: ready ? /1 proposed track change/ : /1 track needing review/,
        }),
      ).toBeVisible();
      await row.scrollIntoViewIfNeeded();
      await page.screenshot({ path: info.outputPath('track-group.png') });
      await page.getByRole('button', { name: 'Release order', exact: true }).click();
      await expect(row).toHaveAttribute('data-status', ready ? 'change' : 'uncertain');
      await row.click();
      for (const label of [
        'Remove Recording ID',
        'Replace MusicBrainz release ID',
      ]) {
        const field = page.getByRole('rowheader', { name: label, exact: true });
        await field.scrollIntoViewIfNeeded();
        await expect(field).toBeVisible();
        expect(await field.locator('xpath=ancestor::details').count()).toBe(0);
      }
      await expect(page.getByText('incorrect-recording-id', { exact: true })).toHaveCount(1);
      await page.getByText('Proposed changes', { exact: true }).scrollIntoViewIfNeeded();
      await page.screenshot({ path: info.outputPath('identity-edits.png') });
      if (!ready) {
        const accept = page.getByRole('button', { name: 'Accept changes', exact: true });
        await accept.scrollIntoViewIfNeeded();
        await expect(accept).toBeEnabled();
      }
      expect(errors).toEqual([]);
      expect(missing).toEqual([]);
    });
  }
}
