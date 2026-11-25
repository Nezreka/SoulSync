/**
 * One index for help search (plan 4.4): the docs articles, the pages this
 * profile can open, the settings (admins) or My Account (members), and the
 * actions it may run. The sheet's inline results and the Ctrl K palette both
 * read it.
 *
 * Built lazily: the docs are read once on the first search (docs.ts), the
 * rest is cheap and rebuilt per open, because a profile switch changes what
 * it may see. Every item's text is folded once and kept (WeakMap), so a key
 * press only scores.
 *
 * Member filtering happens here, at build time, not in the views: what is not
 * in the list cannot be shown, clicked or run.
 */

import {
  ACCOUNT_ENTRIES,
  SETTINGS_ENTRIES,
  landAccount,
  landSetting,
  tabTitle,
  type SettingsEntry,
} from '@/features/settings/search-entries';

import type { DocsArticle } from './docs';

import '../pages/register';
import { goToPage, openArticleInSheet } from '../links';
import { isVideoSide } from '../page';
import { pageHelpFor } from '../pages';
import { useHelpStore } from '../store';
import { actionsFor, type ActionContext, type SearchIconName, type Side } from './actions';
import { loadDocs, loadedDocs } from './docs';
import {
  highlight,
  noteRecent,
  prepare,
  queryWords,
  readRecent,
  recencyBoost,
  score,
  type Prepared,
  type Range,
  type Rankable,
} from './rank';

export type Kind = 'action' | 'setting' | 'page' | 'article';

export const GROUP_LABELS: Record<Kind, string> = {
  action: 'Actions',
  setting: 'Settings',
  page: 'Pages',
  article: 'Help',
};

/** how many of each kind a list shows; the rest is a few keys away */
export const GROUP_LIMIT: Record<Kind, number> = { action: 4, setting: 5, page: 4, article: 6 };

export interface SearchItem extends Rankable {
  id: string;
  kind: Kind;
  icon: SearchIconName;
  keys?: string[];
  /** a docs section id, for the side boost */
  section?: string;
  /** the docs id an article opens */
  articleId?: string;
  run: () => void | Promise<unknown>;
}

export interface Viewer {
  isAdmin: boolean;
  /** the page id the viewer is on (page.ts) */
  page: string;
  side: Side;
}

export interface Hit {
  item: SearchItem;
  score: number;
  snippet?: string;
}

export interface Group {
  kind: Kind;
  label: string;
  hits: Hit[];
}

// ── what this profile may open ─────────────────────────────────────────

/** pages a member never opens, whatever their allowed_pages says (profile-nav.js) */
const ADMIN_PAGES = new Set(['settings', 'video-settings', 'video-import', 'video-automations']);

type Globals = {
  isPageAllowed?: (pageId: string) => boolean;
  profileAllowedSides?: () => string;
};

function navButton(pageId: string): HTMLElement | null {
  const safe = pageId.replace(/[^\w-]/g, '');
  return document.querySelector<HTMLElement>(
    `.sidebar-nav .nav-button[data-page="${safe}"], .sidebar-nav .nav-button[data-video-page="${safe}"]`,
  );
}

export function sidesAllowed(): 'music' | 'video' | 'both' {
  const fn = (window as unknown as Globals).profileAllowedSides;
  const s = typeof fn === 'function' ? fn() : 'both';
  return s === 'music' || s === 'video' ? s : 'both';
}

/**
 * The sidebar's own answer, plus the rules behind it: profile-nav.js hides a
 * button the profile may not open (display none), init.js isPageAllowed
 * gates the music pages, and the side has to be one this profile has.
 */
export function canOpenPage(pageId: string, isAdmin: boolean): boolean {
  const video = pageId.startsWith('video-');
  const sides = sidesAllowed();
  if (video && sides === 'music') return false;
  if (!video && sides === 'video') return false;
  if (!isAdmin && ADMIN_PAGES.has(pageId)) return false;
  const btn = navButton(pageId);
  if (btn && btn.style.display === 'none') return false;
  const allowed = (window as unknown as Globals).isPageAllowed;
  if (!video && typeof allowed === 'function' && !allowed(pageId)) return false;
  return true;
}

