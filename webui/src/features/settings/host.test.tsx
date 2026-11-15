/**
 * The host that puts a ported Settings tab into the vanilla page
 * (SETTINGS_PORT.md): settings.js calls window.SoulSyncSettings, and a tab
 * that is registered fills its slot once.
 */

import { act } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

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
    const host = createSettingsHost({ tabs: { appearance: Appearance }, store });
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
    expect(window.SoulSyncSettings!.ported()).toEqual([]);
    const again = window.SoulSyncSettings;
    mountSettingsHost();
    expect(window.SoulSyncSettings).toBe(again);
  });
});
