/**
 * a show's season as one React list: the bar, the rows, an opened episode,
 * and what can be done to a season, an episode and its files.
 *
 * most of these cases were pinned against the strings video-detail.js built
 * (video-detail-season-actions, -row-grid, -episode-panel, -one-page and
 * -elevation). the list moved here, so the cases did too.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { VideoDetailBridge } from './library-tab';
import type {
  EpisodeListProps,
  LibraryEpisode,
  LibraryFile,
  PageEpisode,
  PageSeason,
  ShowLibrary,
} from './types';

import { EpisodeList } from './episode-list';
import { invalidate, lastChange, picked, showData } from './library-store';

interface Call {
  url: string;
  method: string;
  body: unknown;
}

let calls: Call[] = [];
let routes: Record<string, (body: unknown, url: string) => unknown> = {};
let bridge: VideoDetailBridge & Record<string, ReturnType<typeof vi.fn>>;

function file(over: Partial<LibraryFile> = {}): LibraryFile {
  return {
    id: 31,
    path: '/mnt/tv/Breaking Bad/Season 01/Breaking Bad - S01E01.mkv',
    filename: 'Breaking Bad - S01E01.mkv',
    root: '/mnt/tv',
    size_bytes: 5.7 * 1073741824,
    resolution: '1080',
    resolution_label: '1080p',
    quality: 'WEBDL-1080p',
    video_codec: 'hevc',
    audio_codec: 'eac3',
    audio_channels: 6,
    dynamic_range: 'Dolby Vision',
    atmos: true,
    release_source: null,
    runtime_seconds: 3480,
    added_at: '2026-10-20 09:05:54',
    aspect: '16:9',
    playable: { verdict: 'yes', reasons: [] },
    reachable: true,
    best: true,
    ...over,
  };
}

function libEpisode(over: Partial<LibraryEpisode> = {}): LibraryEpisode {
  return {
    id: 11,
    episode_number: 1,
    title: 'Pilot',
    air_date: '2008-01-20',
    has_file: true,
    monitored: true,
    watched: false,
    aired: true,
    files: [file()],
    ...over,
  };
}

function library(episodes: LibraryEpisode[], over: Partial<ShowLibrary> = {}): ShowLibrary {
  return {
    kind: 'show',
    id: 445,
    title: 'Breaking Bad',
    year: 2008,
    identity: {
      tmdb_id: 1396,
      imdb_id: null,
      tvdb_id: null,
      match_status: 'matched',
      fixed: false,
      manual_link: null,
      server: 'plex',
    },
    summary: {
      owned: 1,
      total: 2,
      missing: 1,
      upcoming: 0,
      specials_owned: 0,
      files: 1,
      size_bytes: 0,
      res_mix: [],
      roots: [],
      folders: [],
      watched_episodes: 0,
      series_type: null,
      monitored: true,
    },
    quality: { profile_id: null, profile_name: 'Default', cutoff_label: '1080p' },
    seasons: [
      {
        id: 90,
        season_number: 1,
        title: 'Season 1',
        owned: 1,
        total: 2,
        missing: 1,
        upcoming: 0,
        size_bytes: 33.2 * 1073741824,
        monitored: true,
        below_cutoff: 0,
        res_mix: [
          { label: '1080p', count: 10 },
          { label: '720p', count: 1 },
        ],
        episodes,
      },
    ],
    detail_season: null,
    recycle: true,
    can_manage: true,
    ...over,
  };
}

const OWNED: PageEpisode = {
  id: 11,
  episode_number: 1,
  title: 'Pilot',
  overview: 'A chemistry teacher makes a decision.',
  air_date: '2008-01-20',
  runtime_minutes: 58,
  owned: true,
  resolution: '1080',
};
const MISSING: PageEpisode = {
  id: 12,
  episode_number: 2,
  title: "Cat's in the Bag",
  air_date: '2008-01-27',
  owned: false,
};

function season(episodes: PageEpisode[] = [OWNED, MISSING], monitored = 2): PageSeason {
  return { id: 90, season_number: 1, episodes, episode_monitored: monitored };
}

function props(over: Partial<EpisodeListProps> = {}): EpisodeListProps {
  return {
    show: { id: 445, title: 'Breaking Bad', year: 2008, tmdbId: 1396, source: 'library' },
    season: season(),
    missingOnly: false,
    nextUp: null,
    canDownload: true,
    canGrab: true,
    downloads: {},
    finished: {},
    ...over,
  };
}

function withLibrary(episodes: LibraryEpisode[] = [libEpisode()], over: Partial<ShowLibrary> = {}) {
  act(() => showData.set(library(episodes, over)));
}

async function menuOf(label: string) {
  fireEvent.click(screen.getByRole('button', { name: label }));
  return screen.findByRole('menu');
}

const items = (menu: HTMLElement) =>
  within(menu)
    .getAllByRole('menuitem')
    .map((item) => item.textContent);

beforeEach(() => {
  calls = [];
  routes = {};
  invalidate('show', 445);
  act(() => {
    showData.set(null);
    picked.set([]);
    lastChange.set(null);
  });
  bridge = {
    manualSearch: vi.fn(),
    grabSeason: vi.fn(async () => ({})),
    wishlistSeason: vi.fn(),
    clearSeasonFailures: vi.fn(),
    subtitleSearch: vi.fn(),
    reloadPage: vi.fn(),
    grabEpisode: vi.fn(),
    wishEpisode: vi.fn(),
    setWatched: vi.fn(),
    setSeasonMonitored: vi.fn(async () => ({})),
  } as never;
  window.SoulSyncVideoDetail = bridge;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(init.body as string) : undefined;
      calls.push({ url: String(url), method, body });
      const key = Object.keys(routes).find((k) => String(url).includes(k));
      const out = key ? routes[key](body, String(url)) : { error: 'no route' };
      return new Response(JSON.stringify(out), {
        status: (out as { error?: string }).error ? 404 : 200,
      });
    }),
  );
  window.showToast = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.showToast;
  delete window.SoulSyncVideoDetail;
  delete window.VideoRequests;
  delete window.VideoIssues;
});

/* ── the season bar ───────────────────────────────────────────────────────── */

