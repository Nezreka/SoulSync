/**
 * The docs articles, read from the registry the Help & Docs page already
 * built: each webui/static/docs-content/*.js file calls
 * registerDocsSection({ id, title, pages: [{ id, title, lede, body }] }) as
 * it loads, and docs.js publishes what it collected as
 * window.SoulSyncDocs.sections(). Nothing is copied or fetched again.
 *
 * Read on the first search or article, never at boot, and kept until the
 * registry grows.
 */

import { plainText } from './rank';

export interface DocsPage {
  id: string;
  title: string;
  /** who the page is for, when it isn't its section's */
  needs?: string | string[];
  lede?: string;
  body?: string;
}

export interface DocsSection {
  id: string;
  title: string;
  /** who the section is for (docs.js reads it, #2174) */
  needs?: string | string[];
  pages: DocsPage[];
}

export interface DocsArticle {
  id: string;
  title: string;
  lede: string;
  sectionId: string;
  section: string;
  /** markdown, for the reader */
  body: string;
  /** the body as plain prose, for matching and snippets */
  text: string;
  /** its ## and ### headings, matched as keywords */
  headings: string[];
}

declare global {
  interface Window {
    /** docs.js's registry (static/docs.js): only what this viewer may read (#2174) */
    SoulSyncDocs?: {
      sections: () => DocsSection[];
      /** the guide exists, but isn't for this viewer */
      hidden?: (id: string) => boolean;
      /** this viewer may read the guide */
      visible?: (id: string) => boolean;
    };
  }
}

export type DocsSource = () => DocsSection[];

/** what docs.js registered; [] before it ran or when it is missing */
export const registrySections: DocsSource = () => {
  if (typeof window === 'undefined') return [];
  try {
    const sections = window.SoulSyncDocs?.sections();
    return Array.isArray(sections) ? sections : [];
  } catch {
    return [];
  }
};

const HEADING = /^#{2,3}\s+(.+)$/gm;

export function toArticles(sections: DocsSection[]): DocsArticle[] {
  const out: DocsArticle[] = [];
  const seen = new Set<string>();
  for (const section of sections) {
    for (const page of section.pages) {
      if (!page || typeof page.id !== 'string' || seen.has(page.id)) continue;
      seen.add(page.id);
      const body = typeof page.body === 'string' ? page.body : '';
      out.push({
        id: page.id,
        title: String(page.title || page.id),
        lede: String(page.lede || ''),
        sectionId: section.id,
        section: String(section.title || ''),
        body,
        text: plainText(body),
        headings: Array.from(body.matchAll(HEADING), (m) => m[1].replace(/[*`]/g, '').trim()),
      });
    }
  }
  return out;
}

let loaded: DocsArticle[] | null = null;
/** what `loaded` was built from: a section registered later, or a viewer who
 *  may read a different set (#2174), rebuilds */
let builtFrom = '';

function shapeOf(sections: DocsSection[]): string {
  return sections
    .map((s) => (s && Array.isArray(s.pages) ? `${s.id}:${s.pages.length}` : ''))
    .join('|');
}
const byId = new Map<string, DocsArticle>();

/**
 * Every article. Built once from the registry, and again only when it has
 * grown. Async for its callers' sake: the sheet and the palette await it.
 */
export function loadDocs(source: DocsSource = registrySections): Promise<DocsArticle[]> {
  const sections = source();
  const shape = shapeOf(sections);
  if (loaded && loaded.length && shape === builtFrom) return Promise.resolve(loaded);
  const articles = toArticles(sections.filter(isSection));
  byId.clear();
  for (const a of articles) {
    byId.set(a.id, a);
    // a section id opens its first page, as docs.js resolves it
    if (!byId.has(a.sectionId)) byId.set(a.sectionId, a);
  }
  loaded = articles;
  // nothing there yet (docs.js not run): the next search asks again
  builtFrom = articles.length ? shape : '';
  return Promise.resolve(articles);
}

function isSection(s: unknown): s is DocsSection {
  const sec = s as DocsSection | null;
  return !!sec && typeof sec.id === 'string' && Array.isArray(sec.pages);
}

/** what loadDocs found, without waiting (null until it has run) */
export function loadedDocs(): DocsArticle[] | null {
  return loaded;
}

export function findArticle(id: string): DocsArticle | undefined {
  return byId.get(id);
}

/** the guide exists but isn't for this viewer (an admin's guide, a part of
 *  SoulSync their profile doesn't have) */
export function hiddenFromViewer(id: string): boolean {
  try {
    return window.SoulSyncDocs?.hidden?.(id) === true;
  } catch {
    return false;
  }
}

/** may this viewer read the guide. true while docs.js hasn't said (the
 *  article view answers then) */
export function visibleToViewer(id: string): boolean {
  try {
    const fn = window.SoulSyncDocs?.visible;
    return typeof fn === 'function' ? fn(id) : true;
  } catch {
    return true;
  }
}

/** for tests */
export function resetDocs(): void {
  loaded = null;
  builtFrom = '';
  byId.clear();
}
