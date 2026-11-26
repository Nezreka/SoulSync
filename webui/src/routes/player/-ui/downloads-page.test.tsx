/**
 * the downloads page: a device's things in groups with their state, remove
 * all asks first, a cancel is a remove wish, and this computer lists what
 * this browser saved.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const calls: { method: string; url: string; body?: unknown }[] = [];
let devices: unknown[] = [];

vi.mock('@/app/api-client', () => {
  const run = (method: string) => (url: string, opts?: { json?: unknown }) => {
    calls.push({ method, url, body: opts?.json });
    const body =
      url === 'player/offline/devices'
        ? { devices }
        : url === 'player/offline/plan'
          ? { kind: 'video', title: 'x', items: [], qualities: [] }
          : { wishes: [] };
    return { json: async () => body };
  };
  return {
    apiClient: { get: run('GET'), post: run('POST'), put: run('PUT'), delete: run('DELETE') },
  };
});
vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (opts: Record<string, unknown>) => ({ options: opts }),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));

const season = { type: 'season', id: '12:2', title: 'Severance', subtitle: 'Season 2' };
const ep = (n: number, state: string, extra: Record<string, unknown> = {}) => ({
  item_type: 'episode',
  item_id: String(100 + n),
  quality: 'phone',
  size_bytes: 7e8,
  state,
  progress: state === 'downloading' ? 0.6 : 1,
  title: `S2 E${n} · Episode ${n}`,
  kind: 'video',
  group: season,
  ...extra,
});

const PIXEL = {
  device_id: 'pixel',
  device_name: 'Pixel 8',
  platform: 'android',
  last_seen: new Date(Date.now() - 2 * 3600_000).toISOString(),
  free_bytes: 6.6e9,
  limit_bytes: 25e9,
  used_bytes: 18.4e9,
  wifi_only: true,
  items: [
    ep(3, 'downloaded', { watched: true }),
    ep(4, 'downloaded'),
    ep(5, 'downloading'),
    {
      item_type: 'movie',
      item_id: '7',
      quality: 'high',
      size_bytes: 3.9e9,
      state: 'downloaded',
      title: 'Dune: Part Two',
      kind: 'video',
      position_s: 2400,
      duration_s: 6000,
      group: { type: 'movie', id: '7', title: 'Dune: Part Two' },
    },
  ],
  rules: [
    {
      rule_id: 9,
      type: 'show',
      id: '12',
      quality: 'phone',
      keep_next: 3,
      delete_watched: true,
      title: 'Severance',
    },
  ],
  wishes: [],
};

async function mount() {
  const { Route } = await import('../downloads');
  const Page = (Route as unknown as { options: { component: () => React.ReactNode } }).options
    .component;
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <Page />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  calls.length = 0;
  devices = [PIXEL];
  localStorage.clear();
});

describe('the downloads page', () => {
  it("groups a phone's things with their state and the smart rule", async () => {
    await mount();
    await waitFor(() => expect(screen.getByRole('region', { name: 'Severance' })).toBeTruthy());
    const show = screen.getByRole('region', { name: 'Severance' });
    expect(within(show).getByText(/2 episodes on Pixel 8/)).toBeTruthy();
    expect(within(show).getByText(/Smart · next 3 unwatched · deletes watched/)).toBeTruthy();
    expect(within(show).getByText('Season 2')).toBeTruthy();
    expect(within(show).getByText('Watched')).toBeTruthy();
    expect(within(show).getByText('On the phone')).toBeTruthy();
    expect(within(show).getByRole('button', { name: 'Add the rest of season 2' })).toBeTruthy();
    // what's still coming sits on top, not in its show
    const coming = screen.getByRole('region', { name: 'Downloading' });
    expect(within(coming).getByText('Downloading 60%')).toBeTruthy();
    expect(within(show).queryByText('Downloading 60%')).toBeNull();
    // filed by kind, tv shows first
    const kinds = screen.getAllByRole('button', { expanded: true }).map((b) => b.textContent);
    expect(kinds[0]).toMatch(/^TV shows2 episodes/);
    expect(kinds[1]).toMatch(/^Movies1 movie/);
    const dune = screen.getByRole('region', { name: 'Dune: Part Two' });
    expect(within(dune).getByText(/1080p · 3.6 GB · watched 40%/)).toBeTruthy();
    // the switcher: the phone with its room and when it was seen, and this computer
    const phone = screen.getByRole('radio', { name: /Pixel 8/ });
    expect(phone.textContent).toContain('seen 2 h ago');
    expect(screen.getByRole('radio', { name: /This computer/ })).toBeTruthy();
  });

  it('remove all asks first, then sends a remove for the show', async () => {
    await mount();
    await waitFor(() => screen.getByRole('region', { name: 'Severance' }));
    const show = screen.getByRole('region', { name: 'Severance' });
    fireEvent.click(within(show).getByRole('button', { name: 'Remove all' }));
    expect(calls.some((c) => c.url === 'player/offline/wishes')).toBe(false);
    const dialog = screen.getByRole('dialog', { name: 'Remove Severance?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove all' }));
    await waitFor(() => expect(calls.some((c) => c.url === 'player/offline/wishes')).toBe(true));
    expect(calls.find((c) => c.url === 'player/offline/wishes')?.body).toEqual({
      device_id: 'pixel',
      action: 'remove',
      type: 'show',
      id: '12',
    });
  });

  it('a download in progress is cancelled with a remove wish for that item', async () => {
    await mount();
    await waitFor(() => screen.getByText('Downloading 60%'));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() =>
      expect(calls.find((c) => c.url === 'player/offline/wishes')?.body).toMatchObject({
        action: 'remove',
        type: 'episode',
        id: '105',
      }),
    );
  });

  it('this computer lists what this browser saved', async () => {
    localStorage.setItem(
      'ss-offline-saved',
      JSON.stringify([
        {
          item_type: 'movie',
          item_id: '7',
          quality: 'original',
          size_bytes: 2e10,
          title: 'Dune: Part Two',
          saved_at: '2026-11-20T10:00:00Z',
        },
      ]),
    );
    devices = [];
    await mount();
    await waitFor(() => screen.getByRole('region', { name: 'Movies' }));
    expect(screen.getByText('Dune: Part Two', { selector: 'b' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Download again' })).toBeTruthy();
    expect(screen.getByText(/Get the SoulSync app on your phone/)).toBeTruthy();
  });

  it('files music by album and playlist, loose songs together, and sorts newest or a to z', async () => {
    const track = (n: number, group: Record<string, unknown> | null, at: string) => ({
      item_type: 'track',
      item_id: String(n),
      quality: '256',
      size_bytes: 8e6,
      state: 'downloaded',
      title: `Song ${n}`,
      subtitle: 'Band',
      kind: 'audio',
      group,
      downloaded_at: at,
    });
    devices = [
      {
        ...PIXEL,
        items: [
          ...PIXEL.items,
          track(1, { type: 'playlist', id: 'user:4', title: 'Road Trip' }, '2026-11-20T10:00:00Z'),
          track(2, { type: 'playlist', id: 'user:4', title: 'Road Trip' }, '2026-11-20T10:00:00Z'),
          track(3, { type: 'album', id: '9', title: 'Abbey Road' }, '2026-10-01T10:00:00Z'),
          track(4, { type: 'track', id: '4', title: 'Song 4' }, '2026-11-01T10:00:00Z'),
          track(5, null, '2026-11-02T10:00:00Z'),
        ],
      },
    ];
    await mount();
    const music = await waitFor(() => screen.getByRole('region', { name: 'Music' }));
    const cards = () =>
      within(music)
        .getAllByRole('region')
        .map((r) => r.getAttribute('aria-label'));
    // newest download first
    expect(cards()).toEqual(['Road Trip', 'Songs', 'Abbey Road']);
    expect(within(music).getByText(/^Playlist · 2 songs on Pixel 8/)).toBeTruthy();
    fireEvent.click(screen.getByRole('radio', { name: 'A-Z' }));
    expect(cards()).toEqual(['Abbey Road', 'Road Trip', 'Songs']);
    // loose songs go one by one
    const songs = within(music).getByRole('region', { name: 'Songs' });
    fireEvent.click(within(songs).getByRole('button', { name: 'Remove all' }));
    fireEvent.click(
      within(screen.getByRole('dialog', { name: 'Remove Songs?' })).getByRole('button', {
        name: 'Remove all',
      }),
    );
    await waitFor(() =>
      expect(calls.filter((c) => c.url === 'player/offline/wishes').map((c) => c.body)).toEqual([
        { device_id: 'pixel', action: 'remove', type: 'track', id: '5' },
        { device_id: 'pixel', action: 'remove', type: 'track', id: '4' },
      ]),
    );
  });

  it('a section folds away and stays folded; a kind chip shows just that kind', async () => {
    await mount();
    const head = await waitFor(() => screen.getByRole('button', { name: /^Movies/ }));
    expect(screen.getByRole('region', { name: 'Dune: Part Two' })).toBeTruthy();
    fireEvent.click(head);
    expect(head.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('region', { name: 'Dune: Part Two' })).toBeNull();
    expect(JSON.parse(localStorage.getItem('ss-downloads-view') ?? '{}').folded).toEqual([
      'movies',
    ]);
    fireEvent.click(screen.getByRole('radio', { name: 'TV shows' }));
    expect(screen.queryByRole('region', { name: 'Movies' })).toBeNull();
    expect(screen.getByRole('region', { name: 'Severance' })).toBeTruthy();
    // picking movies opens it, folded or not
    fireEvent.click(screen.getByRole('radio', { name: 'Movies' }));
    expect(screen.getByRole('region', { name: 'Dune: Part Two' })).toBeTruthy();
  });

  it('this computer files what it saved by its season, album or playlist', async () => {
    const f = (n: number, group: Record<string, unknown>) => ({
      item_type: 'track',
      item_id: String(n),
      quality: 'original',
      size_bytes: 3e7,
      title: `Song ${n}`,
      saved_at: `2026-11-2${n}T10:00:00Z`,
      group,
    });
    localStorage.setItem(
      'ss-offline-saved',
      JSON.stringify([
        f(1, { type: 'playlist', id: 'mirror:8', title: 'Night Drive' }),
        f(2, { type: 'playlist', id: 'mirror:8', title: 'Night Drive' }),
        {
          item_type: 'episode',
          item_id: '101',
          quality: 'phone',
          size_bytes: 7e8,
          title: 'S1 E1 · Pilot',
          saved_at: '2026-11-20T10:00:00Z',
          group: { type: 'season', id: '12:1', title: 'Severance', subtitle: 'Season 1' },
        },
      ]),
    );
    devices = [];
    await mount();
    const music = await waitFor(() => screen.getByRole('region', { name: 'Music' }));
    const list = within(music).getByRole('region', { name: 'Night Drive' });
    expect(within(list).getByText(/Playlist · 2 files/)).toBeTruthy();
    const tv = screen.getByRole('region', { name: 'TV shows' });
    expect(within(tv).getByRole('region', { name: 'Severance' })).toBeTruthy();
    expect(within(tv).getByText('Season 1')).toBeTruthy();
  });
});
