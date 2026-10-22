import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * The video wishlist is a shared page, like Downloads: the React /wishlist route
 * on its Movies / TV / YouTube tabs. From the video sidebar it opens in place,
 * the shell side never flips, and a profile is let in by the VIDEO wishlist's
 * permission. These run the real functions out of video-side.js and init.js.
 */

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');
const SIDE = read('static/video/video-side.js');
const SIDE_CSS = read('static/video/video-side.css');
const INIT = read('static/init.js');
const BRIDGE = read('static/shell-bridge.js');

function between(src: string, start: string, end: string): string {
  const a = src.indexOf(start);
  expect(a, `marker not found: ${start}`).toBeGreaterThan(-1);
  const b = src.indexOf(end, a);
  expect(b, `end marker not found: ${end}`).toBeGreaterThan(-1);
  return src.slice(a, b);
}

/** a fake sessionStorage the lifted functions see as a global. */
function storage(initial: Record<string, string> = {}) {
  const data = { ...initial };
  return {
    data,
    api: {
      getItem: (k: string) => (k in data ? data[k] : null),
      setItem: (k: string, v: string) => {
        data[k] = String(v);
      },
    },
  };
}

function sideFns(session: ReturnType<typeof storage>['api']) {
  const body = [
    'var VIDEO_WISHLIST_TABS = { movies: 1, tv: 1, youtube: 1 };',
    extractFunction('lastVideoWishlistTab', SIDE),
    extractFunction('parseSharedPath', SIDE),
  ].join('\n');
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(
    'sessionStorage',
    `${body}\nreturn { lastVideoWishlistTab, parseSharedPath };`,
  )(session) as {
    lastVideoWishlistTab: () => string;
    parseSharedPath: (path: string, search: string) => string | null;
  };
}

describe('the sidebar hands off to the React page', () => {
  it('navigate() hands video-wishlist off before any subpage is shown', () => {
    const nav = between(SIDE, 'function navigate(', 'function applySide(');
    expect(nav).toContain(
      "if (pageId === 'video-wishlist') { gotoUnifiedWishlist(mode); return; }",
    );
    // the hand-off runs before showPage(), so no stale subpage can flash
    expect(nav.indexOf('gotoUnifiedWishlist(mode)')).toBeLessThan(nav.indexOf('showPage(pageId)'));
  });

  it('routes to /wishlist on the remembered tab, marks the nav and never flips the side', () => {
    const handoff = between(SIDE, 'function gotoUnifiedWishlist(', 'function navigate(');
    expect(handoff).toContain("setActiveNav('video-wishlist')");
    expect(handoff).toContain("document.body.setAttribute('data-video-page', 'video-wishlist')");
    expect(handoff).toContain(
      "navigateToPage('wishlist', { query: { media: media }, replace: replace })",
    );
    expect(handoff).not.toContain('_switchAppSide(');
    expect(handoff).not.toContain('applySide(');
  });

  it('the css reveals the React host and hides the video host for it', () => {
    expect(SIDE_CSS).toContain(
      'body[data-side="video"][data-video-page="video-wishlist"] #webui-react-root',
    );
    expect(SIDE_CSS).toContain(
      'body[data-side="video"][data-video-page="video-wishlist"] #video-page-host',
    );
  });

  it('reopens on the tab you last had, and on Movies when it knows nothing', () => {
    expect(sideFns(storage().api).lastVideoWishlistTab()).toBe('movies');
    expect(sideFns(storage({ 'soulsync.videoWishlistTab': 'tv' }).api).lastVideoWishlistTab()).toBe(
      'tv',
    );
    // junk in storage is not a tab
    expect(
      sideFns(storage({ 'soulsync.videoWishlistTab': 'music' }).api).lastVideoWishlistTab(),
    ).toBe('movies');
    // storage that throws (private mode) still lands somewhere
    const broken = {
      getItem: () => {
        throw new Error('denied');
      },
      setItem: () => {
        throw new Error('denied');
      },
    };
    expect(sideFns(broken).lastVideoWishlistTab()).toBe('movies');
  });
});

