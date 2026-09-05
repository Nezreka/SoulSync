import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Putting the tool cards behind a tab broke every jump that lands on one.
 *
 * helper.js decides whether a target is on the current page with
 * `el.offsetParent !== null`. An element inside a panel hidden with the hidden
 * attribute is mounted but has no offsetParent, so that check reads it as "not
 * here" - it then scrolls to nothing and pins a popover to an invisible node.
 *
 * Six jumps land on tool cards: five HELPER_CONTENT entries (#db-updater-card,
 * #metadata-updater-card, #duplicate-cleaner-card, #discovery-pool-card,
 * #media-scan-card) and the onboarding checklist's "Run First Library Scan".
 * All of them were silently broken by the tab change until this hook existed.
 */

const PAGE = readFileSync(
  resolve(process.cwd(), 'src/routes/tools/-ui/tools-page.tsx'),
  'utf8',
);
const HELPER = readFileSync(resolve(process.cwd(), 'static/helper.js'), 'utf8');

describe('the tools page publishes a way to reveal a hidden tab', () => {
  it('installs and removes the hook rather than leaking it', () => {
    expect(PAGE).toContain('window.revealToolsTabFor = reveal');
    expect(PAGE).toContain('delete window.revealToolsTabFor');
  });

  it('finds the panel by walking up from the target, not by a hardcoded list', () => {
    // A list of "which cards live in which tab" would rot the moment a card
    // moves. The DOM already knows.
    expect(PAGE).toContain("closest<HTMLElement>('[role=\"tabpanel\"]')");
  });

  it('only switches when the panel is actually hidden', () => {
    // Otherwise a jump to something already on screen would yank the tab.
    expect(PAGE).toContain('if (!panel || !panel.hidden) return false');
  });

  it('reports whether it switched, so the caller can wait for the paint', () => {
    expect(PAGE).toMatch(/setTab\(wanted\);\s*\n\s*return true;/);
  });
});

describe('the helper asks before deciding a target is missing', () => {
  it('treats a revealed element as present despite having no offsetParent yet', () => {
    expect(HELPER).toContain('if (el && (el.offsetParent !== null || revealed))');
  });

  it('waits a frame after a tab switch before scrolling to it', () => {
    // The panel has not painted on the same tick it was unhidden, so measuring
    // immediately scrolls to a zero-sized box.
    expect(HELPER).toContain('if (revealed) setTimeout(show, 60); else show();');
  });

  it('asks again after navigating, when the page has only just mounted', () => {
    const after = HELPER.slice(HELPER.indexOf('navigateToPage(pageHint)'));
    expect(after.slice(0, 400)).toContain('window.revealToolsTabFor');
  });

  it('never lets a missing hook break the jump', () => {
    // helper.js also runs on pages that never install it.
    const calls = HELPER.split('window.revealToolsTabFor').length - 1;
    expect(calls).toBeGreaterThanOrEqual(2);
    expect(HELPER).toContain('try { revealed = Boolean(window.revealToolsTabFor');
  });
});

describe('the jumps that depend on this', () => {
  it('still points the first-scan checklist at a real tool card', () => {
    expect(HELPER).toContain("selector: '#db-updater-card'");
  });

  it('keeps every tools-page helper entry that the tab change put behind a panel', () => {
    for (const id of [
      '#db-updater-card',
      '#metadata-updater-card',
      '#duplicate-cleaner-card',
      '#discovery-pool-card',
      '#media-scan-card',
    ]) {
      expect(HELPER, `${id} lost its helper entry`).toContain(`'${id}'`);
    }
  });
});
