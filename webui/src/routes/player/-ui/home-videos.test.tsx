/**
 * home videos (scrap/HOME_VIDEOS_CONTRACT.md): the page reads its overview,
 * each month reads its own clips, a tap plays that month on from the clip,
 * and the edit sheet sends only what was changed (null puts a field back
 * to automatic).
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  countLine,
  homeQueue,
  monthLabel,
  sourcesLine,
  spanLabel,
  yearsAgo,
  type HomeVideo,
} from '../-lib/home-videos';

const play = vi.fn();
const nav = vi.fn();
const puts: { url: string; json: unknown }[] = [];
const asked: string[] = [];

const clip = (id: number, filmed: string, extra: Partial<HomeVideo> = {}): HomeVideo => ({
  id,
  title: `Clip ${id}`,
  filmed_at: filmed,
  duration_s: 125,
  width: 1920,
  height: 1080,
  album: 'Holidays',
  album_key: 'abc123',
  thumb_url: `/api/player/home/thumb/${id}?v=100`,
  progress: { position_s: 0, watched: false },
  missing: false,
  ...extra,
});

const overview = {
  success: true,
  library: { id: 7, name: 'Home videos', in_guide: false },
  counts: { videos: 412, first_year: 2009, last_year: 2026 },
  on_this_day: [clip(1, '2023-11-25T15:00:00', { title: 'Thanksgiving at Grandma’s' })],
  albums: [
    {
      key: 'abc123',
      name: 'Holidays',
      count: 86,
      first: '2010-12-24',
      last: '2025-12-25',
      covers: [1, 2, 3],
    },
    {
      key: 'def456',
      name: 'Beach trip 2024',
      count: 23,
      first: '2024-07-01',
      last: '2024-07-09',
      covers: [4],
    },
  ],
  months: [
    { month: '2026-11', count: 2 },
    { month: '2026-10', count: 1 },
  ],
  sources: ['Home Videos', 'family'],
  can_edit: true,
  scanning: false,
};

const pages: Record<string, unknown> = {
  '2026-11': {
    videos: [
      clip(10, '2026-11-22T09:00:00', { progress: { position_s: 50, watched: false } }),
      clip(11, '2026-11-20T09:00:00'),
    ],
    total: 2,
  },
  '2026-10': {
    videos: [clip(12, '2026-10-31T18:30:12', { title: 'Oct 31, 2026, 6:30 PM' })],
    total: 1,
  },
};

const detail = {
  ...clip(12, '2026-10-31T18:30:12', { title: 'Oct 31, 2026, 6:30 PM' }),
  file_name: 'VID_20261031_183012.mp4',
  auto_title: 'Oct 31, 2026, 6:30 PM',
  auto_filmed_at: '2026-10-31T18:30:12',
  filmed_source: 'camera',
  notes: '',
  thumb_at_s: 12.5,
  edited: { title: false, filmed_at: false, thumb: false, notes: false },
  frames: [12.5, 37.5, 62.5, 87.5].map((at) => ({
    at_s: at,
    url: `/api/player/home/frame/12?at=${at}`,
  })),
  can_edit: true,
};

vi.mock('@/app/api-client', () => ({
  refusalText: async (_e: unknown, fallback: string) => fallback,
  apiClient: {
    get: (url: string, opts?: { searchParams?: Record<string, string | number> }) => ({
      json: async () => {
        const q = opts?.searchParams ?? {};
        asked.push(`${url}?${new URLSearchParams(q as Record<string, string>).toString()}`);
        if (url === 'player/home/overview') return overview;
        if (url === 'player/home/videos') return pages[String(q.month)] ?? { videos: [], total: 0 };
        if (url.startsWith('player/home/video/')) return detail;
        return {};
      },
    }),
    put: (url: string, opts: { json: unknown }) => {
      puts.push({ url, json: opts.json });
      return { json: async () => ({ ...detail, ...(opts.json as object) }) };
    },
  },
}));
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => nav,
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));
vi.mock('./playback/use-player', () => ({ playItems: (...a: unknown[]) => play(...a) }));
vi.mock('./download-button', () => ({ DownloadButton: () => null }));
vi.mock('./sections', () => ({ useSections: () => ({ video: true, isAdmin: true }) }));
vi.mock('./player-setup', () => ({ openPlayerSetup: vi.fn() }));

afterEach(() => {
  play.mockReset();
  nav.mockReset();
  puts.length = 0;
  asked.length = 0;
});

async function mount() {
  const { HomeVideosPage } = await import('./home-videos');
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <HomeVideosPage library={7} />
    </QueryClientProvider>,
  );
  // the skeleton has the same heading: wait for the overview itself
  await screen.findByText(/^412 videos/);
}

describe('the home videos page', () => {
  it('says how many, from where, and lays out this day, the albums and the months', async () => {
    await mount();
    expect(
      screen.getByText('412 videos · 2009 – 2026 · from “Home Videos” and “family”'),
    ).toBeTruthy();
    const today = screen.getByRole('region', { name: 'On this day' });
    expect(within(today).getByText('Thanksgiving at Grandma’s')).toBeTruthy();
    const folders = screen.getByRole('region', { name: 'Folders' });
    expect(within(folders).getByText('86 videos · 2010 – 2025')).toBeTruthy();
    expect(within(folders).getByText('23 videos · Jul 2024')).toBeTruthy();
    expect(screen.getByRole('region', { name: 'November 2026' })).toBeTruthy();
    // each month reads its own clips
    await waitFor(() => expect(screen.getByRole('button', { name: /^Clip 10,/ })).toBeTruthy());
    expect(asked.some((a) => a.includes('month=2026-10'))).toBe(true);
    // the day and the length on the clip, and how far you got
    const card = screen.getByRole('button', { name: /^Clip 10,/ });
    expect(card.textContent).toContain('Nov 22');
    expect(card.textContent).toContain('2:05');
  });

  it('a tap plays that month on from the clip', async () => {
    await mount();
    fireEvent.click(await screen.findByRole('button', { name: /^Clip 11,/ }));
    expect(play).toHaveBeenCalledTimes(1);
    const items = play.mock.calls[0][0] as { type: string; id: string }[];
    expect(items.map((i) => [i.type, i.id])).toEqual([['home', '11']]);
  });

  it('an album opens its own page; Folders shows them all', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Beach trip 2024, 23 videos' }));
    expect(nav).toHaveBeenCalledWith({
      to: '/player/home-videos',
      search: { library: 7, album: 'def456' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Folders' }));
    expect(nav).toHaveBeenLastCalledWith({
      to: '/player/home-videos',
      search: { library: 7, view: 'folders' },
    });
  });
});

describe('the edit sheet', () => {
  it('shows where the name and date came from and sends only what changed', async () => {
    await mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Oct 31, 2026, 6:30 PM' }));
    const sheet = await screen.findByRole('dialog', { name: 'Edit video' });
    await waitFor(() =>
      expect(within(sheet).getByText(/Was “VID_20261031_183012.mp4”/)).toBeTruthy(),
    );
    expect(within(sheet).getByText(/Read from the video itself/)).toBeTruthy();
    // the picture now is the first frame
    const frames = within(sheet).getAllByRole('button', { name: /The frame at/ });
    expect(frames.map((f) => f.getAttribute('aria-pressed'))).toEqual([
      'true',
      'false',
      'false',
      'false',
    ]);
    fireEvent.change(within(sheet).getByLabelText('Name'), {
      target: { value: 'Trick or treating' },
    });
    fireEvent.click(frames[2]);
    fireEvent.change(within(sheet).getByLabelText('Notes'), {
      target: { value: 'Max as a dinosaur' },
    });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toEqual({
      url: 'player/home/video/12',
      json: { title: 'Trick or treating', notes: 'Max as a dinosaur', thumb_at_s: 62.5 },
    });
  });

  it('a right-click on a clip opens it too', async () => {
    await mount();
    fireEvent.contextMenu(await screen.findByRole('button', { name: /^Clip 11,/ }));
    expect(await screen.findByRole('dialog', { name: 'Edit video' })).toBeTruthy();
  });
});

describe('home video words', () => {
  it('reads dates the way people say them', () => {
    expect(monthLabel('2026-11')).toBe('November 2026');
    expect(spanLabel('2010-01-01', '2025-05-05')).toBe('2010 – 2025');
    expect(spanLabel('2024-07-01', '2024-07-09')).toBe('Jul 2024');
    expect(spanLabel('2024-03-01', '2024-07-09')).toBe('2024');
    expect(yearsAgo('2023-11-25', new Date(2026, 10, 25))).toBe('3 years ago');
    expect(yearsAgo('2025-11-25', new Date(2026, 10, 25))).toBe('A year ago');
    expect(countLine({ counts: { videos: 1, first_year: 2026, last_year: 2026 } })).toBe(
      '1 video · 2026',
    );
    expect(sourcesLine(['A'])).toBe('from “A”');
    expect(sourcesLine([])).toBe('');
  });

  it('a missing clip is skipped in the queue', () => {
    const list = [
      clip(1, '2026-01-01'),
      clip(2, '2026-01-02', { missing: true }),
      clip(3, '2026-01-03'),
    ];
    expect(homeQueue(list, 0, 7).map((i) => i.id)).toEqual(['1', '3']);
    expect(homeQueue(list, 2, 7).map((i) => i.id)).toEqual(['3']);
  });
});
