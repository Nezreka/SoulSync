import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Boulder, Oct 28 2026: big gaps between the wishlist's status zones. The
 * zones sat in a column flexbox, and Chrome sized each one before it knew its
 * width, as if every poster had its own row: "Found, but turned down" stood
 * 1,656px tall over 287px of posters. Measured in Chromium; jsdom has no
 * layout, so the declaration that fixed it is pinned as text.
 */
const CSS = readFileSync(resolve(process.cwd(), 'static/wishlist-premier.css'), 'utf8');

function rule(selector: string): string {
  const at = CSS.indexOf(`${selector} {`);
  expect(at, `${selector} not found`).toBeGreaterThan(-1);
  return CSS.slice(at, CSS.indexOf('}', at));
}

describe('wishlist zones', () => {
  it('sit in a one-column grid, so each is only as tall as its own posters', () => {
    const field = rule('.wlp .wl-nebula-field.wlp-field');
    expect(field).toMatch(/display:\s*grid/);
    expect(field).toMatch(/grid-template-columns:\s*minmax\(0,\s*1fr\)/);
    expect(field).not.toMatch(/flex-direction:\s*column/);
  });
});
