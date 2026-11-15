/**
 * The settings a React tab reads and changes, while the Settings page is being
 * ported from vanilla (SETTINGS_PORT.md).
 *
 * The page has one save for everything: settings.js reads every control by id
 * and posts the lot. A ported tab has no controls for it to read, so the tab
 * keeps its values here and posts only the keys that changed. The save route
 * takes a partial settings object and stores each key on its own, so the two
 * saves never overwrite one another.
 *
 * Until the page is all React, the vanilla shell still owns three things and
 * this store follows them:
 *  - the save status button (window.setSettingsSaveState),
 *  - the autosave switch (window._settingsAutosaveOff, System > Saving),
 *  - the guards that stop a save: a failed settings load (#879) and the form
 *    being filled on open (window._suppressSettingsAutoSave).
 */

import { createStore } from 'zustand/vanilla';

import { apiClient, readJson } from '@/app/api-client';

export type SettingsSection = Record<string, unknown>;
export type SettingsPayload = Record<string, SettingsSection | undefined>;
export type SaveState = 'idle' | 'pending' | 'saving' | 'saved' | 'error';

/** the same wait settings.js gives a change before it saves */
export const AUTOSAVE_DELAY_MS = 2000;

type ShellWindow = Window & {
  _settingsPayload?: SettingsPayload;
  _settingsAutosaveOff?: boolean;
  _settingsLoadFailed?: boolean;
  _suppressSettingsAutoSave?: boolean;
  setSettingsSaveState?: (state: SaveState) => void;
};

export interface SettingsState {
  /** what the server holds, with this session's edits applied */
  settings: SettingsPayload;
  /** true once a good payload has arrived; tabs render nothing before it */
  loaded: boolean;
  /** changed keys not saved yet, by section */
  pending: SettingsPayload;
  /** the last save's error text, cleared by the next good save */
  error: string | null;
  /** goes up with every good payload: what a tab loads from elsewhere
   *  (its own endpoints) reloads with it, as the vanilla page did */
  loads: number;
}

/**
 * A value saved through its own endpoint, not /api/settings (the discovery
 * lookback and hemisphere). `save` resolves false when it failed; a saver
 * that only warned (as the vanilla page did for the lookback) resolves true.
 */
export type ExtraSaver = (value: unknown) => Promise<boolean>;

export interface SettingsStore {
  getState: () => SettingsState;
  subscribe: (listener: (state: SettingsState) => void) => () => void;
  /** a fresh payload from GET /api/settings. Edits not saved yet stay on top of it. */
  hydrate: (payload: unknown) => void;
  /** one key changed by the person. Saves on its own unless autosave is off. */
  set: (section: string, key: string, value: unknown) => void;
  /** several keys of one section at once */
  patch: (section: string, values: SettingsSection) => void;
  /** a value with its own endpoint, saved with the same autosave rules */
  setExtra: (key: string, value: unknown, save: ExtraSaver) => void;
  /** true while a value whose key starts with `prefix` waits to be saved */
  hasExtra: (prefix: string) => boolean;
  /** forget the values waiting under `prefix` (a quality preset switch
   *  replaces the profile they were edits of) */
  dropExtras: (prefix: string) => void;
  /** save what is pending now. true when nothing was pending or the save worked. */
  flush: () => Promise<boolean>;
  hasPending: () => boolean;
  /** stop a queued autosave (the page is going away) */
  cancel: () => void;
}

function shell(): ShellWindow {
  return window as ShellWindow;
}

function isPayload(value: unknown): value is SettingsPayload {
  return !!value && typeof value === 'object' && !Array.isArray(value) && !('error' in value);
}

function merge(base: SettingsPayload, over: SettingsPayload): SettingsPayload {
  const out: SettingsPayload = { ...base };
  for (const [section, values] of Object.entries(over)) {
    out[section] = { ...out[section], ...values };
  }
  return out;
}

