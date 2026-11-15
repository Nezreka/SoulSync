/**
 * The three places settings.js hands over to the React Settings tabs while
 * the page is being ported (SETTINGS_PORT.md): the settings it loaded, the tab
 * that was opened, and its save.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

const SETTINGS = readFileSync(resolve(process.cwd(), 'static/settings.js'), 'utf8').replace(
  /\r\n/g,
  '\n',
);

afterEach(() => {
  document.body.innerHTML = '';
});

describe('settings.js hands over to the React tabs', () => {
  it('opening a tab shows its React slot, and a failing mount does not break the switch', () => {
    document.body.innerHTML = `<div id="settings-page">
      <button class="stg-tab" data-tab="appearance"></button>
      <button class="stg-tab" data-tab="logs"></button>
      <div data-stg="appearance" data-stg-react="appearance"></div>
      <div data-stg="logs"></div></div>`;
    const shown: string[] = [];
    const win = {
      SoulSyncSettings: {
        show: (tab: string) => {
          shown.push(tab);
          if (tab === 'logs') throw new Error('boom');
        },
      },
    };
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    // the function also starts the log viewer, the db info and other loaders:
    // every name it does not get here is a no-op
    const scope = new Proxy(
      { window: win, document, console, CustomEvent, Array } as Record<string, unknown>,
      {
        has: () => true,
        get: (target, key) =>
          key === Symbol.unscopables ? undefined : key in target ? target[key as string] : () => {},
      },
    );
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const switchTab = new Function(
      'scope',
      `with (scope) { ${extractFunction('switchSettingsTab', SETTINGS)}\nreturn switchSettingsTab; }`,
    )(scope) as (tab: string) => void;
    switchTab('appearance');
    expect(shown).toEqual(['appearance']);
    expect(
      (document.querySelector('[data-stg-react="appearance"]') as HTMLElement).style.display,
    ).toBe('');
    expect((document.querySelector('[data-stg="logs"]') as HTMLElement).style.display).toBe('none');
    expect(() => switchTab('logs')).not.toThrow();
    expect(document.querySelector('.stg-tab[data-tab="logs"]')!.classList.contains('active')).toBe(
      true,
    );
    error.mockRestore();
  });

  it('the settings it loads reach the React store, after the failed-load guard', () => {
    const load = extractFunction('loadSettingsData', SETTINGS);
    const guard = load.indexOf('window._settingsLoadFailed = true');
    const good = load.indexOf('window._settingsPayload = settings;');
    const hydrate = load.indexOf('window.SoulSyncSettings.hydrate(settings)');
    expect(guard).toBeGreaterThan(-1);
    expect(good).toBeGreaterThan(guard);
    expect(hydrate).toBeGreaterThan(good);
  });

  it('its save flushes the React tabs, and their failure wins over "saved"', () => {
    const save = extractFunction('saveSettings', SETTINGS);
    const flush = save.indexOf('reactSaved = await window.SoulSyncSettings.flush()');
    const failed = save.indexOf('if (result.success && !reactSaved) {');
    const saved = save.indexOf(
      '} else if (result.success && qualityProfileSaved) {',
    );
    expect(flush).toBeGreaterThan(
      save.indexOf('const qualityProfileSaved = await saveQualityProfile();'),
    );
    expect(failed).toBeGreaterThan(flush);
    expect(saved).toBeGreaterThan(failed);
    expect(save.slice(failed, saved)).toContain("setSettingsSaveState('error')");
  });
  it("the page autosave leaves a React tab's controls to the tab", () => {
    document.body.innerHTML = `<div id="settings-page">
      <div data-stg-react="appearance"><select id="sidebar-visualizer-type"></select></div>
      <input id="spotify-client-id"></div>`;
    const timers: unknown[] = [];
    const deps = `let settingsAutoSaveTimer = null;
const _qpHandleProfileControlChange = () => false;
const _isVideoOwnedField = () => false;
const setSettingsSaveState = () => {};
${extractFunction('debouncedAutoSaveSettings', SETTINGS)}
return debouncedAutoSaveSettings;`;
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const autosave = new Function(
      'window',
      'document',
      'setTimeout',
      'clearTimeout',
      'saveSettings',
      deps,
    )(
      {},
      document,
      (cb: unknown) => timers.push(cb),
      () => undefined,
      () => undefined,
    ) as (event: { target: Element | null }) => void;
    autosave({ target: document.getElementById('sidebar-visualizer-type') });
    expect(timers).toHaveLength(0);
    autosave({ target: document.getElementById('spotify-client-id') });
    expect(timers).toHaveLength(1);
  });
});

describe('core.js lends the socket to the React log viewer', () => {
  const CORE = readFileSync(resolve(process.cwd(), 'static/core.js'), 'utf8').replace(
    /\r\n/g,
    '\n',
  );
  const start = CORE.indexOf('window.soulsyncLogStream = {');
  const block = CORE.slice(start, CORE.indexOf('\n};\n', start) + 3);

  function bridge(socket: unknown) {
    const win: { soulsyncLogStream?: { subscribe: Function; unsubscribe: Function } } = {};
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    new Function('window', 'socket', block)(win, socket);
    return win.soulsyncLogStream!;
  }

  it('subscribes and unsubscribes with the calls the vanilla viewer made', () => {
    expect(start).toBeGreaterThan(-1);
    const calls: unknown[][] = [];
    const socket = {
      connected: true,
      emit: (...a: unknown[]) => calls.push(['emit', ...a]),
      on: (...a: unknown[]) => calls.push(['on', ...a]),
      off: (...a: unknown[]) => calls.push(['off', ...a]),
    };
    const onLive = () => {};
    const stream = bridge(socket);
    expect(stream.subscribe('acoustid', onLive)).toBe(true);
    stream.unsubscribe(onLive);
    expect(calls).toEqual([
      ['emit', 'logs:subscribe', { source: 'acoustid' }],
      ['on', 'logs:live', onLive],
      ['off', 'logs:live', onLive],
      ['emit', 'logs:unsubscribe', {}],
    ]);
  });

  it('does nothing without a connected socket', () => {
    expect(bridge(null).subscribe('app', () => {})).toBe(false);
    expect(() => bridge(null).unsubscribe(() => {})).not.toThrow();
    expect(bridge({ connected: false }).subscribe('app', () => {})).toBe(false);
  });
});
