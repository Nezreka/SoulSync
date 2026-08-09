import { readFileSync } from 'node:fs';
import { globSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * No element id may exist BOTH in index.html and in the React sync route.
 *
 * This is the invariant the markup deletion establishes, and the reason that
 * deletion had to ship in the same commit as the route flip rather than after
 * it. The React sync page reproduces many of the vanilla's ids on purpose —
 * they are the CSS contract and, for the adopted regions, the vanilla's own
 * render targets. With both copies present, `getElementById` returns whichever
 * comes first in document order, and every vanilla lookup silently starts
 * addressing the wrong element. Nothing throws; things just stop working, on
 * whichever page you were not looking at.
 *
 * The seven sync JS files are NOT deleted yet (see SYNC_PORT_AUDIT.md, "the
 * 42-edge review"), so those lookups still exist in the codebase. That makes
 * this guard load-bearing rather than ceremonial: it is the thing standing
 * between "inert leftover code" and "leftover code quietly driving React's
 * DOM".
 */

const INDEX = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');

/** Literal `id="..."` / `id='...'` in a source file. */
function literalIds(source: string): Set<string> {
  const out = new Set<string>();
  for (const m of source.matchAll(/\bid=["']([A-Za-z][\w-]*)["']/g)) out.add(m[1]);
  // JSX: id="foo" is caught above; id={`foo-${x}`} is dynamic and skipped —
  // a template id cannot be compared against static markup without evaluating it.
  return out;
}

function syncRouteSources(): { file: string; source: string }[] {
  const files = globSync('src/routes/sync/**/*.{ts,tsx}', { cwd: process.cwd() });
  return files
    .filter((f) => !f.includes('.test.'))
    .map((f) => ({ file: f, source: readFileSync(resolve(process.cwd(), f), 'utf8') }));
}

describe('sync page id collisions', () => {
  it('renders no id that index.html also contains', () => {
    const indexIds = literalIds(INDEX);
    const offenders: string[] = [];

    for (const { file, source } of syncRouteSources()) {
      for (const id of literalIds(source)) {
        if (indexIds.has(id)) offenders.push(`#${id}  (React: ${file})`);
      }
    }

    expect(
      [...new Set(offenders)].sort(),
      'These ids exist in BOTH index.html and the React sync route.\n' +
        'getElementById returns the first in document order, so every vanilla\n' +
        'lookup for one of these is now addressing the wrong element — silently.\n' +
        'Either the vanilla markup was not fully deleted, or React grew an id\n' +
        'that another page already uses.',
    ).toEqual([]);
  });

  it('confirms the vanilla sync markup is actually gone', () => {
    // The deletion this flip depends on. Asserted directly rather than inferred
    // from the absence of collisions, since "no collisions" would also be true
    // if React stopped rendering ids altogether.
    expect(INDEX).not.toContain('id="sync-page"');
    expect(INDEX).not.toContain('id="beatport-rebuild-content"');
    expect(INDEX).not.toContain('id="server-disambig-overlay"');
    expect(INDEX).not.toContain('id="sync-log-area"');
  });

  it('and that React still renders those ids', () => {
    // The other half: the markup moved, it did not evaporate. A flip that
    // deleted the vanilla and forgot the React side would pass the test above.
    const all = syncRouteSources()
      .map((s) => s.source)
      .join('\n');
    for (const id of ['beatport-rebuild-content', 'server-disambig-overlay', 'sync-log-area']) {
      expect(all, `React should render #${id}`).toContain(`id="${id}"`);
    }
  });
});
