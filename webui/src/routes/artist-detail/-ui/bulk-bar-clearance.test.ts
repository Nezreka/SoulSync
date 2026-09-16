import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * #1255: the bulk action bar was a full-width sheet at bottom:0, so the mini
 * player (bottom:24px, 73px tall) and the activity/bell/help buttons
 * (bottom:24px, 48px) sat on top of its buttons. it is a floating dock now,
 * raised past both. measured in chromium: desktop bar bottom 692 vs player
 * top 703 at 800px; phone bar bottom 636 vs player top 648 at 820px.
 */
const STATIC = join(__dirname, '..', '..', '..', '..', 'static');
const CSS = readFileSync(join(STATIC, 'style.css'), 'utf8');
const MOBILE = readFileSync(join(STATIC, 'mobile.css'), 'utf8');

function rules(css: string, selector: string): string {
  const re = new RegExp(
    '(^|[\\s}])' + selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*\\{([^}]*)\\}',
    'gm',
  );
  return Array.from(css.matchAll(re), (m) => m[2]).join('\n');
}

function bottomPx(block: string): number[] {
  return Array.from(block.matchAll(/bottom:\s*(-?\d+)px/g), (m) => Number(m[1]));
}

describe('the bulk bar clears the player and the floating buttons', () => {
  it('sits above the desktop player (24px + 73px) when visible', () => {
    const visible = bottomPx(rules(CSS, '.enhanced-bulk-bar.visible'));
    expect(visible).toHaveLength(1);
    expect(visible[0]).toBeGreaterThanOrEqual(24 + 73 + 8);
    // a dock, not a sheet: inset from the sidebar and the right edge
    expect(rules(CSS, '.enhanced-bulk-bar')).toMatch(/left:\s*calc\(var\(--sidebar-w\) \+ 24px\)/);
    expect(rules(CSS, '.enhanced-bulk-bar')).toMatch(/right:\s*24px/);
  });

  it('sits above the full-width phone player (62px + 110px)', () => {
    const visible = bottomPx(rules(MOBILE, '.enhanced-bulk-bar.visible'));
    expect(visible).toHaveLength(1);
    expect(visible[0]).toBeGreaterThanOrEqual(62 + 110 + 8);
  });
});
