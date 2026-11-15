/**
 * The host that puts a ported Settings tab into the vanilla page
 * (SETTINGS_PORT.md): settings.js calls window.SoulSyncSettings, and a tab
 * that is registered fills its slot once.
 */

import { act } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createSettingsHost, mountSettingsHost, type SettingsTabProps } from './host';
import { createSettingsStore } from './settings-store';

function page(slots: string[]) {
  document.body.innerHTML = `<div id="settings-page">
    <button class="stg-tab active" data-tab="appearance"></button>
    ${slots.map((tab) => `<div data-stg="${tab}" data-stg-react="${tab}"></div>`).join('')}
  </div>`;
}

function Appearance({ store }: SettingsTabProps) {
  return <p>visualizer {String(store.getState().settings.ui_appearance?.sidebar_visualizer)}</p>;
}

afterEach(() => {
  document.body.innerHTML = '';
  delete window.SoulSyncSettings;
  delete (window as Window & { _settingsPayload?: unknown })._settingsPayload;
});

describe('the settings host', () => {
  it('fills a ported tab once and leaves other tabs to the vanilla page', async () => {
    page(['appearance', 'logs']);
    const store = createSettingsStore({ post: () => Promise.resolve({ success: true }) });
    const host = createSettingsHost({ tabs: { appearance: Appearance }, store, effects: [] });
    host.hydrate({ ui_appearance: { sidebar_visualizer: 'wave' } });
    await act(async () => host.show('appearance'));
    const slot = document.querySelector('[data-stg-react="appearance"]')!;
    expect(slot.textContent).toBe('visualizer wave');
    const first = slot.firstElementChild;
    await act(async () => host.show('appearance'));
    expect(slot.firstElementChild).toBe(first);
    await act(async () => host.show('logs'));
    expect(document.querySelector('[data-stg-react="logs"]')!.innerHTML).toBe('');
    expect(host.ported()).toEqual(['appearance']);
  });

  it('a tab with several slots gets each its part, and every ported tab is on the page', async () => {
    document.body.innerHTML = `<div id="settings-page">
      <div data-stg-react="appearance" data-stg-part="ui"></div>
      <div data-stg-react="appearance" data-stg-part="weather"></div>
      <div data-stg-react="logs"></div></div>`;
    const Part = ({ part, opened }: SettingsTabProps) => <p>{`${part ?? 'whole'} ${opened}`}</p>;
    const host = createSettingsHost({ tabs: { appearance: Part, logs: Part }, effects: [] });
    // opening Logs puts Appearance on the page too: the settings search reads
    // every tab, and settings.js is about to hide what is not the open tab
    host.show('logs');
    const text = () =>
      [...document.querySelectorAll('[data-stg-react]')].map((el) => el.textContent);
    expect(text()).toEqual(['ui 0', 'weather 0', 'whole 1']);
    // opening a tab again tells it so (the weather tile reloads on arrival)
    await act(async () => host.show('appearance'));
    expect(text()).toEqual(['ui 1', 'weather 1', 'whole 1']);
  });

  it('loading the settings runs what opening Settings applies, once the payload is good', () => {
    const seen: unknown[] = [];
    const store = createSettingsStore({ post: () => Promise.resolve({ success: true }) });
    const host = createSettingsHost({
      tabs: {},
      store,
      effects: [
        () => {
          throw new Error('one effect failing does not stop the next');
        },
        (settings) => seen.push(settings.ui_appearance),
      ],
    });
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    host.hydrate({ error: 'HTTP 500' });
    expect(seen).toEqual([]);
    host.hydrate({ ui_appearance: { accent_preset: '#8b5cf6' } });
    expect(seen).toEqual([{ accent_preset: '#8b5cf6' }]);
    error.mockRestore();
  });

  it('a tab with no slot on the page is not an error', () => {
    page([]);
    const host = createSettingsHost({ tabs: { appearance: Appearance } });
    expect(() => host.show('appearance')).not.toThrow();
    expect(() => host.show('nope')).not.toThrow();
  });

  it('passes the save button and the unsaved mark through to the store', async () => {
    const posted: unknown[] = [];
    const store = createSettingsStore({
      post: (body) => {
        posted.push(body);
        return Promise.resolve({ success: true });
      },
    });
    const host = createSettingsHost({ tabs: {}, store });
    (window as Window & { _settingsAutosaveOff?: boolean })._settingsAutosaveOff = true;
    store.set('ui_appearance', 'sidebar_visualizer', 'wave');
    expect(host.hasPending()).toBe(true);
    expect(await host.flush()).toBe(true);
    expect(posted).toEqual([{ ui_appearance: { sidebar_visualizer: 'wave' } }]);
    expect(host.hasPending()).toBe(false);
    delete (window as Window & { _settingsAutosaveOff?: boolean })._settingsAutosaveOff;
  });

  it('catches up when the page loaded its settings before this bundle ran', () => {
    page([]);
    (window as Window & { _settingsPayload?: unknown })._settingsPayload = {
      ui_appearance: { sidebar_visualizer: 'mirror' },
    };
    mountSettingsHost();
    expect(window.SoulSyncSettings).toBeDefined();
    expect(window.SoulSyncSettings!.ported()).toContain('appearance');
    const again = window.SoulSyncSettings;
    mountSettingsHost();
    expect(window.SoulSyncSettings).toBe(again);
  });
});
