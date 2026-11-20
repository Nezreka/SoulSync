import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import { StarterBanner } from './starter-banner';

/**
 * The top of Discover for a new profile: shown while the server says the
 * profile is new, gone once dismissed, and the picker only saves once five
 * artists are picked.
 */

const ARTISTS = Array.from({ length: 8 }, (_, i) => ({
  name: `Artist ${i + 1}`,
  deezer_id: String(i + 1),
  image_url: `https://img/${i + 1}.jpg`,
}));

let posts: { url: string; body: unknown }[] = [];

function mount(status: Record<string, unknown>) {
  // like the server: once picks are saved, the status reports the build
  let building: Record<string, unknown> | null = null;
  server.use(
    http.get('/api/discover/starter', () =>
      HttpResponse.json({
        success: true,
        seeds: 1,
        threshold: 5,
        needs_seeds: true,
        dismissed: false,
        show_banner: true,
        picked: [],
        connections: { listenbrainz: false, lastfm: true },
        spotify_import: { available: false, href: null },
        build: { state: 'idle', done: 0, total: 0, stored: 0 },
        ...status,
        ...(building ? { build: building } : {}),
      }),
    ),
    http.get('/api/discover/starter/artists', () =>
      HttpResponse.json({
        success: true,
        artists: ARTISTS,
        genres: [
          { id: 0, name: 'Popular now' },
          { id: 132, name: 'Pop' },
        ],
      }),
    ),
    http.post('/api/discover/starter/dismiss', async ({ request }) => {
      posts.push({ url: request.url, body: null });
      return HttpResponse.json({ success: true });
    }),
    http.post('/api/discover/starter/picks', async ({ request }) => {
      posts.push({ url: request.url, body: await request.json() });
      building = { state: 'running', done: 0, total: 5, stored: 0 };
      return HttpResponse.json({
        success: true,
        build: { state: 'running', done: 0, total: 5, stored: 0 },
      });
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <StarterBanner />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  posts = [];
  window.showToast = vi.fn();
  window.openMyAccountsModal = vi.fn();
});
afterEach(cleanup);

describe('the starter banner', () => {
  it('offers the ways to make discover yours, minus a connected account', async () => {
    mount({});
    expect(await screen.findByText('Tell SoulSync what you love')).toBeTruthy();
    expect(screen.getByText('Pick artists you love')).toBeTruthy();
    expect(screen.getByText('Connect ListenBrainz')).toBeTruthy();
    expect(screen.queryByText('Connect Last.fm')).toBeNull();
    expect(screen.queryByText('Import Spotify history')).toBeNull();
    expect(screen.getByText('1 of 5 artists known')).toBeTruthy();
  });

  it('opens my accounts for listenbrainz', async () => {
    mount({});
    fireEvent.click(await screen.findByText('Connect ListenBrainz'));
    expect(window.openMyAccountsModal).toHaveBeenCalled();
  });

  it('renders nothing once the profile has enough history', async () => {
    const { container } = mount({ show_banner: false, needs_seeds: false, seeds: 9 });
    await new Promise((r) => setTimeout(r, 30));
    expect(container.querySelector('.dsc-starter')).toBeNull();
  });

  it('goes away when dismissed and tells the server', async () => {
    const { container } = mount({});
    fireEvent.click(await screen.findByLabelText('Hide this'));
    await waitFor(() =>
      expect(posts.map((p) => p.url)).toContain(
        'http://localhost:3000/api/discover/starter/dismiss',
      ),
    );
    expect(container.querySelector('.dsc-starter')).toBeNull();
  });

  it('shows the build while the first picks are being made, even past the threshold', async () => {
    mount({
      show_banner: false,
      needs_seeds: false,
      build: { state: 'running', done: 2, total: 5, stored: 10 },
    });
    expect(await screen.findByText('Your first picks are on the way')).toBeTruthy();
    expect(screen.getByText('Building your first picks · 2 of 5 artists')).toBeTruthy();
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('2');
  });
});

describe('the artist picker', () => {
  it('saves only once five artists are picked, then starts the build', async () => {
    mount({});
    fireEvent.click(await screen.findByText('Pick artists you love'));
    expect(await screen.findByRole('dialog', { name: 'Pick artists you love' })).toBeTruthy();
    const save = screen.getByRole('button', { name: 'Build my Discover' }) as HTMLButtonElement;
    for (let i = 1; i <= 4; i++) fireEvent.click(await screen.findByText(`Artist ${i}`));
    expect(screen.getByText('4 of 5 · 1 more to go')).toBeTruthy();
    expect(save.disabled).toBe(true);
    fireEvent.click(screen.getByText('Artist 5'));
    expect(screen.getByText('5 picked')).toBeTruthy();
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(posts.some((p) => p.url.endsWith('/starter/picks'))).toBe(true));
    const sent = posts.find((p) => p.url.endsWith('/starter/picks'))?.body as {
      artists: { name: string }[];
    };
    expect(sent.artists.map((a) => a.name)).toEqual([
      'Artist 1',
      'Artist 2',
      'Artist 3',
      'Artist 4',
      'Artist 5',
    ]);
    expect(await screen.findByText('Your first picks are on the way')).toBeTruthy();
  });

  it('counts artists picked before toward the five and will not pick them twice', async () => {
    mount({ picked: ARTISTS.slice(0, 3) });
    fireEvent.click(await screen.findByText('Pick artists you love'));
    const already = (await screen.findByText('Artist 1')).closest('button') as HTMLButtonElement;
    expect(already.disabled).toBe(true);
    fireEvent.click(screen.getByText('Artist 4'));
    fireEvent.click(screen.getByText('Artist 5'));
    expect(screen.getByText('5 picked')).toBeTruthy();
  });

  it('a second click unpicks', async () => {
    mount({});
    fireEvent.click(await screen.findByText('Pick artists you love'));
    const tile = (await screen.findByText('Artist 2')).closest('button') as HTMLButtonElement;
    fireEvent.click(tile);
    expect(tile.getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(tile);
    expect(tile.getAttribute('aria-pressed')).toBe('false');
  });
});
