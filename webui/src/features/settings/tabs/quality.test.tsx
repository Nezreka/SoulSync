/**
 * Quality > Music (settings-bic phase 3 on the state SETTINGS_PORT.md step 6
 * ported): the page reads the profile back, the profiles are cards with a ⋯
 * menu, the priority list is the shared order list with the cutoff drawn in
 * it, and every control saves to the same routes as before.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import {
  type Ladder,
  EMPTY_ADD,
  EMPTY_LADDER,
  addTargets,
  collectBundle,
  extrasFrom,
  extrasFromConfig,
  ladderFrom,
  profileSummary,
  rtLabel,
  withTargets,
} from './quality';
import { MusicQualityTab } from './quality-tab';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

let store: SettingsStore;
let posted: SettingsPayload[];

const DEFAULT = {
  id: 1,
  name: 'Balanced',
  is_default: true,
  version: 3,
  preset: 'balanced',
  fallback_enabled: true,
  search_mode: 'priority',
  rank_candidates_by_quality: false,
  release_import_mode: 'requested_tracks',
  ranked_targets: [
    { format: 'flac', label: 'FLAC 24-bit', bit_depth: 24 },
    { format: 'flac', label: 'FLAC' },
    { format: 'mp3', label: 'MP3 ≥320kbps', min_bitrate: 320 },
  ],
  acoustid_required: false,
  downsample_enabled: false,
  deep_audio_verify: true,
  replace_lower_quality: true,
  lossy_copy_enabled: false,
  lossy_copy_codec: 'mp3',
  lossy_copy_bitrate: '320',
  lossy_copy_delete_original: false,
  upgrade_policy: 'until_cutoff',
  upgrade_cutoff_index: 1,
};

const ROWS = [
  {
    id: 1,
    name: 'Balanced',
    is_default: true,
    ranked_targets: JSON.stringify(DEFAULT.ranked_targets),
    deep_audio_verify: true,
    upgrade_policy: 'until_cutoff',
    upgrade_cutoff_index: 1,
  },
  {
    id: 2,
    name: 'Strict',
    is_default: false,
    ranked_targets: JSON.stringify([{ format: 'flac', label: 'FLAC' }]),
    acoustid_required: true,
    lossy_copy_enabled: true,
    lossy_copy_codec: 'opus',
    upgrade_policy: 'none',
  },
];

const STRICT = {
  ...DEFAULT,
  id: 2,
  name: 'Strict',
  is_default: false,
  preset: 'custom',
  ranked_targets: [{ format: 'flac', label: 'FLAC' }],
  acoustid_required: true,
  deep_audio_verify: false,
  lossy_copy_enabled: true,
  lossy_copy_codec: 'opus',
  lossy_copy_bitrate: '256',
  upgrade_policy: 'none',
  upgrade_cutoff_index: 0,
};

type Call = { url: string; method: string; body: unknown };
let calls: Call[];
let routes: Record<string, (body: unknown) => unknown>;

function respond(data: unknown, ok = true) {
  return Promise.resolve({ ok, json: () => Promise.resolve(data) } as Response);
}

beforeEach(() => {
  const sink: SettingsPayload[] = [];
  posted = sink;
  store = createSettingsStore({
    post: (body) => {
      sink.push(structuredClone(body));
      return Promise.resolve({ success: true });
    },
    delay: 0,
  });
  calls = [];
  routes = {
    'GET /api/quality-profile': () => ({ success: true, profile: DEFAULT }),
    'GET /api/quality-profile/custom': () => ({ success: true, profiles: ROWS }),
    'GET /api/auto-import/settings': () => ({ quality_profile_id: 0 }),
    'GET /api/quality-profile/custom/2': () => ({ success: true, profile: STRICT }),
    'POST /api/quality-profile/custom/1/update': () => ({ success: true, profiles: ROWS }),
    'POST /api/quality-profile/custom/2/update': () => ({ success: true, profiles: ROWS }),
  };
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method || 'GET';
      const body = init?.body ? JSON.parse(init.body as string) : undefined;
      calls.push({ url, method, body });
      const route = routes[`${method} ${url}`];
      if (!route) return respond({ success: false, error: `no route ${method} ${url}` });
      return respond(route(body));
    }),
  );
  shell.showToast = vi.fn();
  shell.showConfirmDialog = vi.fn(() => Promise.resolve(true));
  shell.invalidatePlaylistQualityProfiles = vi.fn();
});

afterEach(async () => {
  // a save this test left queued goes out now, not into the next test's calls
  await act(() => store.flush());
  vi.unstubAllGlobals();
  delete shell._settingsAutosaveOff;
});

const tab = () => render(<MusicQualityTab store={store} opened={1} active />);
const box = (id: string) => document.getElementById(id) as HTMLInputElement;
const select = (id: string) => document.getElementById(id) as HTMLSelectElement;
const saves = (url: string) => calls.filter((c) => c.method === 'POST' && c.url === url);
/** a choice of a segmented control, by its value */
const pick = (id: string, value: string) =>
  fireEvent.click(document.querySelector(`#${id} [data-value="${value}"]`)!);
