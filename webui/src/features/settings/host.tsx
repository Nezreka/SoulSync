/**
 * Mounts the ported Settings tabs into the vanilla Settings page
 * (SETTINGS_PORT.md).
 *
 * The page stays vanilla until its last tab is ported. A ported tab's markup
 * is replaced in index.html by one empty slot, `<div data-stg="<tab>"
 * data-stg-react="<tab>">`, which settings.js shows and hides with the tab
 * like any other block. This fills the slot.
 *
 * settings.js calls in through window.SoulSyncSettings:
 *  - hydrate(settings) when it has loaded the settings,
 *  - show(tab) when a tab is opened,
 *  - flush() from its own save, so the save button saves a React tab's
 *    changes too,
 *  - hasPending() to know a React tab has something unsaved.
 */

import type { ComponentType } from 'react';

import { createRoot, type Root } from 'react-dom/client';

import { settingsStore, type SettingsStore } from './settings-store';

export type SettingsTabKey =
  | 'connections'
  | 'sources'
  | 'downloads'
  | 'quality'
  | 'library'
  | 'discovery'
  | 'appearance'
  | 'advanced'
  | 'logs';

export interface SettingsTabProps {
  store: SettingsStore;
}

export interface SoulSyncSettingsApi {
  hydrate: (settings: unknown) => void;
  show: (tab: string) => void;
  flush: () => Promise<boolean>;
  hasPending: () => boolean;
  /** the tabs React renders, for settings.js to skip in its own load and save */
  ported: () => string[];
}

declare global {
  interface Window {
    SoulSyncSettings?: SoulSyncSettingsApi;
  }
}

/** A tab is added here in the change that ports it. */
export const SETTINGS_TABS: Partial<Record<SettingsTabKey, ComponentType<SettingsTabProps>>> = {};

const roots = new WeakMap<Element, Root>();

export function createSettingsHost({
  tabs = SETTINGS_TABS,
  store = settingsStore,
  documentRef = document,
}: {
  tabs?: Partial<Record<string, ComponentType<SettingsTabProps>>>;
  store?: SettingsStore;
  documentRef?: Document;
} = {}): SoulSyncSettingsApi {
  function show(tab: string) {
    const Tab = tabs[tab];
    if (!Tab) return;
    for (const slot of documentRef.querySelectorAll(
      `#settings-page [data-stg-react="${CSS.escape(tab)}"]`,
    )) {
      // one root per slot for the life of the page: a tab keeps its state
      // (an open section, a half-typed field) across tab switches
      if (roots.has(slot)) continue;
      const root = createRoot(slot);
      roots.set(slot, root);
      root.render(<Tab store={store} />);
    }
  }

  return {
    hydrate: (settings) => store.hydrate(settings),
    show,
    flush: () => store.flush(),
    hasPending: () => store.hasPending(),
    ported: () => Object.keys(tabs),
  };
}

export function mountSettingsHost(): void {
  if (typeof window === 'undefined' || window.SoulSyncSettings) return;
  const host = createSettingsHost();
  window.SoulSyncSettings = host;
  // this bundle is a deferred module: settings.js may have loaded the settings
  // and opened a tab before it ran
  const payload = (window as Window & { _settingsPayload?: unknown })._settingsPayload;
  if (payload) host.hydrate(payload);
  const open = document.querySelector<HTMLElement>('#settings-page .stg-tab.active')?.dataset.tab;
  if (open) host.show(open);
}