describe('the season bar', () => {
  it('offers management on a season with nothing missing', async () => {
    render(
      <EpisodeList
        {...props({ season: season([OWNED, { ...OWNED, id: 13, episode_number: 3 }]) })}
      />,
    );
    expect(screen.getByText('All 2 here')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Grab season/ })).toBeNull();
    expect(screen.getByRole('switch', { name: 'Monitored' })).toBeTruthy();
    expect(items(await menuOf('More for this season'))).toEqual([
      'Mark season watched',
      'Clear failures',
    ]);
  });

  it('adds the acquisition actions when episodes are missing, with only Grab on the bar', async () => {
    const { container } = render(<EpisodeList {...props()} />);
    expect(screen.getByText('1 missing')).toBeTruthy();
    const onBar = Array.from(
      container.querySelectorAll('.vd-season-actions > button .discog-btn-text'),
    ).map((b) => b.textContent);
    expect(onBar).toEqual(['Grab season']);
    expect(items(await menuOf('More for this season'))).toEqual([
      'Mark season watched',
      'Manual search',
      'Wishlist season',
      'Clear failures',
    ]);
  });

  it('grabs the season and holds the button while the search is out', async () => {
    let done: (value: unknown) => void = () => {};
    bridge.grabSeason = vi.fn(() => new Promise((r) => (done = r)));
    render(<EpisodeList {...props()} />);
    fireEvent.click(screen.getByRole('button', { name: /Grab season/ }));
    expect(bridge.grabSeason).toHaveBeenCalledWith(1);
    const busy = await screen.findByRole('button', { name: /Searching/ });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    await act(async () => done({}));
    expect(screen.getByRole('button', { name: /Grab season/ })).toBeTruthy();
  });

  it('states which way the monitor switch will flip, and flips it', async () => {
    const { rerender } = render(<EpisodeList {...props()} />);
    const on = screen.getByRole('switch', { name: 'Monitored' });
    expect(on.getAttribute('aria-checked')).toBe('true');
    expect(on.getAttribute('title')).toContain('Click to stop hunting this season');
    fireEvent.click(on);
    await waitFor(() => expect(bridge.setSeasonMonitored).toHaveBeenCalledWith(1, false));
    rerender(<EpisodeList {...props({ season: season([OWNED, MISSING], 0) })} />);
    const off = screen.getByRole('switch', { name: 'Monitored' });
    expect(off.getAttribute('aria-checked')).toBe('false');
    expect(off.getAttribute('title')).toContain('Click to hunt its missing episodes again');
  });

  it('reads the hunt from the library once it is in, one episode at a time', () => {
    render(<EpisodeList {...props({ season: season([OWNED, MISSING], 2) })} />);
    // every episode of the season was unmonitored one by one
    withLibrary([libEpisode({ monitored: false })]);
    expect(screen.getByRole('switch', { name: 'Monitored' }).getAttribute('aria-checked')).toBe(
      'false',
    );
  });

  it('hides clear-failures when there is no tmdb id to retry against', async () => {
    render(
      <EpisodeList
        {...props({ show: { id: 445, title: 'Breaking Bad', tmdbId: null, source: 'library' } })}
      />,
    );
    expect(items(await menuOf('More for this season'))).not.toContain('Clear failures');
  });

  it('offers no management on a preview, which has no library row', async () => {
    render(
      <EpisodeList
        {...props({ show: { id: 1396, title: 'Breaking Bad', tmdbId: 1396, source: 'tmdb' } })}
      />,
    );
    expect(screen.queryByRole('switch')).toBeNull();
    expect(items(await menuOf('More for this season'))).toEqual([
      'Manual search',
      'Wishlist season',
    ]);
  });

  it('drops the grab buttons when the grab module never loaded, or the profile can’t download', async () => {
    const { unmount } = render(<EpisodeList {...props({ canGrab: false })} />);
    expect(screen.queryByRole('button', { name: /Grab season/ })).toBeNull();
    expect(items(await menuOf('More for this season'))).toEqual([
      'Mark season watched',
      'Clear failures',
    ]);
    unmount();
    render(<EpisodeList {...props({ canDownload: false })} />);
    expect(screen.queryByRole('button', { name: /Grab season/ })).toBeNull();
  });

  it('a preview with nothing missing has no bar at all', () => {
    const { container } = render(
      <EpisodeList
        {...props({
          show: { id: 1396, title: 'Breaking Bad', tmdbId: 1396, source: 'tmdb' },
          season: season([OWNED]),
        })}
      />,
    );
    expect(container.querySelector('.vd-season-actions')).toBeNull();
  });

  it('says the season’s size and its quality mix once the library is in', () => {
    const { container } = render(<EpisodeList {...props()} />);
    expect(container.querySelector('.vd-season-lib')).toBeNull();
    withLibrary();
    expect(container.querySelector('.vd-season-lib')?.textContent).toBe('33.2 GB1080p 10720p 1');
  });

  it('marks the season watched through the page', async () => {
    render(<EpisodeList {...props()} />);
    fireEvent.click(within(await menuOf('More for this season')).getByText('Mark season watched'));
    expect(bridge.setWatched).toHaveBeenCalledWith('season', 90, true);
  });
});

