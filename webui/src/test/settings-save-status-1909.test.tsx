/**
 * #1909: settings save as you go, but the page also had a Save Settings
 * button and toasted "Settings auto-saved" after every change, and the tour
 * said nothing applied until you clicked. The button is now the one quiet
 * status, and still saves on click.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SettingsPage } from '@/features/settings/settings-page';
import { saveSettings, setSettingsSaveState } from '@/features/settings/shell';

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');
// the page is React (settings-page.tsx, SETTINGS_PORT.md step V4c)
const HTML = renderToStaticMarkup(<SettingsPage tabs={{}} />);
const HELPER = read('static/helper.js');
const STORE = read('src/features/settings/settings-store.ts');

/** the button, and the page's status function (shell.ts, settings.js's
 *  until SETTINGS_PORT.md step V4b) */
function load() {
  document.body.innerHTML =
    '<button id="save-settings" data-state="idle"><svg></svg><span>All changes saved</span></button>';
  return setSettingsSaveState as (state: string) => void;
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

  it('a change marks it pending and a save drives it', async () => {
    // a change: the store, which every control saves through, marks it
    expect(STORE).toContain("show('pending');");
    // a save: saving, then saved, and no toast on every successful save
    load();
    const toast = vi.fn();
    const w = window as unknown as Record<string, unknown>;
    w.showToast = toast;
    w.SoulSyncSettings = { flush: async () => true };
    const states: string[] = [];
    const seen = new MutationObserver(() => states.push(btn().dataset.state || ''));
    seen.observe(btn(), { attributes: true, attributeFilter: ['data-state'] });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ json: async () => ({ success: true }) })),
    );
    await saveSettings(true);
    await new Promise((r) => setTimeout(r, 0));
    seen.disconnect();
    expect(states).toEqual(['saving', 'saved']);
    expect(toast).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
    delete w.showToast;
    delete w.SoulSyncSettings;
  });

  it('the page and the tour tell the same story', () => {
    // it opens at rest (#2187): hidden on the music side until a change
    expect(HTML).toMatch(/id="save-settings" data-state="rest"/);
    expect(HTML).toContain('<span>All changes saved</span>');
    expect(HELPER).not.toContain("Changes aren\\'t applied until you click this button");
  });
});
