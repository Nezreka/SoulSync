import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { RecentlyAddedAlbum } from './-dash.content';
import type { RecentPlay } from './-dash.listening';

import {
  addRecentAlbumToPlaylist,
  addRecentPlayToPlaylist,
  playRecentAlbum,
  queueRecentAlbum,
  queueRecentPlay,
  queueTracks,
  recentAlbumTracks,
} from './-dash.card-actions';

/**
 * the dashboard rail buttons. a Recently Added card is an album, so its
 * buttons act on the whole library album; a Recently Played card is a song.
 */

const loadAlbumTracks = vi.fn();
vi.mock('../library/-library.api', () => ({
  loadAlbumTracks: (id: unknown) => loadAlbumTracks(id),
}));
const resolveLibraryTrack = vi.fn();
vi.mock('../../features/playback/play-track', () => ({
  resolveLibraryTrack: (...a: unknown[]) => resolveLibraryTrack(...a),
}));
const openAddToPlaylist = vi.fn();
vi.mock('../../features/playlists/add-to-playlist', () => ({
  openAddToPlaylist: (...a: unknown[]) => openAddToPlaylist(...a),
}));

const album = (over: Partial<RecentlyAddedAlbum> = {}): RecentlyAddedAlbum => ({
  artistName: 'Ado',
  albumName: 'Kyougen',
  cover: 'k.jpg',
  artistCover: '',
  addedAt: '',
  trackCount: 2,
  quality: 'FLAC',
  source: 'soulseek',
  playTitle: 'Vivarium',
  playFilePath: '/m/v.flac',
  albumId: 'al1',
  artistId: 'ar1',
  ...over,
});

const play: RecentPlay = {
  key: 'k',
  title: 'Odo',
  artist: 'Ado',
  album: 'Kyougen',
  imageUrl: 'o.jpg',
  ago: '',
  source: '',
  artistDbId: null,
  plays: 1,
};

const TRACKS = [
  { id: 1, title: 'Readymade', file_path: '/m/1.flac', track_number: 1 },
  { id: 2, title: 'Odo', file_path: '/m/2.flac', track_number: 2 },
];

beforeEach(() => {
  loadAlbumTracks.mockReset().mockResolvedValue(TRACKS);
  resolveLibraryTrack.mockReset();
  openAddToPlaylist.mockReset();
  window.showToast = vi.fn();
  window.addTracksToQueue = vi.fn();
  window.addToQueue = vi.fn();
  window.playTrackList = vi.fn();
});

afterEach(() => {
  delete window.addTracksToQueue;
  delete window.addToQueue;
  delete window.playTrackList;
});

describe('recentAlbumTracks', () => {
  it('is the whole library album, as playable library rows', async () => {
    const tracks = await recentAlbumTracks(album());
    expect(loadAlbumTracks).toHaveBeenCalledWith('al1');
    expect(tracks.map((t) => t.title)).toEqual(['Readymade', 'Odo']);
    expect(tracks[0]).toMatchObject({
      artist: 'Ado',
      album: 'Kyougen',
      file_path: '/m/1.flac',
      is_library: true,
      playback_status: 'ready',
      image_url: 'k.jpg',
      album_id: 'al1',
      artist_id: 'ar1',
    });
  });

  it('is the one landed track when the library has no album for the card', async () => {
    const tracks = await recentAlbumTracks(album({ albumId: null }));
    expect(loadAlbumTracks).not.toHaveBeenCalled();
    expect(tracks).toHaveLength(1);
    expect(tracks[0]).toMatchObject({ title: 'Vivarium', file_path: '/m/v.flac' });
  });

  it('falls back to the landed track when the album comes back empty', async () => {
    loadAlbumTracks.mockResolvedValue([]);
    const tracks = await recentAlbumTracks(album());
    expect(tracks.map((t) => t.title)).toEqual(['Vivarium']);
  });

  it('is nothing with neither an album nor a file', async () => {
    expect(await recentAlbumTracks(album({ albumId: null, playFilePath: '' }))).toEqual([]);
  });
});

