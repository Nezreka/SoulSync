/**
 * Where on the Settings page a person is (settings-bic DESIGN.md 3.1, 3.2):
 * the tab, the pill inside it (view), the kind of media a pill is showing,
 * and the sheet that is open. It is the address:
 *
 *   /settings?tab=downloads&view=sources&kind=video&open=soulseek
 *
 * One store. The route writes the address into it and follows it; the page's
 * tabs and pills change it; everything that used to call
 * switchSettingsTab('sources') still can, because old tab keys are aliases.
 */

import { useSyncExternalStore } from 'react';

import type { IconName } from './ui/icons';

export type TabKey =
  | 'connections'
  | 'downloads'
  | 'quality'
  | 'library'
  | 'discovery'
  | 'people'
  | 'appearance'
  | 'advanced';

export interface ViewDef {
  key: string;
  label: string;
  /** shown on this side's page only (still opened by a link or the search) */
  side?: 'music' | 'video';
}

export interface TabDef {
  key: TabKey;
  /** the key in the address, where it differs (System is `advanced` in code) */
  slug?: string;
  label: string;
  icon: IconName;
  title: string;
  sub: string;
  /** its pills; a tab with one view shows none */
  views: ViewDef[];
}

const ONE: ViewDef[] = [{ key: 'all', label: 'All' }];

/** the eight tabs, in setup order: connect, choose sources, choose quality,
 *  point at folders */
export const TABS: TabDef[] = [
  {
    key: 'connections',
    label: 'Connections',
    icon: 'plug',
    title: 'Connections',
    sub: 'Your media server, and the accounts and API keys SoulSync connects to',
    views: [
      { key: 'services', label: 'Servers and services' },
      { key: 'metasync', label: 'MetaSync', side: 'music' },
      { key: 'notifications', label: 'Notifications', side: 'video' },
    ],
  },
  {
    key: 'downloads',
    label: 'Downloads',
    icon: 'dl',
    title: 'Downloads',
    sub: 'Where downloads come from, in what order, and what gets skipped',
    views: [
      { key: 'sources', label: 'Sources' },
      { key: 'behaviour', label: 'Behaviour' },
      { key: 'filtering', label: 'Filtering', side: 'music' },
    ],
  },
  {
    key: 'quality',
    label: 'Quality',
    icon: 'sliders',
    title: 'Quality',
    sub: 'Audio and video formats, release profiles and ladders',
    views: ONE,
  },
  {
    key: 'library',
    label: 'Library',
    icon: 'lib',
    title: 'Library',
    sub: 'Folders, file organization, tagging and import',
    views: ONE,
  },
  {
    key: 'discovery',
    label: 'Discovery',
    icon: 'compass',
    title: 'Discovery',
    sub: 'Playlists, listening stats, recommendations and import lists',
    views: ONE,
  },
  {
    key: 'people',
    label: 'People',
    icon: 'people',
    title: 'People',
    sub: 'Who uses SoulSync here, templates, the global policy and invites',
    views: ONE,
  },
  {
    key: 'appearance',
    label: 'Appearance',
    icon: 'palette',
    title: 'Appearance',
    sub: 'Accent colour, effects and the sidebar weather',
    views: ONE,
  },
  {
    key: 'advanced',
    slug: 'system',
    label: 'System',
    icon: 'gear',
    title: 'System',
    sub: 'Security, the database, caches, the API, developer tools and logs',
    views: [
      { key: 'system', label: 'Settings' },
      { key: 'activity', label: 'Server Activity' },
      { key: 'logs', label: 'Logs' },
    ],
  },
];

export const tabDef = (key: TabKey): TabDef => TABS.find((tab) => tab.key === key) ?? TABS[0];

export interface NavState {
  tab: TabKey;
  view: string;
  kind: string | null;
  open: string | null;
}

