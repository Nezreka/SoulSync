/**
 * The Deezer editorial shelf on Discover: genre row, playlist search, and a
 * card click that hands the playlist to Sync while saying what it's doing.
 * the api module is mocked; its own wire shapes are pinned in
 * -discover.deezer-editorial.test.ts.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { DeezerEditorialPlaylist, DeezerHandoffStage } from '../-discover.deezer-editorial';

import * as api from '../-discover.deezer-editorial';
import { DeezerEditorialShelf } from './deezer-editorial-shelf';

vi.mock('../-discover.deezer-editorial', () => ({
  fetchDeezerEditorial: vi.fn(),
  fetchDeezerEditorialGenres: vi.fn(),
  searchDeezerPlaylists: vi.fn(),
  openDeezerPlaylistInSync: vi.fn(),
}));

function playlist(id: string, title: string, tracks = 50): DeezerEditorialPlaylist {
  return {
    id,
    title,
    creator: 'Deezer Editors',
    track_count: tracks,
    image_url: '',
    link: `https://www.deezer.com/playlist/${id}`,
    source: 'deezer',
  };
}

const TOP = [playlist('1', 'Top Worldwide'), playlist('2', 'Hits of the Moment')];
const ROCK = [playlist('9', 'Rock Classics')];

beforeEach(() => {
  vi.mocked(api.fetchDeezerEditorialGenres).mockResolvedValue([
    { id: 0, name: 'All' },
    { id: 152, name: 'Rock' },
  ]);
  vi.mocked(api.fetchDeezerEditorial).mockImplementation(async (genre: number) =>
    genre === 152 ? ROCK : TOP,
  );
  vi.mocked(api.searchDeezerPlaylists).mockResolvedValue([]);
  vi.mocked(api.openDeezerPlaylistInSync).mockResolvedValue(null);
});

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe('DeezerEditorialShelf', () => {
  it("opens on Deezer's everything chart", async () => {
    render(<DeezerEditorialShelf />);
    expect(await screen.findByText('Top Worldwide')).toBeInTheDocument();
    expect(api.fetchDeezerEditorial).toHaveBeenCalledWith(0);
    expect(screen.getByRole('tab', { name: 'All' })).toHaveAttribute('aria-selected', 'true');
  });

  it('switches the row when a genre is picked', async () => {
    render(<DeezerEditorialShelf />);
    await screen.findByText('Top Worldwide');

    fireEvent.click(screen.getByRole('tab', { name: 'Rock' }));

    expect(await screen.findByText('Rock Classics')).toBeInTheDocument();
    expect(screen.queryByText('Top Worldwide')).toBeNull();
    expect(screen.getByRole('tab', { name: 'Rock' })).toHaveAttribute('aria-selected', 'true');
  });

  it('searches once typing settles, not per keystroke', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.searchDeezerPlaylists).mockResolvedValue([playlist('7', 'Lo-fi Beats')]);
    render(<DeezerEditorialShelf />);
    await screen.findByText('Top Worldwide');

    const box = screen.getByRole('searchbox', { name: 'Search Deezer playlists' });
    // a typist's pause between keystrokes, shorter than the debounce
    fireEvent.change(box, { target: { value: 'lo' } });
    await act(async () => {
      vi.advanceTimersByTime(100);
    });
    fireEvent.change(box, { target: { value: 'lofi' } });
    await act(async () => {
      vi.advanceTimersByTime(400);
    });

    expect(await screen.findByText('Lo-fi Beats')).toBeInTheDocument();
    expect(api.searchDeezerPlaylists).toHaveBeenCalledTimes(1);
    expect(api.searchDeezerPlaylists).toHaveBeenCalledWith('lofi');
    // a search is not a genre: no tab claims to be selected
    expect(screen.getByRole('tab', { name: 'All' })).toHaveAttribute('aria-selected', 'false');
  });

  it('says so when a search finds nothing', async () => {
    render(<DeezerEditorialShelf />);
    await screen.findByText('Top Worldwide');
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'zzzz' } });
    expect(await screen.findByText(/No Deezer playlists match “zzzz”/)).toBeInTheDocument();
  });

  it('says when Deezer could not be reached', async () => {
    vi.mocked(api.fetchDeezerEditorial).mockResolvedValue([]);
    render(<DeezerEditorialShelf />);
    expect(await screen.findByText('Could not reach Deezer just now.')).toBeInTheDocument();
  });

  it('a card click hands the playlist to Sync and narrates the load', async () => {
    let report: ((stage: DeezerHandoffStage) => void) | undefined;
    let finish: (error: string | null) => void = () => {};
    vi.mocked(api.openDeezerPlaylistInSync).mockImplementation((_p, onStage) => {
      report = onStage;
      return new Promise((resolve) => {
        finish = resolve;
      });
    });
    const onToast = vi.fn();
    render(<DeezerEditorialShelf onToast={onToast} />);
    const card = await screen.findByRole('button', { name: /Top Worldwide by Deezer Editors/ });

    fireEvent.click(card);
    expect(api.openDeezerPlaylistInSync).toHaveBeenCalledWith(TOP[0], expect.any(Function));
    await act(async () => {
      report?.({ phase: 'loading', done: 10, total: 50 });
    });
    expect(card).toHaveAttribute('aria-busy', 'true');
    expect(card).toHaveTextContent('Loading 10 / 50 tracks…');
    expect(screen.getByRole('progressbar').firstElementChild).toHaveStyle({ width: '20%' });

    await act(async () => {
      finish('Deezer said no');
    });
    await waitFor(() => expect(card).not.toHaveAttribute('aria-busy'));
    expect(onToast).toHaveBeenCalledWith('Deezer said no');
  });
});
