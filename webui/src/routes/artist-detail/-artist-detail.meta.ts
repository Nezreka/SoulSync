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

/**
 * The match-status chip row (library.js:1357-1383): one chip per service,
 * amazon included (12 — one more than the enrich menu, which has no amazon
 * pass), each clickable to rematch.
 */
export const ARTIST_MATCH_SERVICES = [
  {
    key: 'spotify_match_status',
    label: 'Spotify',
    attempted: 'spotify_last_attempted',
    svc: 'spotify',
  },
  {
    key: 'musicbrainz_match_status',
    label: 'MusicBrainz',
    attempted: 'musicbrainz_last_attempted',
    svc: 'musicbrainz',
  },
  {
    key: 'deezer_match_status',
    label: 'Deezer',
    attempted: 'deezer_last_attempted',
    svc: 'deezer',
  },
  {
    key: 'jiosaavn_match_status',
    label: 'JioSaavn',
    attempted: 'jiosaavn_last_attempted',
    svc: 'jiosaavn',
  },
  {
    key: 'audiodb_match_status',
    label: 'AudioDB',
    attempted: 'audiodb_last_attempted',
    svc: 'audiodb',
  },
  {
    key: 'discogs_match_status',
    label: 'Discogs',
    attempted: 'discogs_last_attempted',
    svc: 'discogs',
  },
  {
    key: 'itunes_match_status',
    label: 'iTunes',
    attempted: 'itunes_last_attempted',
    svc: 'itunes',
  },
  {
    key: 'lastfm_match_status',
    label: 'Last.fm',
    attempted: 'lastfm_last_attempted',
    svc: 'lastfm',
  },
  {
    key: 'genius_match_status',
    label: 'Genius',
    attempted: 'genius_last_attempted',
    svc: 'genius',
  },
  { key: 'tidal_match_status', label: 'Tidal', attempted: 'tidal_last_attempted', svc: 'tidal' },
  { key: 'qobuz_match_status', label: 'Qobuz', attempted: 'qobuz_last_attempted', svc: 'qobuz' },
  {
    key: 'amazon_match_status',
    label: 'Amazon',
    attempted: 'amazon_last_attempted',
    svc: 'amazon',
  },
] as const;

export interface ArtistMatchChip {
  service: string;
  label: string;
  status: string;
  className: string;
  title: string;
}

export function artistMatchChips(artist: ArtistInfo): ArtistMatchChip[] {
  const filter = window.filterJiosaavnServiceEntries;
  const services =
    typeof filter === 'function'
      ? (filter([...ARTIST_MATCH_SERVICES], 'svc') as (typeof ARTIST_MATCH_SERVICES)[number][])
      : ARTIST_MATCH_SERVICES.filter((s) => s.svc !== 'jiosaavn');
  return services.map((service) => {
    const record = artist as Record<string, unknown>;
    const status = record[service.key] as string | undefined;
    const attempted = record[service.attempted];
    const state =
      status === 'matched' ? 'matched' : status === 'not_found' ? 'not-found' : 'pending';
    const tip: string[] = [];
    if (attempted) tip.push(`Last: ${new Date(String(attempted)).toLocaleString()}`);
    tip.push('Click to rematch');
    return {
      service: service.svc,
      label: service.label,
      status: status || 'pending',
      className: `enhanced-match-chip clickable ${state}`,
      title: tip.join(' · '),
    };
  });
}

/**
 * The artist-level enrich menu (library.js:1266-1281). Bandcamp intentionally
 * omitted: it has no artist pass (album/track only) — the album-level menu
 * still offers it.
 */
export const ARTIST_ENRICH_SERVICES = [
  { id: 'spotify', label: 'Spotify', icon: '🟢' },
  { id: 'musicbrainz', label: 'MusicBrainz', icon: '🟠' },
  { id: 'deezer', label: 'Deezer', icon: '🟣' },
  { id: 'jiosaavn', label: 'JioSaavn', icon: '🎵' },
  { id: 'discogs', label: 'Discogs', icon: '🟤' },
  { id: 'audiodb', label: 'AudioDB', icon: '🔵' },
  { id: 'itunes', label: 'iTunes', icon: '🔴' },
  { id: 'lastfm', label: 'Last.fm', icon: '⚪' },
  { id: 'genius', label: 'Genius', icon: '🟡' },
  { id: 'tidal', label: 'Tidal', icon: '⬛' },
  { id: 'qobuz', label: 'Qobuz', icon: '🔷' },
] as const;

