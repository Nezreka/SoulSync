import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Mirrored playlist detail modal on phones (#1691).
 *
 * The modal's action bar was a no-wrap flex row of ~596px of nowrap
 * buttons inside an overflow:hidden panel: on a phone "Sync & download"
 * started past the modal's edge and Close / Identify rendered entirely
 * off-screen, so a mirrored playlist could not be managed without
 * switching to desktop view. The track grid kept its six desktop
 * columns and left the title 36px at a 390px viewport (0px at 320px).
 *
 * jsdom does not lay out, so — like the autosync-mobile pins — these
 * assert the declarations the measured Chromium geometry depends on.
 * The before / after measurements (390 / 360 / 320px, replica markup
 * against the real stylesheets, React + vanilla + editable variants)
 * are in the section comment in mobile.css.
 *
 * The pins are scope- and order-sensitive on purpose (hostile review,
 * round 1): every rule is extracted from inside its brace-matched
 * @media block, so a rule that slipped outside the phone scope fails,
 * and the two places where declaration order is load-bearing (the head
 * padding override, the vh -> dvh fallback) pin the order too.
 */

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');

const MOBILE = read('static/mobile.css');

/** The #1691 section: from its marker comment to the end of the file. */
const sectionAt = MOBILE.indexOf('Mirrored playlist detail modal on phones');
/**
 * Comments stripped: the section's own comment names the selectors. The
 * slice starts inside the header comment (the marker is its first line),
 * so drop that comment's remainder before stripping the rest.
 */
const afterMarker = sectionAt >= 0 ? MOBILE.slice(sectionAt) : '';
const SECTION = afterMarker.slice(afterMarker.indexOf('*/') + 2).replace(/\/\*[\s\S]*?\*\//g, '');

/** The brace-matched body of `@media (max-width: <px>px) { ... }` in the section. */
function mediaBlock(px: number): string {
  const at = SECTION.indexOf(`@media (max-width: ${px}px)`);
  if (at < 0) return '';
  const open = SECTION.indexOf('{', at);
  let depth = 0;
  for (let i = open; i < SECTION.length; i++) {
    if (SECTION[i] === '{') depth++;
    if (SECTION[i] === '}') {
      depth--;
      if (depth === 0) return SECTION.slice(open + 1, i);
    }
  }
  return '';
}

const PHONE = mediaBlock(768);
const STACKED = mediaBlock(640);

/**
 * Every declaration block in `block` whose selector list names
 * `selector` as a whole member, merged.
 */
function rule(selector: string, block: string = PHONE): string {
  const bodies: string[] = [];
  for (const m of block.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const members = m[1].split(',').map((s) => s.trim());
    if (members.includes(selector)) bodies.push(m[2]);
  }
  return bodies.join('\n');
}

describe('the mirrored modal phone section exists', () => {
  it('is present and phone-scoped', () => {
    expect(sectionAt).toBeGreaterThan(-1);
    expect(PHONE).not.toBe('');
    expect(STACKED).not.toBe('');
  });

  it('leaves nothing outside a media block', () => {
    // strip both media blocks (header + body) from the section; only
    // whitespace may remain — a rule outside them would apply at every
    // width, desktop included, and no pin below would notice.
    let rest = SECTION;
    for (const px of [768, 640]) {
      const at = rest.indexOf(`@media (max-width: ${px}px)`);
      const open = rest.indexOf('{', at);
      let depth = 0;
      let end = -1;
      for (let i = open; i < rest.length; i++) {
        if (rest[i] === '{') depth++;
        if (rest[i] === '}') {
          depth--;
          if (depth === 0) {
            end = i;
            break;
          }
        }
      }
      rest = rest.slice(0, at) + rest.slice(end + 1);
    }
    expect(rest.trim()).toBe('');
  });
});

/**
 * The cascaded (last-in-block) value of `prop` for `selector` in the
 * phone block, or '' if never declared. rule() merges every matching
 * body, so a duplicate rule later in the block that quietly overrode a
 * pinned declaration would still pass a rule() pin — the load-bearing
 * declarations below are pinned by their final value instead
 * (hostile review, round 2).
 */
function finalValue(selector: string, prop: string): string {
  let value = '';
  for (const m of PHONE.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const members = m[1].split(',').map((x) => x.trim());
    if (!members.includes(selector)) continue;
    for (const decl of m[2].split(';')) {
      const [name, ...rest] = decl.split(':');
      if (name.trim() === prop) value = rest.join(':').trim().replace(/\s+/g, ' ');
    }
  }
  return value;
}

describe('the load-bearing declarations survive the cascade', () => {
  it('final values are the phone layout, not an earlier or later override', () => {
    expect(finalValue('.mm-row', 'grid-template-columns')).toBe(
      '26px 36px minmax(0, 1fr) 48px 28px',
    );
    expect(finalValue('.mm-list--editable .mm-row', 'grid-template-columns')).toBe(
      '26px 36px minmax(0, 1fr) 48px 28px 28px',
    );
    expect(finalValue('.mm-actions', 'flex-direction')).toBe('column');
    expect(finalValue('.mm-actions-right', 'flex-wrap')).toBe('wrap');
    expect(finalValue('.mm-list-head', 'padding')).toBe('0 14px');
    expect(finalValue('.mm-row', 'padding')).toBe('0 10px');
    expect(finalValue('.mirrored-modal', 'max-height')).toBe('calc(100dvh - 20px)');
    expect(finalValue('.mm-row-album', 'display')).toBe('none');
  });
});

describe('every action is reachable', () => {
  it('stacks the action bar instead of running it off the edge', () => {
    const r = rule('.mm-actions');
    expect(r).toMatch(/flex-direction:\s*column/);
    expect(r).toMatch(/align-items:\s*stretch/);
    expect(r).toMatch(/padding:\s*14px 16px/);
  });

  it('wraps the right-hand action group', () => {
    expect(rule('.mm-actions-right')).toMatch(/flex-wrap:\s*wrap/);
  });

  it('bare buttons share rows on a nonzero basis and can shrink', () => {
    const r = rule('.mm-actions-right > .mm-btn');
    // a 0 basis would share a line with a 100%-basis wrap at 0px wide
    expect(r).toMatch(/flex:\s*1 1 30%/);
    expect(r).toMatch(/min-width:\s*0/);
  });

  it('button labels wrap inside their buttons instead of spilling', () => {
    // the vanilla modal's four bare buttons lay out 3-across + 1; nowrap
    // text in a ~82-105px box spills over the neighbouring buttons
    expect(rule('.mm-actions .mm-btn')).toMatch(/white-space:\s*normal/);
  });

  it('the captioned actions take a full row each, button included', () => {
    const wrap = rule('.mm-btn-wrap');
    expect(wrap).toMatch(/flex:\s*1 1 100%/);
    expect(wrap).toMatch(/min-width:\s*0/);
    expect(wrap).toMatch(/align-items:\s*stretch/);
    expect(rule('.mm-btn-wrap > .mm-btn')).toMatch(/width:\s*100%/);
    expect(rule('.mm-btn-caption')).toMatch(/white-space:\s*normal/);
  });
});

describe('track rows give the title the room the album had', () => {
  it('drops to five columns: number, art, title, time, add', () => {
    const r = rule('.mm-row');
    expect(r).toMatch(/grid-template-columns:\s*26px 36px minmax\(0, 1fr\) 48px 28px/);
    expect(r).toMatch(/gap:\s*8px/);
    expect(r).toMatch(/padding:\s*0 10px/);
  });

  it('the editable grid keeps its extra remove column', () => {
    expect(rule('.mm-list--editable .mm-row')).toMatch(
      /grid-template-columns:\s*26px 36px minmax\(0, 1fr\) 48px 28px 28px/,
    );
  });

  it('the editable head gets the same six columns as its rows', () => {
    expect(rule('.mm-list--editable .mm-list-head')).toMatch(
      /grid-template-columns:\s*26px 36px minmax\(0, 1fr\) 48px 28px 28px/,
    );
  });

  it('hides the album cell and the album head label (its 4th span)', () => {
    expect(rule('.mm-row-album')).toMatch(/display:\s*none/);
    expect(rule('.mm-list-head > span:nth-child(4)')).toMatch(/display:\s*none/);
  });

  it('keeps head labels over the row cells despite the row inset', () => {
    // 14px head padding = 4px row margin + 10px row padding, and the
    // override only works because it comes AFTER the combined rule
    expect(rule('.mm-row')).toMatch(/margin:\s*1px 4px/);
    expect(rule('.mm-list-head')).toMatch(/padding:\s*0 14px/);
    const combinedAt = PHONE.indexOf('.mm-row {');
    const headAt = PHONE.indexOf('.mm-list-head {');
    expect(combinedAt).toBeGreaterThan(-1);
    expect(headAt).toBeGreaterThan(combinedAt);
  });

  it('shrinks the row art to match its 36px column', () => {
    const r = rule('.mm-row-art');
    expect(r).toMatch(/width:\s*36px/);
    expect(r).toMatch(/height:\s*36px/);
  });
});

describe('the modal takes more of the phone', () => {
  it('shrinks the overlay float padding and caps height to match', () => {
    expect(rule('.mirrored-modal-overlay')).toMatch(/padding:\s*10px/);
    const modal = rule('.mirrored-modal');
    expect(modal).toMatch(/max-height:\s*calc\(100vh - 20px\)/);
    expect(modal).toMatch(/max-height:\s*calc\(100dvh - 20px\)/);
    // the dvh line only works as a fallback if it comes second
    expect(modal.indexOf('100vh')).toBeLessThan(modal.indexOf('100dvh'));
  });

  it('returns the hero info width only where the hero is stacked', () => {
    // at 641-768px the hero is still a row and the base 44px
    // padding-right is the close button's clearance
    expect(rule('.mm-hero-content', PHONE)).toBe('');
    expect(rule('.mm-hero-content', STACKED)).toMatch(/padding-right:\s*0/);
  });
});

// #1691: the floating chrome (z-index 999999) sat over the modal (10000): the
// hamburger on its cover, and the bell, activity and helper buttons on the
// bottom row of actions, right where Close and Identify land once the bar
// stacks. measured in chromium against the real modal markup: all three sat
// on Identify at 390 and 360px. hidden while the modal is open, the same way
// the mix and download-missing modals do it.
describe('floating chrome while the mirrored modal is open', () => {
  const STYLE = read('static/style.css');
  const at = STYLE.indexOf('body:has(#mirrored-track-modal)');
  const block = at >= 0 ? STYLE.slice(at, STYLE.indexOf('}', at) + 1) : '';

  it('hides every floating control', () => {
    for (const target of [
      '#gsearch-bar',
      '#gsearch-aura',
      '#notif-bell-btn',
      '#helper-float-btn',
      '#activity-float-btn',
      '.hamburger-btn',
    ]) {
      expect(block).toContain(`body:has(#mirrored-track-modal) ${target}`);
    }
    expect(block).toMatch(/\{\s*display:\s*none\s*!important;\s*\}/);
  });
});
