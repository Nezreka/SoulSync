/**
 * the guide's channels, made from what is actually in the library.
 *
 * auto channels come from facets: one per type, then genre, network, decade
 * and a kids channel. custom channels come from the profile's own playlists
 * and from rules someone writes (a kind, a genre, a decade, kids only). a
 * channel too thin to fill a schedule without repeating every hour is left
 * out rather than shown half empty.
 */

import { appURL } from '@/platform/url-base';

import type { CatalogMovie, CatalogShow, CatalogYoutube, VideoCatalog } from '../-player.types';
import type { GuideItem, ScheduleOptions } from './schedule';

import { youtubeThumb } from './images';
import { posterUrl } from './rails';

export type ChannelKind =
  | 'type'
  | 'genre'
  | 'network'
  | 'decade'
  | 'kids'
  | 'pick'
  | 'collection'
  | 'studio'
  | 'playlist'
  | 'rule'
  | 'server'
  | 'music'
  | 'vibe';

export interface Channel {
  id: string;
  name: string;
  kind: ChannelKind;
  /** a short line under the name: what the channel is made of */
  blurb: string;
  pool: GuideItem[];
  ordered?: boolean;
  /** songs: the grid shows blocks, not three-minute slivers */
  music?: boolean;
  /** the classic guide channel number, given once the list is sorted */
  number?: number;
  /** how it's programmed for this profile (what it has seen, day parts): the guide and the tuner alike */
  schedule?: Omit<ScheduleOptions, 'ordered'>;
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
    art: movie.has_poster ? posterUrl('movie', movie.id) : null,
    kind: 'movie',
  };
}

const episodeCache = new WeakMap<CatalogShow, GuideItem[]>();

/** a show's episodes as guide items, made once per show (a show sits in several channels) */
export function episodeItems(show: CatalogShow): GuideItem[] {
  const hit = episodeCache.get(show);
  if (hit) return hit;
  const href = `/player/tv/${show.id}`;
  const art = show.has_poster ? posterUrl('show', show.id) : null;
  const items = show.episodes.map(([id, season, episode, seconds, title]) => ({
    key: `episode:${id}`,
    title: show.title,
    subtitle: `S${season} E${episode}${title ? ` · ${title}` : ''}`,
    seconds: seconds ?? 0,
    href,
    art,
    kind: 'episode',
  }));
  episodeCache.set(show, items);
  return items;
}

export function youtubeItem(video: CatalogYoutube): GuideItem {
  return {
    key: `youtube:${video.id}`,
    title: video.title,
    subtitle: video.channel_title ?? undefined,
    seconds: video.duration_seconds ?? 0,
    art: youtubeThumb(video.thumbnail_url, 'card', video.id),
    kind: 'youtube',
    href: video.channel_id
      ? `/player/youtube/${encodeURIComponent(video.channel_id)}`
      : '/player/youtube',
  };
}

export interface MusicTrackLike {
  id: string | number;
  title: string;
  artist?: string | null;
  seconds: number | null;
  href?: string;
  art?: string | null;
}

export function trackItem(track: MusicTrackLike): GuideItem {
  return {
    key: `track:${track.id}`,
    title: track.title,
    subtitle: track.artist ?? undefined,
    seconds: track.seconds ?? 0,
    href: track.href,
    art: track.art ?? null,
    kind: 'track',
  };
}

/** enough different items to fill a channel? stops counting once it knows */
function hasEnough(pool: GuideItem[], need = MIN_POOL): boolean {
  const seen = new Set<string>();
  for (const item of pool) {
    seen.add(item.key);
    if (seen.size >= need) return true;
  }
  return false;
}

function add(out: Channel[], channel: Channel) {
  const playable = channel.pool.filter((item) => item.seconds >= 60);
  if (hasEnough(playable)) out.push({ ...channel, pool: playable });
}

