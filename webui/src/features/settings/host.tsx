/**
 * Mounts the ported Settings tabs into the vanilla Settings page
 * (SETTINGS_PORT.md).
 *
 * The page stays vanilla until its last tab is ported. A ported tab's markup
 * is replaced in index.html by empty slots, `<div data-stg-react="<tab>"
 * data-stg-part="<part>" style="display: contents">`, one where each run of
 * its blocks sat. The tab renders the same `[data-stg]` blocks into them, so
 * settings.js shows and hides them with the tab, and its search reads them,
 * as it always did.
 *
 * settings.js calls in through window.SoulSyncSettings:
 *  - hydrate(settings) when it has loaded the settings,
 *  - show(tab) when a tab is opened, before it shows and hides the blocks,
 *  - flush() from its own save, so the save button saves a React tab's
 *    changes too,
 *  - hasPending() to know a React tab has something unsaved.
 */

import type { ComponentType } from 'react';

import { flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';

import { settingsStore, type SettingsPayload, type SettingsStore } from './settings-store';
import { applyAppearanceOnLoad } from './tabs/appearance';
import { AppearanceTab } from './tabs/appearance-tab';
import { DiscoveryTab } from './tabs/discovery-tab';
import { DownloadsTab } from './tabs/downloads-tab';
import { LogsTab } from './tabs/logs-tab';
import { QualityTab } from './tabs/quality-tab';
import { sourcesModel } from './tabs/sources-model';
import { SourcesTab } from './tabs/sources-tab';

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
  /** which of the tab's slots this is (data-stg-part) */
  part?: string;
  /** goes up each time the tab is opened, for what reloads on arrival */
  opened: number;
  /** this is the open tab (the log viewer streams only while it is) */
  active: boolean;
}

export interface SoulSyncSettingsApi {
  hydrate: (settings: unknown) => void;
  show: (tab: string) => void;
  flush: () => Promise<boolean>;
  hasPending: () => boolean;
  /** the tabs React renders, for settings.js to skip in its own load and save */
  ported: () => string[];
  /** a vanilla field that writes a React tab's key (the Connections tab's
   *  Deezer ARL mirrors the Deezer source panel's) */
  set: (section: string, key: string, value: unknown) => void;
}

declare global {
  interface Window {
    SoulSyncSettings?: SoulSyncSettingsApi;
  }
}

/** A tab is added here in the change that ports it. */
export const SETTINGS_TABS: Partial<Record<SettingsTabKey, ComponentType<SettingsTabProps>>> = {
  appearance: AppearanceTab,
  discovery: DiscoveryTab,
  downloads: DownloadsTab,
  logs: LogsTab,
  quality: QualityTab,
  sources: SourcesTab,
};

/** What opening Settings applies whatever tab it opens on. settings.js did
 *  these inside loadSettingsData; each moves here with its tab. */
export const SETTINGS_LOAD_EFFECTS: Array<(settings: SettingsPayload) => void> = [
  applyAppearanceOnLoad,
  // the music chain and which of its sources are unready (loadHybridSourceOrder)
  (settings) => sourcesModel.load(settings),
];

const roots = new WeakMap<Element, Root>();

export function createSettingsHost({
  tabs = SETTINGS_TABS,
  effects = SETTINGS_LOAD_EFFECTS,
  store = settingsStore,
  documentRef = document,
}: {
  tabs?: Partial<Record<string, ComponentType<SettingsTabProps>>>;
  effects?: Array<(settings: SettingsPayload) => void>;
  store?: SettingsStore;
  documentRef?: Document;
} = {}): SoulSyncSettingsApi {
  const opened: Record<string, number> = {};

  function show(tab: string) {
    if (tabs[tab]) opened[tab] = (opened[tab] ?? 0) + 1;
    // every ported tab is on the page, not just the open one: the settings
    // search reads the blocks of all of them, and settings.js is about to
    // show and hide them by tab
    for (const slot of documentRef.querySelectorAll<HTMLElement>(
      '#settings-page [data-stg-react]',
    )) {
      const key = slot.dataset.stgReact ?? '';
      const Tab = tabs[key];
      if (!Tab) continue;
      // one root per slot for the life of the page: a tab keeps its state
      // (an open section, a half-typed field) across tab switches
      let root = roots.get(slot);
      const first = !root;
      if (!root) {
        root = createRoot(slot);
        roots.set(slot, root);
      }
      // every mounted tab hears which tab is open, so one that was open can stop
      const render = () =>
        root.render(
          <Tab
            store={store}
            part={slot.dataset.stgPart}
            opened={opened[key] ?? 0}
            active={key === tab}
          />,
        );
      // the first paint has to be in the DOM before settings.js goes on to
      // hide the other tabs' blocks and any column left with nothing to show
      if (first) flushSync(render);
      else render();
    }
  }

  return {
    hydrate(settings) {
      store.hydrate(settings);
      if (!store.getState().loaded) return;
      for (const effect of effects) {
        try {
          effect(store.getState().settings);
        } catch (error) {
          console.error('settings load effect failed', error);
        }
      }
    },
    show,
    flush: () => store.flush(),
    hasPending: () => store.hasPending(),
    ported: () => Object.keys(tabs),
    set: (section, key, value) => store.set(section, key, value),
  };
}

export function mountSettingsHost(): void {
  if (typeof window === 'undefined' || window.SoulSyncSettings) return;
  const host = createSettingsHost();
  window.SoulSyncSettings = host;
  // the vanilla Sources modal (until step 7b) and other pages reach the
  // sources model through these
  const shell = window as unknown as Record<string, unknown>;
  shell.SoulSyncSources = sourcesModel;
  shell.testAllSources = (opts?: { silent?: boolean }) => sourcesModel.testAll(opts);
  shell.switchDownloadChain = (kind: string) => sourcesModel.switchChain(kind);
  shell.openSourceModal = (id: string) => sourcesModel.openModal(id);
  shell.closeSourceModal = () => sourcesModel.closeModal();
  // this bundle is a deferred module: settings.js may have loaded the settings
  // and opened a tab before it ran
  const payload = (window as Window & { _settingsPayload?: unknown })._settingsPayload;
  if (payload) host.hydrate(payload);
  const open = document.querySelector<HTMLElement>('#settings-page .stg-tab.active')?.dataset.tab;
  if (!open) return;
  // through settings.js's own tab switch, which mounts the tabs and then
  // shows and hides every block for the open one
  const switchTab = (window as Window & { switchSettingsTab?: (tab: string) => void })
    .switchSettingsTab;
  if (typeof switchTab === 'function') switchTab(open);
  else host.show(open);
}
