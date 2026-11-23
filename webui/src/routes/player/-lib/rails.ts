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
  /** a play button on the art (hover or focus): plays it here, the card still opens its page */
  play?:
    | { kind: 'album' | 'playlist' | 'movie' | 'show'; id: number }
    /** one of soulsync's discovery playlists (flow, a daily mix) */
    | { kind: 'discover'; key: string };
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
  const art =
    row.item_type === 'youtube'
      ? youtubeThumb(meta.artwork ?? meta.backdrop, 'card', row.item_id)
      : remoteImage(meta.artwork ?? meta.backdrop);
  return {
    key: `${row.item_type}:${row.item_id}`,
    title: meta.title || 'Untitled',
    subtitle: meta.subtitle,
    art,
    href: meta.href,
    shape: SHAPE_BY_TYPE[row.item_type] ?? 'square',
    progress: fraction(row.position_seconds, row.duration_seconds),
    source: { itemType: row.item_type, itemId: row.item_id },
  };
}

/**
 * a row of the media server's own resume list (the video side's
 * /dashboard/continue-watching): something started on the tv shows up here
 * too, and a show you finished an episode of offers the next one.
 */
export interface ServerResume {
  kind: 'movie' | 'show';
  id: number | null;
  show_id?: number;
  reason?: 'in_progress' | 'up_next';
  title: string;
  subtitle?: string | null;
  image_url?: string | null;
  runtime_minutes?: number | null;
  view_offset_ms?: number | null;
  last_viewed_at?: string | number | null;
  season_number?: number;
}

/** epoch ms from an iso string or epoch seconds/ms; unknown sinks */
export function whenMs(value: string | number | null | undefined): number {
  if (value === null || value === undefined || value === '') return 0;
  if (typeof value === 'number') return value < 1e12 ? value * 1000 : value;
  const n = Number(value);
  if (Number.isFinite(n)) return whenMs(n);
  const t = Date.parse(
    value.includes('T') || value.endsWith('Z') ? value : `${value.replace(' ', 'T')}Z`,
  );
  return Number.isFinite(t) ? t : 0;
}

export function serverResumeCard(item: ServerResume): RailCard | null {
  if (item.kind === 'movie' && item.id) {
    const runtimeSec = (item.runtime_minutes ?? 0) * 60;
    return {
      key: `movie:${item.id}`,
      title: item.title,
      subtitle: item.subtitle || undefined,
      art: item.image_url ? remoteImage(item.image_url) : backdropUrl('movie', item.id, 780),
      href: `/player/movies/${item.id}`,
      shape: 'wide',
      progress: fraction((item.view_offset_ms ?? 0) / 1000, runtimeSec || null),
      preview: { kind: 'movie', id: item.id },
    };
  }
  if (item.kind === 'show' && item.show_id) {
    const upNext = item.reason === 'up_next';
    return {
      key: item.id ? `episode:${item.id}` : `show:${item.show_id}`,
      title: item.title,
      subtitle: item.subtitle || undefined,
      art: item.image_url ? remoteImage(item.image_url) : backdropUrl('show', item.show_id, 780),
      href: `/player/tv/${item.show_id}${item.season_number !== undefined ? `?season=${item.season_number}` : ''}`,
      shape: 'wide',
      progress: upNext
        ? undefined
        : fraction((item.view_offset_ms ?? 0) / 1000, (item.runtime_minutes ?? 0) * 60 || null),
      badge: upNext ? 'Up next' : undefined,
      preview: { kind: 'show', id: item.show_id },
    };
  }
  return null;
}

/** continue watching is one row of wide stills: a movie shows its backdrop */
function watchingCard(row: ProgressRow): RailCard {
  const card = continueCard(row);
  const id = Number(row.item_id);
  if (row.item_type !== 'movie' || !Number.isFinite(id)) return card;
  return {
    ...card,
    shape: 'wide',
    art: row.meta?.backdrop ? remoteImage(row.meta.backdrop) : backdropUrl('movie', id, 780),
    preview: { kind: 'movie', id },
  };
}

/**
 * split continue rows into the rails above, newest first. the media
 * server's resume list joins continue watching; where both know the same
 * movie or episode, the player's own row wins (it has the exact spot).
 */
export function continueRails(rows: readonly ProgressRow[], server: readonly ServerResume[] = []) {
  const live = rows.filter((row) => !row.finished && row.position_seconds > 0);
  return CONTINUE_GROUPS.map((group) => {
    const own = live
      .filter((row) => group.types.includes(row.item_type))
      .map((row) => ({ at: whenMs(row.updated_at), card: watchingCard(row) }));
    const extra =
      group.id === 'watching'
        ? server
            .map((item) => ({ at: whenMs(item.last_viewed_at), card: serverResumeCard(item) }))
            .filter((x): x is { at: number; card: RailCard } => !!x.card)
        : [];
    const seen = new Set(own.map((x) => x.card.key));
    const merged = [...own, ...extra.filter((x) => !seen.has(x.card.key) && seen.add(x.card.key))];
    merged.sort((a, b) => b.at - a.at);
    return { ...group, cards: merged.map((x) => x.card) };
  }).filter((rail) => rail.cards.length > 0);
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
    play: { kind: 'album', id: Number(a.id) },
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
