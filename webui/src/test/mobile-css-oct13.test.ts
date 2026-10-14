import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Mobile CSS Oct 13 batch (Boulder's phone screenshots + videos,
 * issue ids M-01..M-19).
 *
 * Every mechanism was measured live in Chromium first (harness replicas +
 * the real pages at 390/360/412px); these pin the declarations and hooks
 * those measurements depend on, in the same style as the batch #2 pins.
 * Pins are string-presence checks by design — they guard against a later
 * refactor silently dropping a declaration, and each one can fail: every
 * string below is absent from the base tree.
 */

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');

const MOBILE = read('static/mobile.css');
const STYLE = read('static/style.css');
const PREMIER = read('static/discover-premier.css');
const DASH_PREMIER = read('static/dashboard-premier.css');
const DOCS_CSS = read('static/docs.css');
const DOCS_JS = read('static/docs.js');
const YEAR_STORY_TSX = read('src/routes/stats/-ui/year-story.tsx');
const YEAR_STORY_CSS = read('src/routes/stats/-ui/year-story.module.css');
const SEARCH_CSS = read('src/routes/search/-ui/search.module.css');
const SOURCE_ROW_TSX = read('src/routes/search/-ui/source-row.tsx');
const MY_ACCOUNTS_TS = read('src/shell/my-accounts.ts');
const ARTMAP_OVERLAY_TSX = read('src/routes/discover/-ui/artist-map-overlay.tsx');
const ARTMAP_ASSEMBLY_TSX = read('src/routes/discover/-ui/artist-map-assembly.tsx');
const ARTMAP_LIFECYCLE_TS = read('src/routes/discover/-discover.artist-map.lifecycle.ts');

/** The block of `css` starting at `marker` up to the next closing brace. */
function blockAfter(css: string, marker: string): string {
  const at = css.indexOf(marker);
  expect(at, `marker not found: ${marker}`).toBeGreaterThan(-1);
  return css.slice(at, css.indexOf('}', at) + 1);
}

describe('M-13/M-14 — the 38px touch-target rule must not deform designed-round controls', () => {
  const ruleStart = MOBILE.indexOf('button:not(.watchlist-card-remove)');
  const chain = MOBILE.slice(ruleStart, MOBILE.indexOf('{', ruleStart));

  it('excludes the mini player transport (measured 28x38 ovals before)', () => {
    for (const cls of [
      'play-button',
      'stop-button',
      'mini-nav-btn',
      'mini-toggle-btn',
      'expand-hint',
    ]) {
      expect(chain).toContain(`:not(.${cls})`);
    }
  });

  it('excludes the sync tab chips and their close button (32x38 chips, 18x38 close oval before)', () => {
    expect(chain).toContain(':not(.sync-tab-button)');
    expect(chain).toContain(':not(.sync-tab-close)');
  });

  it('the sync chips get their round shape back at a tap size in the ≤720px chip band', () => {
    const at = MOBILE.indexOf('@media (max-width: 720px)');
    expect(at).toBeGreaterThan(-1);
    const block = blockAfter(MOBILE.slice(at), '.sync-tab-button {');
    expect(block).toContain('width: 38px');
    expect(block).toContain('height: 38px');
    expect(block).toContain('flex: 0 0 auto');
  });

  it('the sync close button restates its designed 18px size on itself (not only via the :not chain)', () => {
    const block = blockAfter(MOBILE, '.sync-tab-close {');
    expect(block).toContain('width: 18px');
    expect(block).toContain('height: 18px');
    expect(block).toContain('flex: 0 0 auto');
  });

  it('the library source row keeps its chips whole and scrolls instead of squeezing', () => {
    const row = blockAfter(MOBILE, '.library-sources {');
    expect(row).toContain('flex-wrap: nowrap');
    expect(row).toContain('overflow-x: auto');
    const chip = blockAfter(MOBILE, '.library-source {');
    expect(chip).toContain('flex-shrink: 0');
    expect(chip).toContain('white-space: nowrap');
  });
});