// ── the items ─────────────────────────────────────────────────────────

function actionItems(ctx: ActionContext): SearchItem[] {
  return actionsFor(ctx).map((a) => ({
    id: a.id,
    kind: 'action',
    title: a.titleFor ? a.titleFor(ctx) : a.title,
    sub: a.hint,
    keywords: a.keywords,
    icon: a.icon,
    keys: a.keys,
    run: () => a.run(ctx),
  }));
}

function settingItem(entry: SettingsEntry, side: Side): SearchItem {
  const where = `Settings › ${tabTitle(entry.tab)}`;
  return {
    id: entry.id,
    kind: 'setting',
    title: entry.label,
    sub: entry.hint ? `${where} · ${entry.hint}` : where,
    keywords: `${entry.keywords ?? ''} ${tabTitle(entry.tab)} settings`,
    icon: 'gear',
    run: () => landSetting(entry, side),
  };
}

/** an admin's settings for this side; a member's My Account instead */
export function settingItems(viewer: Viewer): SearchItem[] {
  if (!viewer.isAdmin) {
    return ACCOUNT_ENTRIES.map((e) => ({
      id: e.id,
      kind: 'setting' as const,
      title: e.label,
      sub: e.hint,
      keywords: `${e.keywords} my account`,
      icon: 'user' as const,
      run: () => landAccount(e),
    }));
  }
  return SETTINGS_ENTRIES.filter((e) => !e.side || e.side === viewer.side).map((e) =>
    settingItem(e, viewer.side),
  );
}

const PAGE_ICONS: Record<string, SearchIconName> = {
  dashboard: 'home',
  search: 'search',
  discover: 'compass',
  sync: 'list',
  podcasts: 'mic',
  audiobooks: 'book',
  library: 'music',
  watchlist: 'heart',
  wishlist: 'inbox',
  requests: 'inbox',
  'active-downloads': 'down',
  import: 'folder',
  stats: 'chart',
  'sample-studio': 'wave',
  automations: 'bolt',
  chat: 'chat',
  tools: 'sliders',
  settings: 'gear',
  issues: 'flag',
  help: 'book',
  calendar: 'calendar',
};

function pageIcon(pageId: string): SearchIconName {
  const base = pageId.replace(/^video-/, '');
  if (pageId === 'video-library') return 'film';
  return PAGE_ICONS[base] ?? 'doc';
}

/** this side's sidebar pages the profile may open, by the sidebar's own labels */
export function pageItems(viewer: Viewer): SearchItem[] {
  const attr = viewer.side === 'video' ? 'data-video-page' : 'data-page';
  const buttons = Array.from(
    document.querySelectorAll<HTMLElement>(`.sidebar-nav .nav-button[${attr}]`),
  );
  const out: SearchItem[] = [];
  const seen = new Set<string>();
  for (const btn of buttons) {
    const pageId = btn.getAttribute(attr) || '';
    if (!pageId || seen.has(pageId)) continue;
    if (!canOpenPage(pageId, viewer.isAdmin)) continue;
    seen.add(pageId);
    const label = (btn.querySelector('.nav-text')?.textContent || btn.textContent || '')
      .replace(/\s+/g, ' ')
      .trim();
    if (!label) continue;
    const help = pageHelpFor(pageId);
    const summary = (viewer.isAdmin ? help.summary : help.memberSummary || help.summary) || '';
    out.push({
      id: `page:${pageId}`,
      kind: 'page',
      title: label,
      sub: summary,
      keywords: `${help.title} ${pageId.replace(/-/g, ' ')} page go to open`,
      icon: pageIcon(pageId),
      run: () => goToPage(pageId),
    });
  }
  return out;
}

const articleCache = new WeakMap<DocsArticle[], SearchItem[]>();

