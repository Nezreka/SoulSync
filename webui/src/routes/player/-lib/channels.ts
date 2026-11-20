/**
 * the guide's channels, made from what is actually in the library.
 *
 * auto channels come from facets: one per type, then genre, network, decade
 * and a kids channel. custom channels come from the profile's own playlists
 * and from rules someone writes (a kind, a genre, a decade, kids only). a
 * channel too thin to fill a schedule without repeating every hour is left
 * out rather than shown half empty.
 */

import type { CatalogMovie, CatalogShow, CatalogYoutube, VideoCatalog } from '../-player.types';
import type { GuideItem } from './schedule';

export type ChannelKind = 'type' | 'genre' | 'network' | 'decade' | 'kids' | 'playlist' | 'rule';

export interface Channel {
  id: string;
  name: string;
  kind: ChannelKind;
  /** a short line under the name: what the channel is made of */
  blurb: string;
  pool: GuideItem[];
  ordered?: boolean;
}

/** a channel needs this many distinct timed items before it gets a row */
export const MIN_POOL = 3;

/** ratings a kids channel airs. anything unrated stays off it. */
export const KID_RATINGS = new Set(['G', 'PG', 'TV-Y', 'TV-Y7', 'TV-Y7-FV', 'TV-G', 'TV-PG']);

export function isKidSafe(rating: string | null | undefined): boolean {
  return !!rating && KID_RATINGS.has(rating.trim().toUpperCase());
}

export function decadeOf(year: number | null | undefined): number | null {
  if (!year || !Number.isFinite(year) || year < 1900) return null;
  return Math.floor(year / 10) * 10;
}

export function decadeLabel(decade: number): string {
  return `${String(decade).slice(-2)}s`;
}

