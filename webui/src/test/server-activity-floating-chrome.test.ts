import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * server activity drawer vs the floating row (#2090).
 *
 * the drawer sits one z-index step under the floating buttons
 * (.sact-drawer 999998, the buttons 999999), so with the drawer open the
 * activity, bell and helper buttons painted over the bottom playback
 * card. the fix hides the row while the drawer is visible, the same way
 * the mirrored, mix and download-missing modals do it, except it leaves
 * #gsearch-bar and .hamburger-btn alone: they already sit under the
 * drawer's scrim, so there is nothing to fix there.
 *
 * jsdom does not lay out, so these assert the declarations, following
 * the mirrored-modal-mobile.test.ts shape. comments are stripped before
 * asserting: a rule quoted inside a comment must not satisfy the pins.
 */

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');
const css = (p: string) => read(p).replace(/\/\*[\s\S]*?\*\//g, '');

const STYLE = css('static/style.css');

describe('floating chrome while the server activity drawer is open', () => {
  const at = STYLE.indexOf('body:has(.sact-drawer.visible)');
  const block = at >= 0 ? STYLE.slice(at, STYLE.indexOf('}', at) + 1) : '';

  it('hides every floating control', () => {
    for (const target of [
      '#notif-bell-btn',
      '#helper-float-btn',
      '#activity-float-btn',
      '#oidc-override-btn',
    ]) {
      expect(block).toContain(`body:has(.sact-drawer.visible) ${target}`);
    }
    expect(block).toMatch(/\{\s*display:\s*none\s*!important;\s*\}/);
    // exactly one rule under this condition: a later same-condition
    // override would win the cascade and quietly re-show a button.
    expect(STYLE.split('body:has(.sact-drawer.visible)')).toHaveLength(5);
  });

  it('keys on .visible only, never on the parked closed drawer', () => {
    // the closed drawer stays in the dom off-canvas; any :has() that
    // matches .sact-drawer without .visible would hide the buttons
    // forever. checked in every sheet that hides this row elsewhere.
    for (const sheet of [
      'static/style.css',
      'static/mobile.css',
      'static/watchlist-premier.css',
      'src/routes/library/-ui/library-v2-page.module.css',
    ]) {
      expect(css(sheet)).not.toMatch(/:has\([^)]*\.sact-drawer(?!\.visible(?![\w-]))/);
    }
  });

  it('pins the ids to their sources', () => {
    const html = read('index.html');
    for (const id of ['activity-float-btn', 'notif-bell-btn', 'helper-float-btn']) {
      expect(html).toContain(`id="${id}"`);
    }
    const oidc = read('src/shell/oidc-only.ts');
    expect(oidc).toContain("'oidc-override-btn'");
    expect(oidc).toContain('button.id = BUTTON_ID');
  });

  it('pins the drawer visible toggling, not just the scrim', () => {
    // the scrim also gets .visible; the rule only fires on the drawer's
    // own add, and close must remove it again or the row stays hidden.
    const sact = read('src/shell/server-activity.ts');
    expect(sact).toContain("'sact-drawer'");
    expect(sact).toContain("drawer!.classList.add('visible')");
    expect(sact).toContain("drawer.classList.remove('visible')");
  });
});
