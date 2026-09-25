import type {
  ArtistBadge,
  LibraryAlbum,
  LibraryAlbumsResponse,
  LibraryArtist,
  LibraryArtistsResponse,
  LibraryPagination,
} from './-library.types';

import { getServiceUrl } from '../artist-detail/-artist-detail.enhanced-album';
import { filterJiosaavnEntries } from '../artist-detail/-artist-detail.enrichment';

/**
 * Provider logo paths, mirroring the constants in core.js.
 *
 * They are top-level `const`s in a classic script, which creates a global
 * LEXICAL binding rather than a window property — so a module cannot read
 * them. They are static asset paths, so they are restated here and pinned
 * against core.js by a parity test rather than bridged at runtime.
 */
export const BRAND_LOGOS = {
  musicbrainz: '/static/img/brands/musicbrainz.png',
  deezer: '/static/img/brands/deezer.png',
  spotify: '/static/img/brands/spotify.png',
  itunes: '/static/img/brands/itunes.png',
  lastfm: '/static/img/brands/lastfm.png',
  genius: '/static/img/brands/genius.png',
  tidal: '/static/img/brands/tidal.svg',
  qobuz: '/static/img/brands/qobuz.svg',
  discogs: '/static/img/brands/discogs.svg',
  // Album-level only: no artist carries either id, so these two are used by
  // buildAlbumBadges alone.
  jiosaavn: '/static/img/brands/jiosaavn.webp',
  bandcamp: '/static/img/brands/bandcamp.svg',
  amazon: '/static/amazon.svg',
  soulsync: '/static/trans2.png',
} as const;

/** Badges beyond this many spill into the overflow column. */
export const MAX_BADGES_PER_COLUMN = 6;

/** Card entry animation, capped so the last card does not wait. */
export function cardAnimationDelay(index: number): number {
  return Math.min(index * 20, 600);
}

/**
 * AudioDB's logo. Historically this was ONLY readable off an existing
 * `img.audiodb-logo` node — which was always present while the vanilla
 * dashboard markup sat (hidden) in index.html. The dashboard port rehomed the
 * asset to a real file, and core.js's getAudioDBLogoURL now falls back to that
 * constant — so defer to it first (like artist-detail does), keep the DOM read
 * for parity, and the empty-string tail keeps the 'no logo' text fallback for
 * tests without the shell.
 */
function audioDbLogo(): string {
  const fromCore = window.getAudioDBLogoURL?.();
  if (fromCore) return fromCore;
  if (typeof document === 'undefined') return '';
  return document.querySelector<HTMLImageElement>('img.audiodb-logo')?.src ?? '';
}

/** AudioDB artist URLs carry a slug built from the name. */
function audioDbSlug(name: string | undefined): string {
  return (name ?? '').replace(/\s+/g, '-').replace(/[^a-zA-Z0-9-]/g, '');
}

/**
 * The provider badges for one artist, in the vanilla declaration order —
 * Spotify, MusicBrainz, Deezer, AudioDB, iTunes, Last.fm, Genius, Tidal,
 * Qobuz, Discogs, Amazon, SoulID. Order is visible on the card, so it is
 * part of the contract.
 *
 * Amazon and SoulID have no link (url null); the rest deep-link to the
 * provider.
 */
export function buildArtistBadges(artist: LibraryArtist): ArtistBadge[] {
  const badges: ArtistBadge[] = [];
  const add = (key: string, logo: string, fallback: string, title: string, url: string | null) =>
    badges.push({ key, logo, fallback, title, url });

  if (artist.spotify_artist_id)
    add(
      'spotify',
      BRAND_LOGOS.spotify,
      'SP',
      'Spotify',
      `https://open.spotify.com/artist/${artist.spotify_artist_id}`,
    );
  if (artist.musicbrainz_id)
    add(
      'musicbrainz',
      BRAND_LOGOS.musicbrainz,
      'MB',
      'MusicBrainz',
      `https://musicbrainz.org/artist/${artist.musicbrainz_id}`,
    );
  if (artist.deezer_id)
    add(
      'deezer',
      BRAND_LOGOS.deezer,
      'Dz',
      'Deezer',
      `https://www.deezer.com/artist/${artist.deezer_id}`,
    );
  if (artist.audiodb_id)
    add(
      'audiodb',
      audioDbLogo(),
      'ADB',
      'AudioDB',
      `https://www.theaudiodb.com/artist/${artist.audiodb_id}-${audioDbSlug(artist.name)}`,
    );
  if (artist.itunes_artist_id)
    add(
      'itunes',
      BRAND_LOGOS.itunes,
      'IT',
      'Apple Music',
      `https://music.apple.com/artist/${artist.itunes_artist_id}`,
    );
  if (artist.lastfm_url) add('lastfm', BRAND_LOGOS.lastfm, 'LFM', 'Last.fm', artist.lastfm_url);
  if (artist.genius_url) add('genius', BRAND_LOGOS.genius, 'GEN', 'Genius', artist.genius_url);
  if (artist.tidal_id)
    add(
      'tidal',
      BRAND_LOGOS.tidal,
      'TD',
      'Tidal',
      `https://tidal.com/browse/artist/${artist.tidal_id}`,
    );
  if (artist.qobuz_id)
    add(
      'qobuz',
      BRAND_LOGOS.qobuz,
      'Qz',
      'Qobuz',
      `https://www.qobuz.com/artist/${artist.qobuz_id}`,
    );
  if (artist.discogs_id)
    add(
      'discogs',
      BRAND_LOGOS.discogs,
      'DC',
      'Discogs',
      `https://www.discogs.com/artist/${artist.discogs_id}`,
    );
  if (artist.amazon_id) add('amazon', BRAND_LOGOS.amazon, 'AMZ', 'Amazon Music', null);
  // A placeholder soul_id is not a real identity and must not earn a badge.
  if (artist.soul_id && !String(artist.soul_id).startsWith('soul_unnamed_'))
    add('soulsync', BRAND_LOGOS.soulsync, 'SS', `SoulID: ${artist.soul_id}`, null);

  return badges;
}