/* ── a row ────────────────────────────────────────────────────────────────── */

describe('an episode row', () => {
  it('emits the same six children owned or missing, so the actions line up', () => {
    const { container } = render(<EpisodeList {...props()} />);
    const rows = container.querySelectorAll('.vd-ep');
    expect(rows.length).toBe(2);
    expect(rows[0].children.length).toBe(6);
    expect(rows[1].children.length).toBe(6);
    expect(rows[0].querySelector('.vd-ep-badge--none')).not.toBeNull();
    expect(rows[1].querySelector('.vd-ep-badge')?.textContent).toBe('Missing');
  });

  it('says upcoming for an episode that has not aired', () => {
    const { container } = render(
      <EpisodeList {...props({ season: season([{ ...MISSING, air_date: '2999-01-01' }]) })} />,
    );
    expect(container.querySelector('.vd-ep-badge--soon')?.textContent).toBe('Upcoming');
    // nothing to grab yet
    expect(screen.queryByRole('button', { name: 'Get episode' })).toBeNull();
  });

  it('is the row it always was until the library data arrives', () => {
    const { container } = render(<EpisodeList {...props()} />);
    const row = container.querySelector('.vd-ep') as HTMLElement;
    expect(row.querySelector('.vd-ep-file')).toBeNull();
    expect(row.querySelector('.vd-ep-pick')).toBeNull();
    expect(row.querySelector('.vd-ep-rt')?.textContent).toBe('58m · 2008-01-20 · 1080p');
  });

  it('says the file in one line once the library is in', () => {
    const { container } = render(<EpisodeList {...props()} />);
    withLibrary();
    const row = container.querySelector('.vd-ep') as HTMLElement;
    expect(row.querySelector('.vd-ep-file-res')?.textContent).toBe('1080p');
    expect(row.querySelector('.vd-ep-file-res')?.getAttribute('data-tone')).toBe('hd');
    expect(row.querySelector('.vd-ep-file-facts')?.textContent).toContain('WEBDL-1080p');
    expect(row.querySelector('.vd-ep-file-facts')?.textContent).toContain('5.7 GB');
    // the file line says the resolution, so the meta line stops repeating it
    expect(row.querySelector('.vd-ep-rt')?.textContent).toBe('58m · 2008-01-20');
    expect(row.querySelector('.vd-ep-file-chip')).toBeNull();
  });

  it('adds a chip only when it says something', () => {
    const { container } = render(<EpisodeList {...props()} />);
    withLibrary([
      libEpisode({
        files: [file({ below_cutoff: true }), file({ id: 32, best: false, size_bytes: 1 })],
      }),
    ]);
    const chips = Array.from(container.querySelectorAll('.vd-ep .vd-ep-file-chip')).map(
      (c) => c.textContent,
    );
    expect(chips).toEqual(['Below your target', '2 copies']);
  });

  it('leads with the best copy, whatever order the files came in', () => {
    const { container } = render(<EpisodeList {...props()} />);
    withLibrary([
      libEpisode({
        files: [
          file({ id: 32, best: false, resolution_label: '720p', quality: 'HDTV-720p' }),
          file(),
        ],
      }),
    ]);
    expect(container.querySelector('.vd-ep .vd-ep-file-res')?.textContent).toBe('1080p');
  });

  it('offers the tick box to an admin only', () => {
    const { container } = render(<EpisodeList {...props()} />);
    withLibrary([libEpisode()], { can_manage: false });
    expect(container.querySelector('.vd-ep-pick')).toBeNull();
    withLibrary([libEpisode()]);
    expect(container.querySelectorAll('.vd-ep-pick').length).toBe(1);
  });

  it('a missing episode has no file line and no box', () => {
    const { container } = render(<EpisodeList {...props({ season: season([MISSING]) })} />);
    withLibrary([libEpisode({ id: 12, episode_number: 2, has_file: false, files: [] })]);
    expect(container.querySelector('.vd-ep-file')).toBeNull();
    expect(container.querySelector('.vd-ep-pick')).toBeNull();
  });

  it('a preview from tmdb never borrows a library episode with the same id', () => {
    const { container } = render(
      <EpisodeList
        {...props({ show: { id: 445, title: 'Breaking Bad', tmdbId: 1396, source: 'tmdb' } })}
      />,
    );
    withLibrary();
    expect(container.querySelector('.vd-ep-file')).toBeNull();
    expect(container.querySelector('.vd-ep-pick')).toBeNull();
  });

  it('filters to the missing ones, and says so when there are none', () => {
    const { container, rerender } = render(<EpisodeList {...props({ missingOnly: true })} />);
    expect(container.querySelectorAll('.vd-ep').length).toBe(1);
    rerender(<EpisodeList {...props({ missingOnly: true, season: season([OWNED]) })} />);
    expect(screen.getByText(/No missing episodes here/)).toBeTruthy();
  });

  it('wears next up, resume and new', () => {
    const { container } = render(
      <EpisodeList
        {...props({
          nextUp: { season_number: 1, episode_number: 1 },
          season: season([
            {
              ...OWNED,
              view_offset_ms: 29 * 60000,
              added_at: new Date(Date.now() - 86400000).toISOString(),
            },
          ]),
        })}
      />,
    );
    expect(container.querySelector('.vd-ep--next')).not.toBeNull();
    expect(container.querySelector('.vd-ep-next-chip')?.textContent).toBe('Resume');
    expect(container.querySelector('.vd-ep-new-chip')).not.toBeNull();
    expect((container.querySelector('.vd-ep-prog-fill') as HTMLElement).style.width).toBe('50%');
  });
});