describe('M-14 — mini player phone layout', () => {
  it('the expand chevron goes (the bar itself opens Now Playing)', () => {
    expect(blockAfter(MOBILE, '.mini-player-controls .expand-hint {')).toContain('display: none');
  });

  it('the toggles drop only below 340px, so 390px phones keep the full transport', () => {
    const at = MOBILE.indexOf('@media (max-width: 340px)');
    expect(at).toBeGreaterThan(-1);
    expect(blockAfter(MOBILE.slice(at), '.mini-player-controls .mini-toggle-btn {')).toContain(
      'display: none',
    );
    expect(MOBILE).not.toContain('@media (max-width: 400px) {\n    .mini-player-controls');
  });

  it('the play button is a 36px circle', () => {
    const block = blockAfter(MOBILE, '.mini-player-controls .play-button {');
    expect(block).toContain('width: 36px');
    expect(block).toContain('height: 36px');
  });

  it('the art, body gap and info column are tightened so the title gets the room', () => {
    const art = blockAfter(MOBILE, '.media-player .sidebar-album-art {');
    expect(art).toContain('width: 44px');
    expect(art).toContain('height: 44px');
    const body = blockAfter(MOBILE, '.mini-player-body {');
    expect(body).toContain('gap: 8px');
    const info = blockAfter(MOBILE, '.mini-player-body .media-info {');
    expect(info).toContain('min-width: 0');
  });
});

describe('M-02/M-04 — hamburger hides behind the modals the React-root cap exposes', () => {
  it('hides behind EVERY open .modal-overlay (the React-root z-1 cap traps them all, not just the playlist modal)', () => {
    expect(MOBILE).toContain('body:has(.modal-overlay:not(.hidden)');
    expect(MOBILE).not.toContain('.modal-overlay .playlist-modal');
  });

  it('comes back once an overlay closes, however it closes', () => {
    // runs the real selector list, not a string pin. automation run history
    // and the spotify / deezer arl playlist details close with
    // style.display = 'none' and stay in the dom; the first version only
    // let go on .hidden, so one open hid the hamburger until a reload.
    const start = MOBILE.indexOf('html.download-modal-open .hamburger-btn,');
    expect(start).toBeGreaterThan(-1);
    const selector = MOBILE.slice(start, MOBILE.indexOf('{', start)).trim();
    document.body.innerHTML = '<button class="hamburger-btn"></button>';
    const burger = document.querySelector('.hamburger-btn') as HTMLElement;
    expect(burger.matches(selector)).toBe(false);

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.style.display = 'flex';
    document.body.appendChild(overlay);
    expect(burger.matches(selector)).toBe(true);

    overlay.style.display = 'none';
    expect(burger.matches(selector)).toBe(false);

    overlay.removeAttribute('style');
    overlay.classList.add('hidden');
    expect(burger.matches(selector)).toBe(false);

    // markup written as style="display:none" is not re-serialized
    overlay.className = 'modal-overlay';
    overlay.setAttribute('style', 'display:none');
    expect(burger.matches(selector)).toBe(false);
    document.body.innerHTML = '';
  });

  it('hides behind the year story via its data-attribute hook', () => {
    expect(MOBILE).toContain('body:has([data-year-story-overlay]) .hamburger-btn');
    expect(YEAR_STORY_TSX).toContain('data-year-story-overlay');
  });

  it('opening My Account closes the mobile nav drawer first (the button lives inside it)', () => {
    expect(MY_ACCOUNTS_TS).toContain('function _maCloseMobileNav()');
    const open = MY_ACCOUNTS_TS.slice(
      MY_ACCOUNTS_TS.indexOf('export function openMyAccountsModal'),
    );
    expect(open.slice(0, 200)).toContain('_maCloseMobileNav();');
    const helper = MY_ACCOUNTS_TS.slice(MY_ACCOUNTS_TS.indexOf('function _maCloseMobileNav()'));
    expect(helper).toContain("classList.remove('mobile-open')");
    expect(helper).toContain("classList.remove('mobile-nav-open')");
  });
});

describe('the sidebar visualizer stays off on phones', () => {
  it('is hidden in every drawer state, not only while the drawer is closed (the violet line)', () => {
    const at = MOBILE.indexOf('the sidebar visualizer is a desktop thing');
    expect(at).toBeGreaterThan(-1);
    expect(blockAfter(MOBILE.slice(at), '.sidebar-visualizer {')).toContain(
      'display: none !important',
    );
    expect(MOBILE).not.toContain('.sidebar:not(.mobile-open) ~ .sidebar-visualizer');
  });
});

