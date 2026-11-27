/**
 * "Upgrade whole albums" is a quality profile setting (hrk_hrk on Discord):
 * off by default, shown only when the profile upgrades at all, loaded with
 * the profile on screen and saved to that profile. When on, the recent album
 * upgrades show under it.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsStore } from '@/features/settings/settings-store';
import { EMPTY_LADDER, collectLadder, ladderFrom } from '@/features/settings/tabs/quality';
import { MusicQualityTab } from '@/features/settings/tabs/quality-tab';

type Profile = Record<string, unknown>;

const rows = [
  { id: 1, name: 'Default', is_default: true, ranked_targets: '[]' },
  { id: 7, name: 'Albums', is_default: false, ranked_targets: '[]' },
];
const profiles: Record<number, Profile> = {
  1: { id: 1, is_default: true, ranked_targets: [], upgrade_policy: 'none' },
  7: {
    id: 7,
    is_default: false,
    ranked_targets: [],
    upgrade_policy: 'until_cutoff',
    upgrade_whole_albums: true,
  },
};
const attempts = [
  { id: 2, artist_name: 'Band', album_title: 'Record', outcome: 'upgraded', source: 'qobuz' },
  {
    id: 1,
    artist_name: 'Band',
    album_title: 'Other',
    outcome: 'no_complete_release',
    reason: 'soulseek: it has 11 tracks, your album has 12',
  },
];

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
      else if (url.startsWith('/api/quality-profile/album-upgrades'))
        data = { success: true, attempts };
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
});

const toggle = () => document.getElementById('quality-whole-albums') as HTMLInputElement;
const group = () => document.getElementById('quality-whole-albums-group') as HTMLElement;
const cards = () => [
  ...document.querySelectorAll<HTMLElement>('#qp-profile-list [data-profile-id]'),
];

async function open(id?: number) {
  render(<MusicQualityTab store={store} opened={1} active />);
  act(() => store.hydrate({}));
  await waitFor(() => expect(cards()).toHaveLength(2));
  if (id) {
    fireEvent.click(document.querySelector(`[data-profile-id="${id}"] button`)!);
    await waitFor(() => expect(toggle().checked).toBe(profiles[id].upgrade_whole_albums === true));
  }
}

it('is off by default and kept by a profile that never named it', () => {
  expect(EMPTY_LADDER.wholeAlbums).toBe(false);
  expect(collectLadder(EMPTY_LADDER).upgrade_whole_albums).toBe(false);
  const on = ladderFrom({ upgrade_whole_albums: true }, EMPTY_LADDER);
  expect(on.wholeAlbums).toBe(true);
  expect(ladderFrom({ upgrade_whole_albums: false }, on).wholeAlbums).toBe(false);
});

it('hides behind a profile that never upgrades', async () => {
  await open();
  expect(group().hidden).toBe(true);
});

it('loads and saves to the profile on screen, with the recent album upgrades', async () => {
  await open(7);
  expect(group().hidden).toBe(false);
  expect(toggle().checked).toBe(true);
  await waitFor(() =>
    expect(document.querySelector('[data-testid="album-upgrade-activity"]')?.textContent).toContain(
      'Band - Record, upgraded from qobuz',
    ),
  );
  expect(document.querySelector('[data-testid="album-upgrade-activity"]')?.textContent).toContain(
    'left as it is: soulseek: it has 11 tracks',
  );
  fireEvent.click(toggle());
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].url).toBe('/api/quality-profile/custom/7/update');
  expect(posts[0].profile.upgrade_whole_albums).toBe(false);
});