/** tab keys that went when ten tabs became eight, and where each now lives */
const ALIASES: Record<string, { tab: TabKey; view?: string }> = {
  sources: { tab: 'downloads', view: 'sources' },
  logs: { tab: 'advanced', view: 'logs' },
  // it was under the media server on Connections until settings-bic phase 3
  'server-activity': { tab: 'advanced', view: 'activity' },
  // the old System tab had no logs: asked for by key, it is the settings
  advanced: { tab: 'advanced', view: 'system' },
  system: { tab: 'advanced', view: 'system' },
};

/** a tab key from anywhere (a button, an old caller, the address) as a place */
export function resolveTab(key: unknown, view?: unknown): { tab: TabKey; view: string } {
  const name = typeof key === 'string' ? key : '';
  const alias = ALIASES[name];
  const def = TABS.find((tab) => tab.key === (alias?.tab ?? name)) ?? TABS[0];
  // a pill named outright wins over the one an old key implies
  const named = typeof view === 'string' && def.views.some((v) => v.key === view) ? view : null;
  const want = named ?? alias?.view ?? '';
  return {
    tab: def.key,
    view: def.views.some((v) => v.key === want) ? want : def.views[0].key,
  };
}

const clean = (value: unknown) =>
  typeof value === 'string' && /^[\w-]{1,40}$/.test(value) ? value : null;

/** the address's search, as a place (anything missing or odd is the default) */
export function navFromSearch(search: Record<string, unknown>): NavState {
  return {
    ...resolveTab(search.tab, search.view),
    kind: clean(search.kind),
    open: clean(search.open),
  };
}

/** a place, as the address's search */
export function navToSearch(state: NavState): Record<string, string> {
  const def = tabDef(state.tab);
  const out: Record<string, string> = { tab: def.slug ?? def.key };
  if (def.views.length > 1) out.view = state.view;
  if (state.kind) out.kind = state.kind;
  if (state.open) out.open = state.open;
  return out;
}

/** the old pages' key for what is on screen: the tabs not rebuilt yet still
 *  render under it, and their blocks carry it (data-stg) */
export function legacyKey(state: Pick<NavState, 'tab' | 'view'>): string {
  if (state.tab === 'advanced')
    return state.view === 'logs'
      ? 'logs'
      : state.view === 'activity'
        ? 'server-activity'
        : 'advanced';
  return state.tab;
}

export type NavHow = 'push' | 'replace' | 'address';

function createNav() {
  let state: NavState = navFromSearch({});
  const listeners = new Set<(state: NavState, how: NavHow) => void>();
  const same = (a: NavState, b: NavState) =>
    a.tab === b.tab && a.view === b.view && a.kind === b.kind && a.open === b.open;
  const set = (next: NavState, how: NavHow) => {
    if (same(next, state)) return;
    state = next;
    for (const listener of listeners) listener(state, how);
  };
  return {
    get: () => state,
    subscribe: (listener: (state: NavState, how: NavHow) => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    /** open a tab (old keys too), on a pill if one is named: a new place, so
     *  Back returns from it */
    go(key: string, view?: string, more?: Partial<Pick<NavState, 'kind' | 'open'>>) {
      const place = resolveTab(key, view);
      // the same tab asked for again with no pill named stays on its pill
      if (place.tab === state.tab && view === undefined && !ALIASES[key]?.view && !more) return;
      set({ ...place, kind: more?.kind ?? null, open: more?.open ?? null }, 'push');
    },
    /** another pill of this tab */
    view(view: string) {
      set({ ...resolveTab(state.tab, view), kind: null, open: null }, 'push');
    },
    /** the kind switch and the open sheet: the same place, more exactly */
    patch(patch: Partial<Pick<NavState, 'kind' | 'open'>>) {
      set({ ...state, ...patch }, 'replace');
    },
    /** the address changed (a link, Back, a reload) */
    fromAddress(search: Record<string, unknown>) {
      set(navFromSearch(search), 'address');
    },
    /** for tests, and a fresh visit */
    reset() {
      state = navFromSearch({});
    },
  };
}

export const nav = createNav();

export function useNav(): NavState {
  return useSyncExternalStore((listener) => nav.subscribe(listener), nav.get, nav.get);
}
