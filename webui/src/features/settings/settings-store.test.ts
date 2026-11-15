/**
 * The store the ported Settings tabs save through (SETTINGS_PORT.md). It has
 * to behave like the page's own save: a change waits two seconds, only what
 * changed is posted, the autosave switch and the load guards hold it back,
 * and the save button's status follows.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AUTOSAVE_DELAY_MS, createSettingsStore, type SettingsPayload } from './settings-store';

type Shell = Window & {
  _settingsPayload?: SettingsPayload;
  _settingsAutosaveOff?: boolean;
  _settingsLoadFailed?: boolean;
  _suppressSettingsAutoSave?: boolean;
  setSettingsSaveState?: (state: string) => void;
};
const shell = window as Shell;

let states: string[];
let posted: SettingsPayload[];
let answer: () => Promise<{ success?: boolean; error?: string }>;

function make() {
  return createSettingsStore({
    post: (body) => {
      posted.push(structuredClone(body));
      return answer();
    },
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  states = [];
  posted = [];
  answer = () => Promise.resolve({ success: true });
  shell.setSettingsSaveState = (state) => states.push(state);
  shell._settingsPayload = { ui_appearance: { accent_preset: '#1db954' } };
});

afterEach(() => {
  vi.useRealTimers();
  delete shell._settingsAutosaveOff;
  delete shell._settingsLoadFailed;
  delete shell._suppressSettingsAutoSave;
  delete shell._settingsPayload;
  delete shell.setSettingsSaveState;
});

describe('the settings store', () => {
  it('has nothing to show until a payload arrives, and takes a good one', () => {
    const store = make();
    expect(store.getState().loaded).toBe(false);
    store.hydrate({ error: 'boom' });
    expect(store.getState().loaded).toBe(false);
    store.hydrate({ ui_appearance: { sidebar_visualizer: 'wave' } });
    expect(store.getState().loaded).toBe(true);
    expect(store.getState().settings.ui_appearance?.sidebar_visualizer).toBe('wave');
    expect(store.hasPending()).toBe(false);
    expect(states).toEqual([]);
  });

  it('saves a change on its own after the page wait, posting only what changed', async () => {
    const store = make();
    store.hydrate({ ui_appearance: { accent_preset: '#1db954', sidebar_visualizer: 'bars' } });
    store.set('ui_appearance', 'sidebar_visualizer', 'wave');
    expect(store.getState().settings.ui_appearance?.sidebar_visualizer).toBe('wave');
    expect(states).toEqual(['pending']);
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS - 1);
    expect(posted).toEqual([]);
    await vi.advanceTimersByTimeAsync(1);
    expect(posted).toEqual([{ ui_appearance: { sidebar_visualizer: 'wave' } }]);
    expect(states).toEqual(['pending', 'saving', 'saved']);
    expect(store.hasPending()).toBe(false);
    // the copy settings.js and the shared helpers read follows
    expect(shell._settingsPayload?.ui_appearance).toEqual({
      accent_preset: '#1db954',
      sidebar_visualizer: 'wave',
    });
  });

  it('folds changes made inside the wait into one save', async () => {
    const store = make();
    store.set('ui_appearance', 'sidebar_visualizer', 'wave');
    await vi.advanceTimersByTimeAsync(1500);
    store.patch('listening_stats', { enabled: true, interval: 30 });
    await vi.advanceTimersByTimeAsync(1500);
    expect(posted).toEqual([]);
    await vi.advanceTimersByTimeAsync(500);
    expect(posted).toEqual([
      {
        ui_appearance: { sidebar_visualizer: 'wave' },
        listening_stats: { enabled: true, interval: 30 },
      },
    ]);
  });

  it('holds a change for the save button while autosave is off', async () => {
    shell._settingsAutosaveOff = true;
    const store = make();
    store.set('ui_appearance', 'sidebar_visualizer', 'wave');
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS * 5);
    expect(posted).toEqual([]);
    expect(states).toEqual(['pending']);
    expect(store.hasPending()).toBe(true);
    // the save button: settings.js saveSettings calls flush
    expect(await store.flush()).toBe(true);
    expect(posted).toEqual([{ ui_appearance: { sidebar_visualizer: 'wave' } }]);
  });

  it('does not autosave while the form is being filled', async () => {
    shell._suppressSettingsAutoSave = true;
    const store = make();
    store.set('ui_appearance', 'sidebar_visualizer', 'wave');
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS * 2);
    expect(posted).toEqual([]);
  });

  it('never saves over a settings load that failed (#879)', async () => {
    shell._settingsLoadFailed = true;
    const store = make();
    store.set('ui_appearance', 'sidebar_visualizer', 'wave');
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS * 2);
    expect(await store.flush()).toBe(false);
    expect(posted).toEqual([]);
    expect(store.hasPending()).toBe(true);
  });

  it('keeps the change and says so when the save fails, and a retry sends it', async () => {
    const store = make();
    answer = () => Promise.resolve({ success: false, error: 'disk full' });
    store.set('ui_appearance', 'sidebar_visualizer', 'wave');
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS);
    expect(states.at(-1)).toBe('error');
    expect(store.getState().error).toBe('disk full');
    expect(store.hasPending()).toBe(true);
    answer = () => Promise.resolve({ success: true });
    expect(await store.flush()).toBe(true);
    expect(posted).toHaveLength(2);
    expect(posted[1]).toEqual({ ui_appearance: { sidebar_visualizer: 'wave' } });
    expect(store.getState().error).toBeNull();
  });

  it('a change made while a save is in the air goes out in the next save', async () => {
    const store = make();
    let release: (value: { success: boolean }) => void = () => {};
    answer = () => new Promise((resolve) => (release = resolve));
    store.set('ui_appearance', 'sidebar_visualizer', 'wave');
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS);
    expect(posted).toHaveLength(1);
    store.set('ui_appearance', 'accent_preset', '#8b5cf6');
    answer = () => Promise.resolve({ success: true });
    release({ success: true });
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS);
    expect(posted[1]).toEqual({ ui_appearance: { accent_preset: '#8b5cf6' } });
    expect(states.at(-1)).toBe('saved');
  });

  it('a reload keeps an edit that has not been saved yet', () => {
    shell._settingsAutosaveOff = true;
    const store = make();
    store.set('ui_appearance', 'sidebar_visualizer', 'wave');
    store.hydrate({ ui_appearance: { sidebar_visualizer: 'bars', accent_preset: '#f43f5e' } });
    expect(store.getState().settings.ui_appearance).toEqual({
      sidebar_visualizer: 'wave',
      accent_preset: '#f43f5e',
    });
  });

  it('flush with nothing pending posts nothing', async () => {
    const store = make();
    expect(await store.flush()).toBe(true);
    expect(posted).toEqual([]);
    expect(states).toEqual([]);
  });
});