describe('M-05/M-17 — the mini player defers to the drawer and to the year story', () => {
  it('body.mobile-nav-open hides the player (z-99998 over the z-10001 drawer before)', () => {
    expect(blockAfter(MOBILE, 'body.mobile-nav-open #media-player {')).toContain(
      'display: none !important',
    );
  });

  it('the year story hides the player and the floating buttons (they covered Back/Done)', () => {
    const block = blockAfter(MOBILE, 'body:has([data-year-story-overlay]) #media-player,');
    expect(block).toContain('display: none !important');
    expect(MOBILE).toContain('body:has([data-year-story-overlay]) .activity-float-btn');
  });
});

describe('M-01 — video page chrome clears the hamburger', () => {
  it('the Back pill starts right of the fixed hamburger on detail, person and studio pages', () => {
    const block = blockAfter(MOBILE, '.vd-page .vd-back,');
    expect(block).toContain('.vp-page .vd-back');
    expect(block).toContain('.vst-page .vd-back');
    expect(block).toContain('margin-left: 76px');
  });
});

describe('M-19 — the no-op crew-line pin is gone (status: unresolved)', () => {
  it('mobile.css carries no white-space declaration for the crew line', () => {
    expect(MOBILE).not.toMatch(/\.vd-crew-line\s*\{[^}]*white-space/);
  });
});

describe('M-03 — Discover sticky chip bar clears the hamburger', () => {
  it('the bar pads left past the button at phone widths', () => {
    const at = PREMIER.indexOf('@media (max-width: 768px)');
    expect(at).toBeGreaterThan(-1);
    expect(blockAfter(PREMIER.slice(at), '.dsc-nav {')).toContain('padding-left: 64px');
  });

  it('the chip scroller gets end padding and a fade', () => {
    const block = blockAfter(PREMIER, '.dsc-nav-chips {\n        padding-right: 20px;');
    expect(block).toContain(
      'mask-image: linear-gradient(90deg, #000 calc(100% - 20px), transparent)',
    );
  });
});

describe('M-06 — the search field stays inside its container', () => {
  it('the search page source pill is capped so the input keeps its room', () => {
    const at = SEARCH_CSS.indexOf('@media (max-width: 640px)');
    expect(at).toBeGreaterThan(-1);
    expect(blockAfter(SEARCH_CSS.slice(at), '.searchBox {')).toContain('max-width: 100%');
    expect(blockAfter(SEARCH_CSS.slice(at), '.source {')).toContain('max-width: 42%');
  });

  it('the pill label ellipsises in its own element and the caret cannot be clipped off', () => {
    const label = blockAfter(SEARCH_CSS, '.sourceLabel {');
    expect(label).toContain('text-overflow: ellipsis');
    expect(label).toContain('min-width: 0');
    expect(blockAfter(SEARCH_CSS, '.caret {')).toContain('flex-shrink: 0');
    expect(SOURCE_ROW_TSX).toContain('styles.sourceLabel');
  });
});

describe('M-08 — Playlist Explorer empty state fits the phone', () => {
  it('the tree drops its desktop canvas geometry only while the empty state is its child', () => {
    const block = blockAfter(MOBILE, '.explorer-tree:has(> .explorer-empty) {');
    expect(block).toContain('min-width: 0');
    expect(block).toContain('padding: 24px 12px 48px');
  });

  it('the empty state fills its container instead of sizing from 100vw', () => {
    const block = blockAfter(MOBILE, '.explorer-empty {');
    expect(block).toContain('width: 100%');
    expect(block).not.toContain('100vw');
  });
});

describe('M-09 — the loading overlay message wraps on phones', () => {
  it('the message is width-capped and centred', () => {
    const block = blockAfter(MOBILE, '.loading-message {');
    expect(block).toContain('max-width: min(88vw, 480px)');
    expect(block).toContain('overflow-wrap: anywhere');
  });
});

