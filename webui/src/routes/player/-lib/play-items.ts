/**
 * page data to queue items: what each play button hands the player. one
 * place per kind, so every page that plays a movie or an album builds the
 * same item (same id, same art, same link back).
 */

import type { ItemType, PlayItem } from '../-engine/types';
import type { ArtistQueueRow } from '../-player.api';
import type {
  CatalogMovie,
  DetailSeason,
  LibAlbumDetail,
  LibSong,
  LibTrack,
  OwnedBook,
  PodcastEpisode,
  YtVideo,
} from '../-player.types';
import type { GuideProgram } from './schedule';

import { albumArtwork, libraryThumb, remoteImage, youtubeThumb } from './images';
import { backdropUrl, bookCover, episodeKey, posterUrl } from './rails';

export function movieItem(
  m: Pick<CatalogMovie, 'id' | 'title'> & { year?: number | null },
  part?: number | string | null,
): PlayItem {
  return {
    type: 'movie',
    id: String(m.id),
    title: m.title,
    subtitle: m.year ? String(m.year) : undefined,
    artwork: posterUrl('movie', m.id, 342),
    backdrop: backdropUrl('movie', m.id, 780),
    href: `/player/movies/${m.id}`,
    part: part ? String(part) : undefined,
  };
}

/**
 * a show's owned episodes in airing order, specials last, from ``fromId``
 * on: what "play" on a show queues, so the next one follows on its own
 */
export function episodeItems(
  show: { id: number; title: string },
  seasons: readonly DetailSeason[],
  fromId?: number | null,
): PlayItem[] {
  const ordered = seasons
    .slice()
    .sort((a, b) => (a.season_number || 999) - (b.season_number || 999))
    .flatMap((season) =>
      season.episodes
        .filter((e) => e.owned !== false)
        .slice()
        .sort((a, b) => a.episode_number - b.episode_number)
        .map((e) => ({ season, e })),
    );
  const start = fromId
    ? Math.max(
        0,
        ordered.findIndex(({ e }) => e.id === fromId),
      )
    : 0;
  return ordered.slice(start).map(({ season, e }) => ({
    type: 'episode' as const,
    id: String(e.id),
    title: e.title || `Episode ${e.episode_number}`,
    subtitle: `${show.title} · S${season.season_number} E${e.episode_number}`,
    artwork: posterUrl('show', show.id, 342),
    backdrop: e.has_still ? posterUrl('episode', e.id, 500) : backdropUrl('show', show.id, 780),
    href: `/player/tv/${show.id}?season=${season.season_number}`,
    duration: e.runtime_minutes ? e.runtime_minutes * 60 : null,
    parentId: String(show.id),
    parentTitle: show.title,
  }));
}

function artistsOf(t: LibTrack, fallback?: string | null) {
  const names = (t.artists ?? []).map((a) => (typeof a === 'string' ? a : a.name)).filter(Boolean);
  return names.length ? names.join(', ') : (fallback ?? undefined);
}

/** an album's playable tracks, in disc and track order */
/** True only when a library_v2 album track actually has a playable file/stream. */
export function isLibTrackOwned(t: LibTrack): boolean {
  if (t.id === null || t.id === undefined || t.is_missing) return false;
  if (t.file_status === 'missing') return false;
  if ('file' in t && t.file == null && !t.file_count && !t.server_track_id) return false;
  return true;
}

export function albumItems(album: LibAlbumDetail): PlayItem[] {
  const art = libraryThumb(album.image_url);
  return album.tracks
    .filter(isLibTrackOwned)
    .slice()
    .sort(
      (a, b) =>
        (a.disc_number ?? 1) - (b.disc_number ?? 1) ||
        (a.track_number ?? 0) - (b.track_number ?? 0),
    )
    .map((t) => ({
      type: 'track' as const,
      id: String(t.id),
      title: t.title,
      subtitle: artistsOf(t, album.primary_artist?.name),
      artwork: art,
      href: `/player/music/album/${album.id}`,
      duration: t.duration ? t.duration / 1000 : null,
      parentId: String(album.id),
      parentTitle: album.title,
    }));
}

