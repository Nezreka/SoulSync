/**
 * The search page's browse tiles, answered on the search page itself.
 *
 * a tile used to type its name into the search box (#1935), so "Top trending"
 * found songs called trending. then it sent you off to Discover. now it opens
 * a browse view right here, in the same result cards a search uses, so a
 * download, a preview or an artist link works the way it does for a search.
 *
 * the view lives in the url (/search?browse=genre:jazz), so back and refresh
 * keep it.
 */

import type {
  SearchAlbum,
  SearchArtist,
  SearchPlaylist,
  SearchTrack,
  SourceResults,
} from './-search.types';

import { emptySourceResults } from './-search.helpers';

export interface BrowseTile {
  /** the url value: 'charts', 'new' or 'genre:<name>' */
  id: string;
  label: string;
  from: string;
  to: string;
}

/** quiet two-stop gradients, no emoji */
export const BROWSE_TILES: BrowseTile[] = [
  { id: 'charts', label: 'Top trending', from: '#7a1f3d', to: '#3b0f1f' },
  { id: 'new', label: 'New releases', from: '#3a3a8c', to: '#1c1c48' },
  { id: 'genre:electronic', label: 'Electronic & dance', from: '#0f5e6e', to: '#082f38' },
  { id: 'genre:rock', label: 'Rock & alternative', from: '#7a4a12', to: '#3d250a' },
  { id: 'genre:hip hop', label: 'Hip-hop & rap', from: '#5a2d8c', to: '#2c1646' },
  { id: 'genre:ambient', label: 'Ambient & chill', from: '#1f6b4f', to: '#0f3528' },
  { id: 'genre:jazz', label: 'Jazz & soul', from: '#80305e', to: '#401830' },
  { id: 'genre:soundtrack', label: 'Soundtracks & score', from: '#284f8c', to: '#142846' },
];

export interface BrowseResult extends SourceResults {
  title: string;
  subtitle: string;
  /** other genres the same artists play, for the chips under the title */
  related: string[];
}

export function browseHref(id: string): string {
  return `/search?${new URLSearchParams({ browse: id })}`;
}

/** the genre a browse id names, or null for charts / new releases */
export function browseGenre(id: string): string | null {
  return id.startsWith('genre:') ? id.slice('genre:'.length).trim() || null : null;
}

function titleCase(text: string): string {
  return text.replace(/\b\w/g, (c) => c.toUpperCase());
}

export function browseTitle(id: string): string {
  const tile = BROWSE_TILES.find((t) => t.id === id);
  if (tile) return tile.label;
  const genre = browseGenre(id);
  return genre ? titleCase(genre) : 'Browse';
}

type Row = Record<string, unknown>;
const str = (v: unknown): string => (typeof v === 'string' ? v : v == null ? '' : String(v));
const num = (v: unknown): number | undefined => {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : undefined;
};

async function getJson(url: string, signal?: AbortSignal): Promise<Row> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`${url} answered ${response.status}`);
  return ((await response.json()) ?? {}) as Row;
}

const rows = (data: Row, key: string): Row[] =>
  Array.isArray(data[key]) ? (data[key] as Row[]) : [];

/** /api/discover/genre-deep-dive: metadata-cache rows */
export function genreResult(genre: string, data: Row): BrowseResult {
  const artists: SearchArtist[] = rows(data, 'artists')
    .filter((a) => str(a.name))
    .map((a) => ({
      id: str(a.entity_id) || undefined,
      name: str(a.name),
      image_url: str(a.image_url) || undefined,
      source: str(a.source) || undefined,
      followers: num(a.followers),
    }));
  const albums: SearchAlbum[] = rows(data, 'albums')
    .filter((a) => str(a.name))
    .map((a) => ({
      id: str(a.entity_id) || undefined,
      name: str(a.name),
      artist: str(a.artist_name),
      artists: [{ name: str(a.artist_name) }],
      album_type: str(a.album_type) || 'album',
      image_url: str(a.image_url) || undefined,
      release_date: str(a.release_date) || undefined,
      total_tracks: num(a.total_tracks),
      source: str(a.source) || undefined,
    }));
  const tracks: SearchTrack[] = rows(data, 'tracks')
    .filter((t) => str(t.name))
    .map((t) => ({
      id: str(t.entity_id) || undefined,
      name: str(t.name),
      artist: str(t.artist_name),
      artists: [str(t.artist_name)],
      album: str(t.album_name) || undefined,
      album_id: str(t.album_id) || null,
      duration_ms: num(t.duration_ms),
      image_url: str(t.image_url) || undefined,
      source: str(t.source) || undefined,
      popularity: num(t.popularity),
    }));
  const related = rows(data, 'related_genres')
    .map((g) => str(g.genre).trim())
    .filter(Boolean)
    .slice(0, 10);
  return {
    ...emptySourceResults(),
    title: titleCase(genre),
    subtitle: 'The artists, albums and songs your sources know for this sound',
    artists,
    albums,
    tracks,
    related,
  };
}

