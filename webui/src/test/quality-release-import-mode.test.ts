import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

const source = readFileSync(resolve(process.cwd(), 'static/settings.js'), 'utf8');
const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');

type Profile = Record<string, unknown>;
type UI = {
  populateQualityProfileUI: (profile: Profile) => void;
  collectFullQualityBundleFromUI: () => Profile;
  saveQualityProfile: (options?: { targetProfileId: number }) => Promise<boolean>;
  _qpHandleProfileControlChange: (event: { target: HTMLElement }) => boolean;
  editProfile: (id: number) => void;
};
let ui: UI;
let posts: { url: string; profile: Profile }[];

beforeEach(() => {
  const parsed = new DOMParser().parseFromString(html, 'text/html');
  document.body.replaceChildren(parsed.getElementById('settings-page')!);
  posts = [];
  Object.assign(window, { _suppressSettingsAutoSave: false, _settingsLoadFailed: false });
  const fetcher = async (url: string, options: RequestInit) => {
    if (typeof options.body !== 'string') throw new Error('Expected a JSON request body');
    posts.push({ url, profile: JSON.parse(options.body) });
    return { json: async () => ({ success: true }) };
  };
  const functions = [
    'populateQualityProfileUI',
    'collectQualityProfileFromUI',
    'collectFullQualityBundleFromUI',
    'saveQualityProfile',
    'debouncedSaveQualityProfile',
  ];
  // Execute the actual DOM load/collect/save functions; rendering the target
  // ladder and profile sidebar is outside this import-policy contract.
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  ui = new Function(
    'document',
    'fetch',
    `
    let currentRankedTargets = [];
    let currentQualityProfile = null;
    let _qpEditingProfileId = null;
    let qualityProfileAutoSaveTimer = null;
    const _qpDefaultProfileId = () => 1;
    const renderRankedTargets = () => {};
    const renderUpgradeCutoffOptions = () => {};
    const onSearchModeChange = () => {};
    const onUpgradePolicyChange = () => {};
    ${source.slice(source.indexOf('const _QP_BUNDLE_CONTROL_IDS'), source.indexOf('function debouncedAutoSaveSettings'))}
    ${functions.map((name) => extractFunction(name, source)).join('\n')}
    return { populateQualityProfileUI, collectFullQualityBundleFromUI, saveQualityProfile,
      _qpHandleProfileControlChange, editProfile: id => { _qpEditingProfileId = id; } };
  `,
  )(document, fetcher) as UI;
});

afterEach(() => {
  vi.useRealTimers();
});

it('loads and saves album opt-in to the selected named profile', async () => {
  ui.populateQualityProfileUI({ release_import_mode: 'complete_album', ranked_targets: [] });
  const select = document.getElementById('quality-release-import-mode') as HTMLSelectElement;
  expect(select?.value).toBe('complete_album');
  expect(ui.collectFullQualityBundleFromUI().release_import_mode).toBe('complete_album');
  expect(await ui.saveQualityProfile({ targetProfileId: 29 })).toBe(true);
  expect(posts[0].url).toBe('/api/quality-profile/custom/29/update');
  expect(posts[0].profile.release_import_mode).toBe('complete_album');
});

it('loads legacy and malformed policies as requested tracks', () => {
  ui.populateQualityProfileUI({ release_import_mode: 'complete_album', ranked_targets: [] });
  for (const profile of [{}, { release_import_mode: 'anything' }, { release_import_mode: null }]) {
    ui.populateQualityProfileUI(profile);
    expect(ui.collectFullQualityBundleFromUI().release_import_mode).toBe('requested_tracks');
  }
});

it('saves an explicit opt-out after viewing an opted-in profile', async () => {
  ui.populateQualityProfileUI({ release_import_mode: 'complete_album' });
  const select = document.getElementById('quality-release-import-mode') as HTMLSelectElement;
  expect(select).not.toBeNull();
  select.value = 'requested_tracks';
  expect(await ui.saveQualityProfile({ targetProfileId: 29 })).toBe(true);
  expect(posts[0].profile.release_import_mode).toBe('requested_tracks');
});

it('offers both policies within Quality and explains the normal import pipeline', () => {
  const select = document.getElementById('quality-release-import-mode') as HTMLSelectElement;
  expect(select).not.toBeNull();
  expect(select.closest('[data-stg]')?.getAttribute('data-stg')).toBe('quality');
  expect([...select.options].map((option) => option.value)).toEqual([
    'requested_tracks',
    'complete_album',
  ]);
  const help = select.closest('.form-group')?.textContent || '';
  expect(help).toContain('this quality profile');
  expect(help).toContain('normal download and import pipeline');
  expect(help).toContain('requested track');
});

it('autosaves the album policy to its original profile when the user switches profiles', async () => {
  vi.useFakeTimers();
  ui.editProfile(29);
  ui.populateQualityProfileUI({ release_import_mode: 'complete_album' });
  const select = document.getElementById('quality-release-import-mode') as HTMLSelectElement;
  expect(ui._qpHandleProfileControlChange({ target: select })).toBe(true);
  ui.editProfile(42);
  ui.populateQualityProfileUI({ release_import_mode: 'requested_tracks' });
  await vi.runAllTimersAsync();
  expect(posts).toHaveLength(1);
  expect(posts[0].url).toBe('/api/quality-profile/custom/29/update');
  expect(posts[0].profile.release_import_mode).toBe('complete_album');
});
