/**
 * The video library manager: the same library as posters, a table of titles
 * and a table of files, with filters that manage and a bar that acts on
 * whatever is selected.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { BulkStatus, Facets, FileRow, TitleRow } from './types';

import { filterParams } from './api';
import {
  codecName,
  countFilters,
  formatSize,
  languageCodes,
  languageName,
  shortPath,
} from './format';
import { describeDone } from './manager-bulk';
import { EpisodesTab } from './manager-extras';
import { ManagerPage } from './manager-page';
import { changedFields } from './title-sheet';
import { EMPTY_FILTERS } from './types';

function title(over: Partial<TitleRow> = {}): TitleRow {
  return {
    id: 1,
    kind: 'movie',
    title: 'The Matrix',
    year: 1999,
    tmdb_id: 603,
    matched: true,
    monitored: true,
    owned: true,
    watched: false,
    added_at: '2026-06-14 10:00:00',
    content_rating: 'R',
    runtime_minutes: 136,
    rating: 8.7,
    has_poster: false,
    quality_profile_id: null,
    video_library_id: 1,
    locked_fields: [],
    locked: false,
    status: 'released',
    genres: ['Action'],
    below_cutoff: false,
    cutoff: '1080p',
    file_count: 1,
    size_bytes: 9e9,
    resolution: '4K',
    resolution_rank: 4,
    resolution_mix: { '4K': 1 },
    video_codecs: ['hevc'],
    audio_codecs: ['truehd'],
    dynamic_range: ['HDR10'],
    release_sources: ['bluray'],
    audio_channels: 8,
    atmos: true,
    audio_languages: ['en', 'ja'],
    subtitle_languages: ['en'],
    extra_copies: 0,
    unprobed_files: 0,
    files_without_subtitles: 0,
    path: '/mnt/a/Movies/The Matrix (1999)/m.mkv',
    drive: '/mnt/a',
    ...over,
  };
}

function file(over: Partial<FileRow> = {}): FileRow {
  return {
    id: 11,
    kind: 'movie',
    title_id: 1,
    title: 'The Matrix',
    year: 1999,
    episode_id: null,
    season_number: null,
    episode_number: null,
    episode_title: null,
    path: '/mnt/a/Movies/The Matrix (1999)/m.mkv',
    filename: 'm.mkv',
    drive: '/mnt/a',
    size_bytes: 9e9,
    resolution: '4K',
    resolution_rank: 4,
    video_codec: 'hevc',
    audio_codec: 'truehd',
    release_source: 'bluray',
    quality: null,
    runtime_seconds: 8160,
    audio_channels: 8,
    dynamic_range: 'HDR10',
    atmos: true,
    audio_languages: ['en'],
    subtitle_languages: [],
    probed: true,
    added_at: '2026-06-14 10:00:00',
    ...over,
  };
}

const FACETS: Facets = {
  kind: 'movies',
  titles: 2,
  size_bytes: 12e9,
  files: 3,
  owned: 2,
  resolutions: [
    { value: '4K', count: 1 },
    { value: '720p', count: 1 },
  ],
  codecs: [{ value: 'hevc', count: 1 }],
  audio_codecs: [],
  release_sources: [],
  hdr: [{ value: 'HDR10', count: 1 }],
  audio_languages: [{ value: 'ja', count: 1 }],
  subtitle_languages: [{ value: 'en', count: 2 }],
  genres: [{ value: 'Action', count: 2 }],
  drives: [
    { value: '/mnt/a', count: 1, size_bytes: 9e9 },
    { value: '/mnt/b', count: 1, size_bytes: 3e9 },
  ],
  needs: {
    missing: 0,
    below_cutoff: 1,
    copies: 1,
    unmatched: 0,
    no_subtitles: 0,
    unprobed: 0,
    unmonitored: 0,
    unwatched: 2,
    not_owned: 0,
  },
  quality_profiles: [{ id: 3, name: '4K' }],
  libraries: [{ id: 1, name: 'Movies' }],
};

const ALIEN = title({
  id: 2,
  title: 'Alien',
  year: 1979,
  resolution: '720p',
  resolution_rank: 2,
  resolution_mix: { '720p': 2 },
  below_cutoff: true,
  extra_copies: 1,
  file_count: 2,
  dynamic_range: [],
  video_codecs: ['h264'],
  atmos: false,
  audio_channels: 2,
});

interface Server {
  calls: { url: string; method: string; body: unknown }[];
  forbidden: boolean;
  bulk: BulkStatus;
  views: unknown[];
}

let server: Server;

function page(items: unknown[], extra: Record<string, unknown> = {}) {
  return {
    success: true,
    items,
    kind: 'movies',
    totals: { titles: items.length, files: items.length, size_bytes: 12e9 },
    pagination: {
      page: 1,
      limit: 100,
      total_pages: 1,
      total_count: items.length,
      has_prev: false,
      has_next: false,
    },
    ...extra,
  };
}

function answer(url: string, method: string): unknown {
  const u = new URL(url, 'http://x');
  const path = u.pathname;
  if (path.startsWith('/api/video/manager/') && server.forbidden)
    return { __status: 403, error: 'Admin only.' };
  if (path === '/api/video/manager/facets')
    return { success: true, ...FACETS, kind: u.searchParams.get('kind') };
  if (path === '/api/video/manager/titles') {
    const needs = u.searchParams.get('needs') || '';
    const rows = [title(), ALIEN].filter((r) => !needs.includes('copies') || r.extra_copies > 0);
    return page(rows);
  }
  if (path === '/api/video/manager/files')
    return page([file(), file({ id: 12, filename: 'a.mkv' })]);
  if (path === '/api/video/manager/ids') return { success: true, ids: [1, 2], truncated: false };
  if (path === '/api/video/manager/rename-preview')
    return {
      success: true,
      files: 3,
      to_rename: 1,
      already_named: 2,
      unreachable: 0,
      changes: [{ file_id: 9, current: 'a.mkv', expected: 'Alien (1979).mkv' }],
      more: 0,
    };
  if (path === '/api/video/manager/delete-preview')
    return {
      success: true,
      titles: 2,
      sample: ['The Matrix', 'Alien'],
      files: 3,
      size_bytes: 12e9,
      blocked: [],
    };
  if (path === '/api/video/bulk/start' && method === 'POST')
    return { ok: true, total: 2, label: 'Working' };
  if (path === '/api/video/bulk/status') return server.bulk;
  if (path === '/api/video/manager/libraries')
    return {
      success: true,
      libraries: [
        {
          id: 1,
          name: 'Movies',
          path: '/mnt/a/Movies',
          is_default: true,
          // one library on three drives
          folders: [
            { path: '/mnt/a/Movies', primary: true, free_bytes: 5e11 },
            { path: '/mnt/easystore2/Movies', primary: false, free_bytes: 2e12 },
            { path: '/mnt/easystore3/Movies', primary: false, free_bytes: null },
          ],
        },
        { id: 4, name: 'Kids', path: '/mnt/b/Kids', is_default: false },
      ],
    };
  if (path === '/api/video/manager/move-preview')
    return {
      success: true,
      files: 2,
      size_bytes: 12e9,
      copy_bytes: 12e9,
      skipped: 0,
      held: 1,
      free_bytes: 5e11,
      enough_room: true,
      moves: [{ from: '/mnt/a/Movies/Alien (1979)/a.mkv', to: '/mnt/b/Kids/Alien (1979)/a.mkv' }],
    };
  if (path === '/api/video/manager/history' && method === 'GET')
    return {
      success: true,
      total: 1,
      items: [
        {
          id: 7,
          at: '2026-11-13 09:00:00',
          action: 'rename',
          kind: 'movie',
          item_id: 2,
          title: 'Alien',
          label: 'a.mkv',
          from_path: '/mnt/a/Movies/Alien (1979)/a.mkv',
          to_path: '/mnt/a/Movies/Alien (1979)/Alien (1979).mkv',
          from_name: 'a.mkv',
          to_name: 'Alien (1979).mkv',
          batch: 'b1',
          undone: false,
        },
      ],
    };
  if (path === '/api/video/manager/history/7/undo')
    return { success: true, message: 'Put back as a.mkv' };
  if (path === '/api/video/manager/history/undo-batch')
    return { success: true, undone: 1, failed: [] };
  if (path === '/api/video/manager/views') {
    if (method === 'PUT') {
      server.views = (server.calls.at(-1)?.body as { views: unknown[] }).views;
    }
    return { success: true, views: server.views };
  }
  if (path === '/api/video/manager/episodes/9')
    return {
      success: true,
      seasons: [
        {
          season: 1,
          episodes: [
            {
              id: 31,
              episode: 1,
              title: 'Pilot',
              air_date: '2008-01-20',
              files: [
                {
                  file_id: 81,
                  filename: 'wrong.mkv',
                  size_bytes: 1e9,
                  resolution: '1080',
                  quality: null,
                },
              ],
            },
          ],
        },
        {
          season: 2,
          episodes: [{ id: 32, episode: 1, title: 'Seven', air_date: null, files: [] }],
        },
      ],
    };
  if (path === '/api/video/manager/renumber/9') {
    const body = server.calls.at(-1)?.body as { apply?: boolean };
    return body?.apply
      ? { success: true, moved: 1, not_renamed: 0, batch: 'b2' }
      : {
          success: true,
          files: 1,
          moves: [{ file_id: 81, filename: 'wrong.mkv', from: 'S01E01', to: 'S02E01' }],
        };
  }
  if (path.startsWith('/api/video/detail/'))
    return {
      id: 9,
      title: 'Breaking Bad',
      year: 2008,
      tmdb_id: 1396,
      genres: ['Drama'],
      locked_fields: [],
      series_type: 'standard',
      hands_off: false,
    };
  if (path === '/api/video/library')
    return page([{ id: 5, title: 'Plain Poster', year: 2001, has_poster: false }]);
  return { success: true };
}

beforeEach(() => {
  window.localStorage.clear();
  server = {
    calls: [],
    forbidden: false,
    views: [],
    bulk: {
      running: false,
      done: 2,
      total: 2,
      ok: 2,
      failed: 0,
      changed: 1,
      action: 'rename',
      label: 'Rename',
    },
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method || 'GET').toUpperCase();
      server.calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
      const data = answer(url, method) as { __status?: number };
      const status = data.__status ?? 200;
      return { ok: status < 400, status, json: async () => data } as Response;
    }),
  );
  window.showToast = vi.fn();
  window.showConfirmDialog = vi.fn(async () => true) as never;
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.showToast;
  delete window.showConfirmDialog;
});

const onChanged = vi.fn();

const called = (part: string) => server.calls.filter((c) => c.url.includes(part));
const lastCall = (part: string) => called(part).at(-1);

async function mount() {
  render(<ManagerPage />);
  await screen.findByText('The Matrix');
}

describe('the views', () => {
  it('opens on posters and says what the library adds up to', async () => {
    await mount();
    expect(screen.getByRole('heading', { name: 'Library' })).toBeInTheDocument();
    // the header's one line: how many, how big, how many drives
    expect(document.querySelector('.pgf-status')?.textContent).toBe(
      '2 movies · 12 GB in 3 files on 2 drives',
    );
    expect(document.querySelectorAll('article')).toHaveLength(2);
    // a card is a real link to the title's page
    expect(screen.getByRole('link', { name: /The Matrix/ })).toHaveAttribute(
      'href',
      '/video-detail/library/movie/1',
    );
  });

  it('shows the same titles as a table, with what they are made of', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Titles' }));
    const row = (await screen.findByText('Alien')).closest('tr') as HTMLElement;
    expect(within(row).getByText('720p')).toBeInTheDocument();
    expect(within(row).getByText('Below quality')).toBeInTheDocument();
    expect(within(row).getByText('+1')).toBeInTheDocument();
    const matrix = screen.getByText('The Matrix').closest('tr') as HTMLElement;
    expect(within(matrix).getByText('HEVC · HDR10')).toBeInTheDocument();
    expect(within(matrix).getByText('EN, JA · Atmos')).toBeInTheDocument();
    expect(window.localStorage.getItem('vmgr:view')).toBe('"table"');
  });

  it('sorts by a column on the server, and flips on a second click', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Titles' }));
    await screen.findByText('Alien');
    fireEvent.click(screen.getByRole('button', { name: /^Size/ }));
    await waitFor(() => expect(lastCall('manager/titles')?.url).toContain('sort=size&dir=desc'));
    fireEvent.click(screen.getByRole('button', { name: /^Size/ }));
    await waitFor(() => expect(lastCall('manager/titles')?.url).toContain('sort=size&dir=asc'));
  });

  it('lists the files themselves, and never shows the last view under the new one', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Files' }));
    // the posters are gone at once, before the files arrive
    expect(document.querySelectorAll('article')).toHaveLength(0);
    expect(await screen.findByText('m.mkv')).toBeInTheDocument();
    expect(screen.getByText('2 files')).toBeInTheDocument();
    expect(lastCall('manager/files')?.url).toContain('kind=movies');
  });

  it('a profile the manager is not for browses plain posters', async () => {
    server.forbidden = true;
    render(<ManagerPage />);
    expect(await screen.findByText('Plain Poster')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Titles' })).toBeNull();
    expect(screen.queryByText('Filters')).toBeNull();
  });
});

describe('filters', () => {
  it('a "needs a look" pill is a filter with its count, and only shown when it holds something', async () => {
    await mount();
    const pill = screen.getByRole('button', { name: /Extra copies/ });
    expect(pill.textContent).toBe('Extra copies1');
    expect(screen.queryByRole('button', { name: /Unmatched/ })).toBeNull();
    fireEvent.click(pill);
    await waitFor(() => expect(lastCall('manager/titles')?.url).toContain('needs=copies'));
    await waitFor(() => expect(screen.queryByText('The Matrix')).toBeNull());
    expect(pill).toHaveAttribute('aria-pressed', 'true');
  });

  it('the panel filters by what the files are, and each filter is a chip to remove', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: /Filters/ }));
    fireEvent.change(screen.getByLabelText('Quality'), { target: { value: '4K' } });
    fireEvent.change(screen.getByLabelText('Drive'), { target: { value: '/mnt/b' } });
    await waitFor(() => {
      const url = lastCall('manager/titles')?.url ?? '';
      expect(url).toContain('resolution=4K');
      expect(url).toContain('drive=%2Fmnt%2Fb');
    });
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    const chip = screen.getByRole('button', { name: '4K' });
    fireEvent.click(chip);
    await waitFor(() => expect(lastCall('manager/titles')?.url).not.toContain('resolution='));
  });

  it('search waits for typing to stop', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      await mount();
      const before = called('manager/titles').length;
      fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'ali' } });
      expect(called('manager/titles')).toHaveLength(before);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(300);
      });
      expect(lastCall('manager/titles')?.url).toContain('search=ali');
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('acting on a selection', () => {
  async function pickBoth() {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Titles' }));
    await screen.findByText('Alien');
    fireEvent.click(screen.getByLabelText('Select this page'));
    expect(await screen.findByText('selected')).toBeInTheDocument();
  }

  it('the bar appears with the selection and a plain action runs on it', async () => {
    await pickBoth();
    fireEvent.click(screen.getByRole('button', { name: 'Get missing' }));
    await waitFor(() =>
      expect(lastCall('bulk/start')?.body).toEqual({
        kind: 'movie',
        ids: [1, 2],
        action: 'wish',
        params: { mode: 'missing' },
      }),
    );
  });

  it('Rename shows what will change before it changes anything', async () => {
    await pickBoth();
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    expect(await screen.findByText('Alien (1979).mkv')).toBeInTheDocument();
    expect(called('bulk/start')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Rename 1 file' }));
    await waitFor(() => expect(lastCall('bulk/start')?.body).toMatchObject({ action: 'rename' }));
    await waitFor(() => expect(window.showToast).toHaveBeenCalledWith('1 file renamed', 'success'));
  });

  it('Delete needs the number of titles typed back', async () => {
    await pickBoth();
    fireEvent.click(screen.getByRole('button', { name: /More/ }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete from library' }));
    const confirm = await screen.findByRole('button', { name: 'Delete' });
    await screen.findByText(/This removes/);
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Type 2 to confirm'), { target: { value: '3' } });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Type 2 to confirm'), { target: { value: '2' } });
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(lastCall('bulk/start')?.body).toMatchObject({
        action: 'delete',
        params: { confirm: 2 },
      }),
    );
  });

  it('a quality profile is picked from the ones that exist', async () => {
    await pickBoth();
    fireEvent.click(screen.getByRole('button', { name: /More/ }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Quality profile' }));
    fireEvent.click(await screen.findByRole('radio', { name: '4K' }));
    fireEvent.click(screen.getByRole('button', { name: 'Set profile' }));
    await waitFor(() =>
      expect(lastCall('bulk/start')?.body).toMatchObject({
        action: 'quality_profile',
        params: { profile_id: 3 },
      }),
    );
  });

  it('"Select all" takes every title the filters match, not just the page', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Titles' }));
    await screen.findByText('Alien');
    // the page holds everything here, so the offer is not made
    expect(screen.queryByText(/^Select all/)).toBeNull();
  });

  it('in the files view the bar acts on files, and a delete asks first', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Files' }));
    await screen.findByText('m.mkv');
    fireEvent.click(screen.getByLabelText('Select m.mkv'));
    (window.showConfirmDialog as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(false);
    fireEvent.click(await screen.findByRole('button', { name: 'Delete files' }));
    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(called('/api/video/files/bulk')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Rename to template' }));
    await waitFor(() =>
      expect(lastCall('/api/video/files/bulk')?.body).toEqual({
        action: 'rename',
        file_ids: [11],
        kind: 'movie',
      }),
    );
  });
});

describe('history, undo and the tools beside the list', () => {
  async function pickBoth() {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Titles' }));
    await screen.findByText('Alien');
    fireEvent.click(screen.getByLabelText('Select this page'));
    await screen.findByText('selected');
  }

  it('a run that renamed files leaves a line that undoes the whole run', async () => {
    server.bulk = { ...server.bulk, batch: 'b1' };
    await pickBoth();
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Rename 1 file' }));
    const strip = await screen.findByRole('status');
    expect(strip).toHaveTextContent('1 file renamed');
    fireEvent.click(within(strip).getByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(lastCall('undo-batch')?.body).toEqual({ batch: 'b1' }));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('1 file put back', 'success'),
    );
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
  });

  it('a run that moved no files offers nothing to undo', async () => {
    server.bulk = { ...server.bulk, action: 'monitored', batch: null };
    await pickBoth();
    fireEvent.click(screen.getByRole('button', { name: 'Get missing' }));
    await waitFor(() => expect(window.showToast).toHaveBeenCalled());
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('History lists what was done by hand and undoes one change', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    expect(await screen.findByText('Renamed · Alien')).toBeInTheDocument();
    expect(screen.getByText('Alien (1979).mkv')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(lastCall('history/7/undo')?.method).toBe('POST'));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Put back as a.mkv', 'success'),
    );
  });

  it('Move shows what would move to the picked library before it moves anything', async () => {
    await pickBoth();
    fireEvent.click(screen.getByRole('button', { name: /More/ }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Move to another drive or library' }));
    const run = await screen.findByRole('button', { name: 'Move' });
    expect(run).toBeDisabled();
    fireEvent.click(await screen.findByRole('radio', { name: /Kids/ }));
    expect(await screen.findByText('/mnt/b/Kids/Alien (1979)/a.mkv')).toBeInTheDocument();
    expect(screen.getByText(/1 locked title will be left alone/)).toBeInTheDocument();
    expect(lastCall('move-preview')?.url).toContain('library_id=4');
    expect(called('bulk/start')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Move 2 files' }));
    await waitFor(() =>
      expect(lastCall('bulk/start')?.body).toMatchObject({
        action: 'move',
        params: { library_id: 4 },
      }),
    );
  });

  it('Move offers every drive of a library, and says which one was picked', async () => {
    await pickBoth();
    fireEvent.click(screen.getByRole('button', { name: /More/ }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Move to another drive or library' }));
    // the library is named once, with each of its drives under it
    expect(await screen.findByText('· 3 drives')).toBeInTheDocument();
    expect(screen.getAllByRole('radio')).toHaveLength(4);
    expect(screen.getByText(/New titles land here/)).toBeInTheDocument();
    // the library's own folder is the default: no folder is named for it
    fireEvent.click(screen.getByRole('radio', { name: 'Movies, /mnt/a/Movies' }));
    await waitFor(() => expect(lastCall('move-preview')?.url).toContain('library_id=1'));
    expect(lastCall('move-preview')?.url).not.toContain('folder=');
    const drive = screen.getByRole('radio', { name: 'Movies, /mnt/easystore2/Movies' });
    expect(drive.textContent).toContain('free');
    fireEvent.click(drive);
    await waitFor(() =>
      expect(decodeURIComponent(lastCall('move-preview')?.url ?? '')).toContain(
        'folder=/mnt/easystore2/Movies',
      ),
    );
    expect(drive).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: 'Movies, /mnt/a/Movies' })).toHaveAttribute(
      'aria-checked',
      'false',
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Move 2 files' }));
    await waitFor(() =>
      expect(lastCall('bulk/start')?.body).toMatchObject({
        action: 'move',
        params: { library_id: 1, folder: '/mnt/easystore2/Movies' },
      }),
    );
  });

  it('a view saves what is on screen and brings it back in one click', async () => {
    await mount();
    fireEvent.click(await screen.findByRole('button', { name: /Extra copies/ }));
    await waitFor(() => expect(lastCall('manager/titles')?.url).toContain('needs=copies'));
    fireEvent.click(screen.getByRole('button', { name: /Views/ }));
    fireEvent.change(screen.getByLabelText('View name'), { target: { value: 'Clean up' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(lastCall('manager/views')?.method).toBe('PUT'));
    expect(lastCall('manager/views')?.body).toMatchObject({
      views: [
        {
          name: 'Clean up',
          state: { tab: 'movies', view: 'posters', filters: { needs: ['copies'] } },
        },
      ],
    });
    // drop the filter, then the saved view puts it back
    fireEvent.click(screen.getByRole('button', { name: /Extra copies/ }));
    await waitFor(() => expect(lastCall('manager/titles')?.url).not.toContain('needs='));
    fireEvent.click(await screen.findByRole('menuitem', { name: /Clean up/ }));
    await waitFor(() => expect(lastCall('manager/titles')?.url).toContain('needs=copies'));
  });

  it("the padlock in a title's sheet locks it", async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Titles' }));
    await screen.findByText('Alien');
    fireEvent.click(screen.getAllByTitle('Edit')[0]);
    fireEvent.click(await screen.findByRole('button', { name: 'Lock' }));
    await waitFor(() =>
      expect(lastCall('bulk/start')?.body).toMatchObject({
        action: 'lock',
        params: { value: true },
      }),
    );
    expect(await screen.findByRole('button', { name: 'Locked' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('Episodes files a ticked run under another season after showing the plan', async () => {
    render(<EpisodesTab showId={9} onChanged={onChanged} />);
    fireEvent.click(await screen.findByLabelText(/wrong\.mkv/));
    fireEvent.change(screen.getByLabelText('Season'), { target: { value: '2' } });
    expect(await screen.findByText('S01E01 to S02E01')).toBeInTheDocument();
    expect(lastCall('renumber/9')?.body).toEqual({ file_ids: [81], season: 2, start_episode: 1 });
    fireEvent.click(screen.getByRole('button', { name: 'File 1 file' }));
    await waitFor(() => expect(lastCall('renumber/9')?.body).toMatchObject({ apply: true }));
    await waitFor(() =>
      expect(onChanged).toHaveBeenCalledWith({ batch: 'b2', text: '1 file filed under Season 2' }),
    );
  });
});

describe('the words', () => {
  it('sizes, codecs, languages and paths read the way a person says them', () => {
    expect(formatSize(20.4e12)).toBe('20.4 TB');
    expect(formatSize(1.84e9)).toBe('1.8 GB');
    expect(formatSize(168e6)).toBe('168 MB');
    expect(formatSize(0)).toBe('');
    expect(codecName('hevc')).toBe('HEVC');
    expect(codecName('msmpeg4v3')).toBe('DivX 3');
    expect(codecName('weird')).toBe('WEIRD');
    expect(languageCodes(['en', 'ja', 'fr', 'de', 'it'])).toBe('EN, JA, FR +2');
    expect(languageName('ja')).toBe('Japanese');
    expect(shortPath('/mnt/a/Movies/The Matrix (1999)/')).toBe('…/Movies/The Matrix (1999)');
  });

  it('only filters that are set go to the server, and needs go as one list', () => {
    const params = filterParams({
      ...EMPTY_FILTERS,
      codec: 'hevc',
      needs: ['copies', 'unmatched'],
    });
    expect(params.toString()).toBe('codec=hevc&needs=copies%2Cunmatched');
    expect(countFilters({ ...EMPTY_FILTERS, codec: 'hevc', year_from: '1990', search: 'x' })).toBe(
      2,
    );
  });

  it('a finished job says what it did in its own terms', () => {
    const done = (over: Partial<BulkStatus>): BulkStatus => ({
      running: false,
      done: 3,
      total: 3,
      ok: 3,
      failed: 0,
      ...over,
    });
    expect(describeDone(done({ action: 'rename', changed: 4 }), 'shows')).toBe('4 files renamed');
    expect(describeDone(done({ action: 'wish', changed: 0 }), 'movies')).toBe(
      'Nothing to add: the Wishlist already has them',
    );
    expect(describeDone(done({ action: 'subtitles', changed: 2, failed: 1 }), 'movies')).toBe(
      '2 subtitles queued, 1 could not be done',
    );
    expect(describeDone(done({ action: 'move', changed: 6, held: 2 }), 'movies')).toBe(
      '6 files moved, 2 locked titles left alone',
    );
    expect(describeDone(done({ action: 'monitored', label: 'Monitor · 3 movies' }), 'movies')).toBe(
      'Monitor · 3 movies: 3 movies',
    );
  });

  it('the editor sends only what was changed, since every saved field is locked', () => {
    const before = {
      title: 'Alien',
      sort_title: 'alien',
      year: '1979',
      content_rating: 'R',
      tagline: '',
      overview: 'In space.',
      genres: 'Horror, Science Fiction',
      maker: 'Fox',
    };
    expect(changedFields(before, { ...before }, 'movie')).toEqual({});
    expect(
      changedFields(
        before,
        { ...before, year: '1980', genres: 'Horror', maker: 'Disney' },
        'movie',
      ),
    ).toEqual({ year: '1980', genres: ['Horror'], studio: 'Disney' });
    expect(changedFields(before, { ...before, maker: 'FX' }, 'show')).toEqual({ network: 'FX' });
    // a year cleared is a year unset, and spacing alone is not a change
    expect(changedFields(before, { ...before, year: ' ', title: 'Alien ' }, 'movie')).toEqual({
      year: null,
    });
  });
});
