import { expect, test, type Page } from '@playwright/test';

import { expectNoHorizontalOverflow, gotoShellPage, viewports } from './support';

// Nav items that scroll beneath the tall sticky sidebar header aren't
// tappable there (Playwright's minimal auto-scroll lands right under it at
// 375px); center the item first, like a user scrolling to what they tap.
async function clickNavChild(page: Page, text: string) {
  const child = page.locator('.docs-nav-child', { hasText: text });
  await expect(child).toBeVisible();
  await child.evaluate((el) => el.scrollIntoView({ block: 'center' }));
  await child.click();
}

// Characterization of the legacy Help/Docs page (/help) ahead of its React
// port. The page is one long scrollable document: every .docs-section is
// rendered up front, the sidebar nav scrolls to anchors, and the filter box
// hides non-matching sections rather than re-querying anything.

for (const viewport of viewports) {
  test.describe(`help page at ${viewport.name} (${viewport.width}px)`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    test.beforeEach(async ({ page, baseURL }) => {
      test.skip(!baseURL, 'needs a live server');
      await gotoShellPage(page, baseURL!, '/help', 'help');
    });

    test('renders the sidebar nav and full document', async ({ page }) => {
      await expect
        .poll(async () => page.locator('#docs-nav .docs-nav-section').count())
        .toBeGreaterThan(3);
      await expect(page.locator('#docs-nav')).toContainText('Getting Started');
      await expect
        .poll(async () => page.locator('#docs-content .docs-section').count())
        .toBeGreaterThan(3);
    });

    test('nav child scrolls its section into view', async ({ page }) => {
      // The first section ships expanded, so its children are immediately
      // clickable — no need to toggle the title (which sits close enough to
      // the tall sticky sidebar header to be a fiddly click target anyway).
      await expect(
        page.locator('.docs-nav-section-title', { hasText: 'Getting Started' }),
      ).toHaveClass(/expanded/);

      await clickNavChild(page, 'Docker & Deployment');

      const target = page.locator('#gs-docker');
      await expect(target).toBeInViewport();
    });

    test('filter box narrows the nav and document to matching sections', async ({ page }) => {
      const allNavSections = await page.locator('#docs-nav .docs-nav-section').count();

      await page.locator('#docs-search-input').fill('docker');
      await expect
        .poll(async () => page.locator('#docs-nav .docs-nav-section:visible').count())
        .toBeLessThan(allNavSections);
      await expect(
        page.locator('#docs-nav .docs-nav-section', { hasText: 'Getting Started' }),
      ).toBeVisible();

      await page.locator('#docs-search-input').fill('');
      await expect
        .poll(async () => page.locator('#docs-nav .docs-nav-section:visible').count())
        .toBe(allNavSections);
    });

    test('long code blocks stay inside the viewport', async ({ page }) => {
      // Pins the docs overflow fixes from the responsive pass (#997): the
      // Docker section's <pre> paths were the worst genuine offender at 375.
      await clickNavChild(page, 'Docker & Deployment');
      await expect(page.locator('#gs-docker')).toBeInViewport();

      await expectNoHorizontalOverflow(page, `/help docker section at ${viewport.name}`);
    });
  });
}
