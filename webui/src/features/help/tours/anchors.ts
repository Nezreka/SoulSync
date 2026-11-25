/**
 * Every data-tour anchor, and who writes it (plan 4.9, 6.1). A tour step,
 * What's new's Show me and page help name these ids, never a styling class.
 *
 * anchors.test.tsx renders or parses each owner and fails when an anchor is
 * missing from it, when an id here is used by nobody, or when the code
 * carries a data-tour this file doesn't list. So renaming a component
 * fails CI instead of stranding a tour.
 *
 * Several elements may carry one id (the music and the video sidebar both
 * say shell.nav): the first one on screen wins.
 */

export interface AnchorOwner {
  /** where it is written, from webui/ */
  file: string;
  /** what it is, for the next person */
  what: string;
  /**
   * how the test checks it: 'html' parses index.html, 'source' reads the
   * file (vanilla templates built at runtime), 'render' renders the React
   * component in anchors.test.tsx
   */
  check: 'html' | 'source' | 'render';
}

export const ANCHORS = {
  // the shell (index.html), on every page of both sides
  'shell.menu': { file: 'index.html', what: 'the phone menu button', check: 'html' },
  'shell.nav': { file: 'index.html', what: 'the sidebar nav, music and video', check: 'html' },
  'shell.side-switch': { file: 'index.html', what: 'the Music and Video switch', check: 'html' },
  'shell.profile': { file: 'index.html', what: 'the profile chip', check: 'html' },
  'shell.version': { file: 'index.html', what: 'the version button', check: 'html' },
  'shell.activity': { file: 'index.html', what: 'the Server Activity button', check: 'html' },
  'shell.notifications': { file: 'index.html', what: 'the notifications bell', check: 'html' },
  'shell.help': { file: 'index.html', what: 'the ? button', check: 'html' },
  'shell.open-player': { file: 'index.html', what: 'the Open the player link', check: 'html' },

  // music Search (React)
  'search.field': {
    file: 'src/routes/search/-ui/search-bar.tsx',
    what: 'the search field',
    check: 'render',
  },
  'search.modes': {
    file: 'src/routes/search/-ui/source-row.tsx',
    what: 'Catalog, Videos and Files',
    check: 'render',
  },
  'search.source': {
    file: 'src/routes/search/-ui/source-row.tsx',
    what: 'the source picker in the field',
    check: 'render',
  },
  'search.results': {
    file: 'src/routes/search/-ui/search-page.tsx',
    what: 'the results, once there are some',
    check: 'render',
  },

  // the download window (vanilla, built by shared-helpers.js)
  'download.window': {
    file: 'static/shared-helpers.js',
    what: 'an album or track download window',
    check: 'source',
  },
  'download.start': {
    file: 'static/shared-helpers.js',
    what: 'its Begin Analysis button',
    check: 'source',
  },

  // Downloads (React)
  'downloads.header': {
    file: 'src/routes/active-downloads/-ui/dl-header.tsx',
    what: 'the Downloads header, tabs and filters',
    check: 'render',
  },

  // the video side (index.html)
  'video.nav-discover': {
    file: 'index.html',
    what: 'Discover in the video sidebar',
    check: 'html',
  },
  'video.search-field': { file: 'index.html', what: 'the video search field', check: 'html' },
  'video.search-modes': { file: 'index.html', what: 'Titles, Releases and Fresh', check: 'html' },
  'video.search-results': { file: 'index.html', what: 'the video search results', check: 'html' },
  'video.title-actions': {
    file: 'index.html',
    what: 'a movie or show page’s main buttons',
    check: 'html',
  },
  'video.requests': { file: 'index.html', what: 'the video Requests page', check: 'html' },
} as const satisfies Record<string, AnchorOwner>;

export type AnchorId = keyof typeof ANCHORS;

export const ANCHOR_IDS = Object.keys(ANCHORS) as AnchorId[];

export function isAnchorId(value: unknown): value is AnchorId {
  return typeof value === 'string' && value in ANCHORS;
}
