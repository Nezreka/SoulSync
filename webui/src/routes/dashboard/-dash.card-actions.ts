/**
 * What the play / queue / add-to-playlist buttons on the dashboard rails do.
 *
 * A Recently Added card is an ALBUM, so its buttons act on the whole library
 * album (the card carries album_id for it). One with no library album acts on
 * the one track that landed, which is what tapping the card always played.
 *
 * A Recently Played card is a SONG off the play ledger, with no file path. It
 * queues the library's copy when there is one, and the bare title + artist
 * otherwise, which the player resolves like any other metadata row.
 */

import type { RecentlyAddedAlbum } from './-dash.content';
import type { RecentPlay } from './-dash.listening';

import { resolveLibraryTrack } from '../../features/playback/play-track';
import { openAddToPlaylist } from '../../features/playlists/add-to-playlist';
import { loadAlbumTracks } from '../library/-library.api';
import { albumQueueTrack } from '../library/-library.helpers';

export type QueueTrack = Record<string, unknown> & { title: string };

/** the tracks a Recently Added card stands for, ready for the player. */
export async function recentAlbumTracks(album: RecentlyAddedAlbum): Promise<QueueTrack[]> {
  if (album.albumId !== null && album.albumId !== '') {
    const tracks = await loadAlbumTracks(album.albumId);
    if (tracks.length) {
      return tracks.map((track) =>
        albumQueueTrack(track, {
          id: album.albumId as string | number,
          title: album.albumName,
          artist_name: album.artistName,
          artist_id: album.artistId ?? '',
          thumb_url: album.cover || null,
        }),
      );
    }
  }
  if (!album.playFilePath) return [];
  return [
    {
      title: album.playTitle,
      name: album.playTitle,
      artist: album.artistName,
      artists: [{ name: album.artistName }],
      album: album.albumName,
      file_path: album.playFilePath,
      filename: album.playFilePath,
      is_library: true,
      playback_status: 'ready',
      image_url: album.cover || null,
    },
  ];
}

/** onto the end of the queue in one go, so a 12-track album is one toast. */
export function queueTracks(tracks: QueueTrack[]): void {
  if (!tracks.length) return;
  if (typeof window.addTracksToQueue === 'function') window.addTracksToQueue(tracks);
  else for (const track of tracks) window.addToQueue?.(track);
}

export async function playRecentAlbum(album: RecentlyAddedAlbum): Promise<void> {
  try {
    const tracks = await recentAlbumTracks(album);
    if (!tracks.length) {
      window.showToast?.(`Nothing in ${album.albumName} to play`, 'info');
      return;
    }
    await window.playTrackList?.(tracks, album.albumName);
  } catch {
    window.showToast?.(`Could not play ${album.albumName}`, 'error');
  }
}

export async function queueRecentAlbum(album: RecentlyAddedAlbum): Promise<void> {
  try {
    const tracks = await recentAlbumTracks(album);
    if (!tracks.length) {
      window.showToast?.(`Nothing in ${album.albumName} to queue`, 'info');
      return;
    }
    queueTracks(tracks);
  } catch {
    window.showToast?.(`Could not queue ${album.albumName}`, 'error');
  }
}

export async function addRecentAlbumToPlaylist(
  album: RecentlyAddedAlbum,
  anchor: HTMLElement,
): Promise<void> {
  try {
    const tracks = await recentAlbumTracks(album);
    openAddToPlaylist(
      tracks.map((track) => ({
        track_name: track.title,
        artist_name: album.artistName,
        album_name: album.albumName,
        image_url: album.cover || null,
      })),
      anchor,
    );
  } catch {
    window.showToast?.(`Could not load ${album.albumName}`, 'error');
  }
}

/** the library's copy when there is one, else the bare metadata row. */
export async function queueRecentPlay(play: RecentPlay): Promise<void> {
  const owned = await resolveLibraryTrack(play.title, play.artist, play.album);
  queueTracks([
    owned
      ? {
          ...owned,
          name: owned.title,
          artists: [{ name: owned.artist }],
          filename: owned.file_path,
          is_library: true,
          playback_status: 'ready',
          image_url: owned._stats_image || play.imageUrl,
        }
      : {
          title: play.title,
          name: play.title,
          artist: play.artist,
          artists: [{ name: play.artist }],
          album: play.album,
          image_url: play.imageUrl,
        },
  ]);
}

export function addRecentPlayToPlaylist(play: RecentPlay, anchor: HTMLElement): void {
  openAddToPlaylist(
    {
      track_name: play.title,
      artist_name: play.artist,
      album_name: play.album,
      image_url: play.imageUrl,
    },
    anchor,
  );
}
