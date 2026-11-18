/**
 * #2166 follow-up 27, piece F's hide list: controls a member can't use are
 * absent, not disabled. the dashboard's system automation rows have no Run or
 * pause for a member; playlist sync off drops the sync rail; podcasts or
 * audiobooks off drop their watchlist tab; music videos off (or explicit music
 * hidden) drop the Videos parts. before the server's map lands, nothing hides.
 */

import { act, cleanup, render } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { musicVideosAllowed, notOff } from '@/platform/shell/my-permissions-core';
import { ArtistVideosSection } from '@/routes/artist-detail/-ui/artist-videos-section';
import { AutomationsCard } from '@/routes/dashboard/-ui/automations-card';

import { ADULT, clearPerms, permsOf, stubPerms } from './perms';

afterEach(() => {
  cleanup();
  clearPerms();
  vi.unstubAllGlobals();
});

describe('the helpers', () => {
  it('notOff hides only on an explicit no', () => {
    expect(notOff(null, 'music.playlist_sync')).toBe(true);
    expect(notOff(permsOf({}), 'music.playlist_sync')).toBe(true);
    expect(notOff(permsOf({ 'music.playlist_sync': false }), 'music.playlist_sync')).toBe(false);
    expect(
      notOff(permsOf({ 'music.playlist_sync': false }, { isAdmin: true }), 'music.playlist_sync'),
    ).toBe(true);
  });

  it('music videos need the part on and explicit music shown', () => {
    expect(musicVideosAllowed(permsOf(ADULT))).toBe(true);
    expect(musicVideosAllowed(permsOf({ ...ADULT, 'music.videos': false }))).toBe(false);
    expect(musicVideosAllowed(permsOf({ ...ADULT, 'music.hide_explicit': true }))).toBe(false);
    expect(
      musicVideosAllowed(permsOf({ ...ADULT, 'music.hide_explicit': true }, { isAdmin: true })),
    ).toBe(true);
    expect(musicVideosAllowed(null)).toBe(true);
  });
});

describe('the dashboard automations card', () => {
  const ROWS = [
    {
      id: 1,
      name: 'Backup',
      enabled: 1,
      trigger_type: 'schedule',
      trigger_config: { interval: 1, unit: 'hours' },
      action_type: 'clean_downloads',
      next_run: null,
      run_count: 3,
      is_system: 1,
    },
    {
      id: 2,
      name: 'My pull',
      enabled: 1,
      trigger_type: 'schedule',
      trigger_config: { interval: 1, unit: 'hours' },
      action_type: 'refresh_mirrored',
      next_run: null,
      run_count: 1,
      is_system: 0,
    },
  ];
  async function mount() {
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) =>
        Promise.resolve({
          ok: true,
          json: async () => (String(url).endsWith('/progress') ? {} : ROWS),
        }),
      ),
    );
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(<AutomationsCard />);
    });
    await vi.waitFor(() =>
      expect(view.container.querySelectorAll('.dash-autom-row')).toHaveLength(2),
    );
    return view;
  }
  const controls = (view: ReturnType<typeof render>, name: string) => {
    const row = [...view.container.querySelectorAll('.dash-autom-row')].find((r) =>
      r.textContent?.includes(name),
    )!;
    return row.querySelectorAll('.dash-autom-run').length;
  };

  it("a member's system rows have no Run or pause, their own keep both", async () => {
    stubPerms(ADULT);
    const view = await mount();
    expect(controls(view, 'Backup')).toBe(0);
    expect(controls(view, 'My pull')).toBe(2);
  });

  it('an admin keeps every control', async () => {
    stubPerms(ADULT, { isAdmin: true });
    const view = await mount();
    expect(controls(view, 'Backup')).toBe(2);
  });
});

describe('music videos on an artist page', () => {
  it('a hide-explicit profile gets no Videos section', () => {
    stubPerms({ ...ADULT, 'music.hide_explicit': true });
    const view = render(<ArtistVideosSection artistName="Nirvana" />);
    expect(view.container.innerHTML).toBe('');
  });

  it('music videos off: no section', () => {
    stubPerms({ ...ADULT, 'music.videos': false });
    const view = render(<ArtistVideosSection artistName="Nirvana" />);
    expect(view.container.innerHTML).toBe('');
  });
});

describe('the pages that read these rows', () => {
  // the watchlist page and the dashboard need a router and a dozen queries to
  // mount; what they read is pinned here, the helpers above are the behaviour
  const read = (file: string) => readFileSync(resolve(process.cwd(), file), 'utf8');

  it('the sync rail follows playlist sync', () => {
    const src = read('src/routes/dashboard/-ui/dashboard-page.tsx');
    expect(src).toContain("usePermNotOff('music.playlist_sync')");
    expect(src).toMatch(/const playlistsOn = useFeatureOn\('playlists'\) && playlistSync;/);
  });

  it('the watchlist tabs follow podcasts and audiobooks access, even by link', () => {
    const src = read('src/routes/watchlist/-ui/watchlist-page.tsx');
    expect(src).toContain("const isPodcastsTab = search.tab === 'podcasts' && podcastsAllowed;");
    expect(src).toContain("const podcastsOn = useFeatureOn('podcasts') && podcastsAllowed;");
    expect(src).toContain(
      "const isAudiobooksTab = search.tab === 'audiobooks' && audiobooksAllowed;",
    );
  });

  it("search's Videos tab follows music videos", () => {
    const src = read('src/routes/search/-ui/source-row.tsx');
    expect(src).toContain('videosAllowed && (videosOn || mode ===');
  });
});
