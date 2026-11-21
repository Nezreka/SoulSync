/**
 * The guard's page: the real Settings page in jsdom, on the real store and
 * host, opened the way the route opens it (routes/settings/route.tsx), on
 * the guard's backend (backend.ts) and on fake time.
 */

import type { ComponentType } from 'react';

import { act, cleanup, render } from '@testing-library/react';
import { vi } from 'vitest';

import { mountSettingsHost, SETTINGS_TABS, type SettingsTabProps } from '../host';
import { SettingsPage } from '../settings-page';
import { settingsStore } from '../settings-store';
import * as shellModule from '../shell';
import { servicesModel } from '../tabs/connections-services';
import { resetLibraryIndexers } from '../tabs/library-video-folders';
import { videoDownloads } from '../tabs/video-downloads';
import { videoKeys } from '../tabs/video-keys';
import { videoOrganization } from '../tabs/video-organization';
import { videoPrefs } from '../tabs/video-prefs';
import { importLists, notifications } from '../tabs/video-row-lists';
import { videoServer } from '../tabs/video-server';
import { backend, fakeFetch } from './backend';

export type Side = 'music' | 'video';

/** one slot of the page: a tab and which of its parts (host.tsx SettingsTabProps) */
export interface Unit {
  tab: string;
  part?: string;
}

const realTick = () => new Promise<void>((resolve) => setImmediate(resolve));

/** let `ms` of the page's time pass (debounces, the autosave) and every
 *  request it started land. `first` runs inside the same act, so what it
 *  starts (a save's answer coming back) is the page's own time too */
export async function settle(ms = 0, first?: () => void) {
  await act(async () => {
    first?.();
    await vi.advanceTimersByTimeAsync(ms);
    let quiet = 0;
    for (let i = 0; i < 200 && quiet < 3; i += 1) {
      await realTick();
      await vi.advanceTimersByTimeAsync(0);
      quiet = backend.inflight === 0 ? quiet + 1 : 0;
    }
  });
}

type ShellWindow = Window & {
  openSettingsPage?: () => Promise<void>;
  loadSettingsData?: () => Promise<void>;
  switchSettingsTab?: (tab: string) => void;
  getCurrentProfileContext?: () => { profileId: number; isAdmin: boolean };
  showConfirmDialog?: () => Promise<boolean>;
  showToast?: () => void;
};
const shell = () => window as ShellWindow;
const globals = () => window as unknown as Record<string, unknown>;

// init.js's appliers for the four per-device effect switches: they keep the
// choice in these window flags, which is all the Appearance tab reads
const EFFECT_FLAGS = {
  applyParticlesSetting: '_particlesEnabled',
  applyWorkerOrbsSetting: '_workerOrbsEnabled',
  applyReduceEffects: '_reduceEffectsActive',
  applyMaxPerformance: '_maxPerfActive',
};

let installed = false;

/** the globals the page runs on: fake time, the fake backend, the shell
 *  bundle's functions on window, the host */
export function installWorld() {
  if (installed) return;
  installed = true;
  vi.useFakeTimers({
    toFake: [
      'setTimeout',
      'clearTimeout',
      'setInterval',
      'clearInterval',
      'requestAnimationFrame',
      'cancelAnimationFrame',
      'Date',
    ],
    now: new Date('2026-11-19T12:00:00Z'),
  });
  vi.stubGlobal('fetch', fakeFetch);
  vi.stubGlobal('confirm', () => true);
  vi.stubGlobal('alert', () => {});
  Element.prototype.scrollIntoView = () => {};
  // the shell bundle puts shell.ts on window (src/shell/index.ts)
  for (const [name, value] of Object.entries(shellModule))
    if (typeof value === 'function') globals()[name] = value;
  shell().getCurrentProfileContext = () => ({ profileId: 1, isAdmin: true });
  // every "are you sure" is answered yes, so what the yes sends is recorded
  shell().showConfirmDialog = () => Promise.resolve(true);
  shell().showToast = () => {};
  // the sidebar's weather, redrawn after a weather setting changes
  globals().bootSidebarWeather = () => {};
  for (const [applier, flag] of Object.entries(EFFECT_FLAGS))
    globals()[applier] = (enabled: boolean) => {
      globals()[flag] = enabled;
    };
  mountSettingsHost();
}

