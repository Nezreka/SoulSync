/**
 * The docs, read from the registry docs.js keeps (window.SoulSyncDocs), with
 * the real docs.js and docs-content files run the way the page runs them
 * (plan 6.1: every docs id the help names resolves).
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';

import '../pages/register';
import { pageHelpFor, registeredPageIds } from '../pages';
import {
  findArticle,
  loadDocs,
  loadedDocs,
  registrySections,
  resetDocs,
  toArticles,
  type DocsSection,
} from './docs';
import { DOCS_FILES, installRealDocs } from './docs.test-utils';

beforeAll(() => installRealDocs({ readAs: 'admin' }));
afterEach(() => resetDocs());

describe('reading the registry', () => {
  it('finds every section and article, with unique ids', () => {
    const sections = registrySections();
    const articles = toArticles(sections);
    expect(sections.length).toBeGreaterThanOrEqual(DOCS_FILES.length);
    expect(articles.length).toBeGreaterThan(200);
    expect(new Set(articles.map((a) => a.id)).size).toBe(articles.length);
    for (const a of articles) {
      expect(a.title, a.id).toBeTruthy();
      expect(a.text, a.id).not.toMatch(/```|\]\(#/);
    }
  });

  it("hands out copies: a reader cannot reorder the page's own list", () => {
    const first = registrySections();
    first.reverse();
    first[0].pages.length = 0;
    const again = registrySections();
    expect(again[0].id).not.toBe(first[0].id);
    expect(again.every((s) => s.pages.length > 0)).toBe(true);
  });

  it('a malformed section costs only itself', async () => {
    const good = registrySections()[0];
    const articles = await loadDocs(() => [
      null as never,
      { id: 'x' } as never,
      { id: 'y', title: 'Y', pages: [null as never, { id: 'p', title: 'P' }] },
      good,
    ]);
    expect(articles.map((a) => a.id)).toEqual(['p', ...good.pages.map((p) => p.id)]);
  });

  it('builds once, and a section id opens its first page', async () => {
    const first = await loadDocs();
    const again = await loadDocs();
    expect(again).toBe(first);
    expect(findArticle('gs-docker')?.title).toBe('Docker & Deployment');
    expect(findArticle('getting-started')?.id).toBe('gs-overview');
  });

  it('builds again when a section registers later', async () => {
    const first = await loadDocs();
    (window as unknown as { registerDocsSection: (s: DocsSection) => void }).registerDocsSection({
      id: 'late-section',
      title: 'Late',
      pages: [{ id: 'late-page', title: 'Late page', body: 'x' }],
    });
    const again = await loadDocs();
    expect(again).not.toBe(first);
    expect(findArticle('late-page')?.title).toBe('Late page');
  });

  it('asks again next time when docs.js had not run yet', async () => {
    const none = await loadDocs(() => []);
    expect(none).toEqual([]);
    expect(loadedDocs()).toEqual([]);
    const later = await loadDocs();
    expect(later.length).toBeGreaterThan(0);
  });

  it('nothing on the window yet reads as no docs, never a throw', () => {
    const saved = window.SoulSyncDocs;
    delete window.SoulSyncDocs;
    try {
      expect(registrySections()).toEqual([]);
      window.SoulSyncDocs = {
        sections: () => {
          throw new Error('half loaded');
        },
      };
      expect(registrySections()).toEqual([]);
    } finally {
      window.SoulSyncDocs = saved;
    }
  });

  it('reads the registry, never re-fetches or evaluates the docs files', () => {
    const src = readFileSync(join(process.cwd(), 'src/features/help/search/docs.ts'), 'utf8');
    expect(src).not.toMatch(/new Function|\beval\(|fetch\(|eslint-disable/);
    expect(src).toContain('window.SoulSyncDocs');
  });
});

describe('every article the help names exists', () => {
  it('page help articles resolve to real docs ids', async () => {
    await loadDocs();
    const missing: string[] = [];
    for (const page of registeredPageIds()) {
      const entry = pageHelpFor(page);
      for (const a of [...(entry.articles ?? []), ...(entry.adminArticles ?? [])]) {
        if (!findArticle(a.id)) missing.push(`${page}: ${a.id}`);
      }
    }
    expect(missing).toEqual([]);
  });
});