export function artistEnrichServices(): { id: string; label: string; icon: string }[] {
  const filter = window.filterJiosaavnServiceEntries;
  if (typeof filter === 'function') {
    return filter([...ARTIST_ENRICH_SERVICES], 'id') as {
      id: string;
      label: string;
      icon: string;
    }[];
  }
  return ARTIST_ENRICH_SERVICES.filter((s) => s.id !== 'jiosaavn');
}

/** The collapsible edit form's fields (library.js:1391-1398). */
export const ARTIST_EDIT_FIELDS = [
  { key: 'name', label: 'Artist Name', textarea: false, isArray: false, wide: false },
  { key: 'genres', label: 'Genres (comma separated)', textarea: false, isArray: true, wide: false },
  { key: 'label', label: 'Label', textarea: false, isArray: false, wide: false },
  { key: 'style', label: 'Style', textarea: false, isArray: false, wide: false },
  { key: 'mood', label: 'Mood', textarea: false, isArray: false, wide: false },
  { key: 'summary', label: 'Summary / Bio', textarea: true, isArray: false, wide: true },
] as const;

export function artistEditValue(
  artist: ArtistInfo,
  field: (typeof ARTIST_EDIT_FIELDS)[number],
): string {
  const value = (artist as Record<string, unknown>)[field.key];
  if (field.isArray) return Array.isArray(value) ? value.join(', ') : String(value || '');
  return String(value || '');
}

/**
 * Diff the form against the loaded artist (saveArtistMetadata, library.js:
 * 4472-4485): only changed fields go in the PUT; genres compare as arrays.
 */
export function collectArtistMetaUpdates(
  artist: ArtistInfo,
  values: Record<string, string>,
): Record<string, unknown> {
  const updates: Record<string, unknown> = {};
  for (const field of ARTIST_EDIT_FIELDS) {
    if (!(field.key in values)) continue;
    const value = values[field.key].trim();
    const original = (artist as Record<string, unknown>)[field.key];
    if (field.isArray) {
      const next = value
        ? value
            .split(',')
            .map((g) => g.trim())
            .filter(Boolean)
        : [];
      const prev = Array.isArray(original) ? original : [];
      if (JSON.stringify(next) !== JSON.stringify(prev)) updates[field.key] = next;
    } else if ((value || '') !== ((original as string) || '')) {
      updates[field.key] = value || null;
    }
  }
  return updates;
}

/**
 * The Sync button's toast (library.js:1310-1327). removal_skipped means the
 * server view couldn't be trusted, so nothing was deleted — a warning even
 * when additions landed.
 */
export function syncResultMessage(data: Record<string, unknown>): {
  message: string;
  tone: 'success' | 'warning';
  changed: boolean;
} {
  const n = (key: string) => (data[key] as number) || 0;
  const changed =
    n('new_albums') > 0 ||
    n('new_tracks') > 0 ||
    n('stale_removed') > 0 ||
    n('empty_albums_removed') > 0;
  if (data.removal_skipped) {
    const parts: string[] = [];
    if (n('new_albums') > 0) parts.push(`+${data.new_albums} albums`);
    if (n('new_tracks') > 0) parts.push(`+${data.new_tracks} tracks`);
    if (data.name_updated) parts.push('name updated');
    const added = parts.length ? ` (${parts.join(', ')})` : '';
    return {
      message: `${data.artist_name}: couldn't fully confirm against your media server — skipped removing tracks to be safe${added}.`,
      tone: 'warning',
      changed,
    };
  }
  const parts: string[] = [];
  if (n('new_albums') > 0) parts.push(`+${data.new_albums} albums`);
  if (n('new_tracks') > 0) parts.push(`+${data.new_tracks} tracks`);
  if (n('stale_removed') > 0) parts.push(`${data.stale_removed} stale removed`);
  if (n('empty_albums_removed') > 0)
    parts.push(`${data.empty_albums_removed} empty albums cleaned`);
  if (data.name_updated) parts.push('name updated');
  if (parts.length === 0) parts.push('Already in sync');
  return { message: `${data.artist_name}: ${parts.join(', ')}`, tone: 'success', changed };
}
