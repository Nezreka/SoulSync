/**
 * the More menu on a song row: the row keeps the heart and a ⋯, the ⋯ (and
 * right-click) opens one menu that the keyboard can drive, and each of its
 * items does what the old inline buttons did.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { TrackRow } from './music/track-list';

const engine = { append: vi.fn(), playNext: vi.fn() };
let playing = false;
const addToPlaylist = vi.fn();
const itemInfo = vi.fn();

vi.mock('@/app/api-client', () => ({
  refusalText: async (_e: unknown, fallback: string) => fallback,
  readJson: async (p: { json: () => Promise<unknown> }) => p.json(),
  apiClient: {
    get: () => ({ json: async () => ({}) }),
    post: () => ({ json: async () => ({}) }),
  },
}));
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));
vi.mock('./playback/use-player', () => ({
  getEngine: () => engine,
  playItems: vi.fn(),
  usePlayer: () => (playing ? { item: { type: 'track', id: '99' }, status: 'playing' } : {}),
}));
vi.mock('./music/add-to-playlist', () => ({
  openAddToPlaylist: (...a: unknown[]) => addToPlaylist(...a),
}));
vi.mock('./item-info', () => ({ openItemInfo: (...a: unknown[]) => itemInfo(...a) }));
vi.mock('./download-sheet', () => ({
  DownloadSheet: ({ type, id }: { type: string; id: string }) => (
    <div role="dialog" aria-label={`Download ${type} ${id}`} />
  ),
}));

const ROWS: TrackRow[] = [
  {
    key: 'a',
    title: 'A Very Long Song Title That Should Get The Room',
    trackId: 7,
    artist: 'Band',
    duration: 200,
    song: { title: 'A Very Long Song Title That Should Get The Room', artist: 'Band' },
  },
  { key: 'b', title: 'Missing One', trackId: null, artist: 'Band', onMissingAction: vi.fn() },
];

async function mount(onPlay = vi.fn()) {
  const { TrackList } = await import('./music/track-list');
  const { ContextMenuHost } = await import('./context-menu');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TrackList rows={ROWS} onPlay={onPlay} />
      <ContextMenuHost />
    </QueryClientProvider>,
  );
  return onPlay;
}

const more = () => screen.getByRole('button', { name: /^More for A Very Long/ });
const menu = () => screen.getByRole('menu');
const item = (name: string | RegExp) => within(menu()).getByRole('menuitem', { name });

afterEach(async () => {
  const { closeContextMenu } = await import('./context-menu');
  act(() => closeContextMenu());
  vi.clearAllMocks();
  playing = false;
});

describe('a song row', () => {
  it('keeps only the heart and the ⋯ inline (and Request on a missing song)', async () => {
    await mount();
    const row = screen.getAllByRole('listitem')[0];
    const buttons = within(row)
      .getAllByRole('button')
      .map((b) => b.getAttribute('aria-label'));
    expect(buttons).toEqual([
      'Play A Very Long Song Title That Should Get The Room',
      'Like A Very Long Song Title That Should Get The Room',
      'More for A Very Long Song Title That Should Get The Room',
    ]);
    const missing = screen.getAllByRole('listitem')[1];
    expect(within(missing).getByRole('button', { name: 'Request' })).toBeTruthy();
    expect(within(missing).queryByRole('button', { name: /^More for/ })).toBeNull();
  });
});

describe('the More menu', () => {
  it('opens from the ⋯, focuses its first item and says it is open', async () => {
    await mount();
    fireEvent.click(more());
    expect(more().getAttribute('aria-expanded')).toBe('true');
    const items = within(menu()).getAllByRole('menuitem');
    expect(items.map((b) => b.textContent)).toEqual([
      'Play now',
      'Add to queue',
      'Add to Liked Songs',
      'Add to playlist',
      'Download',
      'Info',
      'Report an issue',
    ]);
    expect(document.activeElement).toBe(items[0]);
  });

  it('moves with the arrows, home and end, and escape closes it back onto the ⋯', async () => {
    await mount();
    more().focus();
    fireEvent.click(more());
    const items = within(menu()).getAllByRole('menuitem');
    fireEvent.keyDown(menu(), { key: 'ArrowDown' });
    expect(document.activeElement).toBe(items[1]);
    fireEvent.keyDown(menu(), { key: 'ArrowUp' });
    fireEvent.keyDown(menu(), { key: 'ArrowUp' });
    expect(document.activeElement).toBe(items[items.length - 1]);
    fireEvent.keyDown(menu(), { key: 'Home' });
    expect(document.activeElement).toBe(items[0]);
    fireEvent.keyDown(menu(), { key: 'End' });
    expect(document.activeElement).toBe(items[items.length - 1]);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).toBe(more());
  });

  it('closes on a press outside, on scroll, and on a second press of the ⋯', async () => {
    await mount();
    fireEvent.click(more());
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole('menu')).toBeNull();
    fireEvent.click(more());
    fireEvent.scroll(window);
    expect(screen.queryByRole('menu')).toBeNull();
    fireEvent.click(more());
    fireEvent.mouseDown(more());
    fireEvent.click(more());
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('enter on the ⋯ does not play the row', async () => {
    const onPlay = await mount();
    fireEvent.keyDown(more(), { key: 'Enter' });
    expect(onPlay).not.toHaveBeenCalled();
  });

  it('right-click on the row opens the same menu', async () => {
    await mount();
    fireEvent.contextMenu(screen.getAllByRole('listitem')[0], { clientX: 40, clientY: 50 });
    expect(menu().getAttribute('aria-label')).toBe(
      'Actions for A Very Long Song Title That Should Get The Room',
    );
    expect(item('Report an issue')).toBeTruthy();
  });

  it('offers play next while something plays', async () => {
    playing = true;
    await mount();
    fireEvent.click(more());
    fireEvent.click(item('Play next'));
    expect(engine.playNext).toHaveBeenCalledWith([
      expect.objectContaining({ type: 'track', id: '7' }),
    ]);
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('each item does its job', async () => {
    const onPlay = await mount();
    const pick = (name: string) => {
      fireEvent.click(more());
      fireEvent.click(item(name));
    };

    pick('Play now');
    expect(onPlay).toHaveBeenCalledWith(0);

    pick('Add to queue');
    expect(engine.append).toHaveBeenCalledWith([
      expect.objectContaining({ type: 'track', id: '7' }),
    ]);

    pick('Add to playlist');
    expect(addToPlaylist).toHaveBeenCalledWith([ROWS[0].song]);

    pick('Info');
    expect(itemInfo).toHaveBeenCalledWith(expect.objectContaining({ type: 'track', id: '7' }));

    pick('Download');
    expect(await screen.findByRole('dialog', { name: 'Download track 7' })).toBeTruthy();

    pick('Report an issue');
    expect(
      await screen.findByRole('dialog', {
        name: 'Report an issue with A Very Long Song Title That Should Get The Room',
      }),
    ).toBeTruthy();
  });
});

describe('a page’s ⋯', () => {
  it('leaves play, queue and watchlist to the hero', async () => {
    const { ContextMenuHost, MoreButton } = await import('./context-menu');
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MoreButton
          label="Alien"
          heroClassName="btn"
          target={() => ({
            kind: 'movie',
            page: true,
            id: '1',
            title: 'Alien',
            download: { type: 'movie', id: '1' },
            report: { entityType: 'movie', entityId: 1, title: 'Alien' },
          })}
        />
        <ContextMenuHost />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'More for Alien' }));
    expect(
      within(menu())
        .getAllByRole('menuitem')
        .map((b) => b.textContent),
    ).toEqual(['Download', 'Info', 'Report an issue']);
  });
});
