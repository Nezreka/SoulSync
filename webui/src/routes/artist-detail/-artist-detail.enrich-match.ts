/**
 * Enrichment + manual matching (runEnrichment library.js:5237, manual match
 * modal 4625-4840, clear-match 4680). The requests and their toast shaping;
 * the modal UI lives in -ui/manual-match-modal.tsx.
 */

import type { EnhancedData } from './-artist-detail.enhanced';

/** Modal titles / placeholders use the full names (4630-4634). */
export const MATCH_SERVICE_LABELS: Record<string, string> = {
  spotify: 'Spotify',
  musicbrainz: 'MusicBrainz',
  deezer: 'Deezer',
  audiodb: 'AudioDB',
  itunes: 'iTunes',
  lastfm: 'Last.fm',
  genius: 'Genius',
  tidal: 'Tidal',
  qobuz: 'Qobuz',
  amazon: 'Amazon Music',
  bandcamp: 'Bandcamp',
};

export function matchServiceLabel(service: string): string {
  return MATCH_SERVICE_LABELS[service] || service;
}

/**
 * One enrichment at a time, page-wide — the vanilla's module-level
 * _enrichmentInFlight (5235). Deliberately module scope: the lock must span
 * every mount point (album menus, future artist menu), not one component.
 */
let enrichmentInFlight = false;

export interface EnrichmentParams {
  entityType: 'artist' | 'album';
  entityId: unknown;
  service: string;
  name: string;
  artistName: string;
  artistId: unknown;
}

export interface EnrichmentOutcome {
  /** Fresh payload when the backend returned one (updated_data). */
  updatedData: EnhancedData | null;
}

/**
 * POST /api/library/enrich with the vanilla's toast sequence: an info toast up
 * front, per-service success/failure summaries after, 429 as "already in
 * progress". Throws only on transport/API errors, after toasting them.
 */
export async function runEnrichmentRequest(params: EnrichmentParams): Promise<EnrichmentOutcome> {
  if (enrichmentInFlight) {
    window.showToast?.('An enrichment is already in progress', 'error');
    return { updatedData: null };
  }
  enrichmentInFlight = true;
  window.showToast?.(`Enriching ${params.entityType} from ${params.service}...`, 'info');

  try {
    const response = await fetch('/api/library/enrich', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        entity_type: params.entityType,
        entity_id: params.entityId,
        service: params.service,
        name: params.name,
        artist_name: params.artistName,
        artist_id: params.artistId,
      }),
    });
    const result = await response.json();

    if (response.status === 429) {
      window.showToast?.(result.error || 'Another enrichment is in progress', 'error');
      return { updatedData: null };
    }
    if (!result.success) throw new Error(result.error || 'Enrichment failed');

    const results: Record<string, { success: boolean; error?: string }> = result.results || {};
    const successes = Object.entries(results)
      .filter(([, r]) => r.success)
      .map(([s]) => s);
    const failures = Object.entries(results)
      .filter(([, r]) => !r.success)
      .map(([s, r]) => `${s}: ${r.error}`);
    if (successes.length > 0)
      window.showToast?.(`Enriched from: ${successes.join(', ')}`, 'success');
    if (failures.length > 0) window.showToast?.(`Failed: ${failures.join('; ')}`, 'error');

    return {
      updatedData: result.updated_data && result.updated_data.success ? result.updated_data : null,
    };
  } catch (error) {
    window.showToast?.(`Enrichment error: ${(error as Error).message}`, 'error');
    return { updatedData: null };
  } finally {
    enrichmentInFlight = false;
  }
}

/** Test hook: the lock is module state and must be resettable between tests. */
export function _resetEnrichmentLock(): void {
  enrichmentInFlight = false;
}

// ---- Manual match (4726-4840) ----

/**
 * What a service's match search takes besides a name (#1901): a pasted link,
 * a bare id, or both. Mirrors core/library/direct_id.parse_direct_ref, so the
 * hint never promises a paste the backend won't recognise.
 */
const PASTE_HINTS: Record<string, 'link or id' | 'link' | 'id'> = {
  spotify: 'link or id',
  musicbrainz: 'link or id',
  deezer: 'link or id',
  itunes: 'link or id',
  discogs: 'link or id',
  tidal: 'link or id',
  qobuz: 'link or id',
  amazon: 'link or id',
  audiodb: 'link or id',
  genius: 'id',
  jiosaavn: 'link',
  lastfm: 'link',
  bandcamp: 'link',
};

