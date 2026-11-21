import { describe, expect, it } from 'vitest';

import type { PlaylistSong } from '../-player.api';

import { playlistCover, playlistItems, songFromItem, sourceName } from './playlists';

const song = (over: Partial<PlaylistSong>): PlaylistSong => ({
  position: 1,
  title: 'Geyser',
  artist: 'Mitski',
  album: 'Be the Cowboy',
  album_id: 7,
  artist_id: 3,
  duration_ms: 123_000,
  image_url: null,
  added_at: null,
  track_id: 42,
  ...over,
});

describe('playlists', () => {
  it('queues only the songs this profile can play, by their library track', () => {
    const items = playlistItems({
      id: 5,
      name: 'Mix',
      songs: [
        song({}),
        song({ title: 'Gone', track_id: null }),
        song({ title: 'Nobody', track_id: 43 }),
      ],
    });
    expect(items.map((x) => x.id)).toEqual(['42', '43']);
    expect(items[0]).toMatchObject({
      type: 'track',
      subtitle: 'Mitski',
      parentTitle: 'Be the Cowboy',
      duration: 123,
    });
  });

  it('a queued track goes back to a playlist with its library track', () => {
    expect(
      songFromItem({
        type: 'track',
        id: '42',
        title: 'Geyser',
        subtitle: 'Mitski',
        parentTitle: 'Be the Cowboy',
        duration: 123,
      }),
    ).toEqual({
      title: 'Geyser',
      artist: 'Mitski',
      album: 'Be the Cowboy',
      duration_ms: 123_000,
      lib2_track_id: 42,
    });
    expect(songFromItem({ type: 'movie', id: '1', title: 'x' })).toBeNull();
  });

  it('a cover: its own, else four album covers, else one', () => {
    expect(
      playlistCover({ image_url: 'https://x/c.jpg', cover_tiles: ['https://a'] }).tiles,
    ).toEqual([]);
    expect(
      playlistCover({
        image_url: null,
        cover_tiles: ['https://a', 'https://b', 'https://c', 'https://d'],
      }).tiles,
    ).toHaveLength(4);
    expect(
      playlistCover({ image_url: null, cover_tiles: ['https://a', 'https://b'] }).tiles,
    ).toHaveLength(1);
  });

  it('names where a mirrored playlist comes from', () => {
    expect(sourceName('spotify')).toBe('Spotify');
    expect(sourceName('soulsync')).toBeNull();
    expect(sourceName('napster')).toBe('Napster');
    expect(sourceName('spotify_public')).toBe('Spotify');
    expect(sourceName('some_thing')).toBe('Some thing');
  });
});
