/**
 * The real docs, the way the page gets them: run static/docs.js and every
 * static/docs-content/*.js file, so window.SoulSyncDocs holds the registry
 * the help sheet reads in the app. Once per test file.
 */

import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const STATIC = join(process.cwd(), 'static');
export const DOCS_DIR = join(STATIC, 'docs-content');
export const DOCS_FILES = readdirSync(DOCS_DIR)
  .filter((f) => f.endsWith('.js'))
  .sort();

/** sign the admin in, who may read every guide. docs.js hands out only the
 *  guides the viewer may read (#2174), and a test page has no one signed in */
export function readAsAdmin(): void {
  window.getCurrentProfileContext = () => ({ profileId: 1, isAdmin: true }) as never;
}

/** sign a member in, whose guides follow window.SoulSyncPerms */
export function readAsMember(profileId = 4): void {
  window.getCurrentProfileContext = () => ({ profileId, isAdmin: false }) as never;
}

export function installRealDocs(opts: { readAs?: 'admin' } = {}): void {
  if (opts.readAs === 'admin') readAsAdmin();
  if (window.SoulSyncDocs?.sections().length) return;
  // classic scripts, as index.html loads them: docs.js first, then the content
  (0, eval)(readFileSync(join(STATIC, 'docs.js'), 'utf8'));
  for (const file of DOCS_FILES) (0, eval)(readFileSync(join(DOCS_DIR, file), 'utf8'));
}
