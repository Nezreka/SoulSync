/**
 * The search index: what an admin and a member can find (plan 4.10), the
 * sidebar's own answer for pages, ranking across kinds, the hand-off.
 */

import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { ACTIONS, actionsFor } from './actions';
import { loadDocs } from './docs';
import {
  buildItems,
  canOpenPage,
  handoffFor,
  pageItems,
  searchItems,
  searchMusic,
  settingItems,
  suggestions,
  type Viewer,
} from './index';
import { RECENT_KEY, noteRecent } from './rank';

const DIR = join(process.cwd(), 'static', 'docs-content');
const FILES = readdirSync(DIR).filter((f) => f.endsWith('.js'));

type G = Window & {
  isPageAllowed?: (id: string) => boolean;
  profileAllowedSides?: () => string;
  canDownload?: () => boolean;
};
const g = window as unknown as G;

const ADMIN: Viewer = { isAdmin: true, page: 'active-downloads', side: 'music' };
const MEMBER: Viewer = { isAdmin: false, page: 'discover', side: 'music' };

/** the sidebar as index.html has it, cut down */
function sidebar(hidden: string[] = []) {
  const music = [
    ['dashboard', 'Dashboard'],
    ['search', 'Search'],
    ['discover', 'Discover'],
    ['library', 'Library'],
    ['watchlist', 'Watchlist'],
    ['wishlist', 'Wishlist'],
    ['active-downloads', 'Downloads'],
    ['stats', 'Stats'],
    ['tools', 'Tools'],
    ['settings', 'Settings'],
    ['issues', 'Issues'],
    ['help', 'Help & Docs'],
  ];
  const video = [
    ['video-dashboard', 'Dashboard'],
    ['video-search', 'Search'],
    ['video-library', 'Library'],
    ['video-settings', 'Settings'],
  ];
  const nav = document.createElement('nav');
  nav.className = 'sidebar-nav';
  nav.innerHTML = [
    ...music.map(
      ([id, label]) =>
        `<a class="nav-button" data-page="${id}" ${hidden.includes(id) ? 'style="display: none;"' : ''}><span class="nav-text">${label}</span></a>`,
    ),
    ...video.map(
      ([id, label]) =>
        `<a class="nav-button" data-video-page="${id}"><span class="nav-text">${label}</span></a>`,
    ),
  ].join('');
  document.body.appendChild(nav);
}

beforeAll(async () => {
  await loadDocs(
    async (u) => readFileSync(join(DIR, u.split('/').pop()!), 'utf8'),
    FILES.map((f) => `/static/docs-content/${f}`),
  );
});

beforeEach(() => {
  document.body.innerHTML = '';
  document.body.setAttribute('data-side', 'music');
  localStorage.removeItem(RECENT_KEY);
});

afterEach(() => {
  delete g.isPageAllowed;
  delete g.profileAllowedSides;
  delete g.canDownload;
  delete window.navigateToPage;
  delete window.SoulSyncWebRouter;
  vi.restoreAllMocks();
});

describe('what a member can find', () => {
  it('no admin actions, ever', () => {
    sidebar();
    const ids = buildItems(MEMBER)
      .filter((i) => i.kind === 'action')
      .map((i) => i.id);
    const admin = ACTIONS.filter((a) => a.admin).map((a) => a.id);
    expect(admin.length).toBeGreaterThan(4);
    for (const id of admin) expect(ids).not.toContain(id);
    expect(ids).toContain('action:whats-new');
  });

  it('no Settings, only their own My Account', () => {
    const items = settingItems(MEMBER);
    expect(items.length).toBeGreaterThan(0);
    expect(items.every((i) => i.id.startsWith('account:'))).toBe(true);
    expect(items.some((i) => (i.sub ?? '').includes('Settings ›'))).toBe(false);
  });

  it('a page they were refused is not there, nor is Settings', () => {
    sidebar(['stats']);
    g.isPageAllowed = (id) => id !== 'tools';
    const titles = pageItems(MEMBER).map((i) => i.id);
    expect(titles).not.toContain('page:stats'); // the sidebar hid it
    expect(titles).not.toContain('page:tools'); // init.js refuses it
    expect(titles).not.toContain('page:settings'); // admin only
    expect(titles).toContain('page:library');
    expect(
      searchItems(buildItems(MEMBER), 'stats', MEMBER).flatMap((gr) =>
        gr.hits.map((h) => h.item.id),
      ),
    ).not.toContain('page:stats');
  });

  it('a page on a side they do not have is not there', () => {
    sidebar();
    g.profileAllowedSides = () => 'music';
    expect(canOpenPage('video-library', false)).toBe(false);
    expect(pageItems({ ...MEMBER, side: 'video' })).toEqual([]);
  });

  it('page-bound actions follow the page', () => {
    sidebar(['watchlist']);
    const ctx = {
      isAdmin: false,
      side: 'music' as const,
      canOpen: (p: string) => canOpenPage(p, false),
    };
    expect(actionsFor(ctx).map((a) => a.id)).not.toContain('action:watchlist-scan');
    g.canDownload = () => false;
    expect(actionsFor(ctx).map((a) => a.id)).not.toContain('action:wishlist-process');
  });

  it('a member searching "scan" gets no library scans', () => {
    sidebar();
    const hits = searchItems(buildItems(MEMBER), 'scan', MEMBER).flatMap((gr) => gr.hits);
    expect(hits.map((h) => h.item.id)).not.toContain('action:library-quick-scan');
    expect(hits.map((h) => h.item.id)).not.toContain('action:library-deep-scan');
  });
});

