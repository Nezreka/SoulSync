#!/usr/bin/env node
/**
 * Render another git ref with THIS checkout's visual harness and compare it
 * against THIS checkout's baselines, at a just-noticeable-difference level.
 *
 *   node scripts/visual-compare.mjs <git-ref> [playwright args]
 *   node scripts/visual-compare.mjs origin/feature/css-legacy-dedupe
 *   node scripts/visual-compare.mjs origin/feature/css-legacy-dedupe -g settings
 *
 * Steps: check <ref> out into .visual-compare/<ref> (a detached git worktree,
 * reused on later runs), copy the harness and baselines over it, `npm ci` and
 * build there, then run the shots with an HTML report. Open it with the
 * command printed at the end.
 *
 * Shots are compared with Playwright's perceptual ssim-cie94 comparator, not
 * the suite's default pixelmatch at threshold 0.2. That tolerance hides small
 * colour snaps (rose #f43f5e -> red #ef4444 is about 0.03), but pixelmatch
 * strict enough to catch them also trips on blur and glow rasterisation that
 * differs from run to run on the same build. ssim-cie94 counts a pixel once
 * its colour moves past a just-noticeable difference (CIE94 deltaE > 1, about
 * two grey levels on a flat fill) and isn't such noise.
 * VISUAL_THRESHOLD=<n> switches back to pixelmatch at that threshold.
 *
 * Remove the scratch worktree with `git worktree remove .visual-compare/<ref>`.
 */
import { spawnSync } from 'node:child_process';
import { cpSync, existsSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';

const webui = resolve(import.meta.dirname, '..');
const repo = resolve(webui, '..');
const [ref, ...args] = process.argv.slice(2);
if (!ref) {
  console.error('usage: node scripts/visual-compare.mjs <git-ref> [playwright args]');
  process.exit(2);
}

function sh(cmd, cmdArgs, opts = {}) {
  const r = spawnSync(cmd, cmdArgs, { stdio: 'inherit', ...opts });
  if (r.status !== 0 && !opts.allowFail) {
    console.error(`visual-compare: ${cmd} ${cmdArgs.join(' ')} failed`);
    process.exit(r.status ?? 1);
  }
  return r.status;
}

const target = join(repo, '.visual-compare', ref.replace(/[^\w.-]+/g, '_'));
if (existsSync(target)) {
  sh('git', ['-C', target, 'checkout', '--detach', '--force', ref]);
} else {
  sh('git', ['-C', repo, 'worktree', 'add', '--detach', target, ref]);
}

// Same harness and baselines on both sides; only the app under test differs.
const targetWebui = join(target, 'webui');
for (const path of ['tests/visual', 'scripts/visual.mjs', 'playwright.visual.config.ts']) {
  rmSync(join(targetWebui, path), { recursive: true, force: true });
  cpSync(join(webui, path), join(targetWebui, path), { recursive: true });
}
rmSync(join(targetWebui, 'visual-report'), { recursive: true, force: true });

sh('npm', ['ci', '--no-audit', '--no-fund'], { cwd: targetWebui });
sh('npm', ['run', 'build'], { cwd: targetWebui });
const status = sh('node', ['scripts/visual.mjs', ...args], {
  cwd: targetWebui,
  env: {
    ...process.env,
    ...(!process.env.VISUAL_THRESHOLD && { VISUAL_COMPARATOR: 'ssim-cie94' }),
    VISUAL_HTML: '1',
  },
  allowFail: true,
});

const report = join(targetWebui, 'visual-report');
console.log(
  `\nvisual-compare: ${status === 0 ? 'no visible differences' : 'differences found'} for ${ref}.` +
    `\nReport (expected/actual/diff per shot): npx playwright show-report ${report}`,
);
process.exit(status ?? 1);
