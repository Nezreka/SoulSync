/**
 * #1909: settings save as you go, but the page also had a Save Settings
 * button and toasted "Settings auto-saved" after every change, and the tour
 * said nothing applied until you clicked. The button is now the one quiet
 * status, and still saves on click.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');
const SETTINGS = read('static/settings.js');
const HTML = read('index.html');
const HELPER = read('static/helper.js');

function labels(): string {
  const start = SETTINGS.indexOf('const SETTINGS_SAVE_LABELS = {');
  return SETTINGS.slice(start, SETTINGS.indexOf('};', start) + 2);
}

function load() {
  document.body.innerHTML =
    '<button id="save-settings" data-state="idle"><svg></svg><span>All changes saved</span></button>';
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(
    `${labels()}\nlet _settingsSavedTimer = null;\n${extractFunction('setSettingsSaveState', SETTINGS)}\nreturn setSettingsSaveState;`,
  )() as (state: string) => void;
}

const btn = () => document.getElementById('save-settings')!;

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = '';
});

describe('the settings save status', () => {
  it('walks pending, saving, saved and back to idle', () => {
    vi.useFakeTimers();
    const set = load();
    set('pending');
    expect(btn().textContent).toBe('Save now');
    set('saving');
    expect(btn().dataset.state).toBe('saving');
    set('saved');
    expect(btn().textContent).toBe('Saved');
    vi.advanceTimersByTime(2000);
    expect(btn().textContent).toBe('All changes saved');
    expect(btn().querySelector('svg')).not.toBeNull();
  });

  it('a new change cancels the trip back to idle', () => {
    vi.useFakeTimers();
    const set = load();
    set('saved');
    set('pending');
    vi.advanceTimersByTime(3000);
    expect(btn().textContent).toBe('Save now');
  });

  it('says when a save failed and ignores unknown states', () => {
    const set = load();
    set('error');
    expect(btn().textContent).toBe('Retry save');
    set('bogus');
    expect(btn().dataset.state).toBe('error');
  });

  it('a change marks it pending and a save drives it', () => {
    expect(extractFunction('debouncedAutoSaveSettings', SETTINGS)).toContain(
      "setSettingsSaveState('pending')",
    );
    const save = extractFunction('saveSettings', SETTINGS);
    expect(save).toContain("setSettingsSaveState('saving')");
    expect(save).toContain("setSettingsSaveState('saved')");
    expect(save).toContain("setSettingsSaveState('error')");
    // no toast on every successful save any more, the button says it
    expect(save).not.toContain('Settings auto-saved');
  });

  it('the page and the tour tell the same story', () => {
    expect(HTML).toMatch(/id="save-settings" data-state="idle"/);
    expect(HTML).toContain('<span>All changes saved</span>');
    expect(HELPER).not.toContain("Changes aren\\'t applied until you click this button");
  });
});