/* ── a row's own actions ──────────────────────────────────────────────────── */

describe('an episode’s menu', () => {
  it('keeps every acquisition action on an owned episode, and calls the page for each', async () => {
    render(<EpisodeList {...props({ season: season([OWNED]) })} />);
    const menu = await menuOf('More for episode 1');
    expect(items(menu)).toEqual([
      'Mark watched',
      'Download again (upgrade)',
      'Manual search',
      'Wishlist for an upgrade',
      'Search subtitles',
    ]);
    fireEvent.click(within(menu).getByText('Manual search'));
    expect(bridge.manualSearch).toHaveBeenCalledWith('episode', 1, 1);
    fireEvent.click(within(await menuOf('More for episode 1')).getByText('Search subtitles'));
    expect(bridge.subtitleSearch).toHaveBeenCalledWith('episode', 11, 'Pilot', 'S01E01');
    fireEvent.click(within(await menuOf('More for episode 1')).getByText('Mark watched'));
    expect(bridge.setWatched).toHaveBeenCalledWith('episode', 11, true);
    fireEvent.click(
      within(await menuOf('More for episode 1')).getByText('Download again (upgrade)'),
    );
    expect(bridge.grabEpisode).toHaveBeenCalledWith(1);
  });

  it('a missing episode grabs from the row and wishlists from the menu', async () => {
    render(<EpisodeList {...props({ season: season([MISSING]) })} />);
    fireEvent.click(screen.getByRole('button', { name: 'Get episode' }));
    expect(bridge.grabEpisode).toHaveBeenCalledWith(2);
    const menu = await menuOf('More for episode 2');
    expect(items(menu)).toEqual(['Manual search', 'Add to wishlist']);
    fireEvent.click(within(menu).getByText('Add to wishlist'));
    expect(bridge.wishEpisode).toHaveBeenCalledWith(2);
  });

  it('offers watched on a library episode you have, and nowhere else', async () => {
    // a preview has no library row to mark
    const { unmount } = render(
      <EpisodeList
        {...props({
          season: season([OWNED]),
          show: { id: 1396, title: 'Breaking Bad', tmdbId: 1396, source: 'tmdb' },
        })}
      />,
    );
    expect(items(await menuOf('More for episode 1'))).not.toContain('Mark watched');
    unmount();
    render(<EpisodeList {...props({ season: season([{ ...OWNED, watched: true }]) })} />);
    expect(items(await menuOf('More for episode 1'))).toContain('Mark unwatched');
  });

  it('a view-only profile still gets the watched toggle, and asks for what is missing', async () => {
    window.VideoRequests = {
      cardButton: (o) => `<button type="button" data-vreq-card>Request E${o.episode}</button>`,
    };
    render(<EpisodeList {...props({ canDownload: false })} />);
    expect(items(await menuOf('More for episode 1'))).toEqual(['Mark watched']);
    expect(screen.getByText('Request E2')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Get episode' })).toBeNull();
  });

  it('keeps the row’s actions on screen while its menu is open', async () => {
    const { container } = render(<EpisodeList {...props({ season: season([OWNED]) })} />);
    expect(container.querySelector('.vd-ep--menu')).toBeNull();
    await menuOf('More for episode 1');
    expect(container.querySelector('.vd-ep--menu')).not.toBeNull();
  });

  it('shows a grab on its row as it runs, and as done', () => {
    const { container, rerender } = render(
      <EpisodeList
        {...props({ season: season([MISSING]), downloads: { '1_2': { status: 'searching' } } })}
      />,
    );
    expect(container.querySelector('.vd-ep-get--busy')).not.toBeNull();
    expect(container.querySelector('.vd-ep-dl-txt')?.textContent).toBe('Searching');
    expect(
      (screen.getByRole('button', { name: 'Get episode' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    rerender(
      <EpisodeList
        {...props({
          season: season([MISSING]),
          downloads: { '1_2': { status: 'downloading', progress: 42 } },
        })}
      />,
    );
    expect(container.querySelector('.vd-ep-dl-txt')?.textContent).toBe('42%');
    rerender(<EpisodeList {...props({ season: season([MISSING]), finished: { '1_2': 1 } })} />);
    expect(container.querySelector('.vd-ep-get--done')).not.toBeNull();
    expect(container.querySelector('.vd-ep-dl-txt--done')?.textContent).toBe('✓ Downloaded');
    expect(
      (screen.getByRole('button', { name: 'Get episode' }) as HTMLButtonElement).disabled,
    ).toBe(false);
  });
});

/* ── hunting one episode ──────────────────────────────────────────────────── */

describe('hunting an episode', () => {
  it('stops and starts the hunt for one episode from its menu', async () => {
    routes['/episodes/monitor'] = () => ({ success: true, episodes: 1 });
    render(<EpisodeList {...props({ season: season([OWNED]) })} />);
    withLibrary();
    fireEvent.click(
      within(await menuOf('More for episode 1')).getByText('Stop hunting this episode'),
    );
    await waitFor(() =>
      expect(calls.find((c) => c.url.includes('/detail/show/445/episodes/monitor'))?.body).toEqual({
        episode_ids: [11],
        monitored: false,
      }),
    );
    withLibrary([libEpisode({ monitored: false }), libEpisode({ id: 12, episode_number: 2 })]);
    expect(items(await menuOf('More for episode 1'))).toContain('Hunt this episode');
  });

  it('says on the row when a hunted season leaves one episode out', () => {
    const { container } = render(<EpisodeList {...props()} />);
    withLibrary([
      libEpisode({ monitored: false }),
      libEpisode({ id: 12, episode_number: 2, has_file: false, files: [] }),
    ]);
    const rows = container.querySelectorAll('.vd-ep');
    expect(rows[0].textContent).toContain('Not hunted');
    expect(rows[1].textContent).not.toContain('Not hunted');
  });

  it('says nothing on the rows of a season that is not hunted at all', () => {
    const { container } = render(<EpisodeList {...props()} />);
    withLibrary([libEpisode({ monitored: false })]);
    expect(container.textContent).not.toContain('Not hunted');
  });

  it('a profile that can’t manage is not offered the hunt', async () => {
    render(<EpisodeList {...props({ season: season([OWNED]) })} />);
    withLibrary([libEpisode()], { can_manage: false });
    expect(items(await menuOf('More for episode 1'))).not.toContain('Stop hunting this episode');
  });
});

/* ── opening an episode ───────────────────────────────────────────────────── */

function guests(n: number) {
  return Array.from({ length: n }, (_, i) => ({ name: `Actor ${i}`, character: `Role ${i}` }));
}

async function opened(extra: Record<string, unknown>, ep: PageEpisode = OWNED) {
  routes['/api/video/episode/1396/1/'] = () => extra;
  const view = render(<EpisodeList {...props({ season: season([ep]) })} />);
  fireEvent.click(view.container.querySelector('.vd-ep-title') as HTMLElement);
  await waitFor(() => expect(view.container.querySelector('.vd-ep-extra-body')).not.toBeNull());
  return view.container.querySelector('.vd-ep-extra') as HTMLElement;
}

describe('an opened episode', () => {
  it('does not repeat the description the row already shows', async () => {
    const panel = await opened({ overview: OWNED.overview });
    expect(panel.querySelector('.vd-ep-extra-ov')).toBeNull();
  });

  it('ignores incidental whitespace when comparing', async () => {
    const panel = await opened({ overview: `  ${OWNED.overview}\n` });
    expect(panel.querySelector('.vd-ep-extra-ov')).toBeNull();
  });

  it('still shows a description the row does not have', async () => {
    const panel = await opened({ overview: 'A longer telling of it.' });
    expect(panel.querySelector('.vd-ep-extra-ov')?.textContent).toBe('A longer telling of it.');
  });

  it('renders every guest but folds the overflow behind one button', async () => {
    const panel = await opened({ guest_stars: guests(14) });
    expect(panel.querySelectorAll('.vd-guest').length).toBe(14);
    const more = within(panel).getByRole('button', { name: '+6 more' });
    fireEvent.click(more);
    expect(panel.querySelector('.vd-ep-guests--all')).not.toBeNull();
    expect(within(panel).queryByRole('button', { name: '+6 more' })).toBeNull();
  });

  it('offers no expander when everyone already fits', async () => {
    const panel = await opened({ guest_stars: guests(8) });
    expect(panel.querySelector('.vd-guest-more')).toBeNull();
  });

  it('hides the tail with a rule that matches what the button promises', () => {
    // the CSS cut-off and the count on the button have to agree
    const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');
    expect(CSS).toContain('> .vd-guest:nth-child(n + 9) { display: none; }');
  });

  it('says so plainly when there is genuinely nothing to show', async () => {
    const panel = await opened({});
    expect(panel.querySelector('.vd-ep-extra-empty')?.textContent).toBe('No extra info.');
  });

  it('still opens onto the still image when that is all there is', async () => {
    const panel = await opened({ still_url: '/still.jpg' });
    expect(panel.querySelector('.vd-ep-extra-still')?.getAttribute('src')).toBe('/still.jpg');
  });

  it('links a guest to their page through the page’s own handler, and the episode out', async () => {
    const panel = await opened({ guest_stars: [{ name: 'Jane', tmdb_id: 77 }], imdb_id: 'tt1' });
    expect(panel.querySelector('a.vd-guest')?.getAttribute('data-vd-person')).toBe('77');
    const links = Array.from(panel.querySelectorAll('.vd-ep-link')).map((a) =>
      a.getAttribute('href'),
    );
    expect(links).toEqual([
      'https://www.themoviedb.org/tv/1396/season/1/episode/1',
      'https://www.imdb.com/title/tt1/',
    ]);
  });

  it('offers to report a library episode, with what the issues page needs', async () => {
    window.VideoIssues = {};
    const panel = await opened({});
    const report = panel.querySelector('[data-vi-report-episode]') as HTMLElement;
    expect(report.getAttribute('data-vi-report-episode')).toBe('11');
    expect(report.getAttribute('data-vi-name')).toBe('Breaking Bad S01E01');
  });

  it('mounts the file cards above the extras, and lets them go when closed', async () => {
    routes['/library'] = () =>
      library([libEpisode({ files: [file({ naming: { conforms: true } })] })]);
    routes['/api/video/episode/1396/1/'] = () => ({});
    const { container } = render(<EpisodeList {...props({ season: season([OWNED]) })} />);
    withLibrary();
    const title = container.querySelector('.vd-ep-title') as HTMLElement;
    fireEvent.click(title);
    expect(await screen.findByText('Named by your template')).toBeTruthy();
    const files = container.querySelector('.vd-ep-files') as HTMLElement;
    expect(files.nextElementSibling?.classList.contains('vd-ep-extra')).toBe(true);
    expect(calls.some((c) => c.url.includes('season=1'))).toBe(true);
    fireEvent.click(title);
    expect(container.querySelector('.vd-ep-files')).toBeNull();
    expect(container.querySelector('.vd-ep-extra')).toBeNull();
  });

  it('stays open through a re-render of the page', async () => {
    routes['/api/video/episode/1396/1/'] = () => ({});
    const { container, rerender } = render(<EpisodeList {...props({ season: season([OWNED]) })} />);
    fireEvent.click(container.querySelector('.vd-ep-title') as HTMLElement);
    expect(container.querySelector('.vd-ep--open')).not.toBeNull();
    // the page read the show again: new objects, the same episode
    rerender(<EpisodeList {...props({ season: season([{ ...OWNED }]) })} />);
    expect(container.querySelector('.vd-ep--open')).not.toBeNull();
  });

  it('a tick on a row, or a click on its menu, is not a click that opens it', async () => {
    const { container } = render(<EpisodeList {...props({ season: season([OWNED]) })} />);
    withLibrary();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select episode 1' }));
    fireEvent.click(screen.getByRole('button', { name: 'More for episode 1' }));
    expect(container.querySelector('.vd-ep--open')).toBeNull();
  });
});

/* ── acting on several episodes ───────────────────────────────────────────── */

const TWO = [
  libEpisode(),
  libEpisode({
    id: 12,
    episode_number: 2,
    title: 'Cat',
    files: [file({ id: 32, filename: 'bb.102.mkv' })],
  }),
];
const BOTH_OWNED = season([OWNED, { ...OWNED, id: 12, episode_number: 2, title: 'Cat' }]);

describe('the ticked episodes', () => {
  it('picks every episode with a file from the season bar, and clears', () => {
    render(<EpisodeList {...props({ season: BOTH_OWNED })} />);
    withLibrary(TWO);
    expect(screen.queryByText(/selected/)).toBeNull();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select every episode that has a file' }));
    expect(screen.getByText('2 episodes selected')).toBeTruthy();
    expect(picked.get()).toEqual([11, 12]);
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(screen.queryByText(/selected/)).toBeNull();
  });

  it('forgets the ticks when the season changes', () => {
    const { rerender } = render(<EpisodeList {...props({ season: BOTH_OWNED })} />);
    withLibrary(TWO);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select episode 1' }));
    expect(picked.get()).toEqual([11]);
    rerender(<EpisodeList {...props({ season: { ...BOTH_OWNED, season_number: 2 } })} />);
    expect(picked.get()).toEqual([]);
  });

  it('shows what a rename would do before renaming, then offers to put it back', async () => {
    routes['/files/rename-preview'] = () => ({
      success: true,
      files: 2,
      to_rename: 1,
      already_named: 1,
      unreachable: 0,
      changes: [
        { file_id: 32, current: '/tv/bb.102.mkv', expected: '/tv/Breaking Bad - S01E02.mkv' },
      ],
      more: 0,
    });
    routes['/files/bulk'] = () => ({
      success: true,
      done: 1,
      unchanged: 1,
      failed: 0,
      errors: [],
      batch: 'b1',
    });
    render(<EpisodeList {...props({ season: BOTH_OWNED })} />);
    withLibrary(TWO);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select every episode that has a file' }));
    fireEvent.click(screen.getByRole('button', { name: 'Rename…' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('bb.102.mkv')).toBeTruthy();
    expect(within(dialog).getByText('Breaking Bad - S01E02.mkv')).toBeTruthy();
    expect(dialog.textContent).toContain('1 file is already right.');
    // nothing has been renamed by looking
    expect(calls.some((c) => c.url.includes('/files/bulk'))).toBe(false);
    expect(calls.find((c) => c.url.includes('rename-preview'))?.url).toContain('file_ids=31,32');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Rename 1 file' }));
    await waitFor(() =>
      expect(calls.find((c) => c.url.includes('/files/bulk'))?.body).toEqual({
        action: 'rename',
        file_ids: [31, 32],
        kind: 'show',
        id: 445,
      }),
    );
    await waitFor(() => expect(lastChange.get()?.batch).toBe('b1'));
    expect(lastChange.get()).toMatchObject({ kind: 'show', id: 445, text: 'Renamed 1 file' });
    expect(picked.get()).toEqual([]);
  });

  it('shows every file a delete would take, and can stop the hunt for what it empties', async () => {
    routes['/files/delete-preview'] = () => ({
      success: true,
      files: 2,
      size_bytes: 2 * 1073741824,
      unreachable: 0,
      emptied: 2,
      recycle: true,
      items: [
        { file_id: 31, label: 'S01E01', filename: 'a.mkv', size_bytes: 1, reachable: true },
        { file_id: 32, label: 'S01E02', filename: 'b.mkv', size_bytes: 1, reachable: true },
      ],
      more: 0,
    });
    routes['/files/bulk'] = () => ({
      success: true,
      done: 2,
      unchanged: 0,
      failed: 0,
      errors: [],
      batch: 'b2',
      unmonitored: 2,
    });
    render(<EpisodeList {...props({ season: BOTH_OWNED })} />);
    withLibrary(TWO);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select every episode that has a file' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete…' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('a.mkv')).toBeTruthy();
    expect(dialog.textContent).toContain('2 episodes will have no copy left');
    fireEvent.click(within(dialog).getByRole('switch'));
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Move 2 files to the recycle bin' }),
    );
    await waitFor(() =>
      expect(calls.find((c) => c.url.includes('/files/bulk'))?.body).toEqual({
        action: 'delete',
        file_ids: [31, 32],
        kind: 'show',
        id: 445,
        stop_hunting: true,
      }),
    );
    await waitFor(() => expect(bridge.reloadPage).toHaveBeenCalled());
    expect(lastChange.get()?.batch).toBe('b2');
  });

  it('offers no hunt switch when every episode keeps a copy, and no undo without a recycle bin', async () => {
    routes['/files/delete-preview'] = () => ({
      success: true,
      files: 1,
      size_bytes: 1,
      unreachable: 0,
      emptied: 0,
      recycle: false,
      items: [{ file_id: 31, label: 'S01E01', filename: 'a.mkv', size_bytes: 1, reachable: true }],
      more: 0,
    });
    routes['/files/bulk'] = () => ({
      success: true,
      done: 1,
      unchanged: 0,
      failed: 0,
      errors: [],
      batch: 'b3',
    });
    render(<EpisodeList {...props({ season: BOTH_OWNED })} />);
    withLibrary(TWO);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select episode 1' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete…' }));
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByText('a.mkv');
    expect(within(dialog).queryByRole('switch')).toBeNull();
    expect(dialog.textContent).toContain('deleted permanently');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete 1 file' }));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/files/bulk'))).toBe(true));
    expect(lastChange.get()).toBeNull();
  });

  it('stops or restarts the hunt for the ticked episodes', async () => {
    routes['/episodes/monitor'] = () => ({ success: true, episodes: 2 });
    render(<EpisodeList {...props({ season: BOTH_OWNED })} />);
    withLibrary(TWO);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select every episode that has a file' }));
    fireEvent.click(screen.getByRole('button', { name: 'Stop hunting' }));
    await waitFor(() =>
      expect(calls.find((c) => c.url.includes('/episodes/monitor'))?.body).toEqual({
        episode_ids: [11, 12],
        monitored: false,
      }),
    );
  });

  it('looks for upgrades of the ticked episodes', async () => {
    routes['/wishlist/add'] = () => ({ added: 2 });
    routes['/wishlist/search'] = () => ({ success: true });
    render(<EpisodeList {...props({ season: BOTH_OWNED })} />);
    withLibrary(TWO);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select every episode that has a file' }));
    fireEvent.click(screen.getByRole('button', { name: 'Look for upgrades' }));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/wishlist/search'))).toBe(true));
    const add = calls.find((c) => c.url.includes('/wishlist/add'))?.body as {
      episodes: { episode_number: number }[];
    };
    expect(add.episodes.map((e) => e.episode_number)).toEqual([1, 2]);
  });

  it('opens the renumber editor on this season with the ticked files', async () => {
    routes['/manager/episodes/445'] = () => ({
      success: true,
      seasons: [
        {
          season: 1,
          episodes: [
            {
              id: 11,
              episode: 1,
              title: 'Pilot',
              air_date: null,
              files: [{ file_id: 31, filename: 'a.mkv', size_bytes: 1 }],
            },
            {
              id: 12,
              episode: 2,
              title: 'Cat',
              air_date: null,
              files: [{ file_id: 32, filename: 'b.mkv', size_bytes: 1 }],
            },
          ],
        },
      ],
    });
    routes['/manager/renumber/445'] = () => ({ success: true, files: 0, moves: [] });
    render(<EpisodeList {...props({ season: BOTH_OWNED })} />);
    withLibrary(TWO);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select episode 2' }));
    fireEvent.click(screen.getByRole('button', { name: 'Renumber…' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText(/ticked\. File them under/)).toBeTruthy();
    expect(dialog.textContent).toContain('1 file');
    expect((within(dialog).getByLabelText('Season') as HTMLSelectElement).value).toBe('1');
  });
});