export function articleItems(docs: DocsArticle[] | null): SearchItem[] {
  if (!docs) return [];
  const hit = articleCache.get(docs);
  if (hit) return hit;
  const items = docs.map<SearchItem>((a) => ({
    id: `article:${a.id}`,
    kind: 'article',
    title: a.title,
    sub: a.section,
    keywords: `${a.lede} ${a.headings.join(' ')}`,
    // the lede first: a snippet from it reads best
    body: a.lede ? `${a.lede} ${a.text}` : a.text,
    icon: 'doc',
    section: a.sectionId,
    articleId: a.id,
    run: () => openArticleInSheet(a.id),
  }));
  articleCache.set(docs, items);
  return items;
}

/** an article is read in the sheet, from the inline results or the palette */
export { openArticleInSheet };

export function viewerFromStore(): Viewer {
  const st = useHelpStore.getState();
  return { isAdmin: st.isAdmin, page: st.page, side: isVideoSide() ? 'video' : 'music' };
}

/** everything this viewer may find right now; the docs once they are loaded */
export function buildItems(
  viewer: Viewer,
  docs: DocsArticle[] | null = loadedDocs(),
): SearchItem[] {
  const ctx: ActionContext = {
    isAdmin: viewer.isAdmin,
    side: viewer.side,
    canOpen: (pageId) => canOpenPage(pageId, viewer.isAdmin),
  };
  return [
    ...actionItems(ctx),
    ...settingItems(viewer),
    ...pageItems(viewer),
    ...articleItems(docs),
  ];
}

/** start reading the docs; resolves when they are in */
export function warmDocs(): Promise<unknown> {
  return loadDocs();
}

// ── ranking ───────────────────────────────────────────────────────────

const prepared = new WeakMap<SearchItem, Prepared>();

function prepOf(item: SearchItem): Prepared {
  let p = prepared.get(item);
  if (!p) {
    p = prepare(item);
    prepared.set(item, p);
  }
  return p;
}

function contextBoost(item: SearchItem, viewer: Viewer, q: string[]): number {
  let boost = 0;
  if (item.kind === 'article') {
    // the API reference is for scripts: it waits until someone asks for it
    if (item.section === 'api' && !q.includes('api')) boost -= 25;
    // the articles this page's help names, and this side's guides
    const here = pageHelpFor(viewer.page).articles ?? [];
    if (item.articleId && here.some((a) => a.id === item.articleId)) boost += 25;
    const videoGuide = !!item.section?.startsWith('video-');
    if (viewer.side === 'video') boost += videoGuide ? 15 : 0;
    else if (videoGuide) boost -= 10;
  }
  if (item.kind === 'page' && item.id === `page:${viewer.page}`) boost -= 20;
  if (item.kind === 'setting' && viewer.page.endsWith('settings')) boost += 10;
  return boost;
}

/** a hit under this share of its group's best is noise once the best is strong */
const WEAK = 0.25;

export function searchItems(
  items: SearchItem[],
  query: string,
  viewer: Viewer,
  limits: Partial<Record<Kind, number>> = GROUP_LIMIT,
): Group[] {
  const q = queryWords(query);
  if (!q.length) return [];
  const recent = readRecent();
  const now = Date.now();
  const byKind = new Map<Kind, (Hit & { order: number })[]>();
  for (const [order, item] of items.entries()) {
    const s = score(prepOf(item), q, item.body);
    if (!s.score) continue;
    const total = s.score + contextBoost(item, viewer, q) + recencyBoost(item.id, recent, now);
    const list = byKind.get(item.kind) ?? [];
    list.push({ item, score: total, snippet: s.snippet, order });
    byKind.set(item.kind, list);
  }
  const groups: Group[] = [];
  for (const kind of Object.keys(GROUP_LABELS) as Kind[]) {
    // equal scores keep the registry's order: Quick scan before Deep scan
    const hits = (byKind.get(kind) ?? []).sort((a, b) => b.score - a.score || a.order - b.order);
    if (!hits.length) continue;
    // a strong match in the group hides the ones that only brushed the word
    const floor = hits[0].score >= 100 ? hits[0].score * WEAK : 0;
    const kept = hits
      .filter((h) => h.score >= floor)
      .slice(0, limits[kind] ?? 5)
      .map(({ order: _order, ...hit }) => hit);
    groups.push({ kind, label: GROUP_LABELS[kind], hits: kept });
  }
  // the best match leads, so Enter on the first row is the right one;
  // equal bests keep Actions, Settings, Pages, Help
  return groups.sort((a, b) => b.hits[0].score - a.hits[0].score);
}

