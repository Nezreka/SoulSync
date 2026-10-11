#!/usr/bin/env node
/**
 * Screenshots of the unified downloads page (Phase 2 verification).
 *
 * Renders the committed React fixtures (tests/layout/__fixtures__/
 * unified-downloads-*.fixture.txt) under the REAL stylesheets
 * (static/style.css + static/video/video-side.css) in real Chromium and
 * screenshots: (a) the music view, (b) the video view, (c) an expanded video
 * card with cast info/images.
 *
 * Run: node scripts/shot-unified-downloads.mjs
 * Needs: vitest run src/test/vdl-fixtures.test.tsx  (regenerates fixtures)
 */
import { readFileSync, mkdirSync, existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

const webui = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const OUT = resolve(process.env.HOME ?? '/home/hatch', 'workspace/unified-downloads-shots');
mkdirSync(OUT, { recursive: true });

const styleCss = readFileSync(resolve(webui, 'static/style.css'), 'utf8');
const videoCss = readFileSync(resolve(webui, 'static/video/video-side.css'), 'utf8');

function page(name, side) {
  const fixture = readFileSync(
    resolve(webui, `tests/layout/__fixtures__/unified-downloads-${name}.fixture.txt`),
    'utf8',
  );
  return (
    `<!DOCTYPE html><html><head><meta charset="utf-8">` +
    `<style>${styleCss}\n${videoCss}</style></head>` +
    `<body data-side="${side}"><div id="webui-react-root" class="active">${fixture}</div>` +
    `</body></html>`
  );
}

const SHOTS = [
  ['music', 'music', 'unified-downloads-music.png'],
  ['video', 'video', 'unified-downloads-video.png'],
  ['video-expanded', 'video', 'unified-downloads-video-expanded.png'],
];

/* Prefer an explicit override, then the lab Chromium, then Playwright's own
 * bundled Chromium — so the script runs on ordinary dev machines too. */
const executablePath =
  process.env.CHROME_BIN ||
  (existsSync('/opt/meta-chromium/chrome') ? '/opt/meta-chromium/chrome' : undefined);
const browser = await chromium.launch({
  ...(executablePath ? { executablePath } : {}),
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
});
try {
  for (const [fixture, side, file] of SHOTS) {
    const pg = await browser.newPage({ viewport: { width: 1600, height: 900 } });
    const errors = [];
    pg.on('pageerror', (e) => errors.push(String(e)));
    await pg.setContent(page(fixture, side), { waitUntil: 'load' });
    await pg.waitForTimeout(1200); // let fonts/layout settle
    await pg.screenshot({ path: resolve(OUT, file), fullPage: false });
    console.log(file, errors.length ? `PAGE ERRORS: ${errors.join(' | ')}` : 'ok');
    await pg.close();
  }
} finally {
  await browser.close();
}
console.log('shots in', OUT);