const picked = (id: string) => document.getElementById(id)!.getAttribute('data-value');
const cards = () => [
  ...document.querySelectorAll<HTMLElement>('#qp-profile-list [data-profile-id]'),
];
const card = (name: string) =>
  cards().find((c) => c.querySelector('[data-profile-name]')?.textContent === name)!;
/** a profile card opened: what it holds goes on the page */
const open = (name: string) => fireEvent.click(card(name).querySelector('button')!);
/** a profile card's ⋯ menu, then one of its entries */
function menu(name: string, entry: string) {
  fireEvent.click(screen.getByRole('button', { name: `More for ${name}` }));
  fireEvent.click(within(card(name)).getByRole('menuitem', { name: entry }));
}
const labels = () =>
  [...document.querySelectorAll('#ranked-targets-list li[data-id] [class*="srcName"]')].map(
    (n) => n.textContent,
  );
/** a target row's ⋯ menu entry */
function targetMenu(label: string, entry: string) {
  fireEvent.click(screen.getByRole('button', { name: `More for ${label}` }));
  fireEvent.click(screen.getByRole('menuitem', { name: new RegExp(`^${entry}`) }));
}

/** the page as it is once Settings has loaded: the settings, the default
 *  profile and the profile list */
async function loaded(settings: SettingsPayload = {}) {
  const view = tab();
  act(() => store.hydrate(settings));
  await waitFor(() => expect(cards()).toHaveLength(2));
  await waitFor(() => expect(labels()).toHaveLength(3));
  return view;
}