/** with nothing typed: what was used last, then the actions for here */
export function suggestions(items: SearchItem[], max = 6): Group[] {
  const recent = readRecent();
  const byId = new Map(items.map((i) => [i.id, i]));
  const used = recent
    .map((r) => byId.get(r.id))
    .filter((i): i is SearchItem => !!i)
    .slice(0, 4);
  const groups: Group[] = [];
  if (used.length) {
    groups.push({
      kind: used[0].kind,
      label: 'Recent',
      hits: used.map((item) => ({ item, score: 0 })),
    });
  }
  const shown = new Set(used.map((i) => i.id));
  const actions = items
    .filter((i) => i.kind === 'action' && !shown.has(i.id))
    .slice(0, Math.max(0, max - used.length));
  if (actions.length) {
    groups.push({
      kind: 'action',
      label: 'Actions',
      hits: actions.map((item) => ({ item, score: 0 })),
    });
  }
  return groups;
}

export function highlightTitle(item: SearchItem, query: string): Range[] {
  return highlight(item.title, queryWords(query));
}

export function highlightText(text: string, query: string): Range[] {
  return highlight(text, queryWords(query));
}

/** run what was picked and remember it */
export function runItem(item: SearchItem): void {
  noteRecent(item.id);
  try {
    const result = item.run();
    if (result && typeof (result as Promise<unknown>).catch === 'function') {
      (result as Promise<unknown>).catch(() => {
        window.showToast?.('That did not work. Try it from its page.', 'error');
      });
    }
  } catch {
    window.showToast?.('That did not work. Try it from its page.', 'error');
  }
}

// ── the hand-off to the Search page ───────────────────────────────────

export interface Handoff {
  label: string;
  hint: string;
  run: () => void;
}

/** "Search music for …": the last row, when this profile can open Search */
export function handoffFor(query: string, viewer: Viewer): Handoff | null {
  const text = query.trim();
  if (text.length < 2) return null;
  if (viewer.side === 'video') {
    if (!canOpenPage('video-search', viewer.isAdmin)) return null;
    return {
      label: 'Search movies and shows for',
      hint: 'opens the Search page',
      run: () => searchVideo(text),
    };
  }
  if (!canOpenPage('search', viewer.isAdmin)) return null;
  return {
    label: 'Search music for',
    hint: 'opens the Search page',
    run: () => void searchMusic(text),
  };
}

/** /search?q= runs the search on arrival (search-page.tsx) */
export async function searchMusic(text: string): Promise<void> {
  const href = `/search?q=${encodeURIComponent(text)}`;
  await window.navigateToPage?.('search');
  const router = window.SoulSyncWebRouter;
  if (router?.navigateToHref) {
    const ok = await router.navigateToHref(href);
    if (ok) return;
  }
  window.location.assign(href);
}

/** the video Search page reads its own field (video-search.js): fill it */
export function searchVideo(text: string): void {
  goToPage('video-search');
  let tries = 0;
  const fill = () => {
    const input = document.querySelector<HTMLInputElement>('[data-video-search-input]');
    if (input && (input.offsetParent || input.getClientRects().length)) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.focus({ preventScroll: true });
      return;
    }
    tries += 1;
    if (tries < 30) setTimeout(fill, 100);
  };
  setTimeout(fill, 0);
}