export function uninstallWorld() {
  if (!installed) return;
  installed = false;
  cleanup();
  settingsStore.cancel();
  resetModels();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  for (const name of Object.keys(shellModule)) delete globals()[name];
  for (const names of Object.entries(EFFECT_FLAGS))
    for (const name of names) delete globals()[name];
  delete shell().getCurrentProfileContext;
  delete shell().showConfirmDialog;
  delete shell().showToast;
  delete globals().bootSidebarWeather;
  delete window.SoulSyncSettings;
  delete document.body.dataset.side;
  document.body.innerHTML = '';
}

/** what the tabs keep outside React, back as a fresh page load has it: the
 *  video side's models load once a page and would keep the last mount's answers */
function resetModels() {
  videoDownloads.reset();
  videoKeys.reset();
  videoOrganization.resetState();
  videoPrefs.reset();
  videoServer.reset();
  importLists.reset();
  notifications.reset();
  servicesModel.reset();
  resetLibraryIndexers();
  for (const flag of Object.values(EFFECT_FLAGS)) delete globals()[flag];
}

type TabMap = Partial<Record<string, ComponentType<SettingsTabProps>>>;
const REAL_TABS = SETTINGS_TABS as TabMap;

/** the real tabs, noting each part the page draws them with */
function recordingTabs(seen: Unit[]): TabMap {
  const tabs: TabMap = {};
  for (const [tab, Tab] of Object.entries(REAL_TABS)) {
    if (!Tab) continue;
    tabs[tab] = function Recorded(props: SettingsTabProps) {
      if (!seen.some((u) => u.tab === tab && u.part === props.part))
        seen.push({ tab, part: props.part });
      return <Tab {...props} />;
    };
  }
  return tabs;
}

/** the real page with one slot drawn: the same components on the same
 *  store, at a fraction of the render cost */
function unitTabs(unit: Unit): TabMap {
  const Tab = REAL_TABS[unit.tab];
  if (!Tab) return {};
  return {
    [unit.tab]: function Only(props: SettingsTabProps) {
      return props.part === unit.part ? <Tab {...props} /> : null;
    },
  };
}

/** the video side's settings page was shown (video-side.js says so) */
function shown(side: Side) {
  if (side === 'video')
    document.dispatchEvent(
      new CustomEvent('soulsync:video-page-shown', { detail: 'video-settings' }),
    );
}

/**
 * The page, opened on one side as the route opens it, every tab visited
 * once. With no unit it is the whole page and the slots it drew come back.
 * With a unit it is the page with that one slot drawn, left on its tab.
 */
export async function mountPage(side: Side, unit?: Unit): Promise<Unit[]> {
  cleanup();
  await settle(0);
  resetModels();
  document.body.innerHTML = '';
  document.body.dataset.side = side;
  const seen: Unit[] = [];
  render(<SettingsPage tabs={unit ? unitTabs(unit) : recordingTabs(seen)} />);
  await act(async () => {
    await shell().openSettingsPage?.();
    shown(side);
  });
  await settle(0);
  const tabs = Object.keys(REAL_TABS);
  if (unit) {
    // one slot: the other tabs' visits are only what they announce
    await act(async () => {
      for (const tab of tabs) if (tab !== unit.tab) shell().switchSettingsTab?.(tab);
      shell().switchSettingsTab?.(unit.tab);
    });
  } else {
    for (const tab of [...tabs, 'connections']) {
      await act(async () => shell().switchSettingsTab?.(tab));
      await settle(0);
    }
  }
  // video-prefs.ts reads its two keys the first time the Library tab is
  // shown on a page load, and keeps "first" where a reset cannot reach
  if (tabs.includes('library')) await act(async () => videoPrefs.load());
  await settle(1000);
  backend.requests = [];
  return seen;
}

/** hydrate again from the backend, as opening the page does: the store's
 *  load count goes up and every tab reloads its own endpoints with it */
export async function reload(side: Side) {
  await act(async () => {
    await shell().loadSettingsData?.();
    shown(side);
  });
  await settle(0);
}

export function unmountPage() {
  cleanup();
  document.body.innerHTML = '';
}

/** save what is waiting, the way the save button's flush does */
export async function flushStore() {
  await act(async () => {
    await settingsStore.flush();
  });
  await settle(0);
}

export const page = () => document.getElementById('settings-page') as HTMLElement;
export { settingsStore };
