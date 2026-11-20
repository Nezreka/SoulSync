/**
 * shaping the home rails: continue rows from the progress store, recently
 * added rows from each library. pure, so tested without a server.
 */

import { appURL } from '@/platform/url-base';

import type {
  CatalogMovie,
  CatalogRecentEpisode,
  CatalogYoutube,
  LibAlbum,
  OwnedBook,
  PodcastEpisode,
  ProgressRow,
} from '../-player.types';

export interface RailCard {
  key: string;
  title: string;
  subtitle?: string;
  art?: string | null;
  href?: string;
  shape: 'poster' | 'square' | 'wide';
  /** 0..1 when the card shows a progress bar */
  progress?: number;
  /** the progress row this card came from, for remove / mark finished */
  source?: { itemType: string; itemId: string };
}

/** item types and the shape their artwork is */
const SHAPE_BY_TYPE: Record<string, RailCard['shape']> = {
  movie: 'poster',
  episode: 'wide',
  youtube: 'wide',
  track: 'square',
  podcast_episode: 'square',
  audiobook: 'square',
};

/** which home rail an item type belongs in */
export const CONTINUE_GROUPS: { id: string; label: string; types: string[] }[] = [
  { id: 'watching', label: 'Continue watching', types: ['movie', 'episode', 'youtube'] },
  {
    id: 'listening',
    label: 'Continue listening',
    types: ['audiobook', 'podcast_episode', 'track'],
  },
];

export function fraction(
  position: number,
  duration: number | null | undefined,
): number | undefined {
  if (!duration || duration <= 0 || !Number.isFinite(position)) return undefined;
  return Math.min(1, Math.max(0, position / duration));
}

export function continueCard(row: ProgressRow): RailCard {
  const meta = row.meta ?? {};
  return {
    key: `${row.item_type}:${row.item_id}`,
    title: meta.title || 'Untitled',
    subtitle: meta.subtitle,
    art: meta.artwork ?? meta.backdrop ?? null,
    href: meta.href,
    shape: SHAPE_BY_TYPE[row.item_type] ?? 'square',
    progress: fraction(row.position_seconds, row.duration_seconds),
    source: { itemType: row.item_type, itemId: row.item_id },
  };
}

/** split continue rows into the rails above, keeping server order (newest first) */
export function continueRails(rows: readonly ProgressRow[]) {
  return CONTINUE_GROUPS.map((group) => ({
    ...group,
    cards: rows
      .filter(
        (row) => !row.finished && row.position_seconds > 0 && group.types.includes(row.item_type),
      )
      .map(continueCard),
  })).filter((rail) => rail.cards.length > 0);
}

/** newest first by an iso-ish date string; missing dates sink */
export function newestFirst<T>(
  items: readonly T[],
  date: (item: T) => string | null | undefined,
): T[] {
  return items
    .map((item, index) => ({ item, index, at: date(item) ?? '' }))
    .sort((a, b) => (a.at === b.at ? a.index - b.index : a.at < b.at ? 1 : -1))
    .map((entry) => entry.item);
}

export const posterUrl = (kind: 'movie' | 'show' | 'season' | 'episode', id: number, w = 342) =>
  appURL(`/api/video/poster/${kind}/${id}?w=${w}`);

export const backdropUrl = (kind: 'movie' | 'show', id: number, w = 1280) =>
  appURL(`/api/video/backdrop/${kind}/${id}?w=${w}`);

/** youtube thumbnails go through the video side's allowlisted image proxy */
export const proxiedImage = (url: string | null | undefined) =>
  url
    ? url.startsWith('/')
      ? appURL(url)
      : appURL(`/api/video/img?u=${encodeURIComponent(url)}`)
    : null;

export function recentMovieCards(movies: readonly CatalogMovie[], limit = 20): RailCard[] {
  return newestFirst(movies, (m) => m.added_at)
    .slice(0, limit)
    .map((m) => ({
      key: `movie:${m.id}`,
      title: m.title,
      subtitle: m.year ? String(m.year) : undefined,
      art: m.has_poster ? posterUrl('movie', m.id) : null,
      href: `/player/movies/${m.id}`,
      shape: 'poster' as const,
    }));
}

export function recentEpisodeCards(
  episodes: readonly CatalogRecentEpisode[],
  limit = 20,
): RailCard[] {
  return episodes.slice(0, limit).map((e) => ({
    key: `episode:${e.id}`,
    title: e.show_title,
    subtitle: `S${e.season_number} E${e.episode_number}${e.title ? ` · ${e.title}` : ''}`,
    art: e.has_still ? posterUrl('episode', e.id, 400) : posterUrl('show', e.show_id, 400),
    href: `/player/tv/${e.show_id}`,
    shape: 'wide' as const,
  }));
}

export function recentAlbumCards(albums: readonly LibAlbum[], limit = 20): RailCard[] {
  return albums.slice(0, limit).map((a) => ({
    key: `album:${a.id}`,
    title: a.title,
    subtitle: a.artist_name ?? undefined,
    art: a.image_url ?? a.remote_image_url ?? null,
    href: `/player/music/album/${a.id}`,
    shape: 'square' as const,
  }));
}

export function recentYoutubeCards(videos: readonly CatalogYoutube[], limit = 20): RailCard[] {
  return videos.slice(0, limit).map((v) => ({
    key: `youtube:${v.id}`,
    title: v.title,
    subtitle: v.channel_title ?? undefined,
    art: proxiedImage(v.thumbnail_url),
    href: '/player/youtube',
    shape: 'wide' as const,
  }));
}

export function bookCover(book: OwnedBook): string {
  return book.cover_url
    ? book.cover_url.startsWith('/')
      ? appURL(book.cover_url)
      : book.cover_url
    : appURL(`/api/audiobooks/library/${encodeURIComponent(book.asin)}/cover`);
}

export function recentBookCards(books: readonly OwnedBook[], limit = 20): RailCard[] {
  return newestFirst(books, (b) => b.imported_at)
    .slice(0, limit)
    .map((b) => ({
      key: `audiobook:${b.asin}`,
      title: b.title,
      subtitle: b.author ?? undefined,
      art: bookCover(b),
      href: `/player/audiobooks/${encodeURIComponent(b.asin)}`,
      shape: 'square' as const,
    }));
}

/** an episode's id in the progress store: its guid, else its audio url */
export function episodeKey(episode: PodcastEpisode): string {
  return (episode.guid || episode.enclosure_url || episode.title).slice(0, 200);
}
