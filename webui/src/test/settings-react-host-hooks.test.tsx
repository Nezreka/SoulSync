/**
 * Where the page's shell hands over to the React Settings tabs (shell.ts,
 * settings.js's until SETTINGS_PORT.md step V4b): the tab that was opened, the
 * settings it loaded, and its save. Run, not read.
 */

import { cleanup, render } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SettingsPage } from '@/features/settings/settings-page';
import {
  initializeSettings,
  loadSettingsData,
  saveSettings,
  switchSettingsTab,
  wireRedactedSecrets,
} from '@/features/settings/shell';

// the page's globals, stubbed partly
const shell = window as unknown as Record<string, unknown>;
// the page's buttons call the shell bundle's functions on window
// (src/shell/index.ts puts them there)
const SHELL = { saveSettings, switchSettingsTab };
beforeEach(() => {
  Object.assign(shell, SHELL);
});

afterEach(() => {
  cleanup();
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
  for (const k of [
    'SoulSyncSettings',
    'showToast',
    '_settingsLoadFailed',
    '_settingsPayload',
    ...Object.keys(SHELL),
  ])
    delete shell[k];
});

function respond(body: unknown, ok = true) {
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    void url;
    void init;
    return { ok, status: ok ? 200 : 500, json: async () => body } as Response;
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}

describe('the shell hands over to the React tabs', () => {
  it('opening a tab is handed to the page, and a failing page does not break the caller', () => {
    document.body.innerHTML = `<div id="settings-page">
      <button class="stg-tab" data-tab="appearance"></button>
      <button class="stg-tab" data-tab="logs"></button>
      <div data-stg-react="appearance"></div>
      <div data-stg-react="advanced"></div></div>`;
    const shown: string[] = [];
    shell.SoulSyncSettings = {
      show: (tab: string) => {
        shown.push(tab);
        if (tab === 'logs') throw new Error('boom');
      },
    };
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    switchSettingsTab('appearance');
    // the page owns where it is (nav.ts) and shows its own tabs; old keys
    // such as 'logs' are its aliases, so they are handed over as they came
    expect(shown).toEqual(['appearance']);
    expect(() => switchSettingsTab('logs')).not.toThrow();
    expect(shown).toEqual(['appearance', 'logs']);
    error.mockRestore();
  });

  it('the settings it loads reach the React store, after the failed-load guard', async () => {
    const hydrate = vi.fn();
    shell.SoulSyncSettings = { hydrate };
    shell.showToast = vi.fn();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    // #879: an error body is not settings: nothing is hydrated, saves are held
    respond({ error: 'db locked' }, false);
    await loadSettingsData();
    expect(shell._settingsLoadFailed).toBe(true);
    expect(hydrate).not.toHaveBeenCalled();
    respond({ ui_appearance: {} });
    await loadSettingsData();
    expect(shell._settingsLoadFailed).toBe(false);
    expect(shell._settingsPayload).toEqual({ ui_appearance: {} });
    expect(hydrate).toHaveBeenCalledWith({ ui_appearance: {} });
  });

  it('its save flushes the React tabs, and their failure wins over "saved"', async () => {
    document.body.innerHTML = `<button id="save-settings" data-state="idle"><span></span></button>`;
    shell.showToast = vi.fn();
    const flush = vi.fn(async () => false);
    shell.SoulSyncSettings = { flush };
    const fetcher = respond({ success: true });
    await saveSettings(true);
    // after the page's own post
    expect(fetcher.mock.calls[0][1]?.method).toBe('POST');
    expect(flush).toHaveBeenCalledOnce();
    const btn = document.getElementById('save-settings')!;
    expect(btn.dataset.state).toBe('error');
    expect(shell.showToast).toHaveBeenCalledWith(
      'Some settings failed to save',
      'error',
      'set-services',
    );
    flush.mockResolvedValueOnce(true);
    await saveSettings(true);
    expect(btn.dataset.state).toBe('saved');
  });

  it('a save the settings never loaded for is refused, and a template rule holds it back', async () => {
    shell.showToast = vi.fn();
    const fetcher = respond({ success: true });
    shell._settingsLoadFailed = true;
    await saveSettings(false);
    expect(fetcher).not.toHaveBeenCalled();
    shell._settingsLoadFailed = false;
    shell.SoulSyncSettings = { templateErrors: () => ['album: no $title'], flush: vi.fn() };
    await saveSettings(false);
    expect(fetcher).not.toHaveBeenCalled();
    expect(shell.showToast).toHaveBeenCalledWith(
      'Template validation failed: album: no $title',
      'error',
    );
  });

  it("the page wires no field of its own: every control is a React tab's; the button saves", async () => {
    // the search box is the page's one input of its own: each letter typed in
    // it used to queue a whole save. the page (settings-page.tsx, step V4c)
    // without its tabs
    const fetcher = respond({ success: true });
    shell.SoulSyncSettings = { flush: vi.fn(async () => true) };
    render(<SettingsPage tabs={{}} />);
    initializeSettings();
    document
      .getElementById('stg-search-input')!
      .dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise((r) => setTimeout(r, 0));
    // the page's own reads (System > Server Activity asks for its settings)
    // are not saves: nothing is posted
    const posts = () => fetcher.mock.calls.filter(([, init]) => init?.method === 'POST');
    expect(posts()).toEqual([]);
    // nothing changed: the status stays at rest (#2187)
    expect(document.getElementById('save-settings')!.dataset.state).toBe('rest');
    document.getElementById('save-settings')!.click();
    await vi.waitFor(() => expect(posts()).toHaveLength(1));
  });
});

describe('secret fields', () => {
  it('the mask wiring leaves a React field to React (SecretInput)', () => {
    document.body.innerHTML = `<div id="settings-page">
      <div data-stg-react="sources"><div class="src-config-panel" data-react-panel>
        <input type="password" id="react-secret"></div></div>
      <div id="source-config-home"><div class="src-config-panel">
        <input type="password" id="vanilla-secret"></div></div></div>`;
    wireRedactedSecrets();
    expect(
      (document.getElementById('react-secret') as HTMLElement).dataset.redactWired,
    ).toBeUndefined();
    expect((document.getElementById('vanilla-secret') as HTMLElement).dataset.redactWired).toBe(
      '1',
    );
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