/**
 * Split badges into the two rendered columns.
 *
 * Under the cap everything sits in one row. Over it, the FIRST six go to the
 * primary column and the remainder to the overflow column — note the vanilla
 * markup renders the overflow column FIRST in the DOM.
 */
export function splitBadgeColumns(badges: ArtistBadge[]): {
  overflow: ArtistBadge[];
  primary: ArtistBadge[];
  needsOverflow: boolean;
} {
  const needsOverflow = badges.length > MAX_BADGES_PER_COLUMN;
  return {
    needsOverflow,
    primary: needsOverflow ? badges.slice(0, MAX_BADGES_PER_COLUMN) : badges,
    overflow: needsOverflow ? badges.slice(MAX_BADGES_PER_COLUMN) : [],
  };
}

/**
 * Whether the artist can be added to the watchlist.
 *
 * Watching needs an id on the ACTIVE music source, and which id counts flips
 * with the source: on iTunes the iTunes id is preferred, otherwise Spotify.
 * Either id satisfies it — the order only expresses preference.
 */
export function canWatchArtist(artist: LibraryArtist, musicSource: string | undefined): boolean {
  return watchlistArtistId(artist, musicSource) !== null;
}

/**
 * The id the watchlist endpoints are keyed on — the same preference order, so
 * a card that OFFERS the watch badge can always act on it.
 */
export function watchlistArtistId(
  artist: LibraryArtist,
  musicSource: string | undefined,
): string | null {
  const id =
    musicSource === 'iTunes'
      ? artist.itunes_artist_id || artist.spotify_artist_id
      : artist.spotify_artist_id || artist.itunes_artist_id;
  return id ? String(id) : null;
}

/**
 * The provider badges for one ALBUM.
 *
 * Same shape and rendering as the artist badges, but the ids are different
 * columns and mean different things — musicbrainz is a RELEASE here, spotify
 * and itunes are album ids, and JioSaavn and Bandcamp exist only at album
 * level. Genius has no album id at all, so it never appears.
 *
 * The urls come from getServiceUrl, which the artist page's album header
 * already uses: it knows that Last.fm and Bandcamp store a full url rather
 * than an id, and that a Discogs id carries its own master/release tag.
 */
const ALBUM_BADGE_FIELDS = [
  {
    key: 'spotify',
    field: 'spotify_album_id',
    logo: BRAND_LOGOS.spotify,
    fallback: 'SP',
    title: 'Spotify',
  },
  {
    key: 'musicbrainz',
    field: 'musicbrainz_release_id',
    logo: BRAND_LOGOS.musicbrainz,
    fallback: 'MB',
    title: 'MusicBrainz',
  },
  { key: 'deezer', field: 'deezer_id', logo: BRAND_LOGOS.deezer, fallback: 'Dz', title: 'Deezer' },
  { key: 'audiodb', field: 'audiodb_id', logo: '', fallback: 'ADB', title: 'AudioDB' },
  {
    key: 'itunes',
    field: 'itunes_album_id',
    logo: BRAND_LOGOS.itunes,
    fallback: 'IT',
    title: 'Apple Music',
  },
  {
    key: 'lastfm',
    field: 'lastfm_url',
    logo: BRAND_LOGOS.lastfm,
    fallback: 'LFM',
    title: 'Last.fm',
  },
  { key: 'tidal', field: 'tidal_id', logo: BRAND_LOGOS.tidal, fallback: 'TD', title: 'Tidal' },
  { key: 'qobuz', field: 'qobuz_id', logo: BRAND_LOGOS.qobuz, fallback: 'Qz', title: 'Qobuz' },
  {
    key: 'discogs',
    field: 'discogs_id',
    logo: BRAND_LOGOS.discogs,
    fallback: 'DC',
    title: 'Discogs',
  },
  {
    key: 'jiosaavn',
    field: 'jiosaavn_id',
    logo: BRAND_LOGOS.jiosaavn,
    fallback: 'JS',
    title: 'JioSaavn',
  },
  {
    key: 'bandcamp',
    field: 'bandcamp_url',
    logo: BRAND_LOGOS.bandcamp,
    fallback: 'BC',
    title: 'Bandcamp',
  },
  {
    key: 'amazon',
    field: 'amazon_id',
    logo: BRAND_LOGOS.amazon,
    fallback: 'AMZ',
    title: 'Amazon Music',
  },
] as const;

