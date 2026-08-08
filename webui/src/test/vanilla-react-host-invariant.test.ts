import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * A React-owned page must never be activated as a LEGACY page.
 *
 * `showLegacyPage` strips `active` from every `.page` — including
 * `#webui-react-root` — and hands it to `#<pageId>-page`. Once a page has
 * flipped, that element no longer exists, so the class goes nowhere: the React
 * host goes dark with React still mounted underneath. The page is blank, and
 * navigating anywhere else and back "fixes" it, because that is a real route
 * change and the route's mount effect re-runs `showReactHost`.
 *
 * It shipped that way. `activateLegacyPath` did not check the route kind while
 * its sibling twenty lines below, `syncActivePageFromLocation`, did. The video
 * side reaches it: `/video-dashboard` matches no React route, so the catch-all
 * route's controller calls in, and `_getPageFromPath` maps any unknown path to
 * 'dashboard' (init.js). Switching back to music left a blank dashboard.
 *
 * Source-level because shell-bridge.js is a browser script with no module
 * boundary. The rule is about REACHABILITY, which is what a source check can
 * actually establish: every caller of `activatePage` must first prove the page
 * is not React-owned.
 */
const SOURCE = readFileSync(resolve(__dirname, '../../static/shell-bridge.js'), 'utf8');

/** Everything between `function NAME(` and the next column-0 closing brace. */
function functionBody(name: string): string {
  const start = SOURCE.indexOf(`function ${name}(`);
  expect(start, `${name} should exist in shell-bridge.js`).toBeGreaterThan(-1);
  const end = SOURCE.indexOf('\n}', start);
  expect(end, `${name} should be a top-level function`).toBeGreaterThan(start);
  return SOURCE.slice(start, end);
}

describe('the React host is never deactivated for a React page', () => {
  // Both are entry points from a URL: the catch-all route calls the first,
  // popstate calls the second. Either one reaching showLegacyPage for a
  // flipped page blanks it.
  for (const fn of ['activateLegacyPath', 'syncActivePageFromLocation']) {
    it(`${fn} checks the route kind before activating`, () => {
      const body = functionBody(fn);
      expect(
        body.includes("kind === 'react'"),
        `${fn} activates a page resolved from a URL. Without a route-kind check it ` +
          'sends React-owned pages through showLegacyPage, which deactivates ' +
          '#webui-react-root and hands `active` to markup that no longer exists — ' +
          'a blank page, with React still mounted.',
      ).toBe(true);
      expect(body).toContain('showReactHost');
    });
  }

  it('activatePage is only ever called behind such a check', () => {
    // The real invariant: not "this one function is careful" but "no path
    // reaches the legacy activator for a React page". A new caller added
    // without the guard reintroduces the bug, and this is what catches it.
    const lines = SOURCE.split('\n');
    const callers = lines
      .map((line, i) => ({ line, i }))
      .filter(({ line }) => /(?<!function )\bactivatePage\(/.test(line) && !line.trim().startsWith('//'))
      .map(({ i }) => i);

    expect(callers.length, 'shell-bridge.js should still call activatePage').toBeGreaterThan(0);
    for (const i of callers) {
      const preceding = lines.slice(Math.max(0, i - 25), i).join('\n');
      expect(
        preceding.includes("kind === 'react'"),
        `activatePage() on line ${i + 1} is not preceded by a route-kind check. ` +
          'Add one (see activateLegacyPath) or the page blanks after it flips to React.',
      ).toBe(true);
    }
  });
});