export function matchSearchPlaceholder(service: string, label: string): string {
  const paste = PASTE_HINTS[service];
  return paste ? `Search ${label}, or paste a ${label} ${paste}` : `Search ${label}…`;
}

export interface MatchSearchResult {
  id: string;
  name?: string;
  extra?: string;
  image?: string;
  /** A proxying provider (e.g. hydrabase) that actually served the result. */
  provider?: string;
}

export async function searchServiceRequest(
  service: string,
  entityType: string,
  query: string,
): Promise<MatchSearchResult[]> {
  const response = await fetch('/api/library/search-service', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ service, entity_type: entityType, query: query.trim() }),
  });
  const data = await response.json();
  if (!data.success) throw new Error(data.error);
  return data.results || [];
}

export interface MatchApplyParams {
  entityType: string;
  entityId: unknown;
  service: string;
  serviceId: string;
  artistId: unknown;
}

/** PUT /api/library/manual-match; hands back updated_data when provided. */
/** A manual match's outcome: the refreshed view, and what the watchlist did. */
export interface MatchOutcome extends EnrichmentOutcome {
  /** Watched artists that now point at the new match too (#1901). */
  watchlistUpdated: string[];
  /** Watched artists left alone: another one already has that id. */
  watchlistSkipped: string[];
}

export async function applyManualMatchRequest(params: MatchApplyParams): Promise<MatchOutcome> {
  const response = await fetch('/api/library/manual-match', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      entity_type: params.entityType,
      entity_id: params.entityId,
      service: params.service,
      service_id: params.serviceId,
      artist_id: params.artistId,
    }),
  });
  const result = await response.json();
  if (!result.success) throw new Error(result.error);
  return {
    updatedData: result.updated_data && result.updated_data.success ? result.updated_data : null,
    watchlistUpdated: Array.isArray(result.watchlist_updated) ? result.watchlist_updated : [],
    watchlistSkipped: Array.isArray(result.watchlist_skipped) ? result.watchlist_skipped : [],
  };
}

/**
 * Tell the user what the watchlist did after a match: nothing when it had no
 * entry for the artist, a note when it followed, a warning when it couldn't.
 */
export function announceWatchlistFollow(outcome: MatchOutcome, label: string): string {
  if (outcome.watchlistSkipped.length) {
    window.showToast?.(
      `Your watchlist still has the old ${label} match for ${outcome.watchlistSkipped.join(', ')}: another watched artist already uses this one`,
      'warning',
    );
  }
  return outcome.watchlistUpdated.length ? '. Your watchlist follows it too' : '';
}

/**
 * PUT /api/library/clear-match — revert a wrong match to not_found.
 *
 * The vanilla's success path called renderEnhancedArtistView, a function that
 * exists nowhere (a live ReferenceError: the toast fired, the modal closed,
 * and the view silently never refreshed). Here updated_data flows through the
 * same onUpdated path a successful match uses.
 */
export async function clearMatchRequest(
  params: Omit<MatchApplyParams, 'serviceId'>,
): Promise<EnrichmentOutcome> {
  const response = await fetch('/api/library/clear-match', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      entity_type: params.entityType,
      entity_id: params.entityId,
      service: params.service,
      artist_id: params.artistId,
    }),
  });
  const result = await response.json();
  if (!result.success) throw new Error(result.error || 'Failed to clear match');
  return {
    updatedData: result.updated_data && result.updated_data.success ? result.updated_data : null,
  };
}

/**
 * Fold a fresh server payload into the loaded one IN PLACE — the established
 * Enhanced-view pattern (applyBatch/removeAlbum mutate the prop; the caller's
 * own setState re-renders what is on screen). Returns the caller's album out
 * of the new payload so its panel can re-render with fresh chips.
 */
export function foldUpdatedData(
  current: EnhancedData | null,
  updated: EnhancedData,
  albumId?: unknown,
): Record<string, unknown> | null {
  if (current) {
    current.artist = updated.artist;
    current.albums = updated.albums;
  }
  if (albumId === undefined) return null;
  return (updated.albums ?? []).find((a) => String(a.id) === String(albumId)) ?? null;
}
