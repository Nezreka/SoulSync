import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { LibraryFile, MovieLibrary, ShowLibrary } from './types';

import { videoLibraryTab } from './host';
import { LibraryTab } from './library-tab';

interface Call {
  url: string;
  method: string;
  body: unknown;
}

let calls: Call[] = [];
let routes: Record<string, (body: unknown, url: string) => unknown> = {};

function file(over: Partial<LibraryFile> = {}): LibraryFile {
  return {
    id: 1,
    path: '/mnt/easystore4/MOVIES/Heat (1995)/Heat (1995) Bluray-1080p.mkv',
    filename: 'Heat (1995) Bluray-1080p.mkv',
    root: '/mnt/easystore4',
    size_bytes: 8 * 1073741824,
    resolution: '1080',
    resolution_label: '1080p',
    quality: 'Bluray-1080p',
    video_codec: 'hevc',
    audio_codec: 'eac3',
    audio_channels: 6,
    dynamic_range: null,
    atmos: false,
    release_source: null,
    runtime_seconds: 10200,
    added_at: '2026-10-20 09:05:54',
    aspect: '16:9',
    playable: { verdict: 'maybe', reasons: [] },
    reachable: true,
    local_path: 'X:/Heat.mkv',
    subtitles: [{ language: 'en', hi: false, forced: false }],
    naming: { conforms: true, expected_name: 'Heat (1995) Bluray-1080p.mkv' },
    best: true,
    ...over,
  };
}

function movie(over: Partial<MovieLibrary> = {}): MovieLibrary {
  return {
    kind: 'movie',
    id: 7,
    title: 'Heat',
    year: 1995,
    identity: {
      tmdb_id: 949,
      imdb_id: 'tt0113277',
      tvdb_id: null,
      match_status: 'matched',
      fixed: false,
      manual_link: null,
      server: 'plex',
    },
    summary: {
      files: 2,
      size_bytes: 9 * 1073741824,
      watched: false,
      play_count: 0,
      last_viewed_at: null,
      monitored: true,
      added_at: '2026-10-20 09:05:54',
    },
    quality: {
      profile_id: 0,
      profile_name: 'Default',
      cutoff_label: '1080p',
      meets: true,
      reasons: [],
    },
    copies: {
      count: 2,
      roots: ['/mnt/easystore4', '/mnt/easystore3'],
      spans_drives: true,
      same_resolution: false,
    },
    files: [
      file(),
      file({
        id: 2,
        filename: 'heat.720p.mkv',
        path: '/mnt/easystore3/heat.720p.mkv',
        resolution: '720',
        resolution_label: '720p',
        quality: null,
        size_bytes: 1073741824,
        reachable: false,
        local_path: null,
        subtitles: null,
        naming: null,
        best: false,
      }),
    ],
    recycle: true,
    can_manage: true,
    ...over,
  };
}

