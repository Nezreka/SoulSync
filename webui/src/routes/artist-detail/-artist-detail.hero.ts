import type { ArtistInfo } from './-artist-detail.types';

/**
 * Hero provider badges, ported from `updateArtistDetailPageHeaderWithData`
 * (library.js:690).
 *
 * NOTE this list is NOT the same as the library card's badge list, and the
 * difference is real rather than an oversight in either place:
 *   - the hero has **Bandcamp**, the library card does not
 *   - the hero renders `.artist-hero-badge` anchors that open in a new tab;
 *     the card renders `.source-card-icon` divs driven by a delegated handler
 * They are kept separate deliberately — merging them would change one of the
 * two pages.
 */

const LOGOS = {
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
  bandcamp: '/static/img/brands/bandcamp.svg',
  soulsync: '/static/trans2.png',
} as const;

export interface HeroBadge {
  key: string;
  /** Empty string when there is no logo — the text fallback shows instead. */
  logo: string;
  /** Two/three-letter text shown when the logo is missing or fails to load. */
  fallback: string;
  title: string;
  /** null renders a non-clickable div rather than an anchor. */
  url: string | null;
}

/**
 * AudioDB's logo is not a constant — the vanilla reads it off an existing
 * `img.audiodb-logo` in the DOM and returns null when there isn't one. Ported
 * as a lookup so the badge degrades to its 'ADB' text exactly as it does now.
 */
export function audioDbLogoUrl(): string {
  const el = typeof document === 'undefined' ? null : document.querySelector('img.audiodb-logo');
  return el instanceof HTMLImageElement ? el.src : '';
}

/**
 * The AudioDB url slug: spaces become hyphens BEFORE non-alphanumerics are
 * stripped, so "Sigur Rós" -> "Sigur-Rs" (the hyphen survives, the accent does
 * not). Order matters and is easy to get backwards.
 */
export function audioDbSlug(name: string | undefined): string {
  return (name ?? '').replace(/\s+/g, '-').replace(/[^a-zA-Z0-9-]/g, '');
}

/** A placeholder SoulID the backend uses for un-named artists; not shown. */
function isPlaceholderSoulId(soulId: unknown): boolean {
  return String(soulId ?? '').startsWith('soul_unnamed_');
}

/**
 * Badges in the vanilla's declaration order — the order is visible in the hero,
 * so it is part of the contract.
 */
export function buildHeroBadges(artist: ArtistInfo): HeroBadge[] {
  const badges: HeroBadge[] = [];
  const add = (key: string, logo: string, fallback: string, title: string, url: string | null) =>
    badges.push({ key, logo, fallback, title, url });

  if (artist.spotify_artist_id)
    add(
      'spotify',
      LOGOS.spotify,
      'SP',
      'Spotify',
      `https://open.spotify.com/artist/${artist.spotify_artist_id}`,
    );
  if (artist.musicbrainz_id)
    add(
      'musicbrainz',
      LOGOS.musicbrainz,
      'MB',
      'MusicBrainz',
      `https://musicbrainz.org/artist/${artist.musicbrainz_id}`,
    );
  if (artist.deezer_id)
    add(
      'deezer',
      LOGOS.deezer,
      'Dz',
      'Deezer',
      `https://www.deezer.com/artist/${artist.deezer_id}`,
    );
  if (artist.audiodb_id)
    add(
      'audiodb',
      audioDbLogoUrl(),
      'ADB',
      'AudioDB',
      `https://www.theaudiodb.com/artist/${artist.audiodb_id}-${audioDbSlug(artist.name)}`,
    );
  if (artist.itunes_artist_id)
    add(
      'itunes',
      LOGOS.itunes,
      'IT',
      'Apple Music',
      `https://music.apple.com/artist/${artist.itunes_artist_id}`,
    );
  if (artist.lastfm_url) add('lastfm', LOGOS.lastfm, 'LFM', 'Last.fm', String(artist.lastfm_url));
  if (artist.genius_url) add('genius', LOGOS.genius, 'GEN', 'Genius', String(artist.genius_url));
  if (artist.tidal_id)
    add('tidal', LOGOS.tidal, 'TD', 'Tidal', `https://tidal.com/browse/artist/${artist.tidal_id}`);
  if (artist.qobuz_id)
    add('qobuz', LOGOS.qobuz, 'Qz', 'Qobuz', `https://www.qobuz.com/artist/${artist.qobuz_id}`);
  if (artist.discogs_id)
    add(
      'discogs',
      LOGOS.discogs,
      'DC',
      'Discogs',
      `https://www.discogs.com/artist/${artist.discogs_id}`,
    );
  // Amazon and SoulID have no public artist page — rendered, but not linked.
  if (artist.amazon_id) add('amazon', LOGOS.amazon, 'AMZ', 'Amazon Music', null);
  if (artist.bandcamp_url)
    add('bandcamp', LOGOS.bandcamp, 'BC', 'Bandcamp', String(artist.bandcamp_url));
  if (artist.soul_id && !isPlaceholderSoulId(artist.soul_id))
    add('soulsync', LOGOS.soulsync, 'SS', `SoulID: ${artist.soul_id}`, null);

  return badges;
}
