/**
 * The Appearance tab, ported from the vanilla Settings page (SETTINGS_PORT.md,
 * step 2). The markup it replaced is frozen in __fixtures__ and the React tab
 * has to produce the same elements; the behaviour tests follow what
 * settings.js and init.js did.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { applyAppearanceOnLoad, effectFlags, resolveAppearance } from './appearance';
import { AppearanceTab, weatherStatus } from './appearance-tab';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;
const fixture = (name: string) =>
  readFileSync(resolve(process.cwd(), 'src/features/settings/tabs/__fixtures__', name), 'utf8');

/** every element as tag, id, classes and the attributes the page's css and
 *  scripts key on, in document order, plus the text a person reads */
function shape(root: Element) {
  const elements = [...root.querySelectorAll('*')].map((el) => {
    const attrs = ['data-stg', 'type', 'placeholder', 'for', 'role', 'tabindex', 'value']
      .filter((name) => el.tagName !== 'INPUT' || name !== 'value')
      .map((name) => [name, el.getAttribute(name)] as const)
      .filter(([, value]) => value !== null)
      .map(([name, value]) => `[${name}=${value}]`)
      .join('');
    const classes = [...el.classList]
      .filter((c) => c !== 'setting-overridden')
      .sort()
      .join('.');
    return `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}${classes ? '.' + classes : ''}${attrs}`;
  });
  // the words, whatever the whitespace between the tags
  const text = (root.textContent || '').replace(/\s+/g, '');
  return { elements, text };
}

function vanilla(name: string) {
  const host = document.createElement('div');
  host.innerHTML = fixture(name);
  return host;
}

let store: SettingsStore;
let posted: SettingsPayload[];
let applied: Array<[string, unknown]>;

beforeEach(() => {
  posted = [];
  applied = [];
  store = createSettingsStore({
    post: (body) => {
      posted.push(structuredClone(body));
      return Promise.resolve({ success: true });
    },
    delay: 0,
  });
  localStorage.clear();
  for (const [name, flag] of [
    ['applyParticlesSetting', '_particlesEnabled'],
    ['applyWorkerOrbsSetting', '_workerOrbsEnabled'],
    ['applyReduceEffects', '_reduceEffectsActive'],
    ['applyMaxPerformance', '_maxPerfActive'],
  ] as const) {
    shell[name] = (enabled: boolean) => {
      applied.push([name, enabled]);
      shell[flag] = enabled;
    };
  }
  shell.applyAccentColor = (hex: string) => applied.push(['applyAccentColor', hex]);
  shell.setSidebarVisualizerType = (type: string) =>
    applied.push(['setSidebarVisualizerType', type]);
  shell.showToast = vi.fn();
  shell.bootSidebarWeather = vi.fn(() => Promise.resolve());
  shell._particlesEnabled = false;
  shell._workerOrbsEnabled = true;
  shell._reduceEffectsActive = false;
  shell._maxPerfActive = false;
});

afterEach(() => {
  vi.unstubAllGlobals();
  for (const key of ['_settingsAutosaveOff', '_settingsPayload', 'openFeaturePicker'])
    delete shell[key];
});

describe('the Appearance tab is the same page it replaced', () => {
  it('UI Appearance renders the vanilla blocks: same elements, ids, classes and words', () => {
    store.hydrate({ ui_appearance: {} });
    const { container } = render(<AppearanceTab store={store} part="ui" opened={1} />);
    const want = shape(vanilla('appearance-ui.vanilla.html'));
    const got = shape(container);
    expect(got.elements).toEqual(want.elements);
    expect(got.text).toBe(want.text);
  });

  it('Sidebar Weather renders the vanilla tile, closed, with the same elements and words', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise(() => {})),
    );
    const { container } = render(<AppearanceTab store={store} part="weather" opened={1} />);
    const want = shape(vanilla('appearance-weather.vanilla.html'));
    const got = shape(container);
    expect(got.elements).toEqual(want.elements);
    expect(got.text).toBe(want.text);
    const body = container.querySelector('.settings-section-body') as HTMLElement;
    expect(body.style.display).toBe('none');
  });
});