function show(): ShowLibrary {
  return {
    kind: 'show',
    id: 445,
    title: 'Breaking Bad',
    year: 2008,
    identity: {
      tmdb_id: 1396,
      imdb_id: 'tt0903747',
      tvdb_id: 81189,
      match_status: 'matched',
      fixed: true,
      manual_link: null,
      server: 'plex',
    },
    summary: {
      owned: 3,
      total: 5,
      missing: 1,
      upcoming: 1,
      specials_owned: 0,
      files: 3,
      size_bytes: 3 * 1073741824,
      res_mix: [
        { label: '1080p', count: 1 },
        { label: 'SD', count: 2 },
      ],
      roots: ['/mnt/easystore'],
      folders: [{ path: '/mnt/easystore/TV/Breaking Bad', files: 3 }],
      watched_episodes: 2,
      series_type: null,
      monitored: true,
    },
    quality: {
      profile_id: null,
      profile_name: 'Default',
      cutoff_label: '1080p',
      meets: true,
      below_cutoff: 2,
    },
    seasons: [
      {
        season_number: 1,
        title: 'Season 1',
        owned: 2,
        total: 3,
        missing: 1,
        upcoming: 0,
        size_bytes: 2 * 1073741824,
        monitored: true,
        below_cutoff: 1,
        res_mix: [{ label: '1080p', count: 2 }],
        episodes: [
          {
            id: 11,
            episode_number: 1,
            title: 'Pilot',
            air_date: '2008-01-20',
            has_file: true,
            monitored: true,
            watched: true,
            aired: true,
            files: [file({ id: 31, filename: 'Breaking Bad - S01E01.mkv' })],
          },
          {
            id: 12,
            episode_number: 2,
            title: "Cat's in the Bag",
            air_date: '2008-01-27',
            has_file: false,
            monitored: true,
            watched: false,
            aired: true,
            files: [],
          },
        ],
      },
      {
        season_number: 2,
        title: 'Season 2',
        owned: 1,
        total: 2,
        missing: 0,
        upcoming: 1,
        size_bytes: 1073741824,
        monitored: false,
        below_cutoff: 1,
        res_mix: [{ label: 'SD', count: 1 }],
        episodes: [],
      },
    ],
    detail_season: null,
    recycle: true,
    can_manage: true,
  };
}

beforeEach(() => {
  calls = [];
  routes = {};
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
  window.showConfirmDialog = vi.fn(async () => true);
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.showToast;
  delete window.showConfirmDialog;
  delete window.SoulSyncVideoDetail;
});

async function openMenu(label: string, index = 0) {
  const btn = screen.getAllByRole('button', { name: label })[index];
  fireEvent.click(btn);
  return screen.findByRole('menu');
}

