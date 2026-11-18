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

export function installRealDocs(): void {
  if (window.SoulSyncDocs?.sections().length) return;
  // classic scripts, as index.html loads them: docs.js first, then the content
  (0, eval)(readFileSync(join(STATIC, 'docs.js'), 'utf8'));
  for (const file of DOCS_FILES) (0, eval)(readFileSync(join(DOCS_DIR, file), 'utf8'));
}