/* ── the season's own file tools ──────────────────────────────────────────── */

describe('managing a whole season', () => {
  it('offers rename, renumber and delete for the season’s files to an admin', async () => {
    render(<EpisodeList {...props({ season: BOTH_OWNED })} />);
    withLibrary(TWO);
    expect(items(await menuOf('More for this season'))).toEqual([
      'Mark season watched',
      'Clear failures',
      'Rename files…',
      'Renumber episodes…',
      'Delete files…',
    ]);
  });

  it('keeps them from a profile that can’t manage, and from a season with no files', async () => {
    const { unmount } = render(<EpisodeList {...props({ season: BOTH_OWNED })} />);
    withLibrary(TWO, { can_manage: false });
    expect(items(await menuOf('More for this season'))).not.toContain('Rename files…');
    unmount();
    render(<EpisodeList {...props({ season: season([MISSING]) })} />);
    withLibrary([libEpisode({ id: 12, episode_number: 2, has_file: false, files: [] })]);
    expect(items(await menuOf('More for this season'))).not.toContain('Delete files…');
  });

  it('previews the whole season’s rename by its files', async () => {
    routes['/files/rename-preview'] = () => ({
      success: true,
      files: 2,
      to_rename: 0,
      already_named: 2,
      unreachable: 0,
      changes: [],
      more: 0,
    });
    render(<EpisodeList {...props({ season: BOTH_OWNED })} />);
    withLibrary(TWO);
    fireEvent.click(within(await menuOf('More for this season')).getByText('Rename files…'));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText(/already named by your template/)).toBeTruthy();
    expect(dialog.textContent).toContain('Season 1');
    expect(
      (within(dialog).getByRole('button', { name: 'Nothing to rename' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });
});
