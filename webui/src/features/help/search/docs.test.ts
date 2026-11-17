/**
 * The docs, read from the real docs-content files the way the browser does
 * (plan 6.1: every docs id the help names resolves).
 */

import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

import '../pages/register';
import { pageHelpFor, registeredPageIds } from '../pages';
import {
  docsScriptUrls,
  findArticle,
  loadDocs,
  readDocsScript,
  resetDocs,
  toArticles,
} from './docs';

const DIR = join(process.cwd(), 'static', 'docs-content');
const FILES = readdirSync(DIR)
  .filter((f) => f.endsWith('.js'))
  .sort();
const fromDisk = async (url: string) =>
  readFileSync(join(DIR, url.split('/').pop()!.split('?')[0]), 'utf8');

afterEach(() => resetDocs());

describe('reading the docs files', () => {
  it('finds every section and article, with unique ids', () => {
    const sections = FILES.flatMap((f) => readDocsScript(readFileSync(join(DIR, f), 'utf8')));
    const articles = toArticles(sections);
    expect(sections.length).toBeGreaterThanOrEqual(FILES.length);
    expect(articles.length).toBeGreaterThan(200);
    expect(new Set(articles.map((a) => a.id)).size).toBe(articles.length);
    for (const a of articles) {
      expect(a.title, a.id).toBeTruthy();
      expect(a.text, a.id).not.toMatch(/```|\]\(#/);
    }
  });

  it('a broken file costs its own articles only', () => {
    expect(readDocsScript('registerDocsSection({ id: "x", pages: [')).toEqual([]);
    expect(
      readDocsScript(
        'registerDocsSection({ id: "x", title: "X", pages: [{ id: "p", title: "P" }] })',
      ),
    ).toHaveLength(1);
  });

  it('takes the urls the page loaded, cache query and all', () => {
    document.body.innerHTML =
      '<script src="/static/docs-content/01-getting-started.js?v=9"></script><script src="/static/x.js"></script>';
    expect(docsScriptUrls()).toEqual(['/static/docs-content/01-getting-started.js?v=9']);
    document.body.innerHTML = '';
  });

  it('loads once, and a section id opens its first page', async () => {
    let reads = 0;
    const urls = FILES.map((f) => `/static/docs-content/${f}?v=1`);
    const fetchText = async (u: string) => {
      reads += 1;
      return fromDisk(u);
    };
    const first = await loadDocs(fetchText, urls);
    const again = await loadDocs(fetchText, urls);
    expect(again).toBe(first);
    expect(reads).toBe(FILES.length);
    expect(findArticle('gs-docker')?.title).toBe('Docker & Deployment');
    expect(findArticle('getting-started')?.id).toBe('gs-overview');
  });

  it('asks again next time when nothing came back', async () => {
    const none = await loadDocs(async () => {
      throw new Error('offline');
    }, ['/static/docs-content/01-getting-started.js']);
    expect(none).toEqual([]);
    const later = await loadDocs(fromDisk, ['/static/docs-content/01-getting-started.js']);
    expect(later.length).toBeGreaterThan(0);
  });
});

describe('every article the help names exists', () => {
  it('page help articles resolve to real docs ids', async () => {
    await loadDocs(
      fromDisk,
      FILES.map((f) => `/static/docs-content/${f}`),
    );
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
