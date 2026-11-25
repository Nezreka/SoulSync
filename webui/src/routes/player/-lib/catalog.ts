/**
 * the video catalog arrives as positional rows to stay small on a big
 * library (core/player/catalog.py MOVIE_FIELDS / SHOW_FIELDS). this turns
 * them back into objects, by the field names the server sends with them, so
 * a reordered column can never be read as the wrong thing.
 */

import type {
  CatalogEpisode,
  CatalogMovie,
  CatalogRecentEpisode,
  CatalogShow,
  CatalogYoutube,
  VideoCatalog,
} from '../-player.types';

export interface CatalogWire {
  fields?: { movie?: string[]; show?: string[] };
  genres?: Record<string, string>;
  movies?: unknown[][];
  shows?: unknown[][];
  recent_episodes?: CatalogRecentEpisode[];
  youtube?: CatalogYoutube[];
  kinds?: VideoCatalog['kinds'];
  /** the guide's clock: only sent with the day's frozen pool */
  clock?: { tz?: string | null; offset_minutes?: number | null } | null;
}

function reader(fields: string[] | undefined) {
  const index = new Map((fields ?? []).map((name, i) => [name, i]));
  return (row: unknown[], name: string): unknown => {
    const i = index.get(name);
    return i === undefined ? undefined : row[i];
  };
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);

export function decodeCatalog(wire: CatalogWire): VideoCatalog {
  const genreName = (ids: unknown): string[] =>
    Array.isArray(ids)
      ? ids.map((id) => wire.genres?.[String(id)]).filter((g): g is string => !!g)
      : [];

  const m = reader(wire.fields?.movie);
  const movies: CatalogMovie[] = (wire.movies ?? []).map((row) => ({
    id: Number(m(row, 'id')),
    title: str(m(row, 'title')) ?? '',
    year: num(m(row, 'year')),
    runtime_seconds: num(m(row, 'runtime_seconds')),
    content_rating: str(m(row, 'content_rating')),
    rating: num(m(row, 'rating')),
    genres: genreName(m(row, 'genre_ids')),
    added_at: str(m(row, 'added_at')),
    has_poster: !!m(row, 'has_poster'),
    has_backdrop: !!m(row, 'has_backdrop'),
    studio: str(m(row, 'studio')),
    tmdb_id: num(m(row, 'tmdb_id')),
    collection_id: num(m(row, 'collection_id')),
    collection: str(m(row, 'collection')),
  }));

  const sh = reader(wire.fields?.show);
  const shows: CatalogShow[] = (wire.shows ?? []).map((row) => {
    const episodes = (sh(row, 'episodes') as CatalogEpisode[] | undefined) ?? [];
    return {
      id: Number(sh(row, 'id')),
      title: str(sh(row, 'title')) ?? '',
      year: num(sh(row, 'year')),
      network: str(sh(row, 'network')),
      content_rating: str(sh(row, 'content_rating')),
      rating: num(sh(row, 'rating')),
      genres: genreName(sh(row, 'genre_ids')),
      added_at: str(sh(row, 'added_at')),
      has_poster: !!sh(row, 'has_poster'),
      has_backdrop: !!sh(row, 'has_backdrop'),
      episode_count: num(sh(row, 'episode_count')) ?? episodes.length,
      episodes,
      tmdb_id: num(sh(row, 'tmdb_id')),
    };
  });

  return {
    movies,
    shows,
    recent_episodes: wire.recent_episodes ?? [],
    youtube: wire.youtube ?? [],
    kinds: wire.kinds ?? { movies: false, shows: false, youtube: false },
  };
}