/** /api/discover/deezer/editorial (the charts) + /api/discover/popular-picks */
export function chartsResult(charts: Row, picks: Row): BrowseResult {
  const playlists: SearchPlaylist[] = rows(charts, 'playlists')
    .filter((p) => str(p.title || p.name))
    .map((p) => ({
      id: str(p.id) || undefined,
      name: str(p.title || p.name),
      creator: str(p.creator) || undefined,
      track_count: num(p.track_count),
      image_url: str(p.image_url) || undefined,
      source: str(p.source) || 'deezer',
      link: str(p.link) || undefined,
    }));
  return {
    ...emptySourceResults(),
    title: 'Top trending',
    subtitle: 'The charts right now, and the most popular songs in your discovery pool',
    playlists,
    tracks: picksTracks(picks),
    related: [],
  };
}

function picksTracks(data: Row): SearchTrack[] {
  return rows(data, 'tracks')
    .filter((t) => str(t.track_name))
    .map((t) => ({
      id: str(t.track_id) || undefined,
      name: str(t.track_name),
      artist: str(t.artist_name),
      artists: [str(t.artist_name)],
      album: str(t.album_name) || undefined,
      duration_ms: num(t.duration_ms),
      image_url: str(t.album_cover_url) || undefined,
      source: str(t.source) || undefined,
      popularity: num(t.popularity),
    }));
}

/** /api/discover/recent-releases: discovery_recent_albums rows */
export function newReleasesResult(data: Row): BrowseResult {
  const source = str(data.source) || undefined;
  const albums: SearchAlbum[] = rows(data, 'albums')
    .filter((a) => str(a.album_name))
    .map((a) => ({
      id: str(a.album_deezer_id) || str(a.album_spotify_id) || str(a.album_itunes_id) || undefined,
      name: str(a.album_name),
      artist: str(a.artist_name),
      artists: [{ name: str(a.artist_name) }],
      album_type: str(a.album_type) || 'album',
      image_url: str(a.album_cover_url) || undefined,
      release_date: str(a.release_date) || undefined,
      source: str(a.source) || source,
    }));
  return {
    ...emptySourceResults(),
    title: 'New releases',
    subtitle: 'Fresh from the artists you watch and the ones next to them',
    albums,
    related: [],
  };
}

export async function fetchBrowse(id: string, signal?: AbortSignal): Promise<BrowseResult> {
  const genre = browseGenre(id);
  if (genre) {
    const data = await getJson(
      `/api/discover/genre-deep-dive?${new URLSearchParams({ genre })}`,
      signal,
    );
    return genreResult(genre, data);
  }
  if (id === 'charts') {
    // one half failing still shows the other
    const [charts, picks] = await Promise.all([
      getJson('/api/discover/deezer/editorial', signal).catch(() => ({})),
      getJson('/api/discover/popular-picks', signal).catch(() => ({})),
    ]);
    return chartsResult(charts, picks);
  }
  if (id === 'new') {
    return newReleasesResult(await getJson('/api/discover/recent-releases', signal));
  }
  throw new Error(`nothing to browse for ${id}`);
}

export function browseCount(result: BrowseResult): number {
  return (
    result.artists.length + result.albums.length + result.tracks.length + result.playlists.length
  );
}