export function slug(text: string): string {
  return text
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[^\w]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

export function movieItem(movie: CatalogMovie): GuideItem {
  return {
    key: `movie:${movie.id}`,
    title: movie.title,
    subtitle: movie.year ? String(movie.year) : undefined,
    seconds: movie.runtime_seconds ?? 0,
    href: `/player/movies/${movie.id}`,
  };
}

export function episodeItems(show: CatalogShow): GuideItem[] {
  return show.episodes.map(([id, season, episode, seconds, title]) => ({
    key: `episode:${id}`,
    title: show.title,
    subtitle: `S${season} E${episode}${title ? ` · ${title}` : ''}`,
    seconds: seconds ?? 0,
    href: `/player/tv/${show.id}`,
  }));
}

export function youtubeItem(video: CatalogYoutube): GuideItem {
  return {
    key: `youtube:${video.id}`,
    title: video.title,
    subtitle: video.channel_title ?? undefined,
    seconds: video.duration_seconds ?? 0,
    href: '/player/youtube',
  };
}

export interface MusicTrackLike {
  id: string | number;
  title: string;
  artist?: string | null;
  seconds: number | null;
  href?: string;
}

export function trackItem(track: MusicTrackLike): GuideItem {
  return {
    key: `track:${track.id}`,
    title: track.title,
    subtitle: track.artist ?? undefined,
    seconds: track.seconds ?? 0,
    href: track.href,
  };
}

function distinctItems(pool: GuideItem[]): number {
  return new Set(pool.map((item) => item.key)).size;
}

function add(out: Channel[], channel: Channel) {
  const playable = channel.pool.filter((item) => item.seconds >= 60);
  if (distinctItems(playable) >= MIN_POOL) out.push({ ...channel, pool: playable });
}

function groupBy<T>(items: readonly T[], keys: (item: T) => string[]): Map<string, T[]> {
  const map = new Map<string, T[]>();
  for (const item of items) {
    for (const key of keys(item)) {
      const list = map.get(key);
      if (list) list.push(item);
      else map.set(key, [item]);
    }
  }
  return map;
}

function bySize<T>(map: Map<string, T[]>): [string, T[]][] {
  return [...map.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
}

export function autoChannels(
  catalog: VideoCatalog,
  tracks: readonly MusicTrackLike[] = [],
): Channel[] {
  const out: Channel[] = [];
  const { movies, shows, youtube } = catalog;

  add(out, {
    id: 'type-movies',
    name: 'Movies',
    kind: 'type',
    blurb: 'every film you own',
    pool: movies.map(movieItem),
  });
  add(out, {
    id: 'type-tv',
    name: 'TV',
    kind: 'type',
    blurb: 'episodes from every show',
    pool: shows.flatMap(episodeItems),
  });
  add(out, {
    id: 'type-youtube',
    name: 'YouTube',
    kind: 'type',
    blurb: 'your downloaded videos',
    pool: youtube.map(youtubeItem),
  });
  add(out, {
    id: 'type-music',
    name: 'Music',
    kind: 'type',
    blurb: 'songs from your library',
    pool: tracks.map(trackItem),
  });

  // genres mix films and episodes
  const genreMovies = groupBy(movies, (m) => m.genres);
  const genreShows = groupBy(shows, (s) => s.genres);
  const genres = new Map<string, { movies: CatalogMovie[]; shows: CatalogShow[] }>();
  for (const [name, list] of genreMovies) genres.set(name, { movies: list, shows: [] });
  for (const [name, list] of genreShows) {
    const entry = genres.get(name);
    if (entry) entry.shows = list;
    else genres.set(name, { movies: [], shows: list });
  }
  const genreRows = [...genres.entries()].sort(
    (a, b) =>
      b[1].movies.length + b[1].shows.length - (a[1].movies.length + a[1].shows.length) ||
      a[0].localeCompare(b[0]),
  );
  for (const [name, entry] of genreRows) {
    const parts = [
      entry.movies.length ? `${entry.movies.length} films` : '',
      entry.shows.length ? `${entry.shows.length} shows` : '',
    ].filter(Boolean);
    add(out, {
      id: `genre-${slug(name)}`,
      name,
      kind: 'genre',
      blurb: parts.join(' · '),
      pool: [...entry.movies.map(movieItem), ...entry.shows.flatMap(episodeItems)],
    });
  }

  // a network channel needs more than one show, or it is just that show
  for (const [name, list] of bySize(groupBy(shows, (s) => (s.network ? [s.network] : [])))) {
    if (list.length < 2) continue;
    add(out, {
      id: `network-${slug(name)}`,
      name,
      kind: 'network',
      blurb: `${list.length} shows`,
      pool: list.flatMap(episodeItems),
    });
  }

  const decades = groupBy(movies, (m) => {
    const d = decadeOf(m.year);
    return d === null ? [] : [String(d)];
  });
  for (const [decade, list] of [...decades.entries()].sort((a, b) => Number(a[0]) - Number(b[0]))) {
    add(out, {
      id: `decade-${decade}`,
      name: `${decadeLabel(Number(decade))} movies`,
      kind: 'decade',
      blurb: `${list.length} films from the ${decade}s`,
      pool: list.map(movieItem),
    });
  }

  add(out, {
    id: 'kids',
    name: 'Kids',
    kind: 'kids',
    blurb: 'rated G, PG, TV-Y to TV-PG',
    pool: [
      ...movies.filter((m) => isKidSafe(m.content_rating)).map(movieItem),
      ...shows.filter((s) => isKidSafe(s.content_rating)).flatMap(episodeItems),
    ],
  });

  return out;
}

export interface PlaylistLike {
  id: string | number;
  name: string;
  tracks: MusicTrackLike[];
}

export function playlistChannel(playlist: PlaylistLike): Channel | null {
  const out: Channel[] = [];
  add(out, {
    id: `playlist-${playlist.id}`,
    name: playlist.name,
    kind: 'playlist',
    blurb: `your playlist · ${playlist.tracks.length} songs`,
    pool: playlist.tracks.map(trackItem),
  });
  return out[0] ?? null;
}

export interface ChannelRule {
  id: string;
  name: string;
  kinds: ('movies' | 'tv')[];
  genre?: string | null;
  decade?: number | null;
  kidsOnly?: boolean;
}

function ruleMatches(
  rule: ChannelRule,
  title: { genres: string[]; content_rating: string | null; year: number | null },
) {
  if (rule.genre && !title.genres.some((g) => g.toLowerCase() === rule.genre!.toLowerCase()))
    return false;
  if (rule.decade && decadeOf(title.year) !== rule.decade) return false;
  if (rule.kidsOnly && !isKidSafe(title.content_rating)) return false;
  return true;
}

export function ruleChannel(rule: ChannelRule, catalog: VideoCatalog): Channel | null {
  const pool: GuideItem[] = [];
  if (rule.kinds.includes('movies'))
    pool.push(...catalog.movies.filter((m) => ruleMatches(rule, m)).map(movieItem));
  if (rule.kinds.includes('tv'))
    pool.push(...catalog.shows.filter((s) => ruleMatches(rule, s)).flatMap(episodeItems));
  const out: Channel[] = [];
  const bits = [
    rule.kinds.map((k) => (k === 'tv' ? 'TV' : 'movies')).join(' + '),
    rule.genre ?? '',
    rule.decade ? `${rule.decade}s` : '',
    rule.kidsOnly ? 'kids' : '',
  ].filter(Boolean);
  add(out, { id: `rule-${rule.id}`, name: rule.name, kind: 'rule', blurb: bits.join(' · '), pool });
  return out[0] ?? null;
}

export const CHANNEL_GROUPS: { kind: ChannelKind; label: string }[] = [
  { kind: 'type', label: 'Everything' },
  { kind: 'rule', label: 'Your channels' },
  { kind: 'playlist', label: 'Your playlists' },
  { kind: 'kids', label: 'Kids' },
  { kind: 'genre', label: 'Genres' },
  { kind: 'network', label: 'Networks' },
  { kind: 'decade', label: 'Decades' },
];
