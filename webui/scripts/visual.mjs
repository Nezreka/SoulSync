#!/usr/bin/env node
/**
 * Run the visual baselines inside the official Playwright image.
 *
 * Fonts and text rendering differ between distros, so a baseline only
 * reproduces in the environment that rendered it. Every run, local or CI,
 * happens in the same image: mcr.microsoft.com/playwright at the version
 * @playwright/test is pinned to, so a Playwright bump moves the image too.
 *
 *   node scripts/visual.mjs                       # compare (npm run test:visual)
 *   node scripts/visual.mjs --update-snapshots    # rewrite (npm run test:visual:update)
 *   node scripts/visual.mjs -g settings           # any playwright test flag
 *
 * It only runs the tests; build first (`npm run build`), which the npm
 * scripts do. Inside the image already (CI uses it as the job container),
 * it runs playwright directly.
 */
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const webui = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const { version } = JSON.parse(
  readFileSync(resolve(webui, 'node_modules/@playwright/test/package.json'), 'utf8'),
);
const IMAGE = `mcr.microsoft.com/playwright:v${version}-jammy`;

const playwright = ['npx', 'playwright', 'test', '-c', 'playwright.visual.config.ts'];
const args = process.argv.slice(2);

function run(command, commandArgs) {
  const result = spawnSync(command, commandArgs, { cwd: webui, stdio: 'inherit' });
  if (result.error) {
    console.error(`visual: could not run ${command}: ${result.error.message}`);
    if (command === 'docker') console.error('visual: the visual baselines need Docker.');
    process.exit(1);
  }
  process.exit(result.status ?? 1);
}

// The image sets this; nothing else does.
if (process.env.PLAYWRIGHT_BROWSERS_PATH === '/ms-playwright') {
  run(playwright[0], [...playwright.slice(1), ...args]);
}

const user = process.getuid ? ['--user', `${process.getuid()}:${process.getgid()}`] : [];
// Forward the VISUAL_* knobs read by playwright.visual.config.ts.
const env = Object.keys(process.env)
  .filter((key) => key.startsWith('VISUAL_'))
  .flatMap((key) => ['-e', key]);
run('docker', [
  'run',
  '--rm',
  '--ipc=host',
  ...user,
  ...env,
  '-e',
  'HOME=/tmp',
  '-v',
  `${webui}:/work`,
  '-w',
  '/work',
  IMAGE,
  ...playwright,
  ...args,
]);
