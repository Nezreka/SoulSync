import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AddSongsPanel } from './add-songs-panel';

const fetchEnhancedSearch = vi.hoisted(() => vi.fn());
vi.mock('@/routes/search/-search.api', () => ({ fetchEnhancedSearch }));

let posts: { url: string; body: unknown }[] = [];
let addReply: unknown = { success: true, added: 1, duplicates: [], track_count: 2 };
const RECENT = [
  { title: 'Played Song', artist: 'Recent Artist', image_url: '/r.jpg' },
  { title: 'Already Here', artist: 'In Playlist' },
];

beforeEach(() => {
  posts = [];
  addReply = { success: true, added: 1, duplicates: [], track_count: 2 };
  fetchEnhancedSearch.mockReset().mockResolvedValue({
    spotify_tracks: [
      { name: 'Found Song', artists: ['Search Artist'], album: 'Alb', duration_ms: 200000 },
    ],
  });
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        posts.push({ url, body: JSON.parse(init.body as string) });
        return new Response(JSON.stringify(addReply));
      }
      return new Response(JSON.stringify({ success: true, tracks: RECENT }));
    }),
  );
  window.showToast = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function setup() {
  const props = {
    playlistId: 7,
    playlistName: 'Vibes',
    existing: [{ track_name: 'Already Here', artist_name: 'In Playlist' }],
    onAdded: vi.fn(),
    onClose: vi.fn(),
  };
  render(<AddSongsPanel {...props} />);
  return props;
}

describe('AddSongsPanel', () => {
  it('opens on your recent plays, with the box focused', async () => {
    setup();
    expect(screen.getByText('Let’s find something for Vibes')).toBeInTheDocument();
    expect(screen.getByLabelText('Search for songs')).toHaveFocus();
    expect(await screen.findByText('Played Song')).toBeInTheDocument();
    expect(screen.getByText('From your recent plays')).toBeInTheDocument();
  });

  it('marks a song the playlist already has instead of offering it again', async () => {
    setup();
    expect(await screen.findByLabelText('Already Here is in this playlist')).toBeDisabled();
  });

  it('adds with one tap, says Added, and tells the playlist to refetch', async () => {
    const props = setup();
    fireEvent.click(await screen.findByLabelText('Add Played Song to this playlist'));
    await waitFor(() => expect(screen.getByText('Added')).toBeInTheDocument());
    expect(posts[0].url).toBe('/api/user-playlists/7/tracks');
    expect(posts[0].body).toMatchObject({
      tracks: [{ track_name: 'Played Song', artist_name: 'Recent Artist', image_url: '/r.jpg' }],
    });
    expect(props.onAdded).toHaveBeenCalledTimes(1);
  });

  it('a song the server says is already there reads as in the playlist, no refetch', async () => {
    addReply = { success: true, added: 0, duplicates: [{}], track_count: 1 };
    const props = setup();
    fireEvent.click(await screen.findByLabelText('Add Played Song to this playlist'));
    await waitFor(() => expect(screen.getAllByText('In playlist')).toHaveLength(2));
    expect(props.onAdded).not.toHaveBeenCalled();
  });

  it('searches every source after a pause, and shows the results', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    setup();
    fireEvent.change(screen.getByLabelText('Search for songs'), { target: { value: 'found' } });
    expect(fetchEnhancedSearch).not.toHaveBeenCalled();
    await act(async () => {
      vi.advanceTimersByTime(400);
    });
    expect(fetchEnhancedSearch).toHaveBeenCalledWith('found', 'auto', expect.any(AbortSignal));
    expect(await screen.findByText('Found Song')).toBeInTheDocument();
    expect(screen.getByText('Results')).toBeInTheDocument();
    vi.useRealTimers();
  });

  it('says so when a search finds nothing, or fails', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fetchEnhancedSearch.mockResolvedValueOnce({ spotify_tracks: [] });
    setup();
    fireEvent.change(screen.getByLabelText('Search for songs'), { target: { value: 'zzz' } });
    await act(async () => {
      vi.advanceTimersByTime(400);
    });
    expect(await screen.findByText('Nothing found for “zzz”.')).toBeInTheDocument();
    fetchEnhancedSearch.mockRejectedValueOnce(new Error('down'));
    fireEvent.change(screen.getByLabelText('Search for songs'), { target: { value: 'yyy' } });
    await act(async () => {
      vi.advanceTimersByTime(400);
    });
    expect(
      await screen.findByText('The search didn’t come back. Try again in a moment.'),
    ).toBeInTheDocument();
    vi.useRealTimers();
  });

  it('a failed add puts the button back and says why', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) =>
        init?.method === 'POST'
          ? new Response(JSON.stringify({ error: 'nope' }), { status: 500 })
          : new Response(JSON.stringify({ success: true, tracks: RECENT })),
      ),
    );
    setup();
    fireEvent.click(await screen.findByLabelText('Add Played Song to this playlist'));
    await waitFor(() => expect(window.showToast).toHaveBeenCalledWith('nope', 'error'));
    expect(screen.getByLabelText('Add Played Song to this playlist')).not.toBeDisabled();
  });

  it('closes from its own button', () => {
    const props = setup();
    fireEvent.click(screen.getByLabelText('Close add songs'));
    expect(props.onClose).toHaveBeenCalled();
  });
});