describe('the page', () => {
  it('every control it had is there once, named without a colon, no emoji; the Manage modal went', async () => {
    const { container } = await loaded();
    for (const id of [
      'ranked-targets-list',
      'rt-add-format',
      'rt-add-bitdepth',
      'rt-add-samplerate',
      'rt-add-bitrate',
      'rt-add-bitrate-custom',
      'quality-upgrade-policy',
      'quality-upgrade-cutoff',
      'quality-search-mode',
      'quality-rank-candidates',
      'quality-fallback-enabled',
      'import-replace-lower-quality',
      'quality-release-import-mode',
      'downsample-hires',
      'lossy-copy-enabled',
      'lossy-copy-codec',
      'lossy-copy-bitrate',
      'lossy-copy-delete-original',
      'acoustid-require-verified',
      'audio-completeness-check',
      'verify-flac-decode',
      'qp-profile-list',
      'qp-profile-new-btn',
    ])
      expect(container.querySelectorAll(`#${id}`), id).toHaveLength(1);
    for (const l of container.querySelectorAll('label'))
      expect(l.textContent, l.textContent ?? '').not.toMatch(/:\s*$/);
    for (const b of container.querySelectorAll('button'))
      expect(b.textContent).not.toMatch(/[\u{1F300}-\u{1FAFF}⇩✏✎●]/u);
    expect(screen.queryByText('Manage')).toBeNull();
    expect(document.getElementById('qp-manager-overlay')).toBeNull();
    // the rare checks are behind More, which says how many and which
    const more = screen.getByRole('button', { name: /Checks and imports/ });
    expect(more.textContent).toContain('· 5');
    expect(box('verify-flac-decode').closest('[data-more-body]')).not.toBeNull();
  });

  it('the targets are the order list, the cutoff drawn in it and offered by place', async () => {
    await loaded();
    expect(labels()).toEqual(['FLAC 24-bit', 'FLAC', 'MP3 ≥320kbps']);
    const marker = document.querySelector('#ranked-targets-list [data-marker]') as HTMLElement;
    expect(marker.textContent).toBe('Upgrades stop here');
    // after the second row (the cutoff is the second target)
    expect(marker.style.order).toBe('3');
    expect(select('quality-upgrade-cutoff').value).toBe('1');
    expect([...select('quality-upgrade-cutoff').options].map((o) => o.textContent)).toEqual([
      '1. FLAC 24-bit',
      '2. FLAC',
      '3. MP3 ≥320kbps',
    ]);
    // no cutoff line unless upgrades stop at a target
    pick('quality-upgrade-policy', 'acceptable');
    expect(document.querySelector('#ranked-targets-list [data-marker]')).toBeNull();
  });

  it('the profiles are cards: the default says Downloads, opening one marks it and says so', async () => {
    routes['GET /api/auto-import/settings'] = () => ({ quality_profile_id: 2 });
    await loaded();
    expect(card('Balanced').textContent).toContain('Downloads');
    await waitFor(() => expect(card('Strict').textContent).toContain('Auto-Import'));
    expect(card('Balanced').querySelector('[aria-pressed="true"]')).not.toBeNull();
    expect(document.getElementById('qp-editing-banner')!.hidden).toBe(true);
    open('Strict');
    await waitFor(() =>
      expect(card('Strict').querySelector('[aria-pressed="true"]')).not.toBeNull(),
    );
    const banner = document.getElementById('qp-editing-banner')!;
    expect(banner.hidden).toBe(false);
    expect(banner.querySelector('strong')?.textContent).toBe('Strict');
    expect(document.querySelector('[data-card="What Strict accepts"]')).not.toBeNull();
  });
});

