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
  _forceServiceStatusRefresh?: () => void;
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
  /** one key changed by the person. Saves on its own unless autosave is off.
   *  A dotted key ('templates.album_path') is one key of a dict inside the
   *  section: it is saved alone, the rest of the dict kept. */
  set: (section: string, key: string, value: unknown) => void;
  /** several keys of one section at once */
  patch: (section: string, values: SettingsSection) => void;
  /** keys the server already holds, saved some other way (an immediate
   *  save of its own): shown at once, never queued */
  assume: (section: string, values: SettingsSection) => void;
  /** a value with its own endpoint, saved with the same autosave rules */
  setExtra: (key: string, value: unknown, save: ExtraSaver) => void;
  /** true while a value whose key starts with `prefix` waits to be saved */
  hasExtra: (prefix: string) => boolean;
  /** the value a queued save under this key will send, if one is queued */
  queuedExtra: (key: string) => unknown;
  /** forget the values waiting under `prefix` (a quality preset switch
   *  replaces the profile they were edits of) */
  dropExtras: (prefix: string) => void;
  /** drops the one queued save under this key */
  dropExtra: (key: string) => void;
  /** save what is pending now. true when nothing was pending or the save worked. */
  flush: () => Promise<boolean>;
  hasPending: () => boolean;
  /** a rule that holds every save back while it says so (a naming template
   *  that would not work: the old page refused the whole save). Returns the
   *  way to take it off. */
  block: (holds: () => boolean) => () => void;
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

/**
 * `over` laid onto `base` the way the server stores it. The save route stores
 * each key of a section whole, so a dict one level down (file_organization's
 * templates, metadata_enhancement's tags) is posted as dotted keys
 * ('templates.album_path'), which it stores at that path. Here they land
 * nested, where the tabs read them.
 */
function apply(base: SettingsPayload, over: SettingsPayload): SettingsPayload {
  const out: SettingsPayload = { ...base };
  for (const [section, values] of Object.entries(over)) {
    const target: SettingsSection = { ...out[section] };
    for (const [key, value] of Object.entries(values || {})) {
      const path = key.split('.');
      let node = target;
      for (const part of path.slice(0, -1)) {
        const next = node[part];
        node[part] =
          next && typeof next === 'object' && !Array.isArray(next)
            ? { ...(next as SettingsSection) }
            : {};
        node = node[part] as SettingsSection;
      }
      node[path[path.length - 1]] = value;
    }
    out[section] = target;
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
  const blocks = new Set<() => boolean>();

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
    // nothing goes out, and nothing is lost: the change waits, still pending
    if ([...blocks].some((holds) => holds())) return false;
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
          if (payload) shell()._settingsPayload = apply(payload, body);
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
        // the sidebar's server and source dots follow a saved setting at once,
        // as after every save of the page's (saveSettings, autosaves too)
        if (Object.keys(body).length > 0) shell()._forceServiceStatusRefresh?.();
        return true;
      } finally {
        saving = null;
      }
    })();
    return saving;
  }

  function changed(section: string, values: SettingsSection) {
    store.setState((state) => ({
      settings: apply(state.settings, { [section]: values }),
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
        settings: apply(payload, state.pending),
        loaded: true,
        loads: state.loads + 1,
      }));
    },
    set: (section, key, value) => changed(section, { [key]: value }),
    patch: (section, values) => changed(section, values),
    assume(section, values) {
      store.setState((state) => ({ settings: apply(state.settings, { [section]: values }) }));
      const payload = shell()._settingsPayload;
      if (payload) shell()._settingsPayload = apply(payload, { [section]: values });
    },
    setExtra(key, value, save) {
      extras[key] = { value, save };
      queue();
    },
    queuedExtra: (key) => (key in extras ? extras[key].value : undefined),
    hasExtra: (prefix) => Object.keys(extras).some((key) => key.startsWith(prefix)),
    dropExtra(key) {
      delete extras[key];
      if (!hasPending()) cancel();
    },
    dropExtras(prefix) {
      for (const key of Object.keys(extras)) if (key.startsWith(prefix)) delete extras[key];
      // the save status stays as it is: the vanilla page may have its own
      // save waiting, and the preset switch saves on the server anyway
      if (!hasPending()) cancel();
    },
    flush,
    hasPending,
    block(holds) {
      blocks.add(holds);
      return () => blocks.delete(holds);
    },
    cancel,
  };
}

/** the page's one store */
export const settingsStore = createSettingsStore();
