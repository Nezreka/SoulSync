import { queryOptions } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import {
  type ArtistDetailResponse,
  type Discography,
  normalizeSource,
} from './-artist-detail.types';

export const ARTIST_DETAIL_QUERY_KEY = ['artist-detail'] as const;

/**
 * The one call that backs first paint.
 *
 * Everything else on the page (gap-fill, the completion stream, enhance
 * eligibility) is fire-and-forget AFTER this resolves — the vanilla loader
 * never awaited any of them, and neither should the route.
 *
 * `source` and `name` are only sent when present, matching the vanilla
 * URLSearchParams build: it set() them conditionally, so a library artist's
 * request carries no query string at all and does not fragment the cache.
 */
export function artistDetailQueryOptions(source: string, id: string, name: string) {
  const normalized = normalizeSource(source);
  const params: Record<string, string> = {};
  if (normalized) params.source = normalized;
  if (name) params.name = name;

  return queryOptions({
    queryKey: [...ARTIST_DETAIL_QUERY_KEY, normalized ?? 'library', id, name] as const,
    queryFn: () =>
      readJson<ArtistDetailResponse>(
        apiClient.get(`artist-detail/${encodeURIComponent(id)}`, {
          searchParams: params,
          // A source artist with a cold provider can take a while; the vanilla
          // fetch had no timeout at all, and ky's default 10s would surface as
          // a spurious failure the old page never had.
          timeout: false,
        }),
      ),
  });
}

/**
 * Unwrap the payload the way the vanilla loader did.
 *
 * It threw on `!response.ok || !data.success`, preferring the server's own
 * `error` string. The status-text half of that message has no equivalent here
 * — readJson already throws on a non-2xx — so only the body case is rebuilt.
 */
export function readArtistDetail(payload: ArtistDetailResponse | undefined): ArtistDetailResponse {
  if (!payload || payload.success === false) {
    throw new Error(payload?.error || 'Failed to load artist data');
  }
  return payload;
}

/**
 * A source-only artist has no library to check ownership against, so the
 * vanilla loader rewrote every null/undefined `owned` to false before render.
 * Without it those releases sit in the "checking" state forever, because
 * nothing will ever stream a result for them.
 *
 * Returns a new object; the response from the cache is not mutated.
 */
export function settleOwnershipForSourceArtist(discography: Discography): Discography {
  const settled: Discography = { ...discography };
  for (const bucket of ['albums', 'eps', 'singles'] as const) {
    const releases = discography[bucket];
    if (!releases) continue;
    settled[bucket] = releases.map((release) =>
      release.owned === null || release.owned === undefined
        ? { ...release, owned: false }
        : release,
    );
  }
  return settled;
}

/** True when the artist has no library record — drives several UI gates. */
export function isSourceOnlyArtist(payload: ArtistDetailResponse): boolean {
  return !payload.artist?.server_source;
}

/**
 * Whether the completion stream should run.
 *
 * The vanilla loader started it only for a LIBRARY artist whose discography
 * still had at least one `owned === null`. Starting it otherwise opens an SSE
 * connection that can never report anything.
 */
export function needsCompletionStream(payload: ArtistDetailResponse): boolean {
  if (isSourceOnlyArtist(payload)) return false;
  const disc = payload.discography;
  if (!disc?.albums) return false;
  return [...(disc.albums ?? []), ...(disc.eps ?? []), ...(disc.singles ?? [])].some(
    (release) => release.owned === null,
  );
}