export function createSettingsStore({
  post = (body: SettingsPayload) =>
    readJson<{ success?: boolean; error?: string }>(apiClient.post('settings', { json: body })),
  delay = AUTOSAVE_DELAY_MS,
}: {
  post?: (body: SettingsPayload) => Promise<{ success?: boolean; error?: string }>;
  delay?: number;
} = {}): SettingsStore {
  const store = createStore<SettingsState>(() => ({
    settings: {},
    loaded: false,
    pending: {},
    error: null,
    loads: 0,
  }));
  let extras: Record<string, { value: unknown; save: ExtraSaver }> = {};
  let timer: ReturnType<typeof setTimeout> | null = null;
  let saving: Promise<boolean> | null = null;

  const show = (state: SaveState) => shell().setSettingsSaveState?.(state);
  const hasPending = () =>
    Object.keys(store.getState().pending).length > 0 || Object.keys(extras).length > 0;
  const cancel = () => {
    if (timer) clearTimeout(timer);
    timer = null;
  };

  async function flush(): Promise<boolean> {
    cancel();
    // one save at a time: a change made while a save is in the air goes out
    // in the next one, never in a second request racing the first
    if (saving) await saving;
    if (!hasPending()) return true;
    // #879: the page is showing defaults, not the real config
    if (shell()._settingsLoadFailed) return false;
    const body = store.getState().pending;
    const sending = extras;
    store.setState({ pending: {} });
    extras = {};
    show('saving');
    saving = (async () => {
      let failed: string | null = null;
      if (Object.keys(body).length > 0) {
        try {
          const result = await post(body);
          if (result?.success !== true) throw new Error(result?.error || 'Settings save failed');
          const payload = shell()._settingsPayload;
          // settings.js and the shared helpers read this copy
          if (payload) shell()._settingsPayload = merge(payload, body);
        } catch (error) {
          // put the keys back under anything changed since, so a retry sends them
          store.setState((state) => ({ pending: merge(body, state.pending) }));
          failed = error instanceof Error ? error.message : 'Settings save failed';
        }
      }
      for (const [key, extra] of Object.entries(sending)) {
        let ok = false;
        try {
          ok = await extra.save(extra.value);
        } catch {
          ok = false;
        }
        // a newer value set while this one was saving wins the retry
        if (!ok) {
          if (!(key in extras)) extras[key] = extra;
          failed = failed ?? 'Settings save failed';
        }
      }
      try {
        if (failed) {
          store.setState({ error: failed });
          show('error');
          return false;
        }
        store.setState({ error: null });
        show(hasPending() ? 'pending' : 'saved');
        return true;
      } finally {
        saving = null;
      }
    })();
    return saving;
  }

  function changed(section: string, values: SettingsSection) {
    store.setState((state) => ({
      settings: merge(state.settings, { [section]: values }),
      pending: merge(state.pending, { [section]: values }),
    }));
    queue();
  }

  function queue() {
    cancel();
    show('pending');
    const w = shell();
    // autosave off: the change waits for the save button, which calls flush
    if (w._settingsAutosaveOff === true || w._settingsLoadFailed || w._suppressSettingsAutoSave) {
      return;
    }
    timer = setTimeout(() => void flush(), delay);
  }

  return {
    getState: store.getState,
    subscribe: (listener) => store.subscribe(listener),
    hydrate(payload) {
      if (!isPayload(payload)) return;
      store.setState((state) => ({
        settings: merge(payload, state.pending),
        loaded: true,
        loads: state.loads + 1,
      }));
    },
    set: (section, key, value) => changed(section, { [key]: value }),
    patch: (section, values) => changed(section, values),
    setExtra(key, value, save) {
      extras[key] = { value, save };
      queue();
    },
    hasExtra: (prefix) => Object.keys(extras).some((key) => key.startsWith(prefix)),
    dropExtras(prefix) {
      for (const key of Object.keys(extras)) if (key.startsWith(prefix)) delete extras[key];
      // the save status stays as it is: the vanilla page may have its own
      // save waiting, and the preset switch saves on the server anyway
      if (!hasPending()) cancel();
    },
    flush,
    hasPending,
    cancel,
  };
}

/** the page's one store */
export const settingsStore = createSettingsStore();
