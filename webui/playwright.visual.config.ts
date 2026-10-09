import { defineConfig } from '@playwright/test';

/**
 * Screenshot baselines for the main routes. No server: the harness renders
 * index.html itself, serves webui/static from disk and stubs every /api call.
 * See tests/visual/README.md.
 */

// The baselines are rendered inside the official Playwright image (the image
// sets PLAYWRIGHT_BROWSERS_PATH; nothing else does). Anywhere else the fonts
// differ and every shot would fail, so point at the wrapper instead.
// VISUAL_ON_HOST=1 skips the check, for poking at a page with a throwaway spec.
if (process.env.PLAYWRIGHT_BROWSERS_PATH !== '/ms-playwright' && !process.env.VISUAL_ON_HOST) {
  throw new Error(
    'The visual baselines only reproduce inside the Playwright Docker image. ' +
      'Run `npm run test:visual` (or `node scripts/visual.mjs <playwright args>`).',
  );
}

// Knobs for scripts/visual-compare.mjs, which sets them:
// - VISUAL_THRESHOLD overrides pixelmatch's per-pixel colour tolerance
//   (default 0.2); 0 flags every changed pixel.
// - VISUAL_COMPARATOR=ssim-cie94 swaps pixelmatch for Playwright's perceptual
//   comparator: a pixel only counts once its colour moves by a just-noticeable
//   difference (CIE94 ΔE > 1) and it isn't rasterisation noise. Playwright
//   exposes it as the experimental `_comparator` option, so it may move.
// - VISUAL_HTML=1 also writes an HTML report with expected/actual/diff images
//   to visual-report/.
const threshold = process.env.VISUAL_THRESHOLD ? Number(process.env.VISUAL_THRESHOLD) : undefined;
const comparator = process.env.VISUAL_COMPARATOR || undefined;

export default defineConfig({
  testDir: './tests/visual',
  snapshotPathTemplate: '{testDir}/__screenshots__/{arg}{ext}',
  timeout: 60_000,
  fullyParallel: true,
  reporter: process.env.VISUAL_HTML
    ? [['list'], ['html', { outputFolder: 'visual-report', open: 'never' }]]
    : [['list']],
  expect: {
    toHaveScreenshot: {
      animations: 'disabled',
      caret: 'hide',
      maxDiffPixels: 0,
      threshold,
      // Not in the public types; see VISUAL_COMPARATOR above.
      ...(comparator && { _comparator: comparator }),
    },
  },
  use: {
    // Playwright's own bundled headless chromium, pinned by the
    // @playwright/test version in package-lock.json.
    browserName: 'chromium',
    serviceWorkers: 'block',
    contextOptions: { reducedMotion: 'reduce' },
    locale: 'en-US',
    timezoneId: 'UTC',
    colorScheme: 'dark',
    deviceScaleFactor: 1,
    trace: 'off',
  },
});
