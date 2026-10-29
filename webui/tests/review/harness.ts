import { expect, test as base, type Page } from '@playwright/test';
import type { RequestHandler } from 'msw';

import { installShell as installBuiltShell } from '../visual/harness';
import { reviewShellHandlers } from '../fixtures/review-shell';
export { ORIGIN } from '../visual/harness';
export { expect };

/** Required review journeys fail on missing API fixtures and runtime errors. */
export const test = base.extend({
  page: async ({ page }, use) => {
    const failures: string[] = [];
    page.on('pageerror', (error) => failures.push(error.message));
    page.on('response', (response) => {
      if (response.status() === 501 && new URL(response.url()).pathname.startsWith('/api/'))
        failures.push(`Unexpected API request: ${response.request().method()} ${response.url()}`);
    });
    await use(page);
    expect(failures, 'Review pages must not hide missing API contracts or runtime errors').toEqual([]);
  },
});

export function installShell(page: Page, handlers: RequestHandler[] = []) {
  return installBuiltShell(page, [...handlers, ...reviewShellHandlers], { strictApi: true });
}