describe('movie tab', () => {
  it('lists every file with what the disk says, and the one only the server can see', async () => {
    routes['/library'] = () => movie();
    render(<LibraryTab kind="movie" id={7} />);
    expect(await screen.findByText('Bluray-1080p')).toBeTruthy();
    expect(screen.getByText('Best copy')).toBeTruthy();
    expect(screen.getByText('2 copies · 9.0 GB on 2 drives')).toBeTruthy();
    expect(screen.getByText('Only your media server can see this file')).toBeTruthy();
    expect(screen.getByText('English')).toBeTruthy();
    expect(screen.getByText('✓ Named by your template')).toBeTruthy();
    expect(screen.getByText(/Meets your 1080p target/)).toBeTruthy();
  });

  it('says the audio languages and every subtitle, in the file or beside it', async () => {
    routes['/library'] = () =>
      movie({
        files: [
          file({
            audio_languages: ['ja', 'en'],
            subtitle_languages: ['en', 'es'],
            subtitles: [{ language: 'en', hi: false, forced: false }],
          }),
        ],
      });
    render(<LibraryTab kind="movie" id={7} />);
    expect(await screen.findByText('Japanese, English')).toBeTruthy();
    // english is beside it already; spanish is only inside the file
    expect(screen.getByText('English, Spanish (in file)')).toBeTruthy();
  });

  it('never claims there are no subtitles before the folder was looked at', async () => {
    routes['/library'] = () =>
      movie({ files: [file({ subtitles: null, subtitle_languages: [], audio_languages: [] })] });
    render(<LibraryTab kind="movie" id={7} />);
    expect(await screen.findByText('Bluray-1080p')).toBeTruthy();
    expect(screen.queryByText('No subtitles')).toBeNull();
    expect(screen.queryByText(/Audio/)).toBeNull();
  });

  it('offers a rename for a file whose name drifted from the template', async () => {
    routes['/library'] = () =>
      movie({
        files: [
          file({ naming: { conforms: false, expected_name: 'Heat (1995) Bluray-1080p.mkv' } }),
        ],
      });
    routes['/rename'] = () => ({
      success: true,
      action: 'renamed',
      message: 'Renamed to Heat (1995) Bluray-1080p.mkv',
    });
    render(<LibraryTab kind="movie" id={7} />);
    // the file's own Rename; the same fix is offered up in Needs attention
    fireEvent.click((await screen.findAllByRole('button', { name: 'Rename' })).at(-1)!);
    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith('/files/1/rename') && c.method === 'POST')).toBe(
        true,
      ),
    );
    expect(window.showConfirmDialog).toHaveBeenCalled();
    expect(window.showToast).toHaveBeenCalledWith(
      'Renamed to Heat (1995) Bluray-1080p.mkv',
      'success',
    );
  });

  it('deletes through the recycle bin only after the soulsync confirm', async () => {
    routes['/library'] = () => movie();
    routes['/delete'] = () => ({ success: true, recycled: true, remaining: 1 });
    render(<LibraryTab kind="movie" id={7} />);
    await screen.findByText('Bluray-1080p');
    const menu = await openMenu('File actions');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Delete to recycle bin' }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/files/1/delete'))).toBe(true));
    const opts = vi.mocked(window.showConfirmDialog!).mock.calls[0][0];
    expect((opts as { destructive?: boolean } | undefined)?.destructive).toBe(true);
    expect(opts?.message).toContain('recycle bin');
  });

  it('a cancelled confirm deletes nothing', async () => {
    window.showConfirmDialog = vi.fn(async () => false);
    routes['/library'] = () => movie();
    render(<LibraryTab kind="movie" id={7} />);
    await screen.findByText('Bluray-1080p');
    const menu = await openMenu('File actions');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Delete to recycle bin' }));
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(calls.some((c) => c.url.includes('/delete'))).toBe(false);
  });

  it('a file soulsync cannot reach cannot be renamed or deleted from here', async () => {
    routes['/library'] = () => movie();
    render(<LibraryTab kind="movie" id={7} />);
    await screen.findByText('Bluray-1080p');
    const menu = await openMenu('File actions', 1);
    expect(
      (within(menu).getByRole('menuitem', { name: 'Rename to template' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      (within(menu).getByRole('menuitem', { name: 'Delete to recycle bin' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it('a profile that is not an admin sees the facts, not the controls', async () => {
    routes['/library'] = () => movie({ can_manage: false });
    render(<LibraryTab kind="movie" id={7} />);
    await screen.findByText('Bluray-1080p');
    expect(screen.queryByRole('button', { name: 'Fix match' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'File actions' })).toBeNull();
  });

  it('fix match searches tmdb, re-points the title and reloads the page', async () => {
    const reloadPage = vi.fn();
    window.SoulSyncVideoDetail = { reloadPage };
    routes['/library'] = () => movie();
    routes['/identify/search'] = () => ({
      results: [
        { id: 841, title: 'Dune', year: 1984, overview: 'A duke’s son…', poster_url: null },
      ],
    });
    routes['/identify'] = () => ({
      success: true,
      tmdb_id: 841,
      title: 'Dune',
      year: 1984,
      renamed: true,
    });
    render(<LibraryTab kind="movie" id={7} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Fix match' }));
    fireEvent.click(await screen.findByRole('option', { name: /Dune/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Use this title' }));
    await waitFor(() => expect(reloadPage).toHaveBeenCalled());
    const post = calls.find((c) => c.url.endsWith('/detail/movie/7/identify'));
    expect(post?.body).toEqual({ tmdb_id: 841 });
  });

  it('an upgrade-eligible movie leads with finding one', async () => {
    const manualSearch = vi.fn();
    window.SoulSyncVideoDetail = { manualSearch };
    routes['/library'] = () =>
      movie({
        quality: {
          profile_id: 0,
          profile_name: 'Default',
          cutoff_label: '4K',
          meets: false,
          reasons: [{ ok: false, text: 'Below your 4K target — eligible for an upgrade.' }],
        },
      });
    render(<LibraryTab kind="movie" id={7} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Find an upgrade' }));
    expect(manualSearch).toHaveBeenCalledWith('movie');
  });
});

describe('show tab', () => {
  it('summarises the show and loads a season’s files when it opens', async () => {
    routes['/library'] = (_b, url) => ({
      ...show(),
      detail_season: url.includes('season=1') ? 1 : null,
    });
    render(<LibraryTab kind="show" id={445} />);
    expect(await screen.findByText('2 files below your 1080p target')).toBeTruthy();
    expect(screen.getByText('/mnt/easystore/TV/Breaking Bad')).toBeTruthy();
    expect(screen.getByText('Fixed by you')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Season 1/ }));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/library?season=1'))).toBe(true));
    expect(await screen.findByText('Pilot')).toBeTruthy();
    expect(screen.getByText('Missing')).toBeTruthy();
  });

  it('says a season-wide fact once, not on every episode', async () => {
    const far = (id: number, n: number): LibraryFile =>
      file({
        id,
        filename: `S02E0${n}.mkv`,
        reachable: false,
        subtitles: null,
        naming: null,
        below_cutoff: true,
      });
    routes['/library'] = () => {
      const d = show();
      d.seasons[1] = {
        ...d.seasons[1],
        owned: 2,
        below_cutoff: 2,
        episodes: [1, 2].map((n) => ({
          id: 20 + n,
          episode_number: n,
          title: `Ep ${n}`,
          air_date: '2009-03-08',
          has_file: true,
          monitored: true,
          watched: false,
          aired: true,
          files: [far(40 + n, n)],
        })),
      };
      return d;
    };
    render(<LibraryTab kind="show" id={445} />);
    fireEvent.click(await screen.findByRole('button', { name: /Season 2/ }));
    expect(await screen.findByText(/only your media server can see, so SoulSync/)).toBeTruthy();
    expect(screen.getByText(/Every file here is/)).toBeTruthy();
    expect(screen.queryByText('Only your media server can see this file')).toBeNull();
    expect(screen.queryByText('Below your quality target')).toBeNull();
  });

  it('renames every drifted file in a season after one confirm', async () => {
    routes['/library'] = () => {
      const d = show();
      d.seasons[0].episodes[0].files = [
        file({ id: 51, naming: { conforms: false, expected_name: 'a.mkv' } }),
        file({ id: 52, naming: { conforms: false, expected_name: 'b.mkv' } }),
      ];
      return d;
    };
    routes['/files/bulk'] = () => ({ success: true, done: 2, unchanged: 0, failed: 0, errors: [] });
    render(<LibraryTab kind="show" id={445} />);
    fireEvent.click(await screen.findByRole('button', { name: /Season 1/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Rename all' }));
    // one request for the lot, so the server renames them in order
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/files/bulk'))).toBe(true));
    expect(calls.find((c) => c.url.endsWith('/files/bulk'))?.body).toEqual({
      action: 'rename',
      file_ids: [51, 52],
      kind: 'show',
    });
    expect(window.showConfirmDialog).toHaveBeenCalledTimes(1);
    expect(window.showToast).toHaveBeenCalledWith('Renamed 2 files', 'success');
  });

  it('the monitored switch flips the season on the server', async () => {
    routes['/library'] = () => show();
    routes['/monitor'] = () => ({ success: true });
    render(<LibraryTab kind="show" id={445} />);
    await screen.findByText('Season 2');
    const switches = screen.getAllByRole('checkbox');
    expect((switches[1] as HTMLInputElement).checked).toBe(false);
    fireEvent.click(switches[1]);
    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.url.endsWith('/detail/show/445/season/2/monitor') &&
            (c.body as { monitored: boolean }).monitored,
        ),
      ).toBe(true),
    );
  });

  it('season actions hand off to the page’s own grab and search flows', async () => {
    const grabSeason = vi.fn();
    const manualSearch = vi.fn();
    window.SoulSyncVideoDetail = { grabSeason, manualSearch };
    routes['/library'] = () => show();
    render(<LibraryTab kind="show" id={445} />);
    await screen.findByText('Season 1');
    const menu = await openMenu('Season 1 actions');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Grab 1 missing' }));
    expect(grabSeason).toHaveBeenCalledWith(1);
  });
});

describe('what a file says', () => {
  it('leads with picture and sound, and names the download it came from', async () => {
    routes['/library'] = () =>
      movie({
        files: [
          file({
            dynamic_range: 'DV',
            atmos: true,
            source: {
              release: 'Heat.1995.2160p.UHD.BluRay-GRP',
              via: 'torrent',
              indexer: '1337x',
              at: '2026-11-05',
            },
          }),
        ],
      });
    render(<LibraryTab kind="movie" id={7} />);
    expect(await screen.findByText('Dolby Vision')).toBeTruthy();
    expect(screen.getByText('EAC3 5.1 Atmos')).toBeTruthy();
    expect(screen.getByText(/Heat\.1995\.2160p\.UHD\.BluRay-GRP · 1337x/)).toBeTruthy();
  });

  it('a file soulsync did not download claims no source', async () => {
    routes['/library'] = () => movie();
    render(<LibraryTab kind="movie" id={7} />);
    await screen.findByText('Bluray-1080p');
    expect(screen.queryByText('From')).toBeNull();
  });

  it('names the first few subtitle tracks and counts the rest', async () => {
    routes['/library'] = () =>
      movie({
        files: [file({ subtitles: [], subtitle_languages: ['en', 'ar', 'de', 'fr', 'ja'] })],
      });
    render(<LibraryTab kind="movie" id={7} />);
    const chip = await screen.findByText(/English, Arabic, German and 2 more/);
    // all five are inside the file: said once, and the full list is on hover
    expect(chip.textContent).toContain('CC in file');
    expect(chip.getAttribute('title')).toBe('English, Arabic, German, French, Japanese');
  });
});

describe('needs attention', () => {
  it('says so when nothing does', async () => {
    routes['/library'] = () =>
      movie({ files: [file()], extra_copies: { titles: 0, files: 0, size_bytes: 0 } });
    render(<LibraryTab kind="movie" id={7} />);
    expect(await screen.findByText('Nothing here needs attention.')).toBeTruthy();
  });

  it('a movie with extra copies shows the plan and removes them only on the button', async () => {
    routes['/library'] = () =>
      movie({ extra_copies: { titles: 1, files: 1, size_bytes: 1073741824 } });
    routes['/files/extra-copies/movie/7'] = (body) =>
      body
        ? { success: true, removed: 1, failed: 0, size_bytes: 1073741824, unreachable: 0 }
        : {
            success: true,
            files: 1,
            size_bytes: 1073741824,
            unreachable: 0,
            groups: [
              {
                label: 'Heat',
                keep: {
                  file_id: 1,
                  filename: 'Heat (1995) Bluray-1080p.mkv',
                  size_bytes: 8,
                  resolution_label: '1080p',
                },
                remove: [
                  {
                    file_id: 2,
                    filename: 'heat.720p.mkv',
                    size_bytes: 1073741824,
                    resolution_label: '720p',
                    reachable: true,
                  },
                ],
              },
            ],
          };
    render(<LibraryTab kind="movie" id={7} />);
    expect(await screen.findByText('1 extra copy of this movie')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Keep the best…' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('heat.720p.mkv')).toBeTruthy();
    // opening it only looked
    expect(calls.some((c) => c.url.includes('/extra-copies') && c.method === 'POST')).toBe(false);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove 1 copy (1.0 GB)' }));
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('/extra-copies') && c.method === 'POST')).toBe(true),
    );
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Removed 1 copy, 1.0 GB freed', 'success'),
    );
  });

  it('look for upgrades wishlists exactly the episodes below target, then searches', async () => {
    routes['/library'] = () => {
      const d = show();
      d.seasons[0].episodes[0].files = [file({ id: 31, below_cutoff: true })];
      return d;
    };
    routes['/wishlist/add'] = () => ({ success: true, added: 1 });
    routes['/wishlist/search'] = () => ({ success: true });
    render(<LibraryTab kind="show" id={445} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Look for upgrades' }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/wishlist/search'))).toBe(true));
    const add = calls.find((c) => c.url.endsWith('/wishlist/add'))?.body as {
      show: { tmdb_id: number; library_id: number };
      episodes: { season_number: number; episode_number: number }[];
    };
    expect(add.show).toMatchObject({ tmdb_id: 1396, library_id: 445 });
    expect(add.episodes.map((e) => [e.season_number, e.episode_number])).toEqual([[1, 1]]);
    expect(calls.find((c) => c.url.endsWith('/wishlist/search'))?.body).toEqual({
      scope: 'show',
      tmdb_id: 1396,
    });
  });

  it('episodes already on the wishlist are not an error', async () => {
    routes['/library'] = () => {
      const d = show();
      d.seasons[0].episodes[0].files = [file({ id: 31, below_cutoff: true })];
      return d;
    };
    routes['/wishlist/add'] = () => ({ success: false, added: 0 });
    routes['/wishlist/search'] = () => ({ success: true });
    render(<LibraryTab kind="show" id={445} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Look for upgrades' }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/wishlist/search'))).toBe(true));
    expect(vi.mocked(window.showToast!).mock.calls.at(-1)?.[1] ?? 'success').toBe('success');
  });

  it('get missing wishlists the aired episodes it does not have', async () => {
    routes['/library'] = () => show();
    routes['/wishlist/add'] = () => ({ success: true, added: 1 });
    routes['/wishlist/search'] = () => ({ success: true });
    render(<LibraryTab kind="show" id={445} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Get missing' }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/wishlist/add'))).toBe(true));
    const add = calls.find((c) => c.url.endsWith('/wishlist/add'))?.body as {
      episodes: { season_number: number; episode_number: number }[];
    };
    expect(add.episodes.map((e) => [e.season_number, e.episode_number])).toEqual([[1, 2]]);
  });

  it('a split show is brought together: plan first, then the move with its progress', async () => {
    let polls = 0;
    routes['/library'] = () => {
      const d = show();
      d.summary.folders = [
        {
          path: '/mnt/a/TV/Breaking Bad',
          files: 2,
          name: 'Breaking Bad',
          root: '/mnt/a',
          main: true,
        },
        { path: '/mnt/b/BB.S01E02', files: 1, name: 'BB.S01E02', root: '/mnt/b' },
      ];
      return d;
    };
    routes['/status'] = () => {
      polls += 1;
      return polls < 2
        ? { success: true, known: false, running: false }
        : { success: true, known: true, running: false, total: 1, done: 1, moved: 1, failed: 0 };
    };
    routes['/files/consolidate/445'] = (body) =>
      body
        ? { success: true, files: 1 }
        : {
            success: true,
            target: '/mnt/a/TV/Breaking Bad',
            files: 1,
            size_bytes: 1073741824,
            copy_bytes: 1073741824,
            free_bytes: 50 * 1073741824,
            enough_room: true,
            moves: [],
            skipped: [],
          };
    render(<LibraryTab kind="show" id={445} />);
    expect(await screen.findByText('Split across 2 folders')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Bring together…' }));
    const dialog = await screen.findByRole('dialog');
    // a move to another drive is a copy: it says so, and what room there is
    expect(await within(dialog).findByText(/is on another drive/)).toBeTruthy();
    expect(calls.some((c) => c.url.endsWith('/files/consolidate/445') && c.method === 'POST')).toBe(
      false,
    );
    fireEvent.click(within(dialog).getByRole('button', { name: 'Move 1 file' }));
    await waitFor(() =>
      expect(
        calls.find((c) => c.url.endsWith('/files/consolidate/445') && c.method === 'POST')?.body,
      ).toEqual({
        target: '/mnt/a/TV/Breaking Bad',
      }),
    );
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Moved 1 file into one folder', 'success'),
    );
    expect(within(dialog).getByRole('button', { name: 'Done' })).toBeTruthy();
  });

  it('a profile that is not an admin sees the problems without the fixes', async () => {
    routes['/library'] = () =>
      movie({ can_manage: false, extra_copies: { titles: 1, files: 1, size_bytes: 5 } });
    render(<LibraryTab kind="movie" id={7} />);
    expect(await screen.findByText('1 extra copy of this movie')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Keep the best…' })).toBeNull();
  });
});