describe('what an admin can find', () => {
  it('every kind, settings for this side only', () => {
    sidebar();
    const items = buildItems(ADMIN);
    const kinds = new Set(items.map((i) => i.kind));
    expect([...kinds].sort()).toEqual(['action', 'article', 'page', 'setting']);
    const settings = items.filter((i) => i.kind === 'setting');
    expect(settings.some((i) => i.title === 'Download path')).toBe(true);
    expect(settings.some((i) => i.title === 'Video server')).toBe(false);
    document.body.setAttribute('data-side', 'video');
    const video = settingItems({ ...ADMIN, side: 'video' });
    expect(video.some((i) => i.title === 'Video server')).toBe(true);
    expect(video.some((i) => i.title === 'Plex server URL')).toBe(false);
  });

  it('"scan" leads with the scan actions, like the mockup', () => {
    sidebar();
    const groups = searchItems(buildItems(ADMIN), 'scan', ADMIN);
    expect(groups[0].label).toBe('Actions');
    expect(
      groups[0].hits
        .slice(0, 3)
        .map((h) => h.item.title)
        .sort(),
    ).toEqual(['Deep scan the library', 'Quick scan the library', 'Scan the watchlist now']);
    expect(groups.map((gr) => gr.label)).toContain('Help');
  });

  it('"path" finds the download path setting and the folder guide', () => {
    sidebar();
    const groups = searchItems(buildItems(ADMIN), 'path', ADMIN);
    const settings = groups.find((gr) => gr.label === 'Settings')!;
    expect(settings.hits.map((h) => h.item.title)).toContain('Download path');
    const help = groups.find((gr) => gr.label === 'Help')!;
    expect(help.hits.length).toBeGreaterThan(0);
  });

  it('a typo still finds it', () => {
    sidebar();
    const top = searchItems(buildItems(ADMIN), 'wathclist', ADMIN).flatMap((gr) => gr.hits);
    expect(top.map((h) => h.item.title)).toContain('Watchlist');
  });

  it('what was picked lately comes first among equals, and leads the empty palette', () => {
    sidebar();
    const items = buildItems(ADMIN);
    noteRecent('action:library-deep-scan');
    const groups = searchItems(items, 'scan the library', ADMIN);
    expect(groups[0].hits[0].item.id).toBe('action:library-deep-scan');
    const empty = suggestions(items);
    expect(empty[0].label).toBe('Recent');
    expect(empty[0].hits[0].item.id).toBe('action:library-deep-scan');
  });

  it('this page is not offered as a place to go first', () => {
    sidebar();
    const viewer = { ...ADMIN, page: 'library' };
    const pages = searchItems(buildItems(viewer), 'library', viewer).find(
      (gr) => gr.label === 'Pages',
    );
    expect(pages?.hits[0]?.item.id).toBe('page:library'); // still found, just not boosted
  });
});

describe('the hand-off to Search', () => {
  it('offers music search when Search is allowed, not for one letter', () => {
    sidebar();
    expect(handoffFor('a', ADMIN)).toBeNull();
    expect(handoffFor('aphex', ADMIN)?.label).toBe('Search music for');
    g.isPageAllowed = (id) => id !== 'search';
    expect(handoffFor('aphex', MEMBER)).toBeNull();
  });

  it('opens /search?q= through the app router', async () => {
    const navigateToPage = vi.fn();
    const navigateToHref = vi.fn(async () => true);
    window.navigateToPage = navigateToPage;
    window.SoulSyncWebRouter = { navigateToHref } as unknown as typeof window.SoulSyncWebRouter;
    await searchMusic('Aphex Twin & co');
    expect(navigateToPage).toHaveBeenCalledWith('search');
    expect(navigateToHref).toHaveBeenCalledWith('/search?q=Aphex%20Twin%20%26%20co');
  });

  it('on the video side it is movies and shows', () => {
    sidebar();
    expect(handoffFor('dune', { ...ADMIN, side: 'video' })?.label).toBe(
      'Search movies and shows for',
    );
  });
});
