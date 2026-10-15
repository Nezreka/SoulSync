/**
 * the playlist detail modal, round two: play / shuffle / queue, sort, add
 * songs, copy a mirror to My Playlists, and the Liked Songs cover.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { MirroredTrack } from '../-sync.mirrored';

import { MirroredDetailModal, sortTracks, TRACK_SORTS } from './mirrored-detail-modal';
import { LikedArt } from './playlist-art';

const playPlaylist = vi.hoisted(() => vi.fn(async () => true));
vi.mock('../../../features/playlists/play-playlist', () => ({ playPlaylist }));

// the panel has its own tests; here it only matters that it opens and with what
const panelProps = vi.hoisted(() => [] as Record<string, unknown>[]);
vi.mock('../../../features/playlists/add-songs-panel', () => ({
  AddSongsPanel: (props: Record<string, unknown>) => {
    panelProps.push(props);
    return <div data-testid="add-songs-panel" />;
  },
}));

afterEach(() => {
  playPlaylist.mockClear();
  panelProps.length = 0;
});

const TRACKS: MirroredTrack[] = [
  {
    position: 1,
    track_name: 'Charlie',
    artist_name: 'B',
    album_name: 'Z',
    duration_ms: 200000,
    added_at: '2026-01-02 00:00:00',
  },
  {
    position: 2,
    track_name: 'alpha',
    artist_name: 'C',
    album_name: 'X',
    duration_ms: 100000,
    added_at: null,
  },
  {
    position: 3,
    track_name: 'Bravo',
    artist_name: 'A',
    album_name: 'Y',
    duration_ms: 300000,
    added_at: '2026-03-01 00:00:00',
  },
];

function renderModal(data: Record<string, unknown>, on: Record<string, unknown> = {}) {
  const handlers = {
    onClose: vi.fn(),
    onDelete: vi.fn(),
    onEditSource: vi.fn(),
    onRunPipeline: vi.fn(),
    onDiscover: vi.fn(),
    onRefresh: vi.fn(),
    ...on,
  };
  render(<MirroredDetailModal playlistId={3} data={data as never} now={0} {...handlers} />);
  return handlers;
}

const MINE = { name: 'Late night', source: 'soulsync', tracks: TRACKS };
const EDITS = { onRemoveTrack: vi.fn(), onReorder: vi.fn() };
const titles = () => [...document.querySelectorAll('.mm-row-title')].map((e) => e.textContent);

describe('play, shuffle, queue', () => {
  it.each([
    ['Play Late night', 'play'],
    ['Shuffle play', 'shuffle'],
    ['Add to queue', 'queue'],
  ])('%s hands the playlist over as %s', async (label, mode) => {
    renderModal(MINE, EDITS);
    fireEvent.click(screen.getByLabelText(label));
    await waitFor(() => expect(playPlaylist).toHaveBeenCalledTimes(1));
    const [tracks, name, how] = playPlaylist.mock.calls[0] as unknown as [
      MirroredTrack[],
      string,
      string,
    ];
    expect(tracks.map((t) => t.track_name)).toEqual(['Charlie', 'alpha', 'Bravo']);
    expect(name).toBe('Late night');
    expect(how).toBe(mode);
  });

  it('a mirror plays too, not only your own', () => {
    renderModal({ name: 'Radar', source: 'spotify', tracks: TRACKS });
    expect(screen.getByLabelText('Play Radar')).not.toBeDisabled();
  });

  it('a sorted view plays in the order you see', async () => {
    renderModal(MINE, EDITS);
    fireEvent.change(screen.getByLabelText('Sort tracks'), { target: { value: 'title' } });
    fireEvent.click(screen.getByLabelText('Play Late night'));
    await waitFor(() => expect(playPlaylist).toHaveBeenCalled());
    const [tracks] = playPlaylist.mock.calls[0] as unknown as [MirroredTrack[]];
    expect(tracks.map((t) => t.track_name)).toEqual(['alpha', 'Bravo', 'Charlie']);
  });

  it('nothing to play, nothing to press', () => {
    renderModal({ name: 'Empty', source: 'spotify', tracks: [] });
    expect(screen.getByLabelText('Play Empty')).toBeDisabled();
    expect(screen.getByLabelText('Shuffle play')).toBeDisabled();
  });
});

describe('sort', () => {
  it('re-orders the rows and stops the drag while sorted', () => {
    renderModal(MINE, EDITS);
    expect(titles()).toEqual(['Charlie', 'alpha', 'Bravo']);
    expect(document.querySelector('.mm-row')).toHaveAttribute('draggable', 'true');
    fireEvent.change(screen.getByLabelText('Sort tracks'), { target: { value: 'artist' } });
    expect(titles()).toEqual(['Bravo', 'Charlie', 'alpha']);
    expect(document.querySelector('.mm-row')).not.toHaveAttribute('draggable');
    expect(screen.getByText('Back to custom order to drag tracks around')).toBeInTheDocument();
  });

  it('remove still takes the right track out of a sorted view', () => {
    const onRemoveTrack = vi.fn();
    renderModal(MINE, { ...EDITS, onRemoveTrack });
    fireEvent.change(screen.getByLabelText('Sort tracks'), { target: { value: 'title' } });
    // alpha is shown first but sits at position 2
    fireEvent.click(screen.getByLabelText('Remove alpha from this playlist'));
    expect(onRemoveTrack).toHaveBeenCalledWith(2);
  });

  it('Liked Songs opens newest first', () => {
    renderModal({ ...MINE, name: 'Liked Songs', source_playlist_id: 'liked' }, EDITS);
    expect(screen.getByLabelText('Sort tracks')).toHaveValue('added');
    expect(titles()).toEqual(['Bravo', 'Charlie', 'alpha']);
  });

  it('sortTracks: date added is newest first, undated rows last in their own order', () => {
    expect(sortTracks(TRACKS, 'added').map((t) => t.track_name)).toEqual([
      'Bravo',
      'Charlie',
      'alpha',
    ]);
    expect(sortTracks(TRACKS, 'duration').map((t) => t.track_name)).toEqual([
      'alpha',
      'Charlie',
      'Bravo',
    ]);
    expect(sortTracks(TRACKS, 'album').map((t) => t.track_name)).toEqual([
      'alpha',
      'Bravo',
      'Charlie',
    ]);
    // custom is the playlist's own order, on a copy
    const custom = sortTracks(TRACKS, 'custom');
    expect(custom).toEqual(TRACKS);
    expect(custom).not.toBe(TRACKS);
  });

  it('TRACK_SORTS offers every sort, custom first', () => {
    expect(TRACK_SORTS.map((o) => o.value)).toEqual([
      'custom',
      'added',
      'title',
      'artist',
      'album',
      'duration',
    ]);
  });
});

describe('add songs', () => {
  it('is yours only, and opens the panel for this playlist', () => {
    const onRefresh = vi.fn();
    renderModal(MINE, { ...EDITS, onRefresh });
    fireEvent.click(screen.getByRole('button', { name: 'Add songs' }));
    expect(screen.getByTestId('add-songs-panel')).toBeInTheDocument();
    const props = panelProps[panelProps.length - 1];
    expect(props.playlistId).toBe(3);
    expect(props.playlistName).toBe('Late night');
    expect(props.existing).toEqual(TRACKS);
    expect(props.onAdded).toBe(onRefresh);
  });

  it('a mirror has no Add songs: its tracks are its source’s', () => {
    renderModal({ name: 'Radar', source: 'spotify', tracks: TRACKS });
    expect(screen.queryByRole('button', { name: 'Add songs' })).toBeNull();
  });
});

describe('copy to My Playlists', () => {
  it('a mirror offers it, and says so while it runs', async () => {
    let finish: () => void = () => {};
    const onMakeMine = vi.fn(() => new Promise<void>((r) => (finish = r)));
    renderModal({ name: 'Radar', source: 'spotify', tracks: TRACKS }, { onMakeMine });
    fireEvent.click(screen.getByLabelText('More actions'));
    fireEvent.click(screen.getByRole('menuitem', { name: /Copy to My Playlists/ }));
    expect(onMakeMine).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByLabelText('More actions'));
    expect(screen.getByRole('menuitem', { name: /Copying…/ })).toBeDisabled();
    finish();
    await waitFor(() =>
      expect(screen.getByRole('menuitem', { name: /Copy to My Playlists/ })).not.toBeDisabled(),
    );
  });

  it('your own playlist does not', () => {
    renderModal(MINE, { ...EDITS, onMakeMine: vi.fn() });
    fireEvent.click(screen.getByLabelText('More actions'));
    expect(screen.queryByText('Copy to My Playlists')).toBeNull();
  });
});

describe('Liked Songs', () => {
  it('wears the heart cover until you give it one of your own', () => {
    renderModal({ ...MINE, name: 'Liked Songs', source_playlist_id: 'liked' }, EDITS);
    expect(document.querySelector('.mm-cover-liked .playlist-liked-art')).not.toBeNull();
  });

  it('LikedArt is decorative', () => {
    const { container } = render(<LikedArt />);
    expect(container.querySelector('.playlist-liked-art')).toHaveAttribute('aria-hidden', 'true');
  });
});

describe('play from a row', () => {
  it('plays from that song on, in the order shown', async () => {
    renderModal(MINE, EDITS);
    fireEvent.click(screen.getByLabelText('Play from alpha'));
    await waitFor(() => expect(playPlaylist).toHaveBeenCalled());
    const [tracks, , mode] = playPlaylist.mock.calls[0] as unknown as [
      MirroredTrack[],
      string,
      string,
    ];
    expect(tracks.map((t) => t.track_name)).toEqual(['alpha', 'Bravo']);
    expect(mode).toBe('play');
  });
});
