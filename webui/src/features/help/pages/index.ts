/**
 * "On this page": one entry per page id (page.ts), plan 4.3.
 *
 * P2 ships base.ts: every page's title, one line on what it is for, and the
 * docs articles worth reading there. P5 adds a file per page with live
 * answers and calls registerPageHelp() again for the same id; the later call
 * merges over the earlier one, so a page file only states what it adds.
 *
 *   registerPageHelp({ id: 'active-downloads', useAnswers: useDownloadAnswers });
 *
 * useAnswers is a React hook. The sheet mounts it under a component keyed by
 * the page id, so the hook order is stable. It should read data the page
 * already fetched (React Query cache first) and return [] when it has
 * nothing true to say: no answers is fine, invented ones are not.
 */

import type { ReactNode } from 'react';

import type { IconName } from '../icons';

export interface ArticleRef {
  /** a docs page id from webui/static/docs-content (6.1 checks they resolve) */
  id: string;
  title: string;
  lede?: string;
}

export interface PageAnswer {
  id: string;
  /** the question, in the user's words: "Why are 3 downloads stuck in Queued?" */
  title: string;
  /** built from state right now, not general advice */
  live?: boolean;
  /** 'warn' tints the row: the thing needs the admin */
  tone?: 'warn';
  /** shown expanded when the sheet opens */
  open?: boolean;
  body: ReactNode;
}

export interface AnswerContext {
  page: string;
  isAdmin: boolean;
}

export type UseAnswers = (ctx: AnswerContext) => PageAnswer[];

/** what the heading says right now, over the entry's own title and summary */
export interface PageHeading {
  title?: string;
  summary?: string;
}

/**
 * A hook, like useAnswers, for a page whose heading follows its own state:
 * Settings names the tab you're on ("Settings, Downloads"). null keeps the
 * entry's title and summary.
 */
export type UseHeading = (ctx: AnswerContext) => PageHeading | null;

export interface TourRef {
  id: string;
  title: string;
  /** "5 steps, about a minute" */
  detail?: string;
}

export interface PageHelp {
  id: string;
  title: string;
  summary: string;
  /** what a member reads instead, when the page means something else to them */
  memberSummary?: string;
  icon: IconName;
  articles?: ArticleRef[];
  /** articles that only make sense to an admin (settings, health) */
  adminArticles?: ArticleRef[];
  tour?: TourRef;
  useAnswers?: UseAnswers;
  useHeading?: UseHeading;
}

const registry = new Map<string, PageHelp>();

export function registerPageHelp(entry: Partial<PageHelp> & { id: string }): void {
  const before = registry.get(entry.id);
  registry.set(entry.id, { icon: 'doc', title: '', summary: '', ...before, ...entry } as PageHelp);
}

/** an id with no entry (a new page) still gets a heading, from the sidebar */
export function pageHelpFor(id: string): PageHelp {
  const found = registry.get(id);
  if (found) return found;
  return { id, title: navLabel(id) || 'This page', summary: '', icon: 'doc' };
}

export function registeredPageIds(): string[] {
  return [...registry.keys()];
}

function navLabel(id: string): string {
  if (typeof document === 'undefined') return '';
  const safe = id.replace(/[^\w-]/g, '');
  const btn = document.querySelector(
    `.sidebar-nav .nav-button[data-page="${safe}"], .sidebar-nav .nav-button[data-video-page="${safe}"]`,
  );
  return (btn?.querySelector('.nav-text')?.textContent || btn?.textContent || '').trim();
}

/** the articles this viewer should see, admin ones last */
export function articlesFor(entry: PageHelp, isAdmin: boolean): ArticleRef[] {
  return [...(entry.articles ?? []), ...(isAdmin ? (entry.adminArticles ?? []) : [])];
}
