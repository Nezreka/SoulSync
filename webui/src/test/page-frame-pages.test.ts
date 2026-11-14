import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Every music page but the dashboard is drawn in the page frame
 * (components/page-header, `.pgf-*` in static/style.css): the same panel, the
 * same column, the same title. They drifted apart once, page by page (titles
 * from 20px to 48px, some in a panel and some not), so each page is named here
 * with the file that draws its panel and the file that draws its title.
 *
 * A page that leaves the frame on purpose is listed in OUTSIDE with the reason.
 */
const src = (path: string) => readFileSync(resolve(process.cwd(), 'src/routes', path), 'utf8');

const PANEL = /page-shell pgf-shell|<PageFrame\b/;
const TITLE = /pgf-title|<PageHeader\b/;

const PAGES: Record<string, { panel: string; title: string }> = {
  downloads: {
    panel: 'active-downloads/-ui/unified-downloads-page.tsx',
    title: 'active-downloads/-ui/dl-header.tsx',
  },
  search: { panel: 'search/-ui/search-page.tsx', title: 'search/-ui/search-page.tsx' },
  discover: { panel: 'discover/-ui/discover-page.tsx', title: 'discover/-ui/greeting-grid.tsx' },
  playlists: { panel: 'sync/-ui/sync-shell.tsx', title: 'sync/-ui/sync-shell.tsx' },
  'playlist explorer': {
    panel: 'playlist-explorer/-ui/explorer-page.tsx',
    title: 'playlist-explorer/-ui/explorer-page.tsx',
  },
  podcasts: {
    panel: 'podcasts/-ui/podcasts-layout.tsx',
    title: 'podcasts/-ui/podcast-search-bar.tsx',
  },
  audiobooks: {
    panel: 'audiobooks/-ui/audiobooks-layout.tsx',
    title: 'audiobooks/-ui/audiobooks-page.tsx',
  },
  watchlist: {
    panel: 'watchlist/-ui/watchlist-page.tsx',
    title: 'watchlist/-ui/watchlist-page.tsx',
  },
  wishlist: { panel: 'wishlist/-ui/wishlist-page.tsx', title: 'wishlist/-ui/wishlist-page.tsx' },
  import: { panel: 'import/-ui/import-page.tsx', title: 'import/-ui/import-page.tsx' },
  stats: { panel: 'stats/-ui/stats-page.tsx', title: 'stats/-ui/stats-page.tsx' },
  tools: { panel: 'tools/-ui/tools-page.tsx', title: 'tools/-ui/tools-page.tsx' },
  issues: { panel: 'issues/-ui/issues-page.tsx', title: 'issues/-ui/issues-page.tsx' },
  requests: { panel: 'requests/-ui/requests-page.tsx', title: 'requests/-ui/requests-page.tsx' },
};

/** Pages that do not sit in the frame, and why. */
const OUTSIDE: Record<string, string> = {
  dashboard: 'its own design, by decision',
  library: 'full bleed for the grid; its title and status line copy the frame type',
  automations: 'written before the frame with the same numbers (.automx-page); measured identical',
  settings: 'vanilla page with a narrower form column; its title copies the frame type',
  'sample studio': 'a three pane editor with no page header',
  chat: 'an app inside a panel; its bar is part of the chat chrome',
  help: 'a document reader with its own contents column',
};

describe('the page frame is on every music page', () => {
  for (const [page, files] of Object.entries(PAGES)) {
    it(`${page} sits in the frame's panel and uses its title`, () => {
      expect(src(files.panel), `${page}: ${files.panel} draws no frame panel`).toMatch(PANEL);
      expect(src(files.title), `${page}: ${files.title} draws no frame title`).toMatch(TITLE);
    });
  }

  it('stats keeps the column even though it styles its own header', () => {
    expect(src('stats/-ui/stats-page.tsx')).toContain('pgf-page');
  });

  it('names every page left outside, with a reason', () => {
    for (const [page, reason] of Object.entries(OUTSIDE)) {
      expect(reason.length, `${page} needs a reason`).toBeGreaterThan(10);
      expect(PAGES[page], `${page} is listed both inside and outside`).toBeUndefined();
    }
  });
});
