import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Parse-check the vanilla shell scripts the React migration edits.
 *
 * These are classic scripts: no bundler, no typechecker and no test suite
 * covers them, so a stray brace ships silently and takes the whole app down at
 * load. `new Function` runs the parser without executing anything.
 *
 * Only files this migration actually touches are listed — a guard for the
 * seam, not an attempt to lint the whole legacy tree.
 *
 * This trips the no-implied-eval lint warning by design: parsing IS the point,
 * and the function is never called, so nothing in these scripts executes.
 */
describe('vanilla shell scripts parse', () => {
  for (const file of [
    'static/init.js',
    'static/core.js',
    'static/api-monitor.js',
    'static/downloads.js',
    'static/sync-services.js',
  ]) {
    it(file, () => {
      const source = readFileSync(resolve(process.cwd(), file), 'utf8');
      expect(() => new Function(source)).not.toThrow();
    });
  }
});