describe('M-10 — Your Artists is a fluid grid on phones', () => {
  it('cards flex to half the row instead of a fixed 148px basis', () => {
    const block = blockAfter(MOBILE, '#your-artists-carousel > .ya-card {');
    expect(block).toContain('flex: 1 1 calc(50% - 9px) !important');
    expect(block).toContain('max-width: calc(50% - 9px) !important');
  });

  it('the carousel really leaves the horizontal rail (premier pins it there with !important)', () => {
    const block = blockAfter(MOBILE, '#your-artists-carousel {');
    expect(block).toContain('overflow-x: visible !important');
    expect(block).toContain('scroll-snap-type: none !important');
  });

  it('the tablet band goes three-up instead of stretching phone cards', () => {
    const at = MOBILE.indexOf('@media (min-width: 561px) and (max-width: 768px)');
    expect(at).toBeGreaterThan(-1);
    expect(blockAfter(MOBILE.slice(at), '#your-artists-carousel > .ya-card {')).toContain(
      'max-width: calc(33.333% - 12px) !important',
    );
  });
});

describe('M-11 — Artist Map stacks on phones, in CSS and in the canvas maths', () => {
  it('the content row becomes a column', () => {
    expect(blockAfter(MOBILE, '.artmap-content-row {')).toContain('flex-direction: column');
  });

  it('the genre sidebar becomes a capped full-width strip that is a flex column', () => {
    const block = blockAfter(MOBILE, '.artmap-genre-sidebar {');
    expect(block).toContain('display: flex');
    expect(block).toContain('max-height: 38%');
    expect(block).toContain('min-height: 0');
  });

  it('the genre list is the scroller inside the capped strip', () => {
    const block = blockAfter(MOBILE, '.artmap-genre-sidebar-list {');
    expect(block).toContain('overflow-y: auto');
    expect(block).toContain('min-height: 0');
  });

  it('the canvas may shrink to the row (replaced-element min-width trap)', () => {
    const block = blockAfter(MOBILE, '#artist-map-canvas {');
    expect(block).toContain('min-width: 0');
  });

  it('the island nav anchors to the canvas bottom-right', () => {
    const block = blockAfter(MOBILE, '.artmap-island-nav {');
    expect(block).toContain('bottom: 12px');
    expect(block).toContain('right: 12px');
  });

  it('the island quick-jump menu opens above the nav, inside the viewport', () => {
    const block = blockAfter(MOBILE, '.artmap-island-menu {');
    expect(block).toContain('bottom: 76px');
    expect(block).toContain('right: 12px');
  });

  it('Artist Web: the toolbar cluster gets its own wrapping row', () => {
    const block = blockAfter(MOBILE, '#artist-web-container .artmap-nav-right {');
    expect(block).toContain('flex-wrap: wrap');
    expect(block).toContain('flex: 1 1 100%');
    expect(blockAfter(MOBILE, '#artist-web-container .artweb-lens-btn {')).toContain(
      'font-size: 11px',
    );
  });

  it('the hamburger hides while a map or web is open (container is z-100, below it)', () => {
    expect(MOBILE).toContain('body:has(.artist-map-container) .hamburger-btn');
  });

  it('the shell float stack and mini player defer while a map or web is open', () => {
    // The activity button (z-index 999999) sits exactly where the stacked
    // island nav parks and intercepted its clicks in the visual harness;
    // the mini player covers the genre strip the same way.
    expect(MOBILE).toContain('body:has(.artist-map-container) #media-player');
    expect(MOBILE).toContain('body:has(.artist-map-container) .activity-float-btn');
    expect(MOBILE).toContain('body:has(.artist-map-container) .helper-float-btn');
  });

  it('the genre map measures the canvas itself, not the row that also holds the strip', () => {
    expect(ARTMAP_OVERLAY_TSX).toContain('canvas.clientHeight');
    expect(ARTMAP_OVERLAY_TSX).not.toContain('row?.clientHeight');
  });

  it('a resize in the stacked layout costs height, not width (no 120px canvas strip)', () => {
    expect(ARTMAP_LIFECYCLE_TS).toContain('export function artMapMeasureResizeStacked');
    expect(ARTMAP_OVERLAY_TSX).toContain('artMapMeasureResizeStacked(');
    expect(ARTMAP_LIFECYCLE_TS).toContain('containerHeight - (toolbarHeight');
  });

  it('the chrome variables stop reading the strip width as a left offset when stacked', () => {
    expect(ARTMAP_ASSEMBLY_TSX).toContain('stacked ? 16 : sidebarW + 16');
  });
});

