/**
 * The host the page's shell calls (SETTINGS_PORT.md): window.SoulSyncSettings.
 * The page renders every tab into its slots (settings-page.tsx, step V4c); the
 * host tells it which tab is open, and each tab how often it was opened.
 */

import { act, cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createSettingsHost, mountSettingsHost, type SettingsTabProps } from './host';
import { nav } from './nav';
import { pageTabs } from './page-tabs';
import { SettingsPage } from './settings-page';
import { createSettingsStore } from './settings-store';

function Appearance({ store }: SettingsTabProps) {
  return <p>visualizer {String(store.getState().settings.ui_appearance?.sidebar_visualizer)}</p>;
}

afterEach(() => {
  cleanup();
  document.body.innerHTML = '';
  pageTabs.reset();
  delete window.SoulSyncSettings;
  delete (window as Window & { _settingsPayload?: unknown })._settingsPayload;
});

describe('the settings host', () => {
  it('a registered tab fills its slots once; an unregistered one leaves them empty', async () => {
    const store = createSettingsStore({ post: () => Promise.resolve({ success: true }) });
    const host = createSettingsHost({ tabs: { appearance: Appearance }, store, effects: [] });
    host.hydrate({ ui_appearance: { sidebar_visualizer: 'wave' } });
    render(<SettingsPage store={store} tabs={{ appearance: Appearance }} />);
    await act(async () => host.show('appearance'));
    const slot = document.querySelector('[data-stg-react="appearance"][data-stg-part="ui"]')!;
    expect(slot.textContent).toBe('visualizer wave');
    const first = slot.firstElementChild;
    await act(async () => host.show('appearance'));
    expect(slot.firstElementChild).toBe(first);
    await act(async () => host.show('logs'));
    expect(document.querySelector('[data-stg-react="logs"]')!.innerHTML).toBe('');
    expect(host.ported()).toEqual(['appearance']);
  });

  it('a tab with several slots gets each its part, and every tab is on the page', async () => {
    const Part = ({ part, opened, active }: SettingsTabProps) => (
      <p>{`${part ?? 'whole'} ${opened}${active ? ' open' : ''}`}</p>
    );
    const tabs = { appearance: Part, logs: Part };
    const host = createSettingsHost({ tabs, effects: [] });
    render(<SettingsPage tabs={tabs} />);
    const text = () =>
      [...document.querySelectorAll('[data-stg-react="appearance"], [data-stg-react="logs"]')].map(
        (el) => el.textContent,
      );
    // opening Logs leaves Appearance on the page too: the settings search reads
    // every tab, and the shell is about to hide what is not the open tab
    await act(async () => host.show('logs'));
    expect(text()).toEqual(['ui 0', 'weather 0', 'whole 1 open']);
    // opening a tab again tells it so (the weather tile reloads on arrival)
    await act(async () => host.show('appearance'));
    expect(text()).toEqual(['ui 1 open', 'weather 1 open', 'whole 1']);
  });

  it('a new visit mounts its tabs closed, not as the last visit left them', async () => {
    const seen: string[] = [];
    const Part = ({ part, opened, active }: SettingsTabProps) => {
      seen.push(`${part ?? 'whole'} ${opened}${active ? ' open' : ''}`);
      return null;
    };
    const tabs = { logs: Part };
    const host = createSettingsHost({ tabs, effects: [] });
    const first = render(<SettingsPage tabs={tabs} />);
    await act(async () => host.show('logs'));
    first.unmount();
    seen.length = 0;
    render(<SettingsPage tabs={tabs} />);
    expect(seen[0]).toBe('whole 0');
  });

  it("the page's buttons call the shell bundle's functions, one copy of its state", () => {
    const w = window as unknown as Record<string, unknown>;
    w.saveSettings = vi.fn(() => Promise.resolve());
    render(<SettingsPage tabs={{}} />);
    // a tab moves the page's one place (nav.ts), which the address follows
    act(() => (document.querySelector('[data-tab="advanced"]') as HTMLButtonElement).click());
    expect(nav.get().tab).toBe('advanced');
    (document.getElementById('save-settings') as HTMLButtonElement).click();
    expect(w.saveSettings).toHaveBeenCalledWith(false);
    delete w.saveSettings;
  });

  it('fields added after the page opened are kept from the password managers too', async () => {
    const w = window as unknown as Record<string, unknown>;
    const mark = vi.fn();
    w.initializeSettings = mark;
    render(<SettingsPage tabs={{}} />);
    document.getElementById('settings-page')!.append(document.createElement('input'));
    await vi.waitFor(() => expect(mark).toHaveBeenCalledTimes(1));
    delete w.initializeSettings;
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

  it('a tab with no page on screen is not an error', () => {
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

  it('a bad Playlist File Naming holds the autosave and says why, as the quiet save did', async () => {
    const posted: unknown[] = [];
    const toast = vi.fn();
    window.showToast = toast;
    const store = createSettingsStore({
      post: (body) => {
        posted.push(body);
        return Promise.resolve({ success: true });
      },
      delay: 0,
    });
    createSettingsHost({ tabs: { library: () => null }, store });
    store.set('file_organization', 'templates', { playlist_item: '$position - $artist' });
    await vi.waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        'Playlist File Naming must include $title so every file has a name.',
        'error',
      ),
    );
    expect(posted).toEqual([]);
    expect(store.hasPending()).toBe(true);
    store.set('file_organization', 'templates', { playlist_item: '$position - $title' });
    expect(await store.flush()).toBe(true);
    expect(posted).toHaveLength(1);
    delete window.showToast;
  });

  it('catches up when the page loaded its settings before this bundle ran', () => {
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
