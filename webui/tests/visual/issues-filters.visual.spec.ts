import { expect, test, type Page } from '@playwright/test';
import { getResponse, http, HttpResponse } from 'msw';

import type { IssueListResponse } from '../../src/routes/issues/-issues.types';

import { routeHandlers, shellHandlers, stateHandlers } from './fixtures';
import { installShell, ORIGIN } from './harness';

// Reuse the visual records, but honor the API filters so the shots show the
// matching row rather than pretending the backend returned an unfiltered list.
const filteredIssues = http.get('*/api/issues', async ({ request }) => {
  const response = await getResponse(routeHandlers.issues, request);
  const payload = (await response!.json()) as IssueListResponse;
  const params = new URL(request.url).searchParams;
  const issues = payload.issues.filter(
    (issue) =>
      (!params.has('status') || issue.status === params.get('status')) &&
      (!params.has('category') || issue.category === params.get('category')) &&
      (!params.has('entity_type') || issue.entity_type === params.get('entity_type')),
  );
  return HttpResponse.json({ ...payload, issues, total: issues.length });
});

async function expectFilters(page: Page, status: string, category: string, entity?: string) {
  await expect
    .poll(() => {
      const params = new URL(page.url()).searchParams;
      return [params.get('status'), params.get('category'), params.get('entity')];
    })
    .toEqual([status, category, entity ?? null]);
  await expect(page.getByRole('combobox', { name: 'Status', exact: true })).toHaveValue(status);
  await expect(page.getByRole('combobox', { name: 'Category', exact: true })).toHaveValue(category);
  await expect(page.getByRole('combobox', { name: 'Item type', exact: true })).toHaveValue(
    entity ?? 'all',
  );
}

async function screenshot(page: Page, viewport: string, state: string) {
  const name = `issues-filters-${state}.png`;
  await expect(page).toHaveScreenshot([viewport, name]);
  const path = test.info().outputPath(name);
  await page.screenshot({ path, animations: 'disabled' });
  await test.info().attach(name, { path, contentType: 'image/png' });
}

for (const viewport of [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 768, height: 1024 },
]) {
  test.describe(`issues filters (${viewport.name})`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    test('combines and clears filters, retaining them through history and issue details', async ({
      page,
    }) => {
      await page.clock.setFixedTime(new Date('2025-01-13T10:00:00Z'));
      await page.addInitScript(() => {
        localStorage.setItem('soulsync_setup_welcome_dismissed', '1');
        localStorage.setItem('soulsync_helper_discovered', '1');
      });
      const missing = await installShell(page, [
        filteredIssues,
        ...routeHandlers.issues,
        ...stateHandlers.issueDetail,
        ...routeHandlers.stats,
        ...shellHandlers,
      ]);
      const requests: URL[] = [];
      page.on('request', (request) => {
        const url = new URL(request.url());
        if (url.pathname === '/api/issues') requests.push(url);
      });

      // Keep a previous route so Back can prove filter changes replace history.
      await page.goto(`${ORIGIN}/stats`);
      await expect(page.locator('#helper-float-btn')).toHaveClass(/\bhas-badge\b/);
      await page.goto(`${ORIGIN}/issues`);
      await expect(page.getByTestId('issue-card-7')).toBeVisible();
      await expect(page.locator('#helper-float-btn')).toHaveClass(/\bhas-badge\b/);
      await page.waitForLoadState('networkidle');
      await screenshot(page, viewport.name, 'default');

      await page.getByRole('combobox', { name: 'Status', exact: true }).selectOption('in_progress');
      await expectFilters(page, 'in_progress', 'all');
      await page
        .getByRole('combobox', { name: 'Category', exact: true })
        .selectOption('audio_quality');
      await expectFilters(page, 'in_progress', 'audio_quality');
      await page.getByRole('combobox', { name: 'Item type', exact: true }).selectOption('track');
      await expectFilters(page, 'in_progress', 'audio_quality', 'track');
      await expect
        .poll(() => requests.at(-1)?.searchParams.toString())
        .toBe('limit=50&status=in_progress&category=audio_quality&entity_type=track');
      await expect(page.getByTestId('issue-card-8')).toBeVisible();
      await expect(page.getByTestId('issue-card-7')).toHaveCount(0);
      await page.waitForLoadState('networkidle');
      await screenshot(page, viewport.name, 'combined');

      // Clearing one filter must not clear either of the others.
      await page.getByRole('combobox', { name: 'Item type', exact: true }).selectOption('all');
      await expectFilters(page, 'in_progress', 'audio_quality');
      await page.getByRole('combobox', { name: 'Category', exact: true }).selectOption('all');
      await expectFilters(page, 'in_progress', 'all');
      await page.getByRole('combobox', { name: 'Status', exact: true }).selectOption('all');
      await expectFilters(page, 'all', 'all');
      await expect.poll(() => requests.at(-1)?.searchParams.toString()).toBe('limit=50');

      await page.goBack();
      await expect.poll(() => new URL(page.url()).pathname).toBe('/stats');
      await page.goForward();
      await expectFilters(page, 'all', 'all');

      await page.getByTestId('issue-card-7').click();
      await expect(page.getByRole('dialog')).toContainText('Issue #7');
      await expect(page).toHaveURL(/status=all&category=all&issueId=7/);
      await page.getByRole('button', { name: 'Close issue detail' }).click();
      await expect(page.getByRole('dialog')).toHaveCount(0);
      await expectFilters(page, 'all', 'all');

      expect(missing, 'static files missing; run npm run build').toEqual([]);
    });
  });
}