describe('what opening Settings applies', () => {
  it('resolves the saved values the way loadSettingsData did', () => {
    expect(resolveAppearance({})).toMatchObject({
      preset: '#1db954',
      custom: '#1db954',
      accent: '#1db954',
      visualizer: 'bars',
      particles: false,
      workerOrbs: true,
      reduceEffects: false,
      maxPerformance: false,
    });
    // a saved colour that is not an option shows as Custom, in the custom colour
    expect(
      resolveAppearance({ ui_appearance: { accent_preset: '#123456', accent_color: '#abcdef' } }),
    ).toMatchObject({ preset: 'custom', accent: '#abcdef' });
    // no saved orb choice: the server's browser default on the window flag
    shell._workerOrbsEnabled = false;
    expect(resolveAppearance({}).workerOrbs).toBe(false);
    expect(resolveAppearance({ ui_appearance: { worker_orbs_enabled: true } }).workerOrbs).toBe(
      true,
    );
    // this device's own choice beats the server's for the two device switches
    localStorage.setItem('soulsync-reduce-effects', '1');
    localStorage.setItem('soulsync-max-performance', '0');
    expect(
      resolveAppearance({ ui_appearance: { reduce_effects: false, max_performance: true } }),
    ).toMatchObject({ reduceEffects: true, maxPerformance: false });
  });

  it('applies accent, visualizer and the four switches, Max Performance last', () => {
    applyAppearanceOnLoad({
      ui_appearance: {
        accent_preset: '#8b5cf6',
        sidebar_visualizer: 'wave',
        particles_enabled: true,
        worker_orbs_enabled: false,
        reduce_effects: true,
        max_performance: false,
      },
    });
    expect(applied).toEqual([
      ['applyAccentColor', '#8b5cf6'],
      ['setSidebarVisualizerType', 'wave'],
      ['applyParticlesSetting', true],
      ['applyWorkerOrbsSetting', false],
      ['applyReduceEffects', true],
      ['applyMaxPerformance', false],
    ]);
  });
});

