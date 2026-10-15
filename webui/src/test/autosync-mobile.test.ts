import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Auto-Sync Manager phone rework (Boulder's screenshot, Oct 13, 2026).
 *
 * The modal had a header wrap from mobile batch #2 and nothing else: the
 * <=1100px stacking rule in style.css loses on specificity to the
 * streamlined board's .auto-sync-modal-scoped rules, so on a phone the
 * sidebar kept its 280px desktop column, the lanes were squeezed into
 * the leftover ~90px and clipped, the tab strip overflowed mid-word, and
 * the board rendered 150px tall over a 421px slot of dead space.
 *
 * jsdom does not lay out, so — like the batch #2 pins — these assert the
 * declarations the measured Chromium geometry depends on. The before /
 * after measurements (390 / 360 / 320px, replica markup against the real
 * stylesheets) are in the rework section comment in mobile.css.
 */

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');

const MOBILE = read('static/mobile.css');

/** The rework section: from its marker comment to the end of the file. */
const sectionAt = MOBILE.indexOf('Auto-Sync Manager on phones: the whole modal');
/** Comments stripped: the section's own comment names the selectors. */
const SECTION = (sectionAt >= 0 ? MOBILE.slice(sectionAt) : '').replace(/\/\*[\s\S]*?\*\//g, '');

/**
 * Every declaration block in the section whose selector list names
 * `selector` as a whole member, merged — a selector can legitimately
 * appear in more than one rule (the intros share a padding rule and a
 * stacking rule), and the pins care about the union.
 */
function rule(selector: string): string {
  const bodies: string[] = [];
  for (const m of SECTION.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const members = m[1].split(',').map((s) => s.trim());
    if (members.includes(selector)) bodies.push(m[2]);
  }
  return bodies.join('\n');
}

describe('the Auto-Sync phone rework section exists', () => {
  it('is present and phone-scoped', () => {
    expect(sectionAt).toBeGreaterThan(-1);
    expect(SECTION).toContain('@media (max-width: 768px)');
  });
});

describe('the modal takes the screen', () => {
  it('is 12px shy of the viewport both ways, dvh with a vh fallback', () => {
    const r = rule('.auto-sync-modal');
    expect(r).toMatch(/width:\s*calc\(100vw - 12px\)/);
    expect(r).toMatch(/height:\s*calc\(100vh - 12px\)/);
    expect(r).toMatch(/height:\s*calc\(100dvh - 12px\)/);
  });
});

describe('the header is one compact block', () => {
  it('hides the blurb so title and actions share a row', () => {
    expect(rule('.auto-sync-modal .auto-sync-header p')).toMatch(/display:\s*none/);
  });

  it('summary chips split the row evenly instead of sizing to text', () => {
    const r = rule('.auto-sync-modal .auto-sync-header .auto-sync-summary > div');
    expect(r).toMatch(/flex:\s*1 1 0/);
    expect(r).toMatch(/min-width:\s*0/);
  });

  it('the idle monitor detail line gives its room to the board', () => {
    expect(rule('.auto-sync-monitor--idle .auto-sync-monitor-head small')).toMatch(
      /display:\s*none/,
    );
  });
});

describe('every tab is reachable', () => {
  it('the tab strip scrolls sideways and tabs never shrink or wrap', () => {
    expect(rule('.auto-sync-tabs')).toMatch(/overflow-x:\s*auto/);
    const btn = rule('.auto-sync-tabs button');
    expect(btn).toMatch(/flex:\s*0 0 auto/);
    expect(btn).toMatch(/white-space:\s*nowrap/);
  });
});

describe('the board stacks instead of squeezing', () => {
  it('the body is a flex column, retiring the 280px grid column', () => {
    const r = rule('.auto-sync-modal .auto-sync-body');
    expect(r).toMatch(/display:\s*flex/);
    expect(r).toMatch(/flex-direction:\s*column/);
  });

  it('the sidebar is capped and the source list is vertical again', () => {
    expect(rule('.auto-sync-modal .auto-sync-sidebar')).toMatch(/max-height:\s*40%/);
    const list = rule('.auto-sync-modal .auto-sync-source-list');
    expect(list).toMatch(/display:\s*block/);
    expect(list).toMatch(/overflow-y:\s*auto/);
    expect(list).toMatch(/overflow-x:\s*hidden/);
  });

  it('lanes run in one full-width column that scrolls', () => {
    const r = rule('.auto-sync-modal .auto-sync-lanes');
    expect(r).toMatch(/flex-direction:\s*column/);
    expect(r).toMatch(/overflow-y:\s*auto/);
  });

  it('a filled lane stacks its badge over full-width cards', () => {
    expect(rule('.auto-sync-modal .auto-sync-lane.filled')).toMatch(/flex-direction:\s*column/);
    const card = rule('.auto-sync-modal .auto-sync-lane .auto-sync-scheduled-card');
    expect(card).toMatch(/min-width:\s*0/);
    expect(card).toMatch(/max-width:\s*none/);
    expect(card).toMatch(/width:\s*100%/);
  });

  it('an empty lane unhides its cadence label', () => {
    // The streamlined board hides the badge span on empty lanes (they are
    // chips there). As a full-width phone row the span is the explanation.
    expect(rule('.auto-sync-modal .auto-sync-lane.empty .auto-sync-lane-badge span')).toMatch(
      /display:\s*inline/,
    );
  });
});

describe('the satellite surfaces stay on screen', () => {
  it('the bulk menu cannot exceed the viewport and its buttons are tappable', () => {
    expect(rule('.auto-sync-bulk-menu')).toMatch(/max-width:\s*calc\(100vw - 16px\)/);
    expect(rule('.auto-sync-bulk-menu button')).toMatch(/min-height:\s*40px/);
  });

  it('the weekly editor keeps a margin from the screen edges', () => {
    expect(rule('.auto-sync-weekly-editor-backdrop')).toMatch(/padding:\s*12px/);
  });

  it('the automation and history intros stack heading over detail', () => {
    const r = rule('.auto-sync-automation-intro');
    expect(r).toMatch(/flex-direction:\s*column/);
  });
});