export function buildAlbumBadges(album: LibraryAlbum): ArtistBadge[] {
  // JioSaavn is off unless the shell says otherwise — the same gate the artist
  // page's album header applies, so one album cannot show a badge there and
  // hide it here.
  const badges: ArtistBadge[] = filterJiosaavnEntries(ALBUM_BADGE_FIELDS, 'key')
    .filter((entry) => album[entry.field])
    .map((entry) => ({
      key: entry.key,
      // AudioDB's logo is only resolvable at call time (see audioDbLogo).
      logo: entry.key === 'audiodb' ? audioDbLogo() : entry.logo,
      fallback: entry.fallback,
      title: entry.title,
      url: getServiceUrl(entry.key, 'album', album[entry.field]),
    }));

  // A placeholder soul_id is not a real identity and must not earn a badge.
  if (album.soul_id && !String(album.soul_id).startsWith('soul_unnamed_')) {
    badges.push({
      key: 'soulsync',
      logo: BRAND_LOGOS.soulsync,
      fallback: 'SS',
      title: `SoulID: ${album.soul_id}`,
      url: null,
    });
  }
  return badges;
}

/** One owned track of an album, from /api/library/albums/<id>/tracks. */
export interface LibraryAlbumTrack {
  id: string | number;
  title: string;
  track_number?: number | null;
  file_path: string;
  duration?: number | null;
  bitrate?: number | null;
}

/**
 * Shape one owned track for the player queue.
 *
 * The same fields the artist page's album rows queue (queueTrackPayload):
 * `is_library` and `playback_status` are what make the player read the file
 * instead of treating the row as a miss to download.
 */
export function albumQueueTrack(track: LibraryAlbumTrack, album: LibraryAlbum) {
  const title = track.title || 'Unknown Track';
  return {
    title,
    name: title,
    artist: album.artist_name || 'Unknown Artist',
    artists: [{ name: album.artist_name || 'Unknown Artist' }],
    album: album.title || 'Unknown Album',
    file_path: track.file_path,
    filename: track.file_path,
    is_library: true,
    playback_status: 'ready',
    image_url: album.thumb_url ?? null,
    id: track.id,
    artist_id: album.artist_id,
    album_id: album.id,
    track_number: track.track_number,
    duration: track.duration,
    bitrate: track.bitrate,
  };
}

/** "12 tracks" / "1 track"; empty when the artist has none. */
export function trackCountLabel(count: number | undefined): string {
  if (!count || count <= 0) return '';
  return `${count} track${count !== 1 ? 's' : ''}`;
}

/**
 * Unwrap /api/library/artists.
 *
 * `success: false` carries the reason in `error`, and the vanilla loader threw
 * it so the catch could toast it. Throwing here lets React Query own the error
 * state instead of it being swallowed into an empty grid.
 */
export function readArtistsResponse(payload: LibraryArtistsResponse | undefined): {
  artists: LibraryArtist[];
  pagination: LibraryPageState;
} {
  if (payload?.success === false) throw new Error(payload.error || 'Failed to load artists');
  return { artists: payload?.artists ?? [], pagination: readPagination(payload?.pagination) };
}

/** Unwrap /api/library/albums. Same contract as the artists response. */
export function readAlbumsResponse(payload: LibraryAlbumsResponse | undefined): {
  albums: LibraryAlbum[];
  pagination: LibraryPageState;
} {
  if (payload?.success === false) throw new Error(payload.error || 'Failed to load albums');
  return { albums: payload?.albums ?? [], pagination: readPagination(payload?.pagination) };
}

interface LibraryPageState {
  page: number;
  totalPages: number;
  totalCount: number;
  hasPrev: boolean;
  hasNext: boolean;
}

function readPagination(p: LibraryPagination | undefined): LibraryPageState {
  return {
    page: p?.page ?? 1,
    totalPages: p?.total_pages ?? 0,
    totalCount: p?.total_count ?? 0,
    hasPrev: p?.has_prev ?? false,
    hasNext: p?.has_next ?? false,
  };
}
