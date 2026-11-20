import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Mobile thread batch, Nov 18 (#2184, #2185, #2186).
 *
 * jsdom does not lay out, so these pin the declarations the phone fixes rest
 * on. Each was measured in real Chromium at 390x844 and 360x780:
 *  - #2186 the confirm dialog: a long message pushed the buttons out of the
 *    80dvh box (overflow hidden) with nothing to scroll. Now the message
 *    scrolls and the header and buttons hold their size.
 *  - #2186 the tools fix prompts (Keep Best): the box is capped at the screen
 *    and scrolls; Keep Best's buttons stay pinned to its bottom.
 *  - #2185 the import inbox row: four button stand-ins on one no-wrap line
 *    put Identify 95-125px past the card. Now the side wraps and the
 *    stand-ins go on narrow screens.
 *  - #2184 the U+2B73 download glyph is missing from android's fonts.
 */

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');
const strip = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, '');

/** the body of the first top-level rule whose selector list is exactly `selector` */
function rule(css: string, selector: string): string {
  const src = strip(css);
  const re = new RegExp(
    `(^|\\n|\\})\\s*${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*\\{([^}]*)\\}`,
  );
  return re.exec(src)?.[2] ?? '';
}

/** the brace-matched body of the first `@media (max-width: <px>px)` block */
function media(css: string, px: number): string {
  const src = strip(css);
  const at = src.indexOf(`@media (max-width: ${px}px)`);
  if (at < 0) return '';
  const open = src.indexOf('{', at);
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    if (src[i] === '{') depth++;
    if (src[i] === '}' && --depth === 0) return src.slice(open + 1, i);
  }
  return '';
}

describe('#2186 the confirm dialog keeps its buttons on a phone', () => {
  const STYLE = read('static/style.css');

  it('the message scrolls inside the capped box', () => {
    const body = rule(STYLE, '.confirm-modal-content');
    expect(body).toMatch(/overflow-y:\s*auto/);
    expect(body).toMatch(/min-height:\s*0/);
    expect(body).toMatch(/flex:\s*1 1 auto/);
  });

  it('the header and the buttons never shrink away', () => {
    expect(rule(STYLE, '.confirm-modal-header,\n.confirm-modal-actions')).toMatch(
      /flex-shrink:\s*0/,
    );
  });
});

describe('#2186 the tools fix prompts fit a phone', () => {
  const CSS = read('src/routes/tools/-ui/finding-prompts.module.css');

  it('every prompt box is capped at the screen and scrolls', () => {
    const body = rule(CSS, '.box');
    expect(body).toMatch(/max-height:\s*calc\(100dvh - 32px\)/);
    expect(body).toMatch(/overflow-y:\s*auto/);
  });

  it("Keep Best's buttons stay pinned while it scrolls", () => {
    const body = rule(CSS, '.keepActions');
    expect(body).toMatch(/position:\s*sticky/);
    expect(body).toMatch(/bottom:\s*-28px/);
    expect(body).toMatch(/background:/);
  });
});

describe('#2185 import rows stay inside the card on a phone', () => {
  const CSS = read('src/routes/import/-ui/import-page.module.css');
  const narrow = media(CSS, 900);

  it('the status line and the buttons wrap', () => {
    expect(narrow).toMatch(/\.rowSideTop,\s*\.rowActions\s*\{[^}]*flex-wrap:\s*wrap/);
  });

  it('the invisible stand-ins go on narrow screens', () => {
    expect(narrow).toMatch(/\.actionPlaceholder\s*\{[^}]*display:\s*none/);
    // inline display would beat the class: the stand-in has to be styled by it
    const inbox = read('src/routes/import/-ui/inbox.tsx');
    expect(inbox).toContain('className={styles.actionPlaceholder}');
    expect(inbox).not.toMatch(/visibility:\s*'hidden',\s*display:\s*'inline-flex'/);
  });

  it('on a phone the side takes the full width under the art', () => {
    expect(media(CSS, 560)).toMatch(/'side side side'/);
  });
});

describe('#2184 no download glyph android cannot draw', () => {
  function files(dir: string, out: string[] = []): string[] {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name);
      if (name === 'dist' || name === 'node_modules') continue;
      if (statSync(path).isDirectory()) files(path, out);
      else if (/\.(js|ts|tsx|html|css)$/.test(name)) out.push(path);
    }
    return out;
  }

  it('U+2B73 is nowhere in the webui', () => {
    const hits = [
      ...files(resolve(process.cwd(), 'static')),
      ...files(resolve(process.cwd(), 'src')),
    ]
      .filter((path) => readFileSync(path, 'utf8').includes('\u2B73'))
      .map((path) => path.slice(process.cwd().length + 1));
    expect(hits).toEqual([]);
  });

  it('Get Show draws the svg arrow', () => {
    const js = read('static/video/video-detail.js');
    expect(js).toMatch(/var DL_IC = '<svg/);
    expect(js).toContain(
      `'<span class="discog-btn-icon">' + DL_IC + '</span><span class="discog-btn-text">' + showGetLabel`,
    );
  });
});