describe('M-12 — Help & Docs on phones', () => {
  it('the docs menu button moves right of the global hamburger, in the ≤768px band the hamburger lives in', () => {
    const at = DOCS_CSS.indexOf('@media (max-width: 768px)');
    expect(at).toBeGreaterThan(-1);
    expect(blockAfter(DOCS_CSS.slice(at), '.dd-mobile-menu-btn {')).toContain('left: 64px');
  });

  it('the open drawer header clears the hamburger', () => {
    const at = DOCS_CSS.indexOf('@media (max-width: 768px)');
    expect(blockAfter(DOCS_CSS.slice(at), '.docs-sidebar-header {')).toContain(
      'padding-left: 76px',
    );
  });

  it('rendering a docs page closes the mobile drawer (nav clicks land in renderPage)', () => {
    const at = DOCS_JS.indexOf('function renderPage(pageId)');
    expect(at).toBeGreaterThan(-1);
    const body = DOCS_JS.slice(at, DOCS_JS.indexOf('function renderPrevNext', at));
    expect(body).toContain("classList.remove('dd-sidebar-open')");
  });
});

describe('M-15 — Library Radio kicker stays on one line', () => {
  it('the kicker does not wrap (batch #2 let it wrap word-per-line)', () => {
    const at = DASH_PREMIER.indexOf('@media (max-width: 640px)');
    expect(at).toBeGreaterThan(-1);
    const block = blockAfter(DASH_PREMIER.slice(at), '#dashboard-page .listen-kicker {');
    expect(block).toContain('white-space: nowrap');
    expect(block).not.toContain('white-space: normal');
  });

  it('the play button leaves the flex row so the text column gets the width', () => {
    const block = blockAfter(
      DASH_PREMIER,
      '#dashboard-page .listen-hero-play {\n        position: absolute;',
    );
    expect(block).toContain('right: 16px');
    expect(block).toContain('bottom: 16px');
    const at640 = DASH_PREMIER.indexOf('@media (max-width: 640px)');
    expect(blockAfter(DASH_PREMIER.slice(at640), '#dashboard-page .listen-hero-text {')).toContain(
      'padding-right: 58px',
    );
  });

  it('below 340px the kicker may wrap rather than clip (the hero is overflow:hidden)', () => {
    const at = DASH_PREMIER.indexOf('@media (max-width: 340px)');
    expect(at).toBeGreaterThan(-1);
    expect(blockAfter(DASH_PREMIER.slice(at), '#dashboard-page .listen-kicker {')).toContain(
      'white-space: normal',
    );
  });
});

describe('M-16 — modals size with dvh, not vh', () => {
  it('the generic modal overlay tracks the visible viewport', () => {
    const block = blockAfter(STYLE, '.modal-overlay {');
    expect(block).toContain('height: 100vh');
    expect(block).toContain('height: 100dvh');
  });

  it('the account modal caps at 88dvh', () => {
    expect(STYLE).toContain('max-height: 88vh; max-height: 88dvh');
  });

  it('the playlist detail modal swept up a dvh twin too (fold sweep)', () => {
    expect(blockAfter(STYLE, '.playlist-modal {')).toContain('max-height: 90dvh');
  });
});

describe('M-17 — the year story escapes its backdrop-filter ancestor', () => {
  it('the overlay portals to document.body', () => {
    expect(YEAR_STORY_TSX).toContain("import { createPortal } from 'react-dom'");
    expect(YEAR_STORY_TSX).toContain('return createPortal(');
    expect(YEAR_STORY_TSX).toContain('document.body');
  });

  it('the loading state centres in the stage like the slides do', () => {
    expect(blockAfter(YEAR_STORY_CSS, '.loadingWrap {')).toContain('margin: auto 0');
  });
});
