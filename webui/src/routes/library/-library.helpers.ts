import type { ArtistBadge, LibraryArtist, LibraryArtistsResponse } from './-library.types';

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
 * AudioDB has no fixed logo path — the vanilla card read it off an existing
 * `img.audiodb-logo` in the document. Same lookup, same "no logo, show the
 * fallback text" outcome when it is absent.
 */
function audioDbLogo(): string {
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
  pagination: {
    page: number;
    totalPages: number;
    totalCount: number;
    hasPrev: boolean;
    hasNext: boolean;
  };
} {
  if (payload?.success === false) throw new Error(payload.error || 'Failed to load artists');
  const p = payload?.pagination;
  return {
    artists: payload?.artists ?? [],
    pagination: {
      page: p?.page ?? 1,
      totalPages: p?.total_pages ?? 0,
      totalCount: p?.total_count ?? 0,
      hasPrev: p?.has_prev ?? false,
      hasNext: p?.has_next ?? false,
    },
  };
}
