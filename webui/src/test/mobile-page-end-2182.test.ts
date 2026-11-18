import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * #2182: "can't get to the bottom of the Automations page" on a phone.
 *
 * On a phone the page scrolls inside .main-content, a flex child of a
 * .main-container sized height: 100vh, under a body that does not scroll.
 * 100vh is the screen with the browser's bars hidden, so with the bars up
 * the shell ran past the visible screen by the bottom bar's height and the
 * end of every page sat under it, out of reach (on a short page there was
 * nothing to scroll at all). The installed app has the same band at the
 * home indicator (viewport-fit=cover), where the floating row sat on it.
 *
 * jsdom does not lay out, so these pin the declarations, each inside the
 * phone media block it has to live in. Every pin fails on the tree before.
 */

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');
const MOBILE = read('static/mobile.css').replace(/\/\*[\s\S]*?\*\//g, '');

/** every brace-matched `@media (max-width: <px>px)` body, joined */
function media(px: number): string {
  const out: string[] = [];
  let from = 0;
  for (;;) {
    const at = MOBILE.indexOf(`@media (max-width: ${px}px)`, from);
    if (at < 0) break;
    const open = MOBILE.indexOf('{', at);
    let depth = 0;
    let i = open;
    for (; i < MOBILE.length; i++) {
      if (MOBILE[i] === '{') depth++;
      if (MOBILE[i] === '}' && --depth === 0) break;
    }
    out.push(MOBILE.slice(open + 1, i));
    from = i;
  }
  return out.join('\n');
}

/** the declarations of every top-level `selector {` rule in a block, in order */
function decls(block: string, selector: string): string {
  const re = new RegExp(
    `(^|[\\n}])\\s*${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*\\{([^}]*)\\}`,
    'g',
  );
  return [...block.matchAll(re)].map((m) => m[2]).join('\n');
}

const PHONE = media(768);
const SMALL = media(480);
const SAFE = 'env(safe-area-inset-bottom, 0px)';

describe('#2182: the end of a page is reachable on a phone', () => {
  it('the shell is the visible screen: 100dvh after the 100vh fallback', () => {
    for (const sel of ['body', '.main-container']) {
      const d = decls(PHONE, sel);
      expect(d, sel).toMatch(/height:\s*100vh;[\s\S]*height:\s*100dvh;/);
    }
  });

  it('pages clear the floating row and the home indicator', () => {
    expect(decls(PHONE, '.page')).toContain(`padding: 70px 16px calc(72px + ${SAFE}) 16px`);
    expect(decls(SMALL, '.page')).toContain(`padding: 70px 12px calc(72px + ${SAFE}) 12px`);
  });

  it('the floating row sits above the home indicator at both phone sizes', () => {
    for (const sel of [
      '.notif-bell-btn',
      '.helper-float-btn',
      '.activity-float-btn',
      '.gsearch-bar',
    ]) {
      expect(decls(PHONE, sel), sel).toContain(`bottom: calc(16px + ${SAFE})`);
      expect(decls(SMALL, sel), sel).toContain(`bottom: calc(12px + ${SAFE})`);
    }
  });
});
