import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * #2197: re-identify on a phone. jsdom does not lay out, so these pin the
 * declarations the sheet rests on. Measured in real Chromium at 390x844 and
 * 360x780: before, the result's badge and year column took the row and the
 * title shrank to "L..", Search broke over two lines, the close sat on the
 * eyebrow and Cancel / Re-identify fell out of the box. Desktop is pinned by
 * the base rules staying as they were.
 */

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');
const strip = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, '');
const STYLE = strip(read('static/style.css'));

/** every brace-matched `@media (max-width: 768px)` body */
function phoneBlocks(css: string): string[] {
  const out: string[] = [];
  let at = css.indexOf('@media (max-width: 768px)');
  while (at >= 0) {
    const open = css.indexOf('{', at);
    let depth = 0;
    for (let i = open; i < css.length; i++) {
      if (css[i] === '{') depth++;
      if (css[i] === '}' && --depth === 0) {
        out.push(css.slice(open + 1, i));
        break;
      }
    }
    at = css.indexOf('@media (max-width: 768px)', open);
  }
  return out;
}

/** the body of the rule whose selector list is exactly `selector` */
function rule(css: string, selector: string): string {
  const re = new RegExp(
    `(^|\\n|\\}|\\{)\\s*${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*\\{([^}]*)\\}`,
  );
  return re.exec(css)?.[2] ?? '';
}

const SHEET = phoneBlocks(STYLE).find((b) => b.includes('.modal-overlay:has(> .reid-modal)')) ?? '';

describe('#2197 re-identify is a full-screen sheet on a phone', () => {
  it('has a phone block', () => {
    expect(SHEET).not.toBe('');
  });

  it('the overlay drops its gutter and the sheet fills the screen', () => {
    const overlay = rule(SHEET, '.modal-overlay:has(> .reid-modal)');
    expect(overlay).toMatch(/padding:\s*0/);
    expect(overlay).toMatch(/align-items:\s*stretch/);
    const modal = rule(SHEET, '.reid-modal');
    expect(modal).toMatch(/height:\s*100dvh/);
    expect(modal).toMatch(/max-height:\s*100dvh/);
    expect(modal).toMatch(/width:\s*100%/);
    expect(modal).toMatch(/border-radius:\s*0/);
    // the body scrolls as one page between the pinned header and footer
    expect(modal).toMatch(/overflow-y:\s*auto/);
    expect(rule(SHEET, '.reid-modal > *')).toMatch(/flex-shrink:\s*0/);
    expect(rule(SHEET, '.reid-results')).toMatch(/overflow:\s*visible/);
  });

  it('the header and the buttons pin while the body scrolls', () => {
    const hero = rule(SHEET, '.reid-hero');
    expect(hero).toMatch(/position:\s*sticky;\s*top:\s*0/);
    expect(hero).toMatch(/background:\s*var\(--bg-primary\)/);
    // room on the right so the title never runs under the close
    expect(hero).toMatch(/padding:\s*12px 60px 12px 16px/);
    const footer = rule(SHEET, '.reid-footer');
    expect(footer).toMatch(/position:\s*sticky;\s*bottom:\s*0/);
    expect(footer).toMatch(/background:\s*var\(--bg-primary\)/);
    expect(footer).toMatch(/flex-direction:\s*column/);
  });

  it('each result is a card: title and release on top, type and year under', () => {
    const row = rule(SHEET, '.reid-result');
    expect(row).toMatch(/display:\s*grid/);
    expect(row).toMatch(/grid-template-columns:\s*52px minmax\(0, 1fr\)/);
    expect(row).toMatch(/grid-template-areas:\s*'art info' 'art meta'/);
    expect(rule(SHEET, '.reid-result-meta')).toMatch(/grid-area:\s*meta/);
    expect(rule(SHEET, '.reid-result-title,\n    .reid-result-release')).toMatch(
      /white-space:\s*normal/,
    );
    expect(rule(SHEET, '.reid-result-check')).toMatch(/position:\s*absolute/);
  });

  it('Search stays on one line and the tap targets are big', () => {
    const btn = rule(SHEET, '.reid-search-btn');
    expect(btn).toMatch(/white-space:\s*nowrap/);
    expect(btn).toMatch(/flex:\s*0 0 auto/);
    // 16px stops iOS zooming the page on focus
    expect(rule(SHEET, '.reid-search-input')).toMatch(/font-size:\s*16px/);
    // height, not min-height: mobile.css's button floor outranks any
    // min-height here and would pull them back to 38px
    expect(rule(SHEET, '.reid-footer-actions .btn')).toMatch(/(^|;|\s)height:\s*44px/);
    expect(rule(SHEET, '.reid-close')).toMatch(/width:\s*44px;\s*height:\s*44px/);
    expect(rule(SHEET, '.reid-tab')).toMatch(/height:\s*40px/);
  });

  it('the floating buttons step aside while it is open', () => {
    for (const id of [
      '#activity-float-btn',
      '#helper-float-btn',
      '#notif-bell-btn',
      '#media-player',
    ]) {
      expect(SHEET).toContain(`body:has(.reid-modal) ${id}`);
    }
    expect(SHEET).toMatch(/body:has\(\.reid-modal\) #media-player \{\s*display:\s*none;/);
  });

  it('desktop keeps the centred box', () => {
    const base = rule(STYLE, '.reid-modal');
    expect(base).toMatch(/width:\s*min\(760px, 94vw\)/);
    expect(base).toMatch(/max-height:\s*88dvh/);
    expect(base).toMatch(/border-radius:\s*20px/);
    // and it no longer inherits the track-table cell's right alignment
    expect(base).toMatch(/text-align:\s*left/);
  });
});
