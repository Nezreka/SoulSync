/**
 * The docs articles, read from the same files the Help & Docs page runs
 * (webui/static/docs-content/*.js). Nothing is copied: each file calls
 * registerDocsSection({ id, title, pages: [{ id, title, lede, body }] }),
 * and docs.js keeps that registry private, so this reads the files again
 * (the page loaded them a moment ago, the browser has them cached) and runs
 * each with a stand-in registerDocsSection that just collects the sections.
 *
 * Loaded once, on the first search or article, never at boot.
 */

import { plainText } from './rank';

export interface DocsPage {
  id: string;
  title: string;
  lede?: string;
  body?: string;
}

export interface DocsSection {
  id: string;
  title: string;
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

const SCRIPT_SELECTOR = 'script[src*="docs-content/"]';

/** the docs files this page loaded, in page order, with their cache-busting query */
export function docsScriptUrls(doc: Document = document): string[] {
  return Array.from(doc.querySelectorAll<HTMLScriptElement>(SCRIPT_SELECTOR))
    .map((s) => s.getAttribute('src') || '')
    .filter(Boolean);
}

/**
 * Run one docs file and return what it registered. The files are the app's
 * own static content (already executed by the page as plain scripts); the
 * only name they reach for is registerDocsSection.
 */
export function readDocsScript(source: string): DocsSection[] {
  const found: DocsSection[] = [];
  const collect = (section: unknown) => {
    const s = section as DocsSection | null;
    if (s && typeof s.id === 'string' && Array.isArray(s.pages)) found.push(s);
  };
  try {
    // eslint-disable-next-line @typescript-eslint/no-implied-eval -- first-party docs, see the file comment
    new Function('registerDocsSection', `${source}\n//# sourceURL=help-docs.js`)(collect);
  } catch {
    /* a broken file costs its own articles, never the rest */
  }
  return found;
}

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

export type FetchText = (url: string) => Promise<string>;

const browserFetch: FetchText = async (url) => {
  const res = await fetch(url, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.text();
};

let loading: Promise<DocsArticle[]> | null = null;
let loaded: DocsArticle[] | null = null;
const byId = new Map<string, DocsArticle>();

/** every article, read once; later calls get the same answer */
export function loadDocs(
  fetchText: FetchText = browserFetch,
  urls: string[] = docsScriptUrls(),
): Promise<DocsArticle[]> {
  if (loading) return loading;
  loading = (async () => {
    const texts = await Promise.all(urls.map((u) => fetchText(u).catch(() => '')));
    const articles = toArticles(texts.flatMap((t) => (t ? readDocsScript(t) : [])));
    byId.clear();
    for (const a of articles) {
      byId.set(a.id, a);
      // a section id opens its first page, as docs.js resolves it
      if (!byId.has(a.sectionId)) byId.set(a.sectionId, a);
    }
    loaded = articles;
    // nothing came back (offline, a blip): the next search asks again
    if (!articles.length) loading = null;
    return articles;
  })();
  return loading;
}

/** what loadDocs found, without waiting (null until it has run) */
export function loadedDocs(): DocsArticle[] | null {
  return loaded;
}

export function findArticle(id: string): DocsArticle | undefined {
  return byId.get(id);
}

/** for tests */
export function resetDocs(): void {
  loading = null;
  loaded = null;
  byId.clear();
}
