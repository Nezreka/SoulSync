/**
 * The Settings tabs and what the page's shell reaches them through
 * (SETTINGS_PORT.md). The page is React (settings-page.tsx, step V4c): it
 * renders every tab into the slot wrappers they had in the vanilla page. The
 * shell (shell.ts, in the shell bundle) calls in through
 * window.SoulSyncSettings:
 *  - hydrate(settings) when it has loaded the settings,
 *  - show(tab) when a tab is opened, before it shows and hides the blocks,
 *  - flush() from its own save, so the save button saves the tabs' changes,
 *  - hasPending() to know a tab has something unsaved.
 */

import type { ComponentType } from 'react';

import { pageTabs } from './page-tabs';
import { settingsStore, type SettingsPayload, type SettingsStore } from './settings-store';
import { applyAppearanceOnLoad } from './tabs/appearance';
import { AppearanceTab } from './tabs/appearance-tab';
import { checkQobuzAuthStatus } from './tabs/connections';
import { musicbrainzError, syncMetadataSourceSelection } from './tabs/connections-metadata';
import { testConnection } from './tabs/connections-servers';
import { servicesModel } from './tabs/connections-services';
import { ConnectionsTab } from './tabs/connections-tab';
import { DevicesTab } from './tabs/devices/devices-tab';
import { DiscoveryTab } from './tabs/discovery-tab';
import { DownloadsTab } from './tabs/downloads-tab';
import { LibraryTab } from './tabs/library-folders';
import { LogsTab } from './tabs/logs-tab';
import { playlistItemError, templateErrors } from './tabs/organization';
import { PeopleTab, openPeopleSettings } from './tabs/people/people-tab';
import { QualityTab } from './tabs/quality-tab';
import { sourcesModel } from './tabs/sources-model';
import { SourcesTab } from './tabs/sources-tab';
import { SystemTab } from './tabs/system-tab';

export type SettingsTabKey =
  | 'connections'
  | 'sources'
  | 'downloads'
  | 'quality'
  | 'library'
  | 'discovery'
  | 'people'
  | 'devices'
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
  /** what is wrong with the naming templates (the Library tab's
   *  Organization card): the page save refuses while any is set, and says so */
  templateErrors: () => string[];
  /** the Playlist File Naming rule, which the page save toasts on its own */
  playlistItemError: () => string | null;
  /** what is wrong with the MusicBrainz server fields (Connections): the
   *  page save refuses while it is set, and says so */
  musicbrainzError: () => string | null;
}

declare global {
  interface Window {
    SoulSyncSettings?: SoulSyncSettingsApi;
  }
}

/** A tab is added here in the change that ports it. */
export const SETTINGS_TABS: Partial<Record<SettingsTabKey, ComponentType<SettingsTabProps>>> = {
  advanced: SystemTab,
  appearance: AppearanceTab,
  connections: ConnectionsTab,
  devices: DevicesTab,
  discovery: DiscoveryTab,
  downloads: DownloadsTab,
  library: LibraryTab,
  logs: LogsTab,
  people: PeopleTab,
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

export function createSettingsHost({
  tabs = SETTINGS_TABS,
  effects = SETTINGS_LOAD_EFFECTS,
  store = settingsStore,
}: {
  tabs?: Partial<Record<string, ComponentType<SettingsTabProps>>>;
  effects?: Array<(settings: SettingsPayload) => void>;
  store?: SettingsStore;
} = {}): SoulSyncSettingsApi {
  // a naming template that would not work holds every save back, as the old
  // page refused its whole save (validateFileOrganizationTemplates)
  const errors = () => (tabs.library ? templateErrors(store.getState().settings) : []);
  const itemError = () => (tabs.library ? playlistItemError(store.getState().settings) : null);
  store.block(() => {
    if (errors().length > 0) return true;
    // the Playlist File Naming rule says why on every save it holds, the
    // autosave's too, as saveSettings did even when quiet
    const item = itemError();
    if (item) window.showToast?.(item, 'error');
    return !!item;
  });
  // nor would a MusicBrainz server address that does not work
  const mbError = () => (tabs.connections ? musicbrainzError() : null);
  store.block(() => !!mbError());

  /** the tab the shell opened: the page (settings-page.tsx) renders every
   *  tab and tells each whether it is the open one and how often it was
   *  opened (step V4c; the host rendered them into the vanilla page's slots) */
  function show(tab: string) {
    pageTabs.show(tab);
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
    templateErrors: errors,
    playlistItemError: itemError,
    musicbrainzError: mbError,
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
  // the Sources tab's Qobuz probe and the Spotify rate limit modal's
  // disconnect (settings.js) reach the Connections tab through these
  shell.checkQobuzAuthStatus = checkQobuzAuthStatus;
  shell.syncMetadataSourceSelection = (source: string) =>
    syncMetadataSourceSelection(settingsStore, source);
  // the Connections tiles (connections-services.tsx): the video side's
  // service status card and the shell's service switch open them by these
  shell.buildServiceTiles = () => servicesModel.rebuild();
  shell.switchServiceKind = (kind: string) => servicesModel.switchKind(kind);
  shell.openServiceModal = (id: string) => servicesModel.open(id);
  shell.closeServiceModal = () => servicesModel.close();
  // every card's Test button, React's and the video side's vanilla ones
  shell.testConnection = (service: string) => testConnection(service);
  // Settings > People, from Manage profiles and anywhere else that sends
  // someone to people and access
  shell.openPeopleSettings = openPeopleSettings;
  // this bundle is a deferred module: the shell (shell.ts, in the shell
  // bundle) may have loaded the settings before it ran
  const payload = (window as Window & { _settingsPayload?: unknown })._settingsPayload;
  if (payload) host.hydrate(payload);
}
