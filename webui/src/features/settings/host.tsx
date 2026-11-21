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

import { nav, resolveTab } from './nav';
import { settingsStore, type SettingsPayload, type SettingsStore } from './settings-store';
import { landHit } from './shell/search';
import { applyAppearanceOnLoad } from './tabs/appearance';
import { AppearanceTab } from './tabs/appearance-tab';
import { checkQobuzAuthStatus } from './tabs/connections';
import { musicbrainzError, syncMetadataSourceSelection } from './tabs/connections-metadata';
import { testConnection } from './tabs/connections-servers';
import { servicesModel } from './tabs/connections-services';
import { ConnectionsTab } from './tabs/connections-tab';
import { DiscoveryTab } from './tabs/discovery/discovery-page';
import { DownloadsTab } from './tabs/downloads-tab';
import { LibraryTab } from './tabs/library/library-page';
import {
  type TemplateKey,
  playlistItemError,
  templateErrors,
  templateFieldErrors,
  templateWaits,
} from './tabs/organization';

/** a template's name, as its field says it */
const TEMPLATE_NAMES: Partial<Record<TemplateKey, string>> = {
  album_path: 'The album path template',
  single_path: 'The single path template',
  playlist_path: 'The old playlist path template',
  podcast_path: 'The podcast path template',
  sample_path: 'The sample path template',
};
import { PeopleTab, openPeopleSettings } from './tabs/people/people-tab';
import { QualityTab } from './tabs/quality-tab';
import { sourcesModel } from './tabs/sources-model';
import { SystemTab } from './tabs/system/system-page';

export type SettingsTabKey =
  | 'connections'
  | 'sources'
  | 'downloads'
  | 'quality'
  | 'library'
  | 'discovery'
  | 'people'
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
  /** what is wrong with the naming templates (Library > Naming): a
   *  template that would not work is not saved, everything else is */
  templateErrors: () => string[];
  /** the Playlist File Naming rule: held back the same way */
  playlistItemError: () => string | null;
  /** what is wrong with the MusicBrainz server fields (Connections): the
   *  page save refuses while it is set, and says so */
  musicbrainzError: () => string | null;
  /** how many changed settings wait to be saved (the save status counts them) */
  pendingCount: () => number;
  /** what is held back right now, in words: a template not saved, or a
   *  MusicBrainz address holding every save */
  blockers: () => Array<{ message: string }>;
  /** go to the field the first blocker is about, and light it */
  showBlocker: () => void;
  /** what the server said when the last save failed */
  lastError: () => string | null;
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
  discovery: DiscoveryTab,
  downloads: DownloadsTab,
  library: LibraryTab,
  people: PeopleTab,
  quality: QualityTab,
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
  // a naming template that would not work is not saved: only that key is
  // held back, every other setting saves as usual (the old page refused the
  // whole save). its field says why and the save status goes to it
  const errors = () => (tabs.library ? templateErrors(store.getState().settings) : []);
  const itemError = () => (tabs.library ? playlistItemError(store.getState().settings) : null);
  const badTemplates = (): TemplateKey[] => {
    if (!tabs.library) return [];
    const settings = store.getState().settings;
    const keys = Object.keys(templateFieldErrors(settings)) as TemplateKey[];
    if (playlistItemError(settings)) keys.push('playlist_item');
    return keys;
  };
  store.hold(() => {
    const bad = badTemplates();
    if (!bad.length) return [];
    // the whole dict, when something set it in one go, is held with them
    return [
      { section: 'file_organization', key: 'templates' },
      ...bad.map((key) => ({ section: 'file_organization', key: `templates.${key}` })),
    ];
  });
  /** said after a template's problem when its new value waits, unsaved */
  const unsaved = (key: TemplateKey) =>
    templateWaits(store, key) ? ' It is not saved until it is fixed.' : '';
  // nor would a MusicBrainz server address that does not work
  const mbError = () => (tabs.connections ? musicbrainzError() : null);
  store.block(() => !!mbError());

  /** a tab asked for by key, from anywhere (window.switchSettingsTab): where
   *  the page is lives in nav.ts, and old keys are aliases there */
  function show(tab: string) {
    nav.go(tab);
  }

  /** where each thing that can hold a save is, so the save status can go there */
  function blockers() {
    const found: Array<{
      message: string;
      tab: string;
      id: string;
      service?: string;
      view?: string;
      kind?: string;
    }> = [];
    const mb = mbError();
    if (mb)
      found.push({
        message: mb,
        tab: 'connections',
        id: 'musicbrainz-base-url',
        service: 'musicbrainz',
      });
    // each template that would not work is its own thing to fix, and goes
    // to its own field (Library > Naming, on its kind)
    if (tabs.library)
      for (const [key, problems] of Object.entries(
        templateFieldErrors(store.getState().settings),
      )) {
        const name = TEMPLATE_NAMES[key as TemplateKey] ?? 'A naming template';
        found.push({
          message: `${name} would not work: ${(problems ?? []).join('. ')}.${unsaved(key as TemplateKey)}`,
          tab: 'library',
          view: 'naming',
          kind: key === 'podcast_path' ? 'podcasts' : 'music',
          id: `template-${key.replace('_', '-')}`,
        });
      }
    const item = itemError();
    if (item)
      found.push({
        message: `${item}${unsaved('playlist_item')}`,
        tab: 'library',
        view: 'naming',
        kind: 'music',
        id: 'template-playlist-item',
      });
    return found;
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
    pendingCount: () => store.pendingCount(),
    blockers: () => blockers().map(({ message }) => ({ message })),
    showBlocker() {
      const first = blockers()[0];
      if (!first) return;
      // a template's kind first, so its field is the one on screen
      if (first.view) nav.go(first.tab, first.view, first.kind ? { kind: first.kind } : undefined);
      void landHit({
        key: `blocker:${first.id}`,
        label: first.message,
        path: '',
        words: '',
        tab: resolveTab(first.tab).tab,
        view: first.view,
        curated: true,
        open: first.service ? { service: first.service } : undefined,
        anchor: { id: first.id },
      });
    },
    lastError: () => store.getState().error,
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
