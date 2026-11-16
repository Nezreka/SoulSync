/**
 * settings save as you go, and some people would rather press save
 * themselves. the System tab has a switch for it, on by default. off means a
 * change only marks the button unsaved and waits for the click.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore } from '@/features/settings/settings-store';
import { setSettingsSaveState } from '@/features/settings/shell';

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');
// the page's load and status button (shell.ts, settings.js's until
// SETTINGS_PORT.md step V4b)
const SHELL = read('src/features/settings/shell.ts');
// the switch is React (SETTINGS_PORT.md step 9a); its own behaviour is run in
// src/features/settings/tabs/system-tab.test.tsx
const SWITCH = read('src/features/settings/tabs/system-tab.tsx');

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

/** a field's change, through the store every control saves through, with the
 *  page's status button on window as on the page */
function page(autosaveOff: boolean | undefined) {
  document.body.innerHTML =
    '<button id="save-settings" data-state="idle"><svg></svg><span>All changes saved</span></button>';
  if (autosaveOff === undefined) delete shell._settingsAutosaveOff;
  else shell._settingsAutosaveOff = autosaveOff;
  shell.setSettingsSaveState = setSettingsSaveState;
  const post = vi.fn(() => Promise.resolve({ success: true }));
  const store = createSettingsStore({ post, delay: 0 });
  let n = 0;
  /** whether the change saved on its own */
  const change = async () => {
    post.mockClear();
    store.set('soulseek', 'slskd_url', `http://slskd-${(n += 1)}`);
    await new Promise((r) => setTimeout(r, 10));
    return post.mock.calls.length > 0;
  };
  return { win: shell, change, store };
}

const btn = () => document.getElementById('save-settings')!;

afterEach(() => {
  document.body.innerHTML = '';
  delete shell._settingsAutosaveOff;
  delete shell.setSettingsSaveState;
});

describe('the settings autosave switch', () => {
  it('saves a change on its own while the switch is on', async () => {
    for (const flag of [undefined, false]) {
      const { change } = page(flag);
      expect(await change()).toBe(true);
    }
  });

  it('holds a change for the save button while the switch is off', async () => {
    const { change } = page(true);
    expect(await change()).toBe(false);
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

  it('flipping the switch takes hold on the very next change', async () => {
    // the switch flips the page's flag at once...
    expect(SWITCH).toContain('shell()._settingsAutosaveOff = !checked;');
    // ...and the autosave follows the flag on the next change
    const { win, change } = page(false);
    win._settingsAutosaveOff = true;
    expect(await change()).toBe(false);
    win._settingsAutosaveOff = false;
    expect(await change()).toBe(true);
  });

  it('is on the page, ticked by default, saved and loaded with the settings', () => {
    expect(SWITCH).toContain('id="settings-autosave-enabled"');
    expect(SWITCH).toContain('(settings.ui_appearance || {}).settings_autosave !== false : true');
    expect(SWITCH).toContain("store.set('ui_appearance', 'settings_autosave', checked);");
    // the page's flag still comes from the loaded settings
    expect(SHELL).toContain('appearance?.settings_autosave !== false');
  });
});