export function bookItem(book: OwnedBook): PlayItem {
  return {
    type: 'audiobook',
    id: book.asin,
    title: book.title,
    subtitle: book.author ?? undefined,
    artwork: bookCover(book),
    href: `/player/audiobooks/${encodeURIComponent(book.asin)}`,
    duration: book.runtime_minutes ? book.runtime_minutes * 60 : null,
  };
}

/** a podcast's episodes that have audio, as listed (newest first, usually) */
export function podcastItems(
  podcast: { id: number; title: string; artwork_url?: string | null },
  episodes: readonly PodcastEpisode[],
): PlayItem[] {
  return episodes
    .filter((e) => !!e.enclosure_url)
    .map((e) => ({
      type: 'podcast_episode' as const,
      id: episodeKey(e),
      title: e.title,
      subtitle: podcast.title,
      artwork: remoteImage(e.artwork_url ?? podcast.artwork_url ?? null),
      href: `/player/podcasts/${podcast.id}`,
      duration: e.duration_seconds ?? null,
      url: e.enclosure_url!,
      parentId: String(podcast.id),
      parentTitle: podcast.title,
    }));
}

/** downloaded youtube videos play here; the rest play in youtube's own embed */
export function youtubeItems(videos: readonly YtVideo[]): PlayItem[] {
  return videos
    .filter((v) => v.downloaded)
    .map((v) => ({
      type: 'youtube' as const,
      id: v.id,
      title: v.title,
      subtitle: v.channel_title ?? undefined,
      artwork: youtubeThumb(v.thumbnail, 'card', v.id),
      backdrop: youtubeThumb(v.thumbnail, 'hero', v.id),
      href: v.channel_id
        ? `/player/youtube/${encodeURIComponent(v.channel_id)}`
        : '/player/youtube',
      duration: v.duration_seconds ?? null,
      parentId: v.channel_id ?? undefined,
      parentTitle: v.channel_title ?? undefined,
    }));
}

/** an artist's queue (library v2's artist play-queue rows) */
export function artistItems(rows: readonly ArtistQueueRow[]): PlayItem[] {
  return rows.map((r) => ({
    type: 'track' as const,
    id: String(r.track_id),
    title: r.track_title,
    subtitle: r.artist_name ?? undefined,
    artwork: albumArtwork(r.album_id),
    href: `/player/music/album/${r.album_id}`,
    duration: r.duration ? r.duration / 1000 : null,
    parentId: String(r.album_id),
    parentTitle: r.album_title ?? undefined,
  }));
}

const GUIDE_TYPES: ReadonlySet<string> = new Set([
  'movie',
  'episode',
  'youtube',
  'track',
  'commercial',
]);

/** a guide program as a queue item (its key is "type:id"); null for one that can't play */
export function guideItem(p: GuideProgram): PlayItem | null {
  const at = p.key.indexOf(':');
  const type = p.key.slice(0, at);
  const id = p.key.slice(at + 1);
  if (at < 1 || !id || !GUIDE_TYPES.has(type)) return null;
  return {
    type: type as ItemType,
    id,
    title: p.title,
    subtitle: p.subtitle,
    artwork: p.art ?? null,
    backdrop: p.art ?? null,
    href: p.href,
    duration: p.seconds,
  };
}

/**
 * a channel from a program on: that program and what airs after it, for
 * the next few hours, so the channel plays on like television
 */
export function channelQueue(
  programs: readonly GuideProgram[],
  from: GuideProgram,
  hours = 6,
): PlayItem[] {
  const until = from.start + hours * 3_600_000;
  return programs
    .filter((p) => p.start >= from.start && p.start < until)
    .map(guideItem)
    .filter((x): x is PlayItem => !!x);
}

/** library songs (the songs list), each with its album's art */
export function songItems(songs: readonly LibSong[]): PlayItem[] {
  return songs.map((x) => ({
    type: 'track' as const,
    id: String(x.id),
    title: x.title,
    subtitle: x.artist_name ?? undefined,
    artwork: albumArtwork(x.album_id),
    href: x.album_id ? `/player/music/album/${x.album_id}` : '/player/music',
    duration: x.duration ? x.duration / 1000 : null,
    parentId: x.album_id ? String(x.album_id) : undefined,
    parentTitle: x.album_title ?? undefined,
  }));
}
