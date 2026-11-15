/**
 * settings save as you go, and some people would rather press save
 * themselves. the System tab has a switch for it, on by default. off means a
 * change only marks the button unsaved and waits for the click.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore } from '@/features/settings/settings-store';

import { extractFunction } from './vanilla-extract';

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');
const SETTINGS = read('static/settings.js');
// the switch is React (SETTINGS_PORT.md step 9a); its own behaviour is run in
// src/features/settings/tabs/system-tab.test.tsx
const SWITCH = read('src/features/settings/tabs/system-tab.tsx');

function labels(): string {
  const start = SETTINGS.indexOf('const SETTINGS_SAVE_LABELS = {');
  return SETTINGS.slice(start, SETTINGS.indexOf('};', start) + 2);
}

// the real autosave functions and the real button status, timers captured
function page(autosaveOff: boolean | undefined) {
  document.body.innerHTML =
    '<button id="save-settings" data-state="idle"><svg></svg><span>All changes saved</span></button>' +
    '<input type="checkbox" id="settings-autosave-enabled">' +
    '<input type="text" id="soulseek-url">';
  const win: Record<string, unknown> = {};
  if (autosaveOff !== undefined) win._settingsAutosaveOff = autosaveOff;
  const timers: unknown[] = [];
  const deps = `let settingsAutoSaveTimer = null;
let _settingsSavedTimer = null;
${labels()}
${extractFunction('setSettingsSaveState', SETTINGS)}
${extractFunction('_isVideoOwnedField', SETTINGS)}
${extractFunction('debouncedAutoSaveSettings', SETTINGS)}
return { debouncedAutoSaveSettings };`;
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const fns = new Function('window', 'setTimeout', 'clearTimeout', 'saveSettings', deps)(
    win,
    (cb: unknown) => {
      timers.push(cb);
      return timers.length;
    },
    () => undefined,
    () => undefined,
  ) as {
    debouncedAutoSaveSettings: (event?: { target: Element | null }) => void;
  };
  const change = (id: string) => {
    timers.length = 0;
    fns.debouncedAutoSaveSettings({ target: document.getElementById(id) });
    return timers.length > 0;
  };
  return { win, timers, fns, change };
}

const btn = () => document.getElementById('save-settings')!;

afterEach(() => {
  document.body.innerHTML = '';
});

describe('the settings autosave switch', () => {
  it('saves a change on its own while the switch is on', () => {
    for (const flag of [undefined, false]) {
      const { change } = page(flag);
      expect(change('soulseek-url')).toBe(true);
      expect(btn().textContent).toBe('Save now');
    }
  });

  it('holds a change for the save button while the switch is off', () => {
    const { change } = page(true);
    expect(change('soulseek-url')).toBe(false);
    expect(btn().dataset.state).toBe('pending');
    expect(btn().textContent).toBe('Save changes');
    expect(btn().title).toContain('Autosave is off');
  });

  // the quality profile is saved by the React Quality tab, through the
  // settings store (SETTINGS_PORT.md step 6), which follows the same switch
  it('holds a quality profile change too', async () => {
    const shell = window as unknown as Record<string, unknown>;
    const save = vi.fn(() => Promise.resolve(true));
    try {
      shell._settingsAutosaveOff = true;
      const store = createSettingsStore({ delay: 0 });
      store.setExtra('quality.profile.1', {}, save);
      await new Promise((r) => setTimeout(r, 10));
      expect(save).not.toHaveBeenCalled();
      expect(store.hasPending()).toBe(true);
      await store.flush();
      expect(save).toHaveBeenCalledOnce();
    } finally {
      delete shell._settingsAutosaveOff;
    }
  });

  it('still saves the quality profile on its own while the switch is on', async () => {
    const save = vi.fn(() => Promise.resolve(true));
    const store = createSettingsStore({ delay: 0 });
    store.setExtra('quality.profile.1', {}, save);
    await vi.waitFor(() => expect(save).toHaveBeenCalledOnce());
  });

  it('the switch itself always saves, so it can be turned back on', () => {
    // switched off, it saves itself after the usual wait all the same
    expect(SWITCH).toContain(
      'if (!checked) setTimeout(() => void store.flush(), AUTOSAVE_DELAY_MS);',
    );
  });

  it('flipping the switch takes hold on the very next change', () => {
    // the switch flips the page's flag at once...
    expect(SWITCH).toContain('shell()._settingsAutosaveOff = !checked;');
    // ...and the page's autosave follows the flag on the next change
    const { win, change } = page(false);
    win._settingsAutosaveOff = true;
    expect(change('soulseek-url')).toBe(false);
    win._settingsAutosaveOff = false;
    expect(change('soulseek-url')).toBe(true);
  });

  it('is on the page, ticked by default, saved and loaded with the settings', () => {
    expect(SWITCH).toContain('id="settings-autosave-enabled"');
    expect(SWITCH).toContain('(settings.ui_appearance || {}).settings_autosave !== false : true');
    expect(SWITCH).toContain("store.set('ui_appearance', 'settings_autosave', checked);");
    // the page's flag still comes from the loaded settings
    expect(SETTINGS).toContain('settings.ui_appearance?.settings_autosave !== false');
  });
});
