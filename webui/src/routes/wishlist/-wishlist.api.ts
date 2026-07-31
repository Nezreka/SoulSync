import { queryOptions } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type {
  WishlistCycleResponse,
  WishlistStatsResponse,
  WishlistTracksResponse,
} from './-wishlist.types';

export const WISHLIST_QUERY_KEY = ['wishlist'] as const;

// Like the watchlist endpoints, these resolve the profile from the Flask
// SESSION rather than an X-Profile-Id header, so no profile header is sent.
// The profile still keys every query — switching profiles must not serve the
// previous profile's wishlist out of the cache.

export function wishlistStatsQueryOptions(profileId: number) {
  return queryOptions({
    queryKey: [...WISHLIST_QUERY_KEY, 'stats', profileId] as const,
    queryFn: () => readJson<WishlistStatsResponse>(apiClient.get('wishlist/stats')),
  });
}

export function wishlistCycleQueryOptions(profileId: number) {
  return queryOptions({
    queryKey: [...WISHLIST_QUERY_KEY, 'cycle', profileId] as const,
    queryFn: () => readJson<WishlistCycleResponse>(apiClient.get('wishlist/cycle')),
  });
}

export function wishlistTracksQueryOptions(profileId: number, category: 'albums' | 'singles') {
  return queryOptions({
    queryKey: [...WISHLIST_QUERY_KEY, 'tracks', profileId, category] as const,
    queryFn: () =>
      readJson<WishlistTracksResponse>(
        apiClient.get('wishlist/tracks', { searchParams: { category } }),
      ),
  });
}

/**
 * Watchlist artist photos, used to upgrade the orb art.
 *
 * Optional decoration, so a failure resolves to an empty list rather than
 * taking the page down — the vanilla loader caught this one specifically.
 */
export function wishlistArtistPhotosQueryOptions(profileId: number) {
  return queryOptions({
    queryKey: [...WISHLIST_QUERY_KEY, 'artist-photos', profileId] as const,
    queryFn: async () => {
      try {
        const payload = await readJson<{
          success?: boolean;
          artists?: { artist_name?: string | null; image_url?: string | null }[];
        }>(apiClient.get('watchlist/artists'));
        return payload.success ? (payload.artists ?? []) : [];
      } catch {
        return [];
      }
    },
  });
}

interface SuccessResponse {
  success?: boolean;
  error?: string;
}

function assertSuccess(payload: SuccessResponse, fallback: string): void {
  if (payload.success === false) throw new Error(payload.error || fallback);
}

export async function removeWishlistAlbum(albumName: string): Promise<void> {
  const payload = await readJson<SuccessResponse>(
    apiClient.post('wishlist/remove-album', { json: { album_name: albumName } }),
  );
  assertSuccess(payload, 'Failed');
}

export async function removeWishlistTrack(trackId: string): Promise<void> {
  const payload = await readJson<SuccessResponse>(
    apiClient.post('wishlist/remove-track', { json: { spotify_track_id: trackId } }),
  );
  assertSuccess(payload, 'Failed');
}
