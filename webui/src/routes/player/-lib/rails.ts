/**
 * shaping the home rails: continue rows from the progress store, recently
 * added rows from each library. pure, so tested without a server.
 */

import { appURL } from '@/platform/url-base';

import type {
  CatalogMovie,
  CatalogRecentEpisode,
  CatalogShow,
  CatalogYoutube,
  LibAlbum,
  OwnedBook,
  PodcastEpisode,
  ProgressRow,
  YtChannel,
  YtVideo,
} from '../-player.types';

import {
  ago as agoShort,
  clockDuration as clockDurationShort,
  compactCount as compactCountShort,
} from './format';
import {
  CARD_SIZES,
  libraryThumb,
  posterSet,
  remoteImage,
  stillSet,
  WIDE_SIZES,
  youtubeThumb,
  type ImgSize,
} from './images';

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
  /** the widths the art comes in (images.ts) and how wide it shows */
  artSet?: string;
  artSizes?: string;
  /** a small label over the artwork's corner: a duration, an episode count */
  badge?: string;
  /** you have it: a small check on the art, the words in its tooltip */
  have?: string;
  /** not yours: the card is dimmed, the words in its tooltip */
  muted?: string;
  /** the title this card previews on hover or focus */
  preview?: { kind: 'movie' | 'show'; id: number };
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
    art: remoteImage(meta.artwork ?? meta.backdrop),
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
    .map(movieCard);
}

export function movieCard(m: CatalogMovie): RailCard {
  return {
    key: `movie:${m.id}`,
    title: m.title,
    subtitle: [m.year, m.runtime_seconds ? runtimeShort(m.runtime_seconds) : '']
      .filter(Boolean)
      .join(' · '),
    ...(m.has_poster ? posterArt('movie', m.id) : { art: null }),
    href: `/player/movies/${m.id}`,
    shape: 'poster',
    badge: m.rating ? `★ ${m.rating.toFixed(1)}` : undefined,
    preview: { kind: 'movie', id: m.id },
  };
}

export function showCard(x: CatalogShow): RailCard {
  return {
    key: `show:${x.id}`,
    title: x.title,
    subtitle: [x.year, x.network].filter(Boolean).join(' · '),
    ...(x.has_poster ? posterArt('show', x.id) : { art: null }),
    href: `/player/tv/${x.id}`,
    shape: 'poster',
    badge: `${x.episode_count} ep`,
    preview: { kind: 'show', id: x.id },
  };
}

function runtimeShort(seconds: number): string {
  const m = Math.round(seconds / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
}

/**
 * "more like this": titles sharing the most genres with this one, the better
 * rated first. real library data, nothing recommended from outside it.
 */
export function moreLikeThis<T extends { id: number; genres: string[]; rating: number | null }>(
  items: readonly T[],
  self: { id: number; genres: string[] },
  limit = 20,
): T[] {
  const mine = new Set(self.genres.map((g) => g.toLowerCase()));
  if (!mine.size) return [];
  return items
    .filter((x) => x.id !== self.id)
    .map((x) => ({ x, shared: x.genres.filter((g) => mine.has(g.toLowerCase())).length }))
    .filter((e) => e.shared > 0)
    .sort((a, b) => b.shared - a.shared || (b.x.rating ?? 0) - (a.x.rating ?? 0))
    .slice(0, limit)
    .map((e) => e.x);
}

export function recentEpisodeCards(
  episodes: readonly CatalogRecentEpisode[],
  limit = 20,
): RailCard[] {
  return episodes.slice(0, limit).map((e) => ({
    key: `episode:${e.id}`,
    title: e.show_title,
    subtitle: `S${e.season_number} E${e.episode_number}${e.title ? ` · ${e.title}` : ''}`,
    ...(e.has_still
      ? { art: stillSet(e.id).src, artSet: stillSet(e.id).srcSet, artSizes: WIDE_SIZES }
      : { art: posterUrl('show', e.show_id, 342) }),
    href: `/player/tv/${e.show_id}`,
    shape: 'wide' as const,
  }));
}

export function recentAlbumCards(albums: readonly LibAlbum[], limit = 20): RailCard[] {
  return albums.slice(0, limit).map((a) => ({
    key: `album:${a.id}`,
    title: a.title,
    subtitle: a.artist_name ?? undefined,
    art: libraryThumb(a.image_url) ?? remoteImage(a.remote_image_url),
    href: `/player/music/album/${a.id}`,
    shape: 'square' as const,
  }));
}

export function recentYoutubeCards(videos: readonly CatalogYoutube[], limit = 20): RailCard[] {
  return videos.slice(0, limit).map((v) => ({
    key: `youtube:${v.id}`,
    title: v.title,
    subtitle: v.channel_title ?? undefined,
    art: youtubeThumb(v.thumbnail_url, 'card', v.id),
    href: v.channel_id ? `/player/youtube/${encodeURIComponent(v.channel_id)}` : '/player/youtube',
    shape: 'wide' as const,
    badge: v.duration_seconds ? clockDurationShort(v.duration_seconds) : undefined,
  }));
}

export function bookCover(book: OwnedBook, size: ImgSize = 'card'): string {
  return book.cover_url
    ? book.cover_url.startsWith('/')
      ? appURL(book.cover_url)
      : (remoteImage(book.cover_url, size) ?? book.cover_url)
    : appURL(`/api/audiobooks/library/${encodeURIComponent(book.asin)}/cover`);
}

function posterArt(kind: 'movie' | 'show', id: number) {
  const set = posterSet(kind, id);
  return { art: set.src, artSet: set.srcSet, artSizes: CARD_SIZES };
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

/** a youtube video card: thumbnail, duration on the corner, on disk or embed */
export function ytVideoCard(v: YtVideo, showChannel = true): RailCard {
  const bits = [
    showChannel ? v.channel_title : '',
    v.views ? `${compactCountShort(v.views)} views` : '',
    agoShort(v.published_at),
  ].filter(Boolean);
  return {
    key: `yt:${v.id}`,
    title: v.title,
    subtitle: bits.join(' · '),
    art: youtubeThumb(v.thumbnail, 'card', v.id),
    href: v.channel_id ? `/player/youtube/${encodeURIComponent(v.channel_id)}` : undefined,
    shape: 'wide',
    badge: v.duration_seconds ? clockDurationShort(v.duration_seconds) : undefined,
    have: v.downloaded ? 'Downloaded: plays from your library' : undefined,
  };
}

export function ytChannelCard(c: YtChannel): RailCard {
  return {
    key: `ytc:${c.id}`,
    title: c.title,
    subtitle: [
      c.subscribers ? `${compactCountShort(c.subscribers)} subscribers` : '',
      c.downloaded_count ? `${c.downloaded_count} downloaded` : '',
    ]
      .filter(Boolean)
      .join(' · '),
    art: remoteImage(c.avatar),
    href: `/player/youtube/${encodeURIComponent(c.id)}`,
    shape: 'square',
  };
}
