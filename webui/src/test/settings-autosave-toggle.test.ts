/**
 * settings save as you go, and some people would rather press save
 * themselves. the System tab has a switch for it, on by default. off means a
 * change only marks the button unsaved and waits for the click.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');
const SETTINGS = read('static/settings.js');
const HTML = read('index.html');

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
let qualityProfileAutoSaveTimer = null;
let _settingsSavedTimer = null;
let _qpEditingProfileId = null;
const _qpHandleProfileControlChange = () => false;
const _qpDefaultProfileId = () => 1;
const collectFullQualityBundleFromUI = () => ({});
const saveQualityProfile = () => {};
${labels()}
${extractFunction('setSettingsSaveState', SETTINGS)}
${extractFunction('_isVideoOwnedField', SETTINGS)}
${extractFunction('debouncedAutoSaveSettings', SETTINGS)}
${extractFunction('handleSettingsAutosaveToggle', SETTINGS)}
${extractFunction('debouncedSaveQualityProfile', SETTINGS)}
return { debouncedAutoSaveSettings, handleSettingsAutosaveToggle, debouncedSaveQualityProfile };`;
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
    handleSettingsAutosaveToggle: (event: { target: { checked: boolean } }) => void;
    debouncedSaveQualityProfile: () => void;
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

  it('holds a quality profile change too', () => {
    const { timers, fns } = page(true);
    fns.debouncedSaveQualityProfile();
    expect(timers.length).toBe(0);
    expect(btn().dataset.state).toBe('pending');
  });

  it('still saves the quality profile on its own while the switch is on', () => {
    const { timers, fns } = page(false);
    fns.debouncedSaveQualityProfile();
    expect(timers.length).toBe(1);
  });

  it('the switch itself always saves, so it can be turned back on', () => {
    const { change } = page(true);
    expect(change('settings-autosave-enabled')).toBe(true);
  });

  it('flipping the switch takes hold on the very next change', () => {
    const { win, fns, change } = page(false);
    fns.handleSettingsAutosaveToggle({ target: { checked: false } });
    expect(win._settingsAutosaveOff).toBe(true);
    expect(change('soulseek-url')).toBe(false);
    fns.handleSettingsAutosaveToggle({ target: { checked: true } });
    expect(change('soulseek-url')).toBe(true);
  });

  it('is on the page, ticked by default, saved and loaded with the settings', () => {
    expect(HTML).toMatch(/<input type="checkbox" id="settings-autosave-enabled" checked>/);
    expect(SETTINGS).toContain(
      "settings_autosave: _cfgBool('settings-autosave-enabled') !== false",
    );
    expect(SETTINGS).toContain('settings.ui_appearance?.settings_autosave !== false');
    expect(SETTINGS).toContain(".addEventListener('change', handleSettingsAutosaveToggle)");
  });
});
