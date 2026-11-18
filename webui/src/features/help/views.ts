/**
 * The sheet's views. Home is always at the bottom of the stack; every other
 * view is pushed on top and the back arrow pops it. Each view's component
 * lives in its own file, owned by one phase (README.md), and outlet.tsx maps
 * the id to it.
 */

export type ViewId =
  | 'home'
  | 'search'
  | 'article'
  | 'health'
  | 'report'
  | 'logs'
  | 'whats-new'
  | 'shortcuts';

export interface ViewEntry {
  view: ViewId;
  /** e.g. { id: 'gs-docker' } for an article, { finding: 'paths.download' } for health */
  params?: Record<string, string>;
}

export interface ViewMeta {
  title: string;
  /** 540px instead of 440px on desktop: tables, previews, long findings */
  wide?: boolean;
  /** a member never sees it, and the store refuses to push it for one */
  admin?: boolean;
}

export const VIEW_META: Record<ViewId, ViewMeta> = {
  home: { title: 'Help' },
  search: { title: 'Search' },
  article: { title: 'Help' },
  health: { title: 'Health', wide: true, admin: true },
  report: { title: 'Report a problem', wide: true, admin: true },
  logs: { title: 'Logs', wide: true, admin: true },
  'whats-new': { title: "What's new" },
  shortcuts: { title: 'Shortcuts' },
};

export const VIEW_IDS = Object.keys(VIEW_META) as ViewId[];

export function isViewId(value: unknown): value is ViewId {
  return typeof value === 'string' && VIEW_IDS.includes(value as ViewId);
}

/** typing this many characters in the header field shows the results view */
export const SEARCH_MIN = 2;
