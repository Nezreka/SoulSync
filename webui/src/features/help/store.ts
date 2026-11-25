/**
 * The help sheet's state: open or closed, the view stack, the search text,
 * the page the sheet is about, who is looking, and the admin status that
 * drives the button's dot. One store, read by the sheet, the views and the
 * host (which mirrors open and the dot onto the vanilla ? button).
 */

import { create } from 'zustand';

import type { HelpStatus, UpdateInfo } from './api';

import { SEARCH_MIN, VIEW_META, type ViewEntry, type ViewId } from './views';

const HOME: ViewEntry = { view: 'home' };

export interface HelpState {
  open: boolean;
  /** bumps on every open, so views can reset local state */
  session: number;
  /** [home, ...pushed]; never empty */
  stack: ViewEntry[];
  query: string;
  /** the page id the sheet is about (page.ts), e.g. 'active-downloads', 'video-library' */
  page: string;
  isAdmin: boolean;
  /** admin only; null until the first answer and always for members */
  status: HelpStatus | null;
  /** the last status read failed (the card says so instead of guessing) */
  statusError: string | null;
  update: UpdateInfo | null;
  /** asks the sheet to put the caret in the search field once it renders */
  focusSearch: number;

  openSheet: (entry?: ViewEntry, opts?: { focusSearch?: boolean }) => void;
  close: () => void;
  toggle: () => void;
  push: (entry: ViewEntry) => void;
  back: () => void;
  setQuery: (query: string) => void;
  setPage: (page: string) => void;
  setAdmin: (isAdmin: boolean) => void;
  setStatus: (status: HelpStatus | null, error?: string | null) => void;
  setUpdate: (update: UpdateInfo | null) => void;
}

/** a member can't land on an admin view, from a link, a key or a stale stack */
function allowed(entry: ViewEntry | undefined, isAdmin: boolean): entry is ViewEntry {
  return !!entry && (isAdmin || !VIEW_META[entry.view]?.admin);
}

export const useHelpStore = create<HelpState>((set, get) => ({
  open: false,
  session: 0,
  stack: [HOME],
  query: '',
  page: '',
  isAdmin: false,
  status: null,
  statusError: null,
  update: null,
  focusSearch: 0,

  openSheet: (entry, opts) =>
    set((s) => {
      const stack = allowed(entry, s.isAdmin) && entry.view !== 'home' ? [HOME, entry] : [HOME];
      return {
        open: true,
        session: s.open ? s.session : s.session + 1,
        stack: s.open && !entry ? s.stack : stack,
        query: s.open && !entry ? s.query : '',
        focusSearch: opts?.focusSearch ? s.focusSearch + 1 : s.focusSearch,
      };
    }),
  close: () => set({ open: false }),
  toggle: () => (get().open ? get().close() : get().openSheet()),
  push: (entry) =>
    set((s) => {
      if (!allowed(entry, s.isAdmin)) return {};
      if (entry.view === 'home') return { stack: [HOME], query: '' };
      return { stack: [...s.stack, entry], query: '' };
    }),
  back: () =>
    set((s) => {
      // the results view is the search text, not a stack entry
      if (s.query.trim().length >= SEARCH_MIN) return { query: '' };
      return s.stack.length > 1 ? { stack: s.stack.slice(0, -1) } : {};
    }),
  setQuery: (query) => set({ query }),
  setPage: (page) => set({ page }),
  setAdmin: (isAdmin) =>
    set((s) => ({
      isAdmin,
      // a profile switch to a member drops anything admin and the status
      stack: isAdmin ? s.stack : s.stack.filter((e) => allowed(e, false)),
      status: isAdmin ? s.status : null,
    })),
  setStatus: (status, error = null) => set({ status, statusError: error }),
  setUpdate: (update) => set({ update }),
}));

/** the view on screen: typed text shows results over whatever is stacked */
export function currentView(state: Pick<HelpState, 'stack' | 'query'>): ViewEntry {
  if (state.query.trim().length >= SEARCH_MIN) return { view: 'search' };
  return state.stack[state.stack.length - 1] ?? HOME;
}

export function canGoBack(state: Pick<HelpState, 'stack' | 'query'>): boolean {
  return currentView(state).view !== 'home';
}

/** the amber dot: admins only, errors only, never warnings (plan 4.1, 4.10) */
export function needsAdmin(state: Pick<HelpState, 'isAdmin' | 'status'>): boolean {
  return state.isAdmin && !!state.status && state.status.errors > 0;
}

export type { ViewId };