/** the "Music" channel: songs from across the library */
export function musicTypeChannel(tracks: readonly MusicTrackLike[]): Channel | null {
  const out: Channel[] = [];
  add(out, {
    id: 'type-music',
    name: 'Music',
    kind: 'music',
    blurb: 'songs from your library',
    pool: tracks.map(trackItem),
    music: true,
  });
  return out[0] ?? null;
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
  const music = musicTypeChannel(tracks);
  if (music) out.push(music);

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

  // ── picks: what a channel programmer would put on ──
  const scored = (x: { rating: number | null }) => (x.rating ?? 0) >= 8;
  add(out, {
    id: 'pick-top',
    name: 'Top rated',
    kind: 'pick',
    blurb: 'films and shows scored 8 or better',
    pool: [...movies.filter(scored).map(movieItem), ...shows.filter(scored).flatMap(episodeItems)],
  });
  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const monthAgo = todayStart.getTime() - 30 * 86_400_000;
  const fresh = (x: { added_at: string | null }) => {
    const t = x.added_at
      ? Date.parse(x.added_at.includes('T') ? x.added_at : `${x.added_at.replace(' ', 'T')}Z`)
      : NaN;
    return Number.isFinite(t) && t >= monthAgo;
  };
  add(out, {
    id: 'pick-new',
    name: 'New arrivals',
    kind: 'pick',
    blurb: 'added in the last month',
    pool: [...movies.filter(fresh).map(movieItem), ...shows.filter(fresh).flatMap(episodeItems)],
  });
  add(out, {
    id: 'pick-classics',
    name: 'Classics',
    kind: 'pick',
    blurb: 'films before 1980, scored 7 or better',
    pool: movies.filter((m) => (m.year ?? 9999) < 1980 && (m.rating ?? 0) >= 7).map(movieItem),
  });
  const genre = (x: { genres: string[] }, g: string) => x.genres.some((y) => y.toLowerCase() === g);
  add(out, {
    id: 'pick-comfort',
    name: 'Comfort TV',
    kind: 'pick',
    blurb: 'sitcoms, back to back',
    pool: shows.filter((x) => genre(x, 'comedy')).flatMap(episodeItems),
  });
  add(out, {
    id: 'pick-late',
    name: 'Late night',
    kind: 'pick',
    blurb: 'horror and thrillers',
    pool: movies.filter((m) => genre(m, 'horror') || genre(m, 'thriller')).map(movieItem),
  });

  // ── kids: a section of their own ──
  const kidSafe = <T extends { content_rating: string | null }>(xs: readonly T[]) =>
    xs.filter((x) => isKidSafe(x.content_rating));
  const little = (x: { content_rating: string | null }) =>
    ['TV-Y', 'TV-Y7', 'TV-Y7-FV', 'G'].includes((x.content_rating ?? '').trim().toUpperCase());
  add(out, {
    id: 'kids',
    name: 'Kids',
    kind: 'kids',
    blurb: 'rated G, PG, TV-Y to TV-PG',
    pool: [...kidSafe(movies).map(movieItem), ...kidSafe(shows).flatMap(episodeItems)],
  });
  add(out, {
    id: 'kids-little',
    name: 'Little ones',
    kind: 'kids',
    blurb: 'rated TV-Y, TV-Y7 and G',
    pool: [...movies.filter(little).map(movieItem), ...shows.filter(little).flatMap(episodeItems)],
  });
  add(out, {
    id: 'kids-cartoons',
    name: 'Cartoons',
    kind: 'kids',
    blurb: 'animation for kids',
    pool: [
      ...kidSafe(movies)
        .filter((m) => genre(m, 'animation'))
        .map(movieItem),
      ...kidSafe(shows)
        .filter((x) => genre(x, 'animation'))
        .flatMap(episodeItems),
    ],
  });
  add(out, {
    id: 'kids-tv',
    name: 'Kids TV',
    kind: 'kids',
    blurb: 'shows for kids, back to back',
    pool: kidSafe(shows).flatMap(episodeItems),
  });
  add(out, {
    id: 'kids-family',
    name: 'Family movies',
    kind: 'kids',
    blurb: 'G and PG films for everyone',
    pool: kidSafe(movies).map(movieItem),
  });

  // ── studios and film series ──
  for (const [name, list] of bySize(groupBy(movies, (m) => (m.studio ? [m.studio] : [])))) {
    if (list.length < 6) continue;
    add(out, {
      id: `studio-${slug(name)}`,
      name,
      kind: 'studio',
      blurb: `${list.length} films`,
      pool: list.map(movieItem),
    });
  }
  for (const [name, list] of bySize(groupBy(movies, (m) => (m.collection ? [m.collection] : [])))) {
    if (list.length < 3) continue;
    // a series airs in order, oldest first, like a marathon
    const inOrder = [...list].sort((a, b) => (a.year ?? 0) - (b.year ?? 0));
    add(out, {
      id: `collection-${slug(name)}`,
      name: name.replace(/\s+collection$/i, ''),
      kind: 'collection',
      blurb: `${list.length} films, in order`,
      pool: inOrder.map(movieItem),
      ordered: true,
    });
  }

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
    music: true,
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

/** a music channel from the server (core/player/music_channels.py) */
export interface ServerMusicChannel {
  id: string;
  name: string;
  kind: 'genre' | 'vibe';
  count: number;
  /** [track id, title, artist, seconds, album id] */
  tracks: [number, string, string, number, number][];
}

export function serverMusicChannel(c: ServerMusicChannel): Channel | null {
  const out: Channel[] = [];
  add(out, {
    id: c.id,
    name: c.name,
    kind: c.kind === 'vibe' ? 'vibe' : 'music',
    blurb: `${c.count.toLocaleString()} songs${c.kind === 'vibe' ? ' · by mood' : ''}`,
    music: true,
    pool: c.tracks.map(([id, title, artist, seconds, albumId]) =>
      trackItem({
        id,
        title,
        artist,
        seconds,
        href: `/player/music/album/${albumId}`,
        art: appURL(`/api/library/v2/artwork/album/${albumId}?size=thumb`),
      }),
    ),
  });
  return out[0] ?? null;
}

export type ChannelFilter = 'all' | 'favorites' | 'video' | 'music' | 'kids';

export function matchesFilter(
  channel: Channel,
  filter: ChannelFilter,
  favorites: ReadonlySet<string>,
) {
  switch (filter) {
    case 'favorites':
      return favorites.has(channel.id);
    case 'music':
      return !!channel.music;
    case 'video':
      return !channel.music;
    case 'kids':
      return channel.kind === 'kids' || (channel.kind === 'rule' && channel.blurb.includes('kids'));
    default:
      return true;
  }
}

export const CHANNEL_GROUPS: { kind: ChannelKind; label: string }[] = [
  { kind: 'network', label: 'Networks' },
  { kind: 'pick', label: 'Picks' },
  { kind: 'server', label: 'Channels for everyone' },
  { kind: 'rule', label: 'Your channels' },
  { kind: 'type', label: 'Everything' },
  { kind: 'genre', label: 'Movie and TV genres' },
  { kind: 'studio', label: 'Studios' },
  { kind: 'collection', label: 'Film series' },
  { kind: 'decade', label: 'Decades' },
  { kind: 'kids', label: 'Kids' },
  { kind: 'playlist', label: 'Your playlists' },
  { kind: 'vibe', label: 'Vibes' },
  { kind: 'music', label: 'Music' },
];

/** channel numbers in guide order, from 101: stable while the library is */
export function numberChannels(channels: readonly Channel[]): Channel[] {
  const order = new Map(CHANNEL_GROUPS.map((g, i) => [g.kind, i]));
  return channels
    .map((c, i) => ({ c, i }))
    .sort((a, b) => (order.get(a.c.kind) ?? 99) - (order.get(b.c.kind) ?? 99) || a.i - b.i)
    .map(({ c }, i) => ({ ...c, number: 101 + i }));
}

// ── channels people make: a spec over the library ──────────────────────

/** what goes on a channel someone made (core/player/lineup.py clean_spec) */
export interface ChannelSpec {
  name: string;
  kinds: ('movies' | 'tv')[];
  genres: string[];
  decades: number[];
  studios: string[];
  networks: string[];
  collection: string | null;
  min_score: number | null;
  kids: boolean;
  added_days: number | null;
  /** one show, start to finish (a marathon) */
  show: number | null;
  /** a channel of these people's work (actors, directors) */
  people: { id: number; name: string }[];
  order: 'shuffle' | 'in_order';
}

/** the films and shows a channel's people are in (its people, together) */
export interface PeopleTitles {
  movies: ReadonlySet<number>;
  shows: ReadonlySet<number>;
}

export interface SavedChannel {
  id: string;
  /** server: an admin made it for everyone; mine: this profile's own */
  scope: 'server' | 'mine';
  spec: ChannelSpec;
}

export const EMPTY_SPEC: ChannelSpec = {
  name: '',
  kinds: ['movies', 'tv'],
  genres: [],
  decades: [],
  studios: [],
  networks: [],
  collection: null,
  min_score: null,
  kids: false,
  added_days: null,
  show: null,
  people: [],
  order: 'shuffle',
};

const DAY_MS = 86_400_000;

function addedWithin(added: string | null, days: number | null, now: number): boolean {
  if (!days) return true;
  const t = added ? Date.parse(added.includes('T') ? added : `${added.replace(' ', 'T')}Z`) : NaN;
  return Number.isFinite(t) && now - t <= days * DAY_MS;
}

function sameWord(list: readonly string[], value: string | null | undefined): boolean {
  if (!list.length) return true;
  const v = (value ?? '').toLowerCase();
  return !!v && list.some((x) => x.toLowerCase() === v);
}

/** a movie or show fits the spec (a show's network, a movie's studio, each only checked on its own kind) */
export function specMatches(
  spec: ChannelSpec,
  title: (CatalogMovie | CatalogShow) & { studio?: string | null; network?: string | null },
  kind: 'movies' | 'tv',
  now: number,
  people?: PeopleTitles | null,
): boolean {
  if (spec.people?.length) {
    // someone's work: only what they're in (nothing until it's known)
    if (!people) return false;
    if (!(kind === 'movies' ? people.movies : people.shows).has(title.id)) return false;
  }
  if (spec.genres.length && !title.genres.some((g) => sameWord(spec.genres, g))) return false;
  if (spec.decades.length && !spec.decades.includes(decadeOf(title.year) ?? -1)) return false;
  if (spec.kids && !isKidSafe(title.content_rating)) return false;
  if (spec.min_score != null && (title.rating ?? 0) < spec.min_score) return false;
  if (!addedWithin(title.added_at, spec.added_days, now)) return false;
  if (kind === 'movies') {
    if (spec.networks.length && !spec.studios.length) return false;
    if (!sameWord(spec.studios, (title as CatalogMovie).studio)) return false;
    if (spec.collection && (title as CatalogMovie).collection !== spec.collection) return false;
  } else {
    if (spec.studios.length && !spec.networks.length) return false;
    if (!sameWord(spec.networks, (title as CatalogShow).network)) return false;
    if (spec.collection) return false;
  }
  return true;
}

/** the words under a channel's name: what it's made of */
export function specBlurb(spec: ChannelSpec, showTitle?: string | null): string {
  if (spec.show) return `${showTitle ?? 'one show'}, start to finish`;
  return [
    (spec.people ?? []).map((p) => p.name).join(', '),
    spec.kinds.map((k) => (k === 'tv' ? 'TV' : 'movies')).join(' + '),
    spec.genres.slice(0, 2).join(', '),
    spec.decades.length ? spec.decades.map((d) => `${d}s`).join(', ') : '',
    [...spec.studios, ...spec.networks].slice(0, 2).join(', '),
    spec.collection ?? '',
    spec.min_score != null ? `★ ${spec.min_score}+` : '',
    spec.added_days ? `added in ${spec.added_days} days` : '',
    spec.kids ? 'kids' : '',
  ]
    .filter(Boolean)
    .join(' · ');
}

/** what a spec would air (the builder's live count; the channel's pool) */
export function specPool(
  spec: ChannelSpec,
  catalog: VideoCatalog,
  now: number,
  people?: PeopleTitles | null,
): GuideItem[] {
  const pool: GuideItem[] = [];
  if (spec.kinds.includes('movies'))
    pool.push(
      ...catalog.movies.filter((m) => specMatches(spec, m, 'movies', now, people)).map(movieItem),
    );
  if (spec.kinds.includes('tv'))
    pool.push(
      ...catalog.shows.filter((x) => specMatches(spec, x, 'tv', now, people)).flatMap(episodeItems),
    );
  return pool;
}

/** a made channel. a marathon needs its show's every episode (``marathon``), in order */
export function specChannel(
  saved: SavedChannel,
  catalog: VideoCatalog,
  now: number,
  marathon?: { title: string; episodes: GuideItem[] } | null,
  people?: PeopleTitles | null,
): Channel | null {
  const spec = saved.spec;
  const pool = spec.show ? (marathon?.episodes ?? []) : specPool(spec, catalog, now, people);
  const out: Channel[] = [];
  add(out, {
    id: `made-${saved.id}`,
    name: spec.name,
    kind: saved.scope === 'server' ? 'server' : 'rule',
    blurb: specBlurb(spec, marathon?.title),
    pool,
    ordered: spec.order === 'in_order',
  });
  return out[0] ?? null;
}

/** a rule from before channels lived on the server, as a spec */
export function specFromRule(rule: ChannelRule): ChannelSpec {
  return {
    ...EMPTY_SPEC,
    name: rule.name,
    kinds: rule.kinds,
    genres: rule.genre ? [rule.genre] : [],
    decades: rule.decade ? [rule.decade] : [],
    kids: !!rule.kidsOnly,
  };
}

// ── a profile's lineup: its order, what it hid, what it renamed ────────

export interface LineupEntry {
  channel_id: string;
  hidden: boolean;
  name: string | null;
}

/**
 * the channels in this profile's order and names; hidden ones kept but
 * marked (the guide leaves them out, the editor shows them). channels the
 * lineup doesn't name keep the guide's own order, after it. numbering is
 * the guide's (numberChannels), which keeps this order within each group.
 */
export function applyLineup(
  channels: readonly Channel[],
  entries: readonly LineupEntry[],
): (Channel & { hidden?: boolean; ownName?: string })[] {
  const base = channels;
  const at = new Map(entries.map((e, i) => [e.channel_id, i]));
  const byId = new Map(entries.map((e) => [e.channel_id, e]));
  return base
    .map((c, i) => ({ c, i, pos: at.get(c.id) }))
    .sort((a, b) => {
      if (a.pos !== undefined && b.pos !== undefined) return a.pos - b.pos;
      if (a.pos !== undefined) return -1;
      if (b.pos !== undefined) return 1;
      return a.i - b.i;
    })
    .map(({ c }) => {
      const e = byId.get(c.id);
      return {
        ...c,
        ownName: c.name,
        name: e?.name || c.name,
        hidden: !!e?.hidden,
      };
    });
}

/** every owned episode of a show, in airing order (specials last): a marathon's pool */
export function marathonItems(show: {
  id: number;
  title: string;
  has_poster?: boolean;
  seasons?: readonly {
    season_number: number;
    episodes: readonly {
      id: number;
      episode_number: number;
      title?: string | null;
      runtime_minutes?: number | null;
      owned?: boolean;
    }[];
  }[];
}): GuideItem[] {
  const href = `/player/tv/${show.id}`;
  const art = show.has_poster !== false ? posterUrl('show', show.id) : null;
  return [...(show.seasons ?? [])]
    .sort((a, b) => (a.season_number || 999) - (b.season_number || 999))
    .flatMap((season) =>
      [...season.episodes]
        .filter((e) => e.owned !== false)
        .sort((a, b) => a.episode_number - b.episode_number)
        .map((e) => ({
          key: `episode:${e.id}`,
          title: show.title,
          subtitle: `S${season.season_number} E${e.episode_number}${e.title ? ` · ${e.title}` : ''}`,
          seconds: (e.runtime_minutes ?? 0) * 60,
          href,
          art,
          kind: 'episode',
        })),
    );
}

/** a channel's picture: four of its titles' artwork (fewer when that's all it has) */
const logoCache = new WeakMap<object, string[]>();
export function channelArt(channel: Pick<Channel, 'pool'>, n = 4): string[] {
  const hit = logoCache.get(channel.pool);
  if (hit) return hit;
  const out: string[] = [];
  const seen = new Set<string>();
  for (const item of channel.pool) {
    if (!item.art || seen.has(item.art)) continue;
    seen.add(item.art);
    out.push(item.art);
    if (out.length >= n) break;
  }
  logoCache.set(channel.pool, out);
  return out;
}

/** a channel that fits what's typed in the guide's search: its name, what it's made of, its number */
export function channelMatches(channel: Channel, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  if (/^\d+$/.test(q)) return String(channel.number ?? '').startsWith(q);
  return `${channel.name} ${channel.blurb}`.toLowerCase().includes(q);
}
