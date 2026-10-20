import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Mobile CSS batch #2 (Thibs / c5pie / gzetk screenshots, all on 3.5.3).
 *
 * Six root causes, each pinned at the declaration that has to hold. jsdom
 * does not lay out, so — like the video-request-button test — these pin the
 * CSS/TSX hooks the measured geometry depends on. The investigation report
 * (~/workspace/soulsync-mobile-css-batch2/REPORT.md) has the before/after
 * measurements from the real Chromium harness.
 */

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');

const MOBILE = read('static/mobile.css');
const STYLE = read('static/style.css');
const PLAYER_V2 = read('static/media-player-v2.css');
const PREMIER = read('static/dashboard-premier.css');
const VIDEO_SIDE = read('static/video/video-side.css');
const WATCHLIST_TSX = read('src/routes/watchlist/-ui/watchlist-page.tsx');
const INBOX_TSX = read('src/routes/import/-ui/inbox.tsx');
const AUDIOBOOKS_CSS = read('src/routes/audiobooks/-ui/audiobooks-page.module.css');
const AUDIOBOOK_OVERLAY_TSX = read('src/routes/audiobooks/-ui/audiobook-overlay.tsx');

/** The block of `css` starting at `marker` up to the next closing brace. */
function blockAfter(css: string, marker: string): string {
  const at = css.indexOf(marker);
  expect(at, `marker not found: ${marker}`).toBeGreaterThan(-1);
  return css.slice(at, css.indexOf('}', at) + 1);
}

describe('RC1 — the global 38px touch-target rule must not stretch designed-short buttons', () => {
  // The rule in mobile.css is one giant button:not(...) chain; its
  // specificity is the point — counter-rules lose, exclusions are the
  // established escape hatch.
  const ruleStart = MOBILE.indexOf('button:not(.watchlist-card-remove)');
  const chain = MOBILE.slice(ruleStart, MOBILE.indexOf('{', ruleStart));

  it('excludes the dashboard theme swatches (16px circles, measured 16×38 before)', () => {
    expect(chain).toContain(':not(.dash-qs-swatch)');
  });

  it('excludes the segmented filter pills (28px in a 36px track, measured 38px before)', () => {
    expect(chain).toContain(':not(.watchlist-filter-btn)');
  });

  it('excludes the import pills via their data attribute (CSS-module class is hashed)', () => {
    expect(chain).toContain(':not([data-import-page-pill])');
    expect(INBOX_TSX).toContain('data-import-page-pill');
  });

  it('still applies the 38px target to standalone buttons', () => {
    expect(MOBILE.slice(ruleStart, MOBILE.indexOf('}', ruleStart))).toContain('min-height: 38px');
  });
});

describe('RC2 — tab rails that #1661 could not reach', () => {
  it('watchlist page tabs opt into the global [role="tablist"] rail rule', () => {
    expect(WATCHLIST_TSX).toContain('role="tablist"');
    expect(WATCHLIST_TSX).toContain('aria-selected={search.tab ===');
  });

  it('the folders media switcher scrolls instead of squeezing its labels', () => {
    const at = STYLE.indexOf('#settings-page .stg-media-switcher button { flex: 1; min-width: 0');
    expect(at, 'the old flex:1 squeeze rule is still in style.css').toBe(-1);
    const block = blockAfter(STYLE, '#settings-page .stg-media-switcher {');
    expect(block).toContain('overflow-x: auto');
    expect(block).toContain('flex-wrap: nowrap');
  });
});

