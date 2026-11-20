import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * #2218: opening the nav drawer on a phone made the page's scrollbar vanish
 * and slid the whole page sideways, then back on close.
 *
 * On a phone the page scrolls inside .main-content, and the drawer locks it
 * with `body.mobile-nav-open .main-content { overflow: hidden }`. Hiding the
 * overflow drops the scrollbar, and where the scrollbar takes space (the
 * installed app, a narrow desktop window: 12px from the global
 * ::-webkit-scrollbar rule) the content box grew by that much. Measured in
 * chromium with real scrollbars at 390 and 360: centred content moved 6px,
 * right-aligned 12px. With `scrollbar-gutter: stable` it moves 0px, and a
 * page too short to scroll keeps the same gutter, so it does not jump either.
 *
 * jsdom does not lay out, so this pins the declarations inside the phone
 * media block they have to live in.
 */

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');
const MOBILE = read('static/mobile.css').replace(/\/\*[\s\S]*?\*\//g, '');

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

function decls(block: string, selector: string): string {
  const re = new RegExp(
    `(^|[\\n}])\\s*${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*\\{([^}]*)\\}`,
    'g',
  );
  return [...block.matchAll(re)].map((m) => m[2]).join('\n');
}

const PHONE = media(768);

describe('#2218: opening the nav drawer does not move the page', () => {
  it('the drawer still locks the page scroller', () => {
    expect(decls(PHONE, 'body.mobile-nav-open .main-content')).toMatch(/overflow:\s*hidden/);
  });

  it('the page scroller keeps its scrollbar space while locked', () => {
    expect(decls(PHONE, '.main-content')).toMatch(/scrollbar-gutter:\s*stable/);
  });

  it('nothing on a phone takes the gutter back off the page scroller', () => {
    expect(MOBILE).not.toMatch(/\.main-content[^{]*\{[^}]*scrollbar-gutter:\s*auto/);
  });
});