describe('the values', () => {
  it('labels targets as settings.js did', () => {
    expect(rtLabel({ format: 'flac', bit_depth: 24, min_sample_rate: 96000 })).toBe(
      'FLAC 24-bit/≥96kHz',
    );
    expect(rtLabel({ format: 'flac' })).toBe('FLAC');
    expect(rtLabel({ format: 'mp3', min_bitrate: 256 })).toBe('MP3 ≥256kbps');
    expect(rtLabel({ format: '' })).toBe('ANY');
  });

  it('reads a profile the way populateQualityProfileUI did', () => {
    const l = ladderFrom(
      {
        upgrade_policy: 'until_top',
        ranked_targets: [{ format: 'flac' }],
        upgrade_cutoff_index: 5,
      },
      EMPTY_LADDER,
    );
    expect(l).toMatchObject({
      upgradePolicy: 'until_cutoff',
      cutoff: 0,
      fallback: true,
      searchMode: 'priority',
    });
    // no cutoff in the profile: the one on screen stays (clamped to the list)
    const prev: Ladder = { ...EMPTY_LADDER, cutoff: 1 };
    expect(
      ladderFrom({ ranked_targets: [{ format: 'a' }, { format: 'b' }, { format: 'c' }] }, prev)
        .cutoff,
    ).toBe(1);
    expect(
      ladderFrom({ upgrade_policy: 'weird', fallback_enabled: false }, EMPTY_LADDER),
    ).toMatchObject({
      upgradePolicy: 'none',
      fallback: false,
    });
  });

  it('keeps the cutoff inside the list as targets come and go', () => {
    const three = ladderFrom({ ...DEFAULT }, EMPTY_LADDER);
    const cut = withTargets({ ...three, cutoff: 2 }, three.targets.slice(0, 1));
    expect(cut.cutoff).toBe(0);
    // and it does not spring back when the list grows again
    expect(withTargets(cut, three.targets).cutoff).toBe(0);
  });

  it('adds a group as one target per format, never twice', () => {
    const lossy = addTargets([], {
      ...EMPTY_ADD,
      format: 'group:lossy',
      bitrate: 'custom',
      custom: '200',
    });
    expect(lossy.map((t) => t.format)).toEqual(['mp3', 'aac', 'ogg', 'opus', 'wma']);
    expect(lossy[0]).toEqual({ format: 'mp3', min_bitrate: 200, label: 'MP3 ≥200kbps' });
    expect(addTargets(lossy, { ...EMPTY_ADD, format: 'mp3', bitrate: '200' })).toHaveLength(5);
    const flac = addTargets([], {
      ...EMPTY_ADD,
      format: 'flac',
      bitDepth: '24',
      sampleRate: '96000',
    });
    expect(flac).toEqual([
      { format: 'flac', bit_depth: 24, min_sample_rate: 96000, label: 'FLAC 24-bit/≥96kHz' },
    ]);
  });

  it('summarises a profile as qpProfileSummary did', () => {
    expect(profileSummary({ id: 1, name: 'x' })).toBe('Accepts anything · upgrades off');
    expect(
      profileSummary({
        id: 1,
        name: 'x',
        ranked_targets: 'not json',
        lossy_copy_enabled: true,
        lossy_copy_delete_original: true,
        upgrade_policy: 'acceptable',
      }),
    ).toBe(
      'Accepts anything · retain MP3 only (acquisition remembered) · upgrade until any accepted target',
    );
  });

  it('reads the eight profile controls from config until the profile arrives', () => {
    expect(extrasFromConfig({ lossy_copy: { codec: 'opus', bitrate: '320' } })).toMatchObject({
      lossyCodec: 'opus',
      lossyBitrate: '256',
    });
    const base = extrasFromConfig({ acoustid: { require_verified: true } });
    expect(base.acoustidRequired).toBe(true);
    // a profile without the key (the legacy shape) leaves the control as it was
    expect(extrasFrom({}, base).acoustidRequired).toBe(true);
    expect(extrasFrom({ acoustid_required: false }, base).acoustidRequired).toBe(false);
  });

  it('collects the bundle collectFullQualityBundleFromUI posted', () => {
    const l = ladderFrom({ ...DEFAULT }, EMPTY_LADDER);
    const bundle = collectBundle(l, extrasFrom(DEFAULT, extrasFromConfig({})));
    expect(bundle).toEqual({
      version: 3,
      preset: 'balanced',
      fallback_enabled: true,
      search_mode: 'priority',
      rank_candidates_by_quality: false,
      release_import_mode: 'requested_tracks',
      upgrade_policy: 'until_cutoff',
      upgrade_cutoff_index: 1,
      ranked_targets: DEFAULT.ranked_targets,
      acoustid_required: false,
      downsample_enabled: false,
      deep_audio_verify: true,
      replace_lower_quality: true,
      lossy_copy_enabled: false,
      lossy_copy_codec: 'mp3',
      lossy_copy_bitrate: '320',
      lossy_copy_delete_original: false,
    });
  });
});