describe('RC3 — min-width floors collapse at phone widths', () => {
  it('tools grids cap their floor at the container width', () => {
    expect(STYLE).toContain('minmax(min(280px, 100%), 1fr)');
    expect(STYLE).toContain('minmax(min(320px, 100%), 1fr)');
  });

  it('the pillars header stacks on phones instead of crushing the paragraph', () => {
    const at = STYLE.indexOf('.operations-pillars-header {\n        flex-direction: column;');
    expect(at).toBeGreaterThan(-1);
  });

  it('the logs toolbar wraps so the Auto-scroll toggle stays on screen', () => {
    expect(blockAfter(STYLE, '.log-viewer-actions {')).toContain('flex-wrap: wrap');
  });

  it('quality presets wrap instead of cutting the third preset', () => {
    expect(blockAfter(STYLE, '.preset-buttons {')).toContain('flex-wrap: wrap');
  });

  it('the downloads chain columns may shrink below their desktop min-content', () => {
    expect(STYLE).toContain('.dlchain-columns > * { min-width: 0; }');
  });

  it('chain step rows shed their decorative chrome on phones so the name fits', () => {
    const at = STYLE.indexOf('.dlchain-step-art, .dlchain-grip { display: none; }');
    expect(at).toBeGreaterThan(-1);
  });

  it('video issues stats get a phone layout', () => {
    const at = VIDEO_SIDE.indexOf('.vi-stats { grid-template-columns: repeat(2, 1fr); }');
    expect(at).toBeGreaterThan(-1);
  });

  it('the audiobooks search basis is capped at the bar width', () => {
    const block = blockAfter(AUDIOBOOKS_CSS, '.searchInputWrap {');
    expect(block).toContain('flex: 1 1 min(420px, 100%)');
    // Without min-width: 0 the wrap's automatic minimum size (the input's
    // intrinsic width) becomes the floor and the basis cap never engages.
    expect(block).toContain('min-width: 0');
  });

  it('below 360px the operations mode toggle tightens so the tools page cannot scroll sideways', () => {
    // 320px only: the two buttons total ~282px and ran to x=328 (fold-in).
    const at = STYLE.indexOf('@media (max-width: 359px)');
    expect(at).toBeGreaterThan(-1);
    const block = STYLE.slice(
      at,
      STYLE.indexOf('}', STYLE.indexOf('.operations-mode-toggle-btn', at)) + 1,
    );
    expect(block).toContain('.operations-mode-toggle-btn');
    expect(block).toContain('padding: 8px 10px');
  });

  it('below 360px the audiobooks mode strip scrolls inside the search bar instead of painting past it', () => {
    const at = AUDIOBOOKS_CSS.indexOf('@media (max-width: 359px)');
    expect(at).toBeGreaterThan(-1);
    const block = blockAfter(AUDIOBOOKS_CSS.slice(at), '.searchModes {');
    expect(block).toContain('overflow-x: auto');
    expect(block).toContain('min-width: 0');
  });

  it('below 360px chain steps drop the rank numeral so the source name fits', () => {
    expect(STYLE).toContain('.dlchain-step-rank { display: none; }');
  });
});

describe('RC4 — hamburger coverage matches the classes the app actually sets', () => {
  it('hides behind the settings/profile/player modal body classes', () => {
    for (const cls of ['src-modal-open', 'pf-modal-open', 'np-modal-open']) {
      expect(MOBILE).toContain(`body.${cls} .hamburger-btn`);
    }
  });

  it('hides behind React modals via their overlay roots', () => {
    expect(MOBILE).toContain('body:has(.auto-sync-overlay) .hamburger-btn');
    expect(MOBILE).toContain('body:has(.confirm-modal-overlay) .hamburger-btn');
  });

  it('hides behind the watchlist-add, disambiguation and genre-browser overlays (fold-in)', () => {
    // All three stack below the hamburger (z 1000 / 9000 / 8888 < 9999), set
    // no body class, and mount (or carry their open class) only while open.
    expect(MOBILE).toContain('body:has(.watchadd-modal-overlay.visible) .hamburger-btn');
    expect(MOBILE).toContain('body:has(.server-disambig-overlay.visible) .hamburger-btn');
    expect(MOBILE).toContain('body:has(.genre-browser-modal-overlay.active) .hamburger-btn');
  });

  it('hides behind audiobook overlays via the component’s data-attribute hook (fold-in)', () => {
    // The backdrop class is a hashed CSS module, unnameable from
    // mobile.css — the overlay stamps a data attribute on it instead, and
    // unmount removes it, so no body class can leak.
    expect(MOBILE).toContain('body:has([data-audiobook-overlay]) .hamburger-btn');
    expect(AUDIOBOOK_OVERLAY_TSX).toContain('data-audiobook-overlay');
  });

  it('settings sub-page titles clear the hamburger', () => {
    expect(blockAfter(MOBILE, '#settings-page .stg-active-section-title {')).toContain(
      'padding-left: 56px',
    );
  });

  it('the Auto-Sync header wraps so the summary cannot crush the title', () => {
    expect(blockAfter(MOBILE, '.auto-sync-header {\n        flex-wrap: wrap;')).toContain(
      'flex-wrap: wrap',
    );
    expect(MOBILE).toContain('.auto-sync-header .auto-sync-summary');
  });
});

