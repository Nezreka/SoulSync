/**
 * playlists in the player, the pure parts: a playlist's songs as a queue
 * (only the ones this profile can play), a song as the playlist routes take
 * it, and the cover a playlist shows.
 */

import type { PlayItem } from '../-engine/types';
import type { PlaylistDetail, PlaylistSong, PlaylistSummary, SongRef } from '../-player.api';

import { albumArtwork, remoteImage } from './images';

/** the songs this profile can play, in order, as a queue */
export function playlistItems(p: Pick<PlaylistDetail, 'id' | 'name' | 'songs'>): PlayItem[] {
  return p.songs
    .filter((x): x is PlaylistSong & { track_id: number } => x.track_id !== null)
    .map((x) => ({
      type: 'track' as const,
      id: String(x.track_id),
      title: x.title,
      subtitle: x.artist || undefined,
      artwork: albumArtwork(x.album_id) ?? remoteImage(x.image_url),
      href: x.album_id ? `/player/music/album/${x.album_id}` : `/player/music/playlist/${p.id}`,
      duration: x.duration_ms ? x.duration_ms / 1000 : null,
      parentId: x.album_id ? String(x.album_id) : undefined,
      parentTitle: x.album || undefined,
    }));
}

/** a queued song as the playlist routes take it (by name, with its library track) */
export function songFromItem(item: PlayItem): SongRef | null {
  if (item.type !== 'track') return null;
  const id = Number(item.id);
  return {
    title: item.title,
    artist: item.subtitle ?? '',
    album: item.parentTitle ?? null,
    duration_ms: item.duration ? Math.round(item.duration * 1000) : null,
    lib2_track_id: Number.isInteger(id) && id > 0 ? id : null,
  };
}

/** a playlist song as the routes take it */
export function songFromRow(x: PlaylistSong): SongRef {
  return {
    title: x.title,
    artist: x.artist,
    album: x.album || null,
    duration_ms: x.duration_ms || null,
    lib2_track_id: x.track_id,
  };
}

/** what a playlist shows: its own cover, else up to four album covers, else nothing (a tint) */
export function playlistCover(p: Pick<PlaylistSummary, 'image_url' | 'cover_tiles'>): {
  image: string | null;
  tiles: string[];
} {
  const image = remoteImage(p.image_url);
  if (image) return { image, tiles: [] };
  const tiles = p.cover_tiles.map((t) => remoteImage(t)).filter((t): t is string => !!t);
  return { image: null, tiles: tiles.length >= 4 ? tiles.slice(0, 4) : tiles.slice(0, 1) };
}

const SOURCES: Record<string, string> = {
  spotify: 'Spotify',
  deezer: 'Deezer',
  tidal: 'Tidal',
  youtube: 'YouTube',
  youtube_music: 'YouTube Music',
  apple: 'Apple Music',
  qobuz: 'Qobuz',
  beatport: 'Beatport',
  listenbrainz: 'ListenBrainz',
  lastfm: 'Last.fm',
  soundcloud: 'SoundCloud',
  file: 'a file',
  m3u: 'a file',
};

/** where a mirrored playlist comes from, in words */
export function sourceName(source: string | null | undefined): string | null {
  if (!source || source === 'soulsync') return null;
  return SOURCES[source.toLowerCase()] ?? source.charAt(0).toUpperCase() + source.slice(1);
}

/** "12 songs · 48 min", "1 song" */
export function songCount(n: number): string {
  return `${n.toLocaleString()} ${n === 1 ? 'song' : 'songs'}`;
}