describe('saving', () => {
  it('an edit saves the whole profile to the default, never to /api/settings', async () => {
    await loaded();
    fireEvent.click(box('acoustid-require-verified'));
    pick('quality-search-mode', 'best_quality');
    await waitFor(() => expect(store.hasPending()).toBe(false));
    const sent = saves('/api/quality-profile/custom/1/update');
    expect(sent.length).toBeGreaterThan(0);
    expect(sent.at(-1)!.body).toMatchObject({
      acoustid_required: true,
      search_mode: 'best_quality',
      ranked_targets: DEFAULT.ranked_targets,
    });
    expect(posted).toEqual([]);
    // the list in the answer drops every other picker's cache
    expect(shell.invalidatePlaylistQualityProfiles).toHaveBeenCalled();
  });

  it('Verify FLAC decode is a plain setting', async () => {
    await loaded({ post_processing: { verify_flac_decode: false } });
    fireEvent.click(box('verify-flac-decode'));
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(posted).toEqual([{ post_processing: { verify_flac_decode: true } }]);
    expect(saves('/api/quality-profile/custom/1/update')).toEqual([]);
  });

  it('an edit to an opened profile goes to that profile, even after another is opened', async () => {
    shell._settingsAutosaveOff = true;
    await loaded();
    open('Strict');
    await waitFor(() => expect(box('acoustid-require-verified').checked).toBe(true));
    fireEvent.click(box('quality-fallback-enabled'));
    // back to the default before anything saved
    open('Balanced');
    routes['GET /api/quality-profile/custom/1'] = () => ({ success: true, profile: DEFAULT });
    open('Balanced');
    await waitFor(() => expect(box('acoustid-require-verified').checked).toBe(false));
    // autosave is off: nothing went out yet
    expect(saves('/api/quality-profile/custom/2/update')).toEqual([]);
    await act(() => store.flush());
    const sent = saves('/api/quality-profile/custom/2/update');
    expect(sent).toHaveLength(1);
    expect(sent[0].body).toMatchObject({
      fallback_enabled: false,
      acoustid_required: true,
      ranked_targets: [{ format: 'flac', label: 'FLAC' }],
    });
    expect(saves('/api/quality-profile/custom/1/update')).toEqual([]);
  });

  it('nothing is saved before a profile has loaded', async () => {
    routes['GET /api/quality-profile'] = () => ({ success: false, error: 'db down' });
    tab();
    act(() => store.hydrate({}));
    await waitFor(() => expect(cards()).toHaveLength(2));
    fireEvent.click(box('quality-fallback-enabled'));
    expect(store.hasPending()).toBe(false);
  });

  it('a profile opened after the first load failed is one its edits save to', async () => {
    shell._settingsAutosaveOff = true;
    routes['GET /api/quality-profile'] = () => ({ success: false, error: 'db down' });
    tab();
    act(() => store.hydrate({}));
    await waitFor(() => expect(cards()).toHaveLength(2));
    open('Strict');
    await waitFor(() => expect(box('acoustid-require-verified').checked).toBe(true));
    fireEvent.click(box('quality-fallback-enabled'));
    expect(store.hasPending()).toBe(true);
    await act(() => store.flush());
    expect(saves('/api/quality-profile/custom/2/update')).toHaveLength(1);
  });

  it('a new visit reloads the default, unless an edit is waiting to be saved', async () => {
    shell._settingsAutosaveOff = true;
    await loaded();
    fireEvent.click(box('downsample-hires'));
    const before = calls.filter((c) => c.url === '/api/quality-profile').length;
    act(() => store.hydrate({}));
    await waitFor(() =>
      expect(calls.filter((c) => c.url === '/api/quality-profile/custom').length).toBe(2),
    );
    expect(calls.filter((c) => c.url === '/api/quality-profile').length).toBe(before);
    expect(box('downsample-hires').checked).toBe(true);
    await act(() => store.flush());
    act(() => store.hydrate({}));
    await waitFor(() =>
      expect(calls.filter((c) => c.url === '/api/quality-profile').length).toBe(before + 1),
    );
    await waitFor(() => expect(box('downsample-hires').checked).toBe(false));
  });
});