describe('RC5 — the player transport cannot be flex-shrunk into an oval', () => {
  it('play and side buttons have flex-shrink: 0', () => {
    expect(blockAfter(STYLE, '.np-btn-play {\n    flex-shrink: 0;')).toContain('flex-shrink: 0');
    const btn = blockAfter(STYLE, '.np-btn {\n    width: 46px !important;');
    expect(btn).toContain('flex-shrink: 0');
  });

  it('the minimize chevron keeps its absolute positioning (measured x=-11 before)', () => {
    // The v2 stacking rule (.np-modal > *) out-specificities .np-close-btn's
    // position:absolute; as a relative box its right:20px shifted it -20px
    // off the modal's top-left corner. It must stay out of that rule.
    expect(PLAYER_V2).toContain(':not(.np-close-btn)');
  });

  it('the minimize chevron paints above the body subtree so it stays tappable (fold-in)', () => {
    // Its own z-index is 2, the same as the stacking rule gives .np-body,
    // and the body follows it in the DOM — without this it is on screen but
    // elementFromPoint at its centre returns .np-left and clicks miss it.
    expect(blockAfter(PLAYER_V2, '#np-modal-overlay .np-close-btn {')).toContain('z-index: 3');
  });

  it('the phone transport shrink is scoped to the controls row so it cannot crush the Stop pill (fold-in)', () => {
    // Stop is an .np-btn too, but it lives in .np-stop-row and is a text
    // pill sized auto on purpose; the unscoped ≤860px rule (specificity
    // 1,1,0 + !important) beat its exemption and forced it into 42×42.
    expect(PLAYER_V2).toContain('#np-modal-overlay .np-controls-row .np-btn {');
    expect(PLAYER_V2).toContain('#np-modal-overlay .np-controls-row .np-btn-play {');
    expect(PLAYER_V2).not.toContain('#np-modal-overlay .np-btn { width: 42px');
  });

  it('tab panels flow in the body scroll on mobile (no nested 46vh scroller)', () => {
    const mediaAt = PLAYER_V2.indexOf('@media (max-width: 860px)');
    expect(mediaAt).toBeGreaterThan(-1);
    const at = PLAYER_V2.indexOf('.np-v2-tabpanels {', mediaAt);
    const block = PLAYER_V2.slice(at, PLAYER_V2.indexOf('}', at) + 1);
    expect(block).toContain('max-height: none');
    expect(block).not.toContain('46vh');
  });
});

describe('RC6 — the dashboard listen band stacks on phones', () => {
  it('single column at ≤640px', () => {
    const at = PREMIER.indexOf(
      '#dashboard-page .listen-band {\n        grid-template-columns: 1fr;',
    );
    expect(at).toBeGreaterThan(-1);
  });

  it('desktop keeps the two-column band', () => {
    expect(PREMIER).toContain('grid-template-columns: minmax(320px, 360px) minmax(0, 1fr);');
  });
});
