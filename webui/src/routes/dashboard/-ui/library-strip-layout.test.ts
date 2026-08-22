/**
 * The dashboard library strip's wrap invariants, asserted against style.css.
 *
 * WHY THIS FILE EXISTS. jsdom has no layout engine, so no rendered test here
 * can see a row running off the side of a card. The strip went from six buttons
 * to eleven (the library verbs plus the quick links) and clipped immediately.
 * Measured in Chromium at four widths, not reasoned about:
 *
 *   viewport   six buttons   eleven, before      eleven, after
 *   1440       fits          fits                fits, 1 row
 *   1100       fits          62px past the card  fits, 2 rows
 *    820       fits          334px past          fits, 2 rows
 *    390       2 rows        4 rows, no clip     4 rows
 *
 * Three attempts that looked right and did nothing, recorded because the
 * tempting thing on reading these rules is to tidy them back:
 *
 *   1. `.library-status-actions { flex-wrap: wrap }` was ALREADY in the file,
 *      up at the "Six buttons now" rule. It never did anything: the base rule
 *      further down sets `flex-shrink: 0`, wins on source order, and an item
 *      that cannot shrink never reaches a width where it must wrap. At six
 *      buttons it fit anyway, so nobody noticed.
 *   2. adding the shrink to that same earlier rule also did nothing, same
 *      cascade reason. It has to go on the later one.
 *   3. `.library-status-info { flex: 1 1 220px }` to stop the title collapsing
 *      was dead on arrival. `.dash-card[data-card="library"] .library-status-info`
 *      declares the `flex` shorthand and outranks it. Deleting it changed no
 *      measurement, which is how we know. Wrapping the HEADER is what actually
 *      fixes the title, because then the buttons take their own line and the
 *      title gets the full width regardless of basis.
 *
 * The specificity detail that makes the surviving rules work: the
 * `.dash-card[data-card="library"]` block outranks everything asserted here,
 * but it never declares `flex-wrap`, `flex-shrink`, `min-width` or
 * `justify-content`, and the cascade resolves per property, so these land.
 * If someone adds those properties to that block, these rules go dead silently.
 * Hence the conflict checks below rather than a plain "is it in the file".
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

/** Comments stripped: they name the very properties being asserted. */
const css = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8').replace(
  /\/\*[\s\S]*?\*\//g,
  '',
);

/**
 * style.css with every @media block removed, brace-counted.
 *
 * The strip has phone overrides and they come LAST, so a naive "take the final
 * match" reads the 768px rule and reports the desktop cascade wrong.
 */
const base = (() => {
  let out = '';
  for (let i = 0; i < css.length; ) {
    const at = css.indexOf('@media', i);
    if (at === -1) {
      out += css.slice(i);
      break;
    }
    out += css.slice(i, at);
    const open = css.indexOf('{', at);
    if (open === -1) break;
    let depth = 1;
    let j = open + 1;
    for (; j < css.length && depth > 0; j++) {
      if (css[j] === '{') depth++;
      else if (css[j] === '}') depth--;
    }
    i = j;
  }
  return out;
})();

/** Every non-media rule body whose selector list mentions `cls`. */
function rulesFor(cls: string): string[] {
  const escaped = cls.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return [...base.matchAll(new RegExp(`([^{}]*${escaped}[^{}]*)\\{([^}]*)\\}`, 'g'))].map(
    (m) => m[2]!,
  );
}

/** Declared values of `prop` across every rule touching `cls`, in source order. */
function declared(cls: string, prop: string): string[] {
  return rulesFor(cls)
    .map((body) => new RegExp(`(?:^|;)\\s*${prop}\\s*:\\s*([^;]+)`).exec(body)?.[1]?.trim())
    .filter((v): v is string => Boolean(v));
}

describe('the library strip wraps instead of clipping', () => {
  it('lets the actions row shrink, or it can never wrap', () => {
    expect(declared('.library-status-actions', 'flex-wrap')).toContain('wrap');
    // the whole bug in one declaration. nothing may reintroduce it.
    expect(declared('.library-status-actions', 'flex-shrink')).not.toContain('0');
  });

  it('wraps the header so the buttons drop below the title', () => {
    const values = declared('.library-status-header', 'flex-wrap');
    expect(values).toContain('wrap');
    // a later or more specific `nowrap` would silently undo it.
    expect(values).not.toContain('nowrap');
  });

  it('styles the review badge and the group divider it renders', () => {
    expect(css).toMatch(/\.library-status-btn-badge\s*\{/);
    expect(css).toMatch(/\.library-status-btn-attention\s*\{/);
    expect(css).toMatch(/\.library-status-divider\s*\{/);
  });
});