describe('a reload onto the video wishlist stays on the video side', () => {
  it('reads /wishlist?media=<video tab> as the video wishlist and remembers that tab', () => {
    const s = storage({ 'soulsync.videoWishlistTab': 'movies' });
    const { parseSharedPath } = sideFns(s.api);
    expect(parseSharedPath('/wishlist', '?media=youtube&q=x')).toBe('video-wishlist');
    // the URL's tab wins over the remembered one, so the hand-off keeps it
    expect(s.data['soulsync.videoWishlistTab']).toBe('youtube');
    expect(parseSharedPath('/wishlist/', '?media=tv')).toBe('video-wishlist');
  });

  it('leaves the music wishlist and every other page alone', () => {
    const { parseSharedPath } = sideFns(storage().api);
    expect(parseSharedPath('/wishlist', '')).toBeNull();
    expect(parseSharedPath('/wishlist', '?media=music')).toBeNull();
    expect(parseSharedPath('/wishlist', '?media=audiobooks')).toBeNull();
    expect(parseSharedPath('/active-downloads', '?media=tv')).toBeNull();
    expect(parseSharedPath('/wishlistx', '?media=tv')).toBeNull();
    expect(parseSharedPath('', '?media=tv')).toBeNull();
  });

  it('is not a bootPage, so the boot never re-asserts /video-wishlist over the React url', () => {
    const boot = between(SIDE, 'function init() {', 'window.SoulSyncVideo = window.SoulSyncVideo');
    expect(boot).toContain(
      'var bootShared = (bootDetail || bootPage) ? null : parseSharedPath(BOOT_PATH, BOOT_SEARCH);',
    );
    expect(boot).toContain(
      "var bootSide = (bootDetail || bootPage || bootShared) ? 'video' : readSide();",
    );
    expect(boot).toContain('var initialPage = bootPage || bootShared ||');
    // a /video-wishlist deep link IS a bootPage, but its url is handed off to
    // /wishlist?media=...; re-asserting /video-wishlist over that sent the
    // router to an unknown path and the shell bounced the profile home
    expect(SIDE).toContain("var HANDED_OFF = { 'video-wishlist': 1 };");
    expect(boot).toContain('if (bootPage && !HANDED_OFF[initialPage] &&');
    // the re-assert is still gated on bootPage only
    expect(boot).toContain('if (bootPage) [120, 350, 700].forEach');
    expect(boot).toMatch(/var reassertPage = function \(\) \{\s*if \(bootPage &&/);
  });
});

describe('permissions follow the side the page is showing', () => {
  function access(profile: { allowedPages: string[] | null; sides: 'music' | 'video' | 'both' }) {
    // the real init.js functions, over a stub of the profile state they read
    const body = [
      "var SHARED_VIDEO_VIEWS = { wishlist: { media: ['movies', 'tv', 'youtube'], videoPage: 'video-wishlist' } };",
      extractFunction('isPageAccessAllowed', INIT),
    ].join('\n');
    const isPageAllowed = (id: string) =>
      !profile.allowedPages || profile.allowedPages.includes(id);
    const profileAllowedSides = () => profile.sides;
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    return new Function(
      'isPageAllowed',
      'profileAllowedSides',
      `${body}\nreturn isPageAccessAllowed;`,
    )(isPageAllowed, profileAllowedSides) as (page: string, search: string) => boolean;
  }

  it('the frozen SHARED_VIDEO_VIEWS above is the one init.js declares', () => {
    expect(INIT).toContain(
      "    wishlist: { media: ['movies', 'tv', 'youtube'], videoPage: 'video-wishlist' },",
    );
  });

  it('a video-only profile reaches the video wishlist without the music one', () => {
    const can = access({ allowedPages: ['video-wishlist'], sides: 'video' });
    expect(can('wishlist', '?media=tv')).toBe(true);
    expect(can('wishlist', '?media=movies&q=dune')).toBe(true);
    expect(can('wishlist', '')).toBe(false); // the music wishlist is still theirs to be denied
    expect(can('wishlist', '?media=music')).toBe(false);
  });

  it('a music-only profile is kept off the video tabs even with every page allowed', () => {
    const can = access({ allowedPages: null, sides: 'music' });
    expect(can('wishlist', '?media=youtube')).toBe(false);
    expect(can('wishlist', '?media=music')).toBe(true);
  });

  it('every other page asks exactly what it always asked', () => {
    const can = access({ allowedPages: ['active-downloads'], sides: 'both' });
    expect(can('active-downloads', '?media=video')).toBe(true);
    expect(can('library', '?media=tv')).toBe(false);
  });

  it('the music boot asks it too, or a video-only profile is sent home on load', () => {
    const boot = between(
      INIT,
      'const homePage = getProfileHomePage();',
      'const videoHome = profileVideoHomePage();',
    );
    expect(boot).toContain('isPageAccessAllowed(urlPage, window.location.search)');
    expect(boot).toContain('isPageAccessAllowed(redirectedPage, window.location.search)');
    expect(boot).not.toMatch(/isPageAllowed\((urlPage|redirectedPage)\)/);
  });

  it('the shell asks it on Back, and the bridge hands it to the React guard', () => {
    const sync = between(
      BRIDGE,
      'function syncActivePageFromLocation() {',
      'const SHELL_BRIDGE_READY_EVENT',
    );
    expect(sync).toContain('if (!isPageAccessAllowed(targetPage, window.location.search)) {');
    expect(BRIDGE).toContain(
      'return search === undefined ? isPageAllowed(pageId) : isPageAccessAllowed(pageId, search);',
    );
  });
});
