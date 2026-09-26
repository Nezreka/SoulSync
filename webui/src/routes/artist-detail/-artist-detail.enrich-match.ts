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
export async function applyManualMatchRequest(
  params: MatchApplyParams,
): Promise<EnrichmentOutcome> {
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
  };
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
