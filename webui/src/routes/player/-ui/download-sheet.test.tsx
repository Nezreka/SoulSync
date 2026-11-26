/**
 * the download sheet: sizes from the plan, where it goes, the smart part,
 * and this computer's prepare, wait, save. the button's words come from the
 * devices, this computer's jobs and its saved files.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const calls: { method: string; url: string; body?: unknown }[] = [];
const answers: Record<string, unknown[]> = {};

function answer(method: string, url: string) {
  const key = `${method} ${url.split('?')[0]}`;
  const list = answers[key];
  if (!list?.length) return {};
  return list.length > 1 ? list.shift() : list[0];
}

vi.mock('@/app/api-client', () => {
  const run =
    (method: string) =>
    (url: string, opts?: { json?: unknown; searchParams?: Record<string, string> }) => {
      calls.push({ method, url, body: opts?.json ?? opts?.searchParams });
      return { json: async () => answer(method, url) };
    };
  return {
    apiClient: { get: run('GET'), post: run('POST'), put: run('PUT'), delete: run('DELETE') },
    readJson: async (p: { json: () => Promise<unknown> }) => p.json(),
  };
});

const PHONE = {
  device_id: 'pixel',
  device_name: 'Pixel 8',
  platform: 'android',
  last_seen: new Date().toISOString(),
  free_bytes: 6.6e9,
  limit_bytes: 25e9,
  used_bytes: 18.4e9,
  wifi_only: true,
  items: [],
  rules: [],
  wishes: [],
};

const ep = (n: number, onDevice: boolean) => ({
  item_type: 'episode',
  item_id: String(100 + n),
  title: `Episode ${n}`,
  duration_s: 3000,
  size_bytes: 4e9,
  on_device: onDevice,
});

const PLAN = {
  kind: 'video',
  title: 'Severance',
  subtitle: 'Season 2',
  items: [ep(1, true), ep(2, true), ep(3, false), ep(4, false)],
  qualities: [
    {
      id: 'original',
      label: 'Original',
      sub: '4K HDR, as it is on the server',
      total_bytes: 16e9,
      per_item_bytes: {
        'episode:101': 4e9,
        'episode:102': 4e9,
        'episode:103': 4e9,
        'episode:104': 4e9,
      },
    },
    {
      id: 'phone',
      label: 'Phone',
      sub: '720p, good on a phone screen',
      total_bytes: 2.8e9,
      per_item_bytes: {
        'episode:101': 7e8,
        'episode:102': 7e8,
        'episode:103': 7e8,
        'episode:104': 7e8,
      },
    },
  ],
  rule: null,
  free_bytes: 6.6e9,
  limit_bytes: 25e9,
};

function mount(ui: React.ReactNode) {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      {ui}
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  calls.length = 0;
  for (const k of Object.keys(answers)) delete answers[k];
  answers['GET player/offline/devices'] = [{ devices: [PHONE] }];
  answers['GET player/offline/plan'] = [PLAN];
  answers['GET player/offline/settings'] = [
    {
      settings: {},
      is_admin: false,
      allow_downloads: true,
      can_convert: true,
      cache_used_bytes: 0,
    },
  ];
  localStorage.clear();
  const jobs = await import('../-lib/offline-jobs');
  jobs._resetForTests();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('the download sheet', () => {
  it('sends the episodes the phone lacks, sized from the plan, and says if they fit', async () => {
    const { DownloadSheet } = await import('./download-sheet');
    const onClose = vi.fn();
    mount(<DownloadSheet type="season" id="12:2" device="pixel" onClose={onClose} />);
    await waitFor(() => expect(screen.getByText('4K HDR, as it is on the server')).toBeTruthy());
    // two of four are on the phone already: the button counts the other two
    expect(screen.getByRole('button', { name: /Download 2 episodes · 7.5 GB/ })).toBeTruthy();
    expect(screen.getByText(/Needs .* more than the 6.1 GB free on Pixel 8/)).toBeTruthy();
    fireEvent.click(screen.getByRole('radio', { name: /Phone/ }));
    expect(screen.getByRole('button', { name: /Download 2 episodes · 1.3 GB/ })).toBeTruthy();
    expect(screen.getByText(/Fits: 6.1 GB free under your 23.3 GB limit/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Download 2 episodes/ }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const wish = calls.find((c) => c.url === 'player/offline/wishes');
    expect(wish?.body).toEqual({
      device_id: 'pixel',
      action: 'add',
      type: 'season',
      id: '12:2',
      quality: 'phone',
    });
    // no smart part asked for: no rule made
    expect(calls.some((c) => c.url === 'player/offline/rules')).toBe(false);
  });

  it('keep the next few ready on a show is a smart download, not the whole show', async () => {
    const { DownloadSheet } = await import('./download-sheet');
    mount(<DownloadSheet type="show" id="12" device="pixel" onClose={() => {}} />);
    await waitFor(() => screen.getByText('4K HDR, as it is on the server'));
    fireEvent.click(screen.getByRole('switch', { name: /Keep the next 3 unwatched ready/ }));
    fireEvent.click(screen.getByRole('radio', { name: '5' }));
    fireEvent.click(screen.getByRole('switch', { name: /Delete an episode once it's watched/ }));
    fireEvent.click(screen.getByRole('button', { name: /Keep the next 5 ready/ }));
    await waitFor(() => expect(calls.some((c) => c.url === 'player/offline/rules')).toBe(true));
    const rule = calls.find((c) => c.url === 'player/offline/rules');
    expect(rule?.body).toMatchObject({
      type: 'show',
      id: '12',
      keep_next: 5,
      delete_watched: true,
      quality: 'original',
    });
    expect(calls.some((c) => c.url === 'player/offline/wishes')).toBe(false);
  });

  it('this computer: prepares, waits for the conversion, then saves the file', async () => {
    answers['POST player/offline/prepare'] = [{ id: 'a1', status: 'converting', progress: 0.4 }];
    answers['GET player/offline/prepare/a1'] = [
      { id: 'a1', status: 'converting', progress: 0.8 },
      {
        id: 'a1',
        status: 'ready',
        progress: 1,
        url: '/api/player/offline/file/a1',
        size_bytes: 7e8,
        filename: 'Severance [phone].mp4',
      },
    ];
    answers['GET player/offline/plan'] = [{ ...PLAN, items: [ep(3, false)], free_bytes: null }];
    const clicks: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(
      function (this: HTMLAnchorElement) {
        clicks.push(`${this.getAttribute('href')} ${this.download}`);
      },
    );
    const { DownloadSheet } = await import('./download-sheet');
    mount(<DownloadSheet type="episode" id="103" onClose={() => {}} />);
    await waitFor(() => screen.getByText('4K HDR, as it is on the server'));
    // this computer has no smart part
    expect(screen.queryByRole('switch')).toBeNull();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Download/ }));
    });
    await waitFor(() => expect(clicks).toHaveLength(1));
    expect(clicks[0]).toContain('/api/player/offline/file/a1');
    expect(clicks[0]).toContain('Severance [phone].mp4');
    const prep = calls.find((c) => c.url === 'player/offline/prepare');
    expect(prep?.body).toEqual({ item_type: 'episode', item_id: '103', quality: 'original' });
    const { savedFiles } = await import('../-lib/offline');
    await waitFor(() => expect(savedFiles().map((f) => f.item_id)).toEqual(['103']));
    // and the server hears what this computer now holds
    await waitFor(() =>
      expect(calls.some((c) => c.url === 'player/offline/devices/report')).toBe(true),
    );
    const report = calls.find((c) => c.url === 'player/offline/devices/report');
    expect(report?.body).toMatchObject({
      platform: 'web',
      items: [{ item_id: '103', state: 'downloaded' }],
    });
  });
});

describe('the download button', () => {
  it('says where the thing is', async () => {
    const { buttonState } = await import('./download-button');
    const dev = (items: unknown[], wishes: unknown[] = []) =>
      [{ ...PHONE, items, wishes }] as never;
    const item = (state: string, progress = 0) => ({
      item_type: 'episode',
      item_id: '101',
      quality: 'phone',
      size_bytes: 1,
      state,
      progress,
      group: { type: 'season', id: '12:2' },
    });
    expect(buttonState('show', '12', dev([]), [], []).label).toBe('Download');
    expect(buttonState('show', '12', dev([item('downloading', 0.6)]), [], []).label).toBe('60%');
    expect(buttonState('season', '12:2', dev([item('queued')]), [], []).label).toBe('Waiting');
    expect(buttonState('show', '12', dev([item('downloaded')]), [], []).label).toBe('On Pixel 8');
    expect(buttonState('episode', '101', dev([item('downloaded')]), [], []).tone).toBe('on');
    expect(
      buttonState(
        'movie',
        '5',
        dev([], [{ wish_id: 1, action: 'add', type: 'movie', id: '5' }]),
        [],
        [],
      ).label,
    ).toBe('Waiting for Pixel 8');
    expect(buttonState('movie', '5', [], [], [{ item_type: 'movie', item_id: '5' }]).label).toBe(
      'Downloaded',
    );
  });

  it('is hidden for a profile whose downloads are off', async () => {
    answers['GET player/offline/settings'] = [
      {
        settings: {},
        is_admin: false,
        allow_downloads: false,
        can_convert: false,
        cache_used_bytes: 0,
      },
    ];
    const { DownloadButton } = await import('./download-button');
    const { container } = mount(<DownloadButton type="movie" id="5" title="Dune" />);
    await waitFor(() => expect(container.querySelector('button')).toBeNull());
  });

  it('with nothing to download it shows greyed, saying why', async () => {
    const { DownloadButton } = await import('./download-button');
    mount(
      <DownloadButton type="playlist" id="mirror:4" title="Mix" why="None of these are here." />,
    );
    const b = screen.getByRole('button', { name: 'Download: None of these are here.' });
    expect((b as HTMLButtonElement).disabled).toBe(true);
  });
});

describe('a playlist to this computer', () => {
  it('saves each song filed under the playlist', async () => {
    const track = (n: number) => ({
      item_type: 'track',
      item_id: String(n),
      title: `Song ${n}`,
      duration_s: 200,
      size_bytes: 3e7,
    });
    answers['GET player/offline/plan'] = [
      {
        kind: 'audio',
        title: 'Road Trip',
        subtitle: 'Playlist',
        artwork: null,
        items: [track(1), track(2)],
        qualities: [
          {
            id: 'original',
            label: 'Original',
            total_bytes: 6e7,
            per_item_bytes: { 'track:1': 3e7, 'track:2': 3e7 },
          },
        ],
        free_bytes: null,
      },
    ];
    let n = 0;
    answers['POST player/offline/prepare'] = [
      {
        id: 'p1',
        status: 'ready',
        progress: 1,
        url: '/api/player/offline/file/p1',
        size_bytes: 3e7,
      },
    ];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {
      n += 1;
    });
    const { DownloadSheet } = await import('./download-sheet');
    mount(<DownloadSheet type="playlist" id="user:4" onClose={() => {}} />);
    await waitFor(() => screen.getByRole('button', { name: /Download 2 songs/ }));
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Download 2 songs/ }));
    });
    await waitFor(() => expect(n).toBe(2));
    expect(calls.find((c) => c.url === 'player/offline/plan')?.body).toMatchObject({
      type: 'playlist',
      id: 'user:4',
    });
    const { savedFiles } = await import('../-lib/offline');
    await waitFor(() => expect(savedFiles()).toHaveLength(2));
    expect(savedFiles().map((f) => f.group)).toEqual([
      { type: 'playlist', id: 'user:4', title: 'Road Trip', subtitle: 'Playlist', artwork: null },
      { type: 'playlist', id: 'user:4', title: 'Road Trip', subtitle: 'Playlist', artwork: null },
    ]);
  });
});
