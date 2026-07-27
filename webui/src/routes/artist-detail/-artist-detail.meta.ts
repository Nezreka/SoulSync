/**
 * Enhanced-view artist meta panel, ported from `renderArtistMetaPanel`
 * (library.js:2968).
 *
 * Only the data-shaped parts live here: which id badges exist and in what
 * order. The panel's admin actions and the reorganize-status mount stay with
 * the component, since they are behaviour rather than derivation.
 */

import type { ArtistInfo } from './-artist-detail.types';

export interface IdBadgeSource {
  /** Field on the artist record that holds the id or url. */
  key: string;
  label: string;
  /** Service slug — drives the badge's icon and its deep link. */
  svc: string;
}

/**
 * Declaration order is the on-screen order.
 *
 * This is a THIRD provider list, and it is deliberately not the hero's:
 *   - it has JioSaavn (experimental, filtered at runtime); the hero does not
 *   - it has NO Bandcamp and NO SoulID; the hero has both
 *   - it keys off `*_id` field names because the badges are built from the
 *     artist record, not from a fixed set
 * Do not merge with buildHeroBadges — they show different things.
 */
export const ID_BADGE_SOURCES: readonly IdBadgeSource[] = [
  { key: 'spotify_artist_id', label: 'Spotify', svc: 'spotify' },
  { key: 'musicbrainz_id', label: 'MusicBrainz', svc: 'musicbrainz' },
  { key: 'deezer_id', label: 'Deezer', svc: 'deezer' },
  { key: 'jiosaavn_id', label: 'JioSaavn', svc: 'jiosaavn' },
  { key: 'audiodb_id', label: 'AudioDB', svc: 'audiodb' },
  { key: 'discogs_id', label: 'Discogs', svc: 'discogs' },
  { key: 'itunes_artist_id', label: 'iTunes', svc: 'itunes' },
  { key: 'lastfm_url', label: 'Last.fm', svc: 'lastfm' },
  { key: 'genius_url', label: 'Genius', svc: 'genius' },
  { key: 'tidal_id', label: 'Tidal', svc: 'tidal' },
  { key: 'qobuz_id', label: 'Qobuz', svc: 'qobuz' },
  { key: 'amazon_id', label: 'Amazon Music', svc: 'amazon' },
] as const;

export interface IdBadge extends IdBadgeSource {
  value: string;
}

/**
 * JioSaavn is filtered by the SAME shared helper the enrichment rings use, but
 * keyed on 'svc' here rather than 'key' — the vanilla passes a different id
 * field for each list, and passing the wrong one silently disables the filter.
 */
export function visibleIdBadgeSources(
  sources: readonly IdBadgeSource[] = ID_BADGE_SOURCES,
): IdBadgeSource[] {
  const filter = window.filterJiosaavnServiceEntries;
  if (typeof filter === 'function') {
    return filter([...sources], 'svc') as IdBadgeSource[];
  }
  return sources.filter((s) => s.svc !== 'jiosaavn');
}

/** Badges for the ids this artist actually has, in declaration order. */
export function buildIdBadges(
  artist: ArtistInfo,
  sources: IdBadgeSource[] = visibleIdBadgeSources(),
): IdBadge[] {
  const badges: IdBadge[] = [];
  for (const source of sources) {
    const value = (artist as Record<string, unknown>)[source.key];
    // Truthiness, matching the vanilla — a 0 id counts as absent.
    if (value) badges.push({ ...source, value: String(value) });
  }
  return badges;
}

/** The panel falls back to this rather than rendering an empty heading. */
export function artistDisplayName(artist: ArtistInfo): string {
  return artist.name || 'Unknown Artist';
}