describe('queueTracks', () => {
  it('hands the player the whole list at once, so it is one toast', () => {
    queueTracks([{ title: 'a' }, { title: 'b' }]);
    expect(window.addTracksToQueue).toHaveBeenCalledTimes(1);
    expect(window.addToQueue).not.toHaveBeenCalled();
  });

  it('falls back to one at a time on a player without the bulk call', () => {
    delete window.addTracksToQueue;
    queueTracks([{ title: 'a' }, { title: 'b' }]);
    expect(window.addToQueue).toHaveBeenCalledTimes(2);
  });

  it('does nothing with nothing', () => {
    queueTracks([]);
    expect(window.addTracksToQueue).not.toHaveBeenCalled();
  });
});

describe('the Recently Added buttons', () => {
  it('play the album as the queue, named for it', async () => {
    await playRecentAlbum(album());
    expect(window.playTrackList).toHaveBeenCalledTimes(1);
    const [tracks, context] = vi.mocked(window.playTrackList!).mock.calls[0];
    expect((tracks as { title: string }[]).map((t) => t.title)).toEqual(['Readymade', 'Odo']);
    expect(context).toBe('Kyougen');
  });

  it('queue the album', async () => {
    await queueRecentAlbum(album());
    const [tracks] = vi.mocked(window.addTracksToQueue!).mock.calls[0];
    expect(tracks).toHaveLength(2);
  });

  it('say so when there is nothing to play, instead of doing nothing', async () => {
    await playRecentAlbum(album({ albumId: null, playFilePath: '' }));
    expect(window.playTrackList).not.toHaveBeenCalled();
    expect(window.showToast).toHaveBeenCalledWith('Nothing in Kyougen to play', 'info');
  });

  it('say so when the album will not load', async () => {
    loadAlbumTracks.mockRejectedValue(new Error('500'));
    await queueRecentAlbum(album());
    expect(window.showToast).toHaveBeenCalledWith('Could not queue Kyougen', 'error');
  });

  it('put every track of the album in the playlist picker', async () => {
    const anchor = document.createElement('button');
    await addRecentAlbumToPlaylist(album(), anchor);
    expect(openAddToPlaylist).toHaveBeenCalledWith(
      [
        { track_name: 'Readymade', artist_name: 'Ado', album_name: 'Kyougen', image_url: 'k.jpg' },
        { track_name: 'Odo', artist_name: 'Ado', album_name: 'Kyougen', image_url: 'k.jpg' },
      ],
      anchor,
    );
  });
});

describe('the Recently Played buttons', () => {
  it('queue the library copy when there is one', async () => {
    resolveLibraryTrack.mockResolvedValue({
      id: 7,
      title: 'Odo',
      file_path: '/m/2.flac',
      _stats_image: null,
      album: 'Kyougen',
      artist: 'Ado',
    });
    await queueRecentPlay(play);
    expect(resolveLibraryTrack).toHaveBeenCalledWith('Odo', 'Ado', 'Kyougen');
    const [[track]] = vi.mocked(window.addTracksToQueue!).mock.calls[0] as [unknown[]];
    expect(track).toMatchObject({
      id: 7,
      file_path: '/m/2.flac',
      is_library: true,
      playback_status: 'ready',
      image_url: 'o.jpg',
    });
  });

  it('queue the bare song when the library has no copy', async () => {
    resolveLibraryTrack.mockResolvedValue(null);
    await queueRecentPlay(play);
    const [[track]] = vi.mocked(window.addTracksToQueue!).mock.calls[0] as [
      Record<string, unknown>[],
    ];
    expect(track).toMatchObject({ title: 'Odo', artist: 'Ado', album: 'Kyougen' });
    // no file: the player resolves it like any metadata row
    expect(track.file_path).toBeUndefined();
  });

  it('put the song in the playlist picker', () => {
    const anchor = document.createElement('button');
    addRecentPlayToPlaylist(play, anchor);
    expect(openAddToPlaylist).toHaveBeenCalledWith(
      { track_name: 'Odo', artist_name: 'Ado', album_name: 'Kyougen', image_url: 'o.jpg' },
      anchor,
    );
  });
});
