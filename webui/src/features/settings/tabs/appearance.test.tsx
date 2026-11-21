/**
 * The Appearance tab on the parts (settings-bic phase 5): one page, a line
 * saying what it is for, every control in its card. The behaviour tests
 * follow what settings.js and init.js did.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { nav } from '../nav';
import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { applyAppearanceOnLoad, effectFlags, resolveAppearance } from './appearance';
import { AppearanceTab, weatherStatus } from './appearance-tab';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

let store: SettingsStore;
let posted: SettingsPayload[];
let applied: Array<[string, unknown]>;

beforeEach(() => {
  // each store saves into its own list: a save the last test left queued
  // must not land in this one's
  const sink: SettingsPayload[] = [];
  posted = sink;
  applied = [];
  store = createSettingsStore({
    post: (body) => {
      sink.push(structuredClone(body));
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
  nav.reset();
  act(() => nav.go('appearance'));
});

afterEach(() => {
  vi.unstubAllGlobals();
  nav.reset();
  for (const key of ['_settingsAutosaveOff', '_settingsPayload', 'openFeaturePicker'])
    delete shell[key];
});

const page = (opened = 1) =>
  render(<AppearanceTab store={store} part="page" opened={opened} active />);
const quiet = () =>
  vi.stubGlobal(
    'fetch',
    vi.fn(() => new Promise(() => {})),
  );

describe('the Appearance page', () => {
  it('every control in its card, named without a colon, no folded section', () => {
    quiet();
    store.hydrate({ ui_appearance: {} });
    page();
    const cards: Record<string, string[]> = {
      'Show only what you use': ['settings-open-feature-picker'],
      Look: ['accent-preset', 'accent-custom-color', 'sidebar-visualizer-type'],
      Effects: [
        'particles-enabled',
        'worker-orbs-enabled',
        'reduce-effects-enabled',
        'max-performance-enabled',
      ],
      'Sidebar weather': ['sw-location', 'sw-enabled', 'sw-holidays', 'sw-celsius', 'sw-status'],
      'Video pages': ['video-billboard-autoplay', 'video-watch-region'],
    };
    for (const [card, ids] of Object.entries(cards)) {
      const el = document.querySelector(`[data-card="${card}"]`)!;
      expect(el, card).not.toBeNull();
      for (const id of ids) expect(el.contains(document.getElementById(id)), id).toBe(true);
    }
    // the video pages are the video side's own; the weather is open, not folded
    expect(
      document.getElementById('video-billboard-autoplay')!.closest('[data-video-only]'),
    ).not.toBeNull();
    expect(document.querySelector('.settings-section-header, .form-group')).toBeNull();
    for (const label of document.querySelectorAll('label'))
      expect(label.textContent).not.toMatch(/:\s*$/);
    expect(document.body.textContent).not.toContain('—');
    // the two switches kept per device say so
    for (const id of ['reduce-effects-enabled', 'max-performance-enabled'])
      expect(document.getElementById(id)!.closest('[data-row]')!.textContent).toContain(
        'This device',
      );
    for (const id of ['particles-enabled', 'worker-orbs-enabled'])
      expect(document.getElementById(id)!.closest('[data-row]')!.textContent).not.toContain(
        'This device',
      );
  });

  it('opens with what the page is for, in one line', () => {
    store.hydrate({ ui_appearance: { accent_preset: '#8b5cf6' } });
    page();
    expect(document.querySelector('[data-stg-view="all"] [data-about]')!.textContent).toBe(
      'How SoulSync looks, on every device and on this one.',
    );
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
  const ui = () => {
    quiet();
    return page();
  };

  it('a preset applies at once and saves only its own key', async () => {
    store.hydrate({ ui_appearance: { accent_preset: '#1db954', accent_color: '#abcdef' } });
    ui();
    const custom = document.getElementById('custom-color-group') as HTMLElement;
    expect(custom.hidden).toBe(true);
    const swatch = (value: string) =>
      document.querySelector<HTMLElement>(`#accent-preset [data-value="${value}"]`)!;
    expect(swatch('#1db954').getAttribute('aria-checked')).toBe('true');
    // every preset is a named swatch, Custom last
    expect(
      [...document.querySelectorAll('#accent-preset [role="radio"]')].map((b) =>
        b.getAttribute('aria-label'),
      ),
    ).toEqual([
      'Spotify Green',
      'Ocean Blue',
      'Purple',
      'Boulder Purple',
      'Sunset Orange',
      'Rose',
      'Teal',
      'Custom',
    ]);
    fireEvent.click(swatch('#8b5cf6'));
    expect(applied.at(-1)).toEqual(['applyAccentColor', '#8b5cf6']);
    await waitFor(() => expect(posted).toEqual([{ ui_appearance: { accent_preset: '#8b5cf6' } }]));
    // Custom shows the picker and applies the custom colour
    expect(swatch('#8b5cf6').getAttribute('aria-checked')).toBe('true');
    fireEvent.click(swatch('custom'));
    expect(custom.hidden).toBe(false);
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
      expect(box(id).closest('[data-row]')!.textContent).toContain(
        'Off while max performance is on',
      );
    }
    await waitFor(() => expect(posted).toEqual([{ ui_appearance: { max_performance: true } }]));
    // the person's own choice was never saved as off
    expect(effectFlags()).toMatchObject({ maxPerformance: true, particles: false });
    fireEvent.click(box('max-performance-enabled'));
    expect(box('particles-enabled').checked).toBe(true);
    expect(box('particles-enabled').disabled).toBe(false);
  });

  it('#2164: no Customize door while an admin has the profile locked', () => {
    const features = window.SoulSyncFeatures;
    window.SoulSyncFeatures = {
      get: () => ({ loaded: true, off: [], hiddenPages: [], payload: { locked: true } }),
    } as unknown as typeof window.SoulSyncFeatures;
    try {
      store.hydrate({});
      ui();
      expect(screen.queryByText('Customize SoulSync')).toBeNull();
      expect(screen.queryByText('Show only what you use')).toBeNull();
      // the rest of the tab is still there
      expect(screen.getByText('Look')).toBeInTheDocument();
    } finally {
      window.SoulSyncFeatures = features;
    }
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

  const tile = (opened = 1) => page(opened);
  const box = (id: string) => document.getElementById(id) as HTMLInputElement;

  it('says where, how warm and how fresh, in every state the page had', () => {
    expect(
      weatherStatus({
        location: { name: 'Medford' },
        snapshot: { fetched_at: '10:30', current: { temp: 61.6 } },
        units: 'celsius',
      }),
    ).toBe('Medford, 62°C, updated 10:30');
    expect(weatherStatus({ location: { name: 'Medford' }, snapshot: { current: null } })).toBe(
      'Medford: waiting for the first forecast',
    );
    expect(weatherStatus({ location: { name: 'Medford' }, enabled: false })).toBe(
      'Medford: weather display off',
    );
    expect(weatherStatus({ location: { name: 'Medford' } })).toBe(
      'Medford: forecast unavailable right now',
    );
    expect(weatherStatus({})).toBe('Off: set a location to turn it on');
  });

  it('loads when the tab is opened, and again each time it is opened', async () => {
    // on the page because Settings opened on another tab: not loaded yet
    const view = tile(0);
    await act(async () => {});
    expect(calls).toEqual([]);
    view.rerender(<AppearanceTab store={store} part="page" opened={1} active />);
    await waitFor(() => expect(box('sw-location').value).toBe('97501'));
    expect(box('sw-enabled').checked).toBe(true);
    expect(box('sw-celsius').checked).toBe(false);
    expect(box('sw-holidays').checked).toBe(false);
    expect(document.getElementById('sw-status')!.textContent).toBe(
      'Medford, Oregon, 62°F, updated 10:30',
    );
    expect(calls.filter((c) => c.method === 'GET')).toHaveLength(1);
    view.rerender(<AppearanceTab store={store} part="page" opened={2} active />);
    await waitFor(() => expect(calls.filter((c) => c.method === 'GET')).toHaveLength(2));
  });

  it('is open on the page, with Enter in the field saving like Save', async () => {
    tile();
    await waitFor(() => expect(box('sw-location').value).toBe('97501'));
    expect(box('sw-location').closest('[hidden]')).toBeNull();
    fireEvent.change(box('sw-location'), { target: { value: 'Ashland' } });
    fireEvent.keyDown(box('sw-location'), { key: 'Enter' });
    await waitFor(() =>
      expect(calls).toContainEqual({
        url: '/api/weather/location',
        method: 'PUT',
        body: { location: 'Ashland' },
      }),
    );
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