describe('changing the appearance', () => {
  const ui = () => render(<AppearanceTab store={store} part="ui" opened={1} />);

  it('a preset applies at once and saves only its own key', async () => {
    store.hydrate({ ui_appearance: { accent_preset: '#1db954', accent_color: '#abcdef' } });
    ui();
    const custom = document.getElementById('custom-color-group') as HTMLElement;
    expect(custom.style.display).toBe('none');
    fireEvent.change(document.getElementById('accent-preset')!, { target: { value: '#8b5cf6' } });
    expect(applied.at(-1)).toEqual(['applyAccentColor', '#8b5cf6']);
    await waitFor(() => expect(posted).toEqual([{ ui_appearance: { accent_preset: '#8b5cf6' } }]));
    // Custom shows the picker and applies the custom colour
    fireEvent.change(document.getElementById('accent-preset')!, { target: { value: 'custom' } });
    expect(custom.style.display).toBe('');
    expect(applied.at(-1)).toEqual(['applyAccentColor', '#abcdef']);
    await waitFor(() =>
      expect(posted.at(-1)).toEqual({ ui_appearance: { accent_preset: 'custom' } }),
    );
    fireEvent.change(document.getElementById('accent-custom-color')!, {
      target: { value: '#112233' },
    });
    expect(applied.at(-1)).toEqual(['applyAccentColor', '#112233']);
    await waitFor(() =>
      expect(posted.at(-1)).toEqual({ ui_appearance: { accent_color: '#112233' } }),
    );
  });

  it('the visualizer change still reaches media-player.js through the document', async () => {
    store.hydrate({ ui_appearance: { sidebar_visualizer: 'bars' } });
    ui();
    const heard: string[] = [];
    const listen = (event: Event) => {
      const target = event.target as HTMLSelectElement;
      if (target.id === 'sidebar-visualizer-type') heard.push(target.value);
    };
    document.addEventListener('change', listen);
    const select = document.getElementById('sidebar-visualizer-type') as HTMLSelectElement;
    select.value = 'mirror';
    select.dispatchEvent(new Event('change', { bubbles: true }));
    document.removeEventListener('change', listen);
    expect(heard).toEqual(['mirror']);
    await waitFor(() =>
      expect(posted).toEqual([{ ui_appearance: { sidebar_visualizer: 'mirror' } }]),
    );
  });

  it('an effect switch calls init.js and saves what the flag says', async () => {
    store.hydrate({ ui_appearance: {} });
    ui();
    const particles = document.getElementById('particles-enabled') as HTMLInputElement;
    expect(particles.checked).toBe(false);
    fireEvent.click(particles);
    expect(applied.at(-1)).toEqual(['applyParticlesSetting', true]);
    expect(particles.checked).toBe(true);
    await waitFor(() => expect(posted).toEqual([{ ui_appearance: { particles_enabled: true } }]));
  });

  it('Max Performance locks the other three off and keeps what they were set to', async () => {
    shell._particlesEnabled = true;
    store.hydrate({ ui_appearance: {} });
    ui();
    const box = (id: string) => document.getElementById(id) as HTMLInputElement;
    expect(box('particles-enabled').checked).toBe(true);
    fireEvent.click(box('max-performance-enabled'));
    expect(applied.at(-1)).toEqual(['applyMaxPerformance', true]);
    for (const id of ['particles-enabled', 'worker-orbs-enabled', 'reduce-effects-enabled']) {
      expect(box(id).checked).toBe(false);
      expect(box(id).disabled).toBe(true);
      expect(box(id).closest('.form-group')!.classList.contains('setting-overridden')).toBe(true);
    }
    await waitFor(() => expect(posted).toEqual([{ ui_appearance: { max_performance: true } }]));
    // the person's own choice was never saved as off
    expect(effectFlags()).toMatchObject({ maxPerformance: true, particles: false });
    fireEvent.click(box('max-performance-enabled'));
    expect(box('particles-enabled').checked).toBe(true);
    expect(box('particles-enabled').disabled).toBe(false);
  });

  it('Customize SoulSync opens the feature picker', () => {
    const open = vi.fn();
    shell.openFeaturePicker = open;
    store.hydrate({});
    ui();
    fireEvent.click(screen.getByText('Customize SoulSync'));
    expect(open).toHaveBeenCalledOnce();
  });
});