describe('acting on episodes', () => {
  function twoOwned(): ShowLibrary {
    const d = show();
    d.seasons[0].episodes[1] = {
      ...d.seasons[0].episodes[1],
      has_file: true,
      files: [file({ id: 32, filename: 'Breaking Bad - S01E02.mkv' })],
    };
    return d;
  }

  it('deletes the picked episodes in one request, after asking', async () => {
    routes['/library'] = () => twoOwned();
    routes['/files/bulk'] = () => ({ success: true, done: 2, unchanged: 0, failed: 0, errors: [] });
    render(<LibraryTab kind="show" id={445} />);
    fireEvent.click(await screen.findByRole('button', { name: /Season 1/ }));
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select S01E01' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select S01E02' }));
    expect(screen.getByText('2 selected')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/files/bulk'))).toBe(true));
    const opts = vi.mocked(window.showConfirmDialog!).mock.calls[0][0];
    expect((opts as { destructive?: boolean } | undefined)?.destructive).toBe(true);
    expect(calls.find((c) => c.url.endsWith('/files/bulk'))?.body).toEqual({
      action: 'delete',
      file_ids: [31, 32],
      kind: 'show',
    });
  });

  it('a cancelled confirm deletes none of them', async () => {
    window.showConfirmDialog = vi.fn(async () => false);
    routes['/library'] = () => twoOwned();
    render(<LibraryTab kind="show" id={445} />);
    fireEvent.click(await screen.findByRole('button', { name: /Season 1/ }));
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select S01E01' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(calls.some((c) => c.url.endsWith('/files/bulk'))).toBe(false);
  });

  it('moves a file to the episode it really is', async () => {
    routes['/library'] = () => twoOwned();
    routes['/reassign'] = () => ({ success: true, action: 'reassigned', renamed: true });
    render(<LibraryTab kind="show" id={445} />);
    fireEvent.click(await screen.findByRole('button', { name: /Season 1/ }));
    const menu = await openMenu('S01E01 file actions');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Move to another episode…' }));
    const dialog = await screen.findByRole('dialog');
    // where it already is can't be picked
    expect(
      (within(dialog).getByRole('option', { name: /Pilot/ }) as HTMLButtonElement).disabled,
    ).toBe(true);
    fireEvent.click(within(dialog).getByRole('option', { name: /Cat's in the Bag/ }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Move here' }));
    await waitFor(() =>
      expect(calls.find((c) => c.url.endsWith('/files/31/reassign'))?.body).toEqual({
        episode_id: 12,
      }),
    );
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        'Moved, and renamed to match the episode',
        'success',
      ),
    );
  });

  it('a file that kept its name says the server may file it back', async () => {
    routes['/library'] = () => twoOwned();
    routes['/reassign'] = () => ({ success: true, action: 'reassigned', renamed: false });
    render(<LibraryTab kind="show" id={445} />);
    fireEvent.click(await screen.findByRole('button', { name: /Season 1/ }));
    const menu = await openMenu('S01E01 file actions');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Move to another episode…' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('option', { name: /Cat's in the Bag/ }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Move here' }));
    await waitFor(() => expect(window.showToast).toHaveBeenCalled());
    expect(vi.mocked(window.showToast!).mock.calls.at(-1)).toEqual([
      expect.stringContaining('may file it back'),
      'warning',
    ]);
  });
});

describe('host', () => {
  it('mounts into a vanilla element and unmounts cleanly', async () => {
    routes['/library'] = () => movie();
    const el = document.createElement('div');
    document.body.appendChild(el);
    act(() => videoLibraryTab.mount(el, { kind: 'movie', id: 7 }));
    await waitFor(() => expect(el.textContent).toContain('Bluray-1080p'));
    act(() => videoLibraryTab.unmount(el));
    expect(el.textContent).toBe('');
    el.remove();
  });
});
