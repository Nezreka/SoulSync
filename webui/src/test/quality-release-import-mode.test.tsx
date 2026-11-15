/**
 * The Album Releases policy (release_import_mode) is a quality profile
 * setting: it loads with the profile on screen and saves to that profile.
 * The Quality tab is React now (SETTINGS_PORT.md step 6), so this runs the
 * real tab against stubbed profile routes.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsStore } from '@/features/settings/settings-store';
import { EMPTY_LADDER, ladderFrom } from '@/features/settings/tabs/quality';
import { QualityTab } from '@/features/settings/tabs/quality-tab';

type Profile = Record<string, unknown>;
const shell = window as unknown as Record<string, unknown>;

const rows = [
  { id: 1, name: 'Default', is_default: true, ranked_targets: '[]' },
  { id: 29, name: 'Albums', is_default: false, ranked_targets: '[]' },
  { id: 42, name: 'Tracks', is_default: false, ranked_targets: '[]' },
];
const profiles: Record<number, Profile> = {
  1: { id: 1, is_default: true, ranked_targets: [], release_import_mode: 'requested_tracks' },
  29: { id: 29, is_default: false, ranked_targets: [], release_import_mode: 'album_tracks' },
  42: { id: 42, is_default: false, ranked_targets: [], release_import_mode: 'requested_tracks' },
};

let store: SettingsStore;
let posts: { url: string; profile: Profile }[];

beforeEach(() => {
  posts = [];
  store = createSettingsStore({ post: () => Promise.resolve({ success: true }), delay: 0 });
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      let data: unknown = { success: false };
      if (init?.method === 'POST') {
        posts.push({ url, profile: JSON.parse(init.body as string) as Profile });
        data = { success: true };
      } else if (url === '/api/quality-profile') data = { success: true, profile: profiles[1] };
      else if (url === '/api/quality-profile/custom') data = { success: true, profiles: rows };
      else {
        const id = Number(url.split('/').pop());
        if (profiles[id]) data = { success: true, profile: profiles[id] };
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data) } as Response);
    }),
  );
});

afterEach(async () => {
  await act(() => store.flush());
  vi.unstubAllGlobals();
  delete shell._settingsAutosaveOff;
});

const mode = () => document.getElementById('quality-release-import-mode') as HTMLSelectElement;

async function open(id?: number) {
  render(<QualityTab store={store} opened={1} active />);
  act(() => store.hydrate({}));
  await waitFor(() => expect(document.querySelectorAll('.qp-profile-row')).toHaveLength(3));
  if (id) await view(id);
}

async function view(id: number) {
  const name = rows.find((r) => r.id === id)!.name;
  fireEvent.click(screen.getByText(name));
  const want = profiles[id].release_import_mode as string;
  await waitFor(() => expect(mode().value).toBe(want));
}

it('loads and saves album opt-in to the selected named profile', async () => {
  await open(29);
  expect(mode().value).toBe('album_tracks');
  // any edit saves the whole profile, the album policy with it
  fireEvent.click(document.getElementById('quality-fallback-enabled')!);
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].url).toBe('/api/quality-profile/custom/29/update');
  expect(posts[0].profile.release_import_mode).toBe('album_tracks');
});

it('loads legacy and malformed policies as requested tracks', () => {
  const opted = ladderFrom({ release_import_mode: 'album_tracks' }, EMPTY_LADDER);
  for (const profile of [{}, { release_import_mode: 'anything' }, { release_import_mode: null }]) {
    expect(ladderFrom(profile, opted).releaseImportMode).toBe('requested_tracks');
  }
});

it('saves an explicit opt-out after viewing an opted-in profile', async () => {
  await open(29);
  fireEvent.change(mode(), { target: { value: 'requested_tracks' } });
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].url).toBe('/api/quality-profile/custom/29/update');
  expect(posts[0].profile.release_import_mode).toBe('requested_tracks');
});

it('offers both policies in a profile card of Quality and explains the normal import pipeline', async () => {
  await open();
  const select = mode();
  expect(select.closest('[data-stg]')?.getAttribute('data-stg')).toBe('quality');
  const body = select.closest('.settings-section-body');
  const header = body?.previousElementSibling;
  expect(header?.querySelector('h3')?.textContent).toBe('Album Releases');
  expect(header?.querySelector('.qp-governed-badge')).not.toBeNull();
  expect([...select.options].map((option) => option.value)).toEqual([
    'requested_tracks',
    'album_tracks',
  ]);
  const help = select.closest('.form-group')?.textContent || '';
  expect(help).toContain('this quality');
  expect(help).toContain('normal import pipeline');
  expect(help).toContain('quarantined on its own');
});

it('autosaves the album policy to its original profile when the user switches profiles', async () => {
  shell._settingsAutosaveOff = true;
  await open(29);
  fireEvent.change(mode(), { target: { value: 'album_tracks' } });
  fireEvent.click(document.getElementById('quality-fallback-enabled')!);
  await view(42);
  expect(posts).toHaveLength(0);
  await act(() => store.flush());
  expect(posts).toHaveLength(1);
  expect(posts[0].url).toBe('/api/quality-profile/custom/29/update');
  expect(posts[0].profile.release_import_mode).toBe('album_tracks');
});