describe('Sidebar Weather', () => {
  type Call = { url: string; method: string; body: unknown };
  let calls: Call[];
  let weather: Record<string, unknown>;

  beforeEach(() => {
    calls = [];
    weather = {
      success: true,
      enabled: true,
      units: 'fahrenheit',
      holidays: false,
      location: { name: 'Medford, Oregon', query: '97501' },
      snapshot: { fetched_at: '10:30', current: { temp: 61.6 } },
    };
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string, init?: RequestInit) => {
        calls.push({
          url,
          method: init?.method ?? 'GET',
          body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
        });
        const body =
          init?.method === 'PUT'
            ? { success: true, location: { name: 'Ashland, Oregon' } }
            : weather;
        return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
      }),
    );
  });

  const tile = (opened = 1) =>
    render(<AppearanceTab store={store} part="weather" opened={opened} />);
  const box = (id: string) => document.getElementById(id) as HTMLInputElement;

  it('says where, how warm and how fresh, in every state the page had', () => {
    expect(
      weatherStatus({
        location: { name: 'Medford' },
        snapshot: { fetched_at: '10:30', current: { temp: 61.6 } },
        units: 'celsius',
      }),
    ).toBe('Medford — 62°C, updated 10:30');
    expect(weatherStatus({ location: { name: 'Medford' }, snapshot: { current: null } })).toBe(
      'Medford — waiting for first forecast',
    );
    expect(weatherStatus({ location: { name: 'Medford' }, enabled: false })).toBe(
      'Medford — weather display off',
    );
    expect(weatherStatus({ location: { name: 'Medford' } })).toBe(
      'Medford — forecast unavailable right now',
    );
    expect(weatherStatus({})).toBe('Off — set a location to turn it on');
  });

  it('loads when the tab is opened, and again each time it is opened', async () => {
    // on the page because Settings opened on another tab: not loaded yet
    const view = tile(0);
    await act(async () => {});
    expect(calls).toEqual([]);
    view.rerender(<AppearanceTab store={store} part="weather" opened={1} />);
    await waitFor(() => expect(box('sw-location').value).toBe('97501'));
    expect(box('sw-enabled').checked).toBe(true);
    expect(box('sw-celsius').checked).toBe(false);
    expect(box('sw-holidays').checked).toBe(false);
    expect(document.getElementById('sw-status')!.textContent).toBe(
      'Medford, Oregon — 62°F, updated 10:30',
    );
    expect(calls.filter((c) => c.method === 'GET')).toHaveLength(1);
    view.rerender(<AppearanceTab store={store} part="weather" opened={2} />);
    await waitFor(() => expect(calls.filter((c) => c.method === 'GET')).toHaveLength(2));
  });

  it('opens and closes like every other tile on the page', async () => {
    const { container } = tile();
    const header = container.querySelector('.settings-section-header') as HTMLElement;
    const body = container.querySelector('.settings-section-body') as HTMLElement;
    fireEvent.click(header);
    expect(header.classList.contains('collapsed')).toBe(false);
    expect(body.classList.contains('collapsed')).toBe(false);
    expect(body.style.display).toBe('');
    fireEvent.click(header);
    expect(body.classList.contains('collapsed')).toBe(true);
    expect(body.style.display).toBe('none');
    await act(async () => {});
  });

  it('Save sets the location, says so, reloads and re-reads the sidebar', async () => {
    tile();
    await waitFor(() => expect(box('sw-location').value).toBe('97501'));
    fireEvent.change(box('sw-location'), { target: { value: ' Ashland ' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() =>
      expect(calls).toContainEqual({
        url: '/api/weather/location',
        method: 'PUT',
        body: { location: 'Ashland' },
      }),
    );
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith(
        'Weather location set to Ashland, Oregon',
        'success',
      ),
    );
    expect(shell.bootSidebarWeather).toHaveBeenCalled();
  });

  it('Save with nothing typed asks for a place and sends nothing', async () => {
    weather.location = null;
    tile();
    await waitFor(() => expect(document.getElementById('sw-status')!.textContent).toMatch(/^Off/));
    fireEvent.click(screen.getByText('Save'));
    expect(shell.showToast).toHaveBeenCalledWith('Enter a ZIP or city first', 'error');
    expect(calls.filter((c) => c.method === 'PUT')).toEqual([]);
  });

  it('Clear, the master switch, holidays and units each call their own endpoint', async () => {
    tile();
    await waitFor(() => expect(box('sw-location').value).toBe('97501'));
    fireEvent.click(screen.getByText('Clear'));
    fireEvent.click(box('sw-enabled'));
    fireEvent.click(box('sw-holidays'));
    fireEvent.click(box('sw-celsius'));
    await waitFor(() => {
      const puts = calls.filter((c) => c.method === 'PUT');
      expect(puts).toContainEqual({
        url: '/api/weather/location',
        method: 'PUT',
        body: { location: '' },
      });
      expect(puts).toContainEqual({
        url: '/api/weather/display',
        method: 'PUT',
        body: { enabled: false },
      });
      expect(puts).toContainEqual({
        url: '/api/weather/holidays',
        method: 'PUT',
        body: { enabled: true },
      });
      expect(puts).toContainEqual({
        url: '/api/weather/units',
        method: 'PUT',
        body: { use_celsius: true },
      });
    });
    await waitFor(() => expect(shell.showToast).toHaveBeenCalledWith('Using °C', 'success'));
    expect(shell.showToast).toHaveBeenCalledWith('Sidebar weather turned off', 'success');
    expect(shell.showToast).toHaveBeenCalledWith('Sidebar weather off', 'success');
  });
});