describe('the controls', () => {
  it('Best quality hides rank-based order; the cutoff shows only for "selected target"', async () => {
    await loaded();
    const group = document.getElementById('quality-rank-candidates-group')!;
    const cutoff = document.getElementById('quality-upgrade-cutoff-group')!;
    expect(group.hidden).toBe(false);
    expect(cutoff.hidden).toBe(false);
    pick('quality-search-mode', 'best_quality');
    pick('quality-upgrade-policy', 'none');
    expect(group.hidden).toBe(true);
    expect(cutoff.hidden).toBe(true);
    expect(picked('quality-search-mode')).toBe('best_quality');
  });

  it('Opus greys out 320 kbps and drops to 256', async () => {
    await loaded({ lossy_copy: { enabled: false } });
    const options = document.getElementById('lossy-copy-options')!;
    expect(options.hidden).toBe(true);
    fireEvent.click(box('lossy-copy-enabled'));
    expect(options.hidden).toBe(false);
    pick('lossy-copy-codec', 'opus');
    expect(picked('lossy-copy-bitrate')).toBe('256');
    expect(
      (document.querySelector('#lossy-copy-bitrate [data-value="320"]') as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    // the switch that deletes says so beside its name
    expect(
      document.getElementById('lossy-copy-delete-original')!.closest('[data-row]')!.textContent,
    ).toContain('deletes the lossless source');
  });

  it('adds, moves by its menu and by the keyboard, and removes targets', async () => {
    await loaded();
    const lossyFields = document.getElementById('rt-add-bitrate')!.closest('[hidden], span')!;
    expect(document.getElementById('rt-add-bitrate')!.closest('[hidden]')).not.toBeNull();
    fireEvent.change(select('rt-add-format'), { target: { value: 'opus' } });
    expect(document.getElementById('rt-add-bitdepth')!.closest('[hidden]')).not.toBeNull();
    expect(lossyFields.closest('[hidden]')).toBeNull();
    fireEvent.change(select('rt-add-bitrate'), { target: { value: 'custom' } });
    expect(document.getElementById('rt-add-bitrate-custom-wrap')!.hidden).toBe(false);
    fireEvent.change(box('rt-add-bitrate-custom'), { target: { value: '160' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add target' }));
    expect(labels()).toEqual(['FLAC 24-bit', 'FLAC', 'MP3 ≥320kbps', 'OPUS ≥160kbps']);
    targetMenu('OPUS ≥160kbps', 'Move up');
    expect(labels()).toEqual(['FLAC 24-bit', 'FLAC', 'OPUS ≥160kbps', 'MP3 ≥320kbps']);
    // the handle with the keyboard: Space picks it up, the arrows move it, Space drops it
    const grip = screen.getByRole('button', { name: /^Reorder MP3 ≥320kbps/ });
    fireEvent.keyDown(grip, { key: ' ' });
    for (let i = 0; i < 3; i += 1) fireEvent.keyDown(grip, { key: 'ArrowUp' });
    fireEvent.keyDown(grip, { key: ' ' });
    expect(labels()).toEqual(['MP3 ≥320kbps', 'FLAC 24-bit', 'FLAC', 'OPUS ≥160kbps']);
    targetMenu('MP3 ≥320kbps', 'Remove');
    expect(labels()).toEqual(['FLAC 24-bit', 'FLAC', 'OPUS ≥160kbps']);
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saves('/api/quality-profile/custom/1/update').at(-1)!.body).toMatchObject({
      ranked_targets: [
        { format: 'flac', label: 'FLAC 24-bit', bit_depth: 24 },
        { format: 'flac', label: 'FLAC' },
        { format: 'opus', label: 'OPUS ≥160kbps', min_bitrate: 160 },
      ],
    });
  });

  it('every target gone shows the empty note and a single cutoff option', async () => {
    await loaded();
    for (const label of ['FLAC 24-bit', 'FLAC', 'MP3 ≥320kbps']) targetMenu(label, 'Remove');
    expect(document.getElementById('ranked-targets-list')!.textContent).toContain('No targets yet');
    expect([...select('quality-upgrade-cutoff').options].map((o) => o.textContent)).toEqual([
      'Top ranked target',
    ]);
  });
});

describe('presets', () => {
  it('on the default: the preset route, and a queued edit never lands after it', async () => {
    shell._settingsAutosaveOff = true;
    routes['POST /api/quality-profile/preset/audiophile'] = () => ({
      success: true,
      profile: {
        ...DEFAULT,
        preset: 'audiophile',
        ranked_targets: [{ format: 'flac', label: 'FLAC' }],
      },
    });
    await loaded();
    fireEvent.click(box('quality-fallback-enabled'));
    expect(store.hasPending()).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Audiophile' }));
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith("Switched to 'Audiophile'", 'success'),
    );
    expect(store.hasPending()).toBe(false);
    expect(screen.getByRole('button', { name: 'Audiophile' }).getAttribute('aria-pressed')).toBe(
      'true',
    );
    expect(labels()).toEqual(['FLAC']);
    await act(() => store.flush());
    expect(saves('/api/quality-profile/custom/1/update').map((c) => c.body)).toEqual([]);
  });

  it('on another profile: the preset is loaded here and saved to that profile', async () => {
    routes['GET /api/quality-profile/presets'] = () => ({
      success: true,
      presets: {
        space_saver: {
          version: 3,
          preset: 'space_saver',
          fallback_enabled: true,
          search_mode: 'priority',
          ranked_targets: [{ format: 'mp3', label: 'MP3' }],
        },
      },
    });
    await loaded();
    open('Strict');
    await waitFor(() => expect(box('acoustid-require-verified').checked).toBe(true));
    pick('quality-search-mode', 'best_quality');
    fireEvent.click(screen.getByRole('button', { name: 'Space Saver' }));
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith(
        "Applied 'Space Saver' to this profile",
        'success',
      ),
    );
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(calls.some((c) => c.url.startsWith('/api/quality-profile/preset/'))).toBe(false);
    expect(saves('/api/quality-profile/custom/2/update').at(-1)!.body).toMatchObject({
      preset: 'space_saver',
      search_mode: 'best_quality',
      ranked_targets: [{ format: 'mp3', label: 'MP3' }],
      acoustid_required: true,
    });
  });

  it('reset: refused on another profile, the reset route on the default', async () => {
    routes['POST /api/quality-profile/preset/balanced/reset'] = () => ({
      success: true,
      profile: DEFAULT,
    });
    await loaded();
    open('Strict');
    await waitFor(() => expect(box('acoustid-require-verified').checked).toBe(true));
    fireEvent.click(screen.getByText(/Reset to/));
    expect(shell.showToast).toHaveBeenCalledWith(
      'Reset to defaults only applies to the live default profile — this one has no factory preset to reset to',
      'info',
    );
    routes['GET /api/quality-profile/custom/1'] = () => ({ success: true, profile: DEFAULT });
    open('Balanced');
    await waitFor(() => expect(box('acoustid-require-verified').checked).toBe(false));
    fireEvent.click(screen.getByText(/Reset to/));
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith("Reset 'Balanced' to defaults", 'success'),
    );
  });
});

describe('the profile cards and their menu', () => {
  it('sets the default only after the confirm, and shows it', async () => {
    routes['POST /api/quality-profile/custom/2/apply'] = () => ({
      success: true,
      profile: { ...STRICT, is_default: true },
    });
    await loaded();
    (shell.showConfirmDialog as ReturnType<typeof vi.fn>).mockResolvedValueOnce(false);
    // the default's own entry says it is the default and does nothing
    fireEvent.click(screen.getByRole('button', { name: 'More for Balanced' }));
    expect(
      within(card('Balanced')).getByRole('menuitem', { name: 'The active default' }),
    ).toHaveProperty('disabled', true);
    menu('Strict', 'Set as default');
    await waitFor(() => expect(shell.showConfirmDialog).toHaveBeenCalledTimes(1));
    expect(calls.some((c) => c.url.endsWith('/apply'))).toBe(false);
    menu('Strict', 'Set as default');
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith(
        "Now using 'Strict' (all Quality-page settings updated)",
        'success',
      ),
    );
    expect(box('acoustid-require-verified').checked).toBe(true);
    expect(document.getElementById('qp-editing-banner')!.hidden).toBe(true);
  });

  it('renames in place: Enter saves, Escape cancels', async () => {
    routes['PUT /api/quality-profile/custom/2'] = (body) => ({
      success: true,
      profiles: [ROWS[0], { ...ROWS[1], name: (body as { name: string }).name }],
    });
    await loaded();
    menu('Strict', 'Rename');
    const input = screen.getByRole('textbox', { name: 'New name' }) as HTMLInputElement;
    expect(input.value).toBe('Strict');
    fireEvent.change(input, { target: { value: 'Stricter' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(screen.getByText('Stricter')).toBeTruthy());
    expect(calls.find((c) => c.method === 'PUT')!.body).toEqual({ name: 'Stricter' });
    menu('Balanced', 'Rename');
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'New name' }), { key: 'Escape' });
    expect(screen.queryByRole('textbox', { name: 'New name' })).toBeNull();
    expect(calls.filter((c) => c.method === 'PUT')).toHaveLength(1);
  });

  it('a new profile saves the page under its name and opens it', async () => {
    routes['POST /api/quality-profile/custom'] = () => ({
      success: true,
      id: 3,
      profiles: [...ROWS, { id: 3, name: 'Mine' }],
    });
    await loaded();
    fireEvent.click(screen.getByText('New profile'));
    const input = document.getElementById('qp-profile-new-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '  Mine ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(screen.getByText('New profile')).toBeTruthy());
    const sent = calls.find((c) => c.method === 'POST' && c.url === '/api/quality-profile/custom')!;
    expect(sent.body).toMatchObject({
      name: 'Mine',
      ranked_targets: DEFAULT.ranked_targets,
      deep_audio_verify: true,
    });
    expect(document.getElementById('qp-editing-banner')!.textContent).toContain('Mine');
    // an empty name just closes the box
    fireEvent.click(screen.getByText('New profile'));
    fireEvent.keyDown(document.getElementById('qp-profile-new-input')!, { key: 'Enter' });
    expect(document.getElementById('qp-profile-new-input')).toBeNull();
  });

  it('save now asks first, then writes the page to that row', async () => {
    await loaded();
    menu('Strict', 'Save this page to it now');
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith("Updated 'Strict'", 'success'),
    );
    expect(saves('/api/quality-profile/custom/2/update')[0].body).toMatchObject({
      ranked_targets: DEFAULT.ranked_targets,
    });
  });

  it('delete asks first; deleting the open profile goes back to the default', async () => {
    routes['DELETE /api/quality-profile/custom/2'] = () => ({ success: true, profiles: [ROWS[0]] });
    await loaded();
    open('Strict');
    await waitFor(() => expect(document.getElementById('qp-editing-banner')!.hidden).toBe(false));
    routes['GET /api/quality-profile/custom'] = () => ({ success: true, profiles: [ROWS[0]] });
    menu('Strict', 'Delete');
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith("Deleted 'Strict'", 'success'),
    );
    expect((shell.showConfirmDialog as ReturnType<typeof vi.fn>).mock.calls[0][0]).toMatchObject({
      destructive: true,
    });
    expect(document.getElementById('qp-editing-banner')!.hidden).toBe(true);
    // the last profile cannot be deleted
    await waitFor(() => expect(cards()).toHaveLength(1));
    fireEvent.click(screen.getByRole('button', { name: 'More for Balanced' }));
    expect(screen.queryByRole('menuitem', { name: 'Delete' })).toBeNull();
  });

  it('deleting a profile drops its edit not saved yet, so nothing is stuck retrying', async () => {
    shell._settingsAutosaveOff = true;
    routes['DELETE /api/quality-profile/custom/2'] = () => ({ success: true, profiles: [ROWS[0]] });
    await loaded();
    open('Strict');
    await waitFor(() => expect(box('acoustid-require-verified').checked).toBe(true));
    fireEvent.click(box('quality-fallback-enabled'));
    expect(store.hasPending()).toBe(true);
    routes['GET /api/quality-profile/custom'] = () => ({ success: true, profiles: [ROWS[0]] });
    menu('Strict', 'Delete');
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith("Deleted 'Strict'", 'success'),
    );
    expect(store.hasPending()).toBe(false);
    expect(await act(() => store.flush())).toBe(true);
    expect(saves('/api/quality-profile/custom/2/update')).toEqual([]);
    // and the next visit reloads the default again
    const before = calls.filter((c) => c.url === '/api/quality-profile').length;
    act(() => store.hydrate({}));
    await waitFor(() =>
      expect(calls.filter((c) => c.url === '/api/quality-profile').length).toBe(before + 1),
    );
  });

  it('Auto-Import toggles on and back off', async () => {
    routes['POST /api/auto-import/settings'] = () => ({ success: true });
    await loaded();
    menu('Strict', 'Use for Auto-Import');
    await waitFor(() => expect(card('Strict').textContent).toContain('Auto-Import'));
    expect(calls.at(-1)!.body).toEqual({ quality_profile_id: 2 });
    menu('Strict', 'Stop using for Auto-Import');
    await waitFor(() => expect(card('Strict').textContent).not.toContain('Auto-Import'));
    expect(calls.at(-1)!.body).toEqual({ quality_profile_id: null });
    expect(shell.showToast).toHaveBeenLastCalledWith(
      'Auto-Import now uses the default profile',
      'success',
    );
  });
});
