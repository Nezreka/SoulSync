/**
 * Which Settings tab is open, and how many times each has been opened
 * (SETTINGS_PORT.md step V4c). The shell's tab switch tells the host
 * (`SoulSyncSettings.show`), the host tells this, and the page passes each tab
 * its `active` and `opened`, as the host did when it rendered the tabs into
 * the vanilla page's slots.
 */

import { useSyncExternalStore } from 'react';

interface PageTabs {
  tab: string;
  opened: Record<string, number>;
}

function createPageTabs() {
  let state: PageTabs = { tab: '', opened: {} };
  const listeners = new Set<() => void>();
  return {
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    get: () => state,
    show: (tab: string) => {
      state = { tab, opened: { ...state.opened, [tab]: (state.opened[tab] ?? 0) + 1 } };
      for (const listener of listeners) listener();
    },
    /** for tests */
    reset: () => {
      state = { tab: '', opened: {} };
    },
  };
}

export const pageTabs = createPageTabs();

export function usePageTabs() {
  // the same snapshot when rendered to a string (the tests read the page's markup so)
  return useSyncExternalStore(pageTabs.subscribe, pageTabs.get, pageTabs.get);
}
