import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';

import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';

import type { ParsedWishlistTrack } from '../-wishlist.types';

import {
  wishlistArtistPhotosQueryOptions,
  wishlistCycleQueryOptions,
  wishlistStatsQueryOptions,
  wishlistTracksQueryOptions,
} from '../-wishlist.api';
import {
  buildArtistImageMap,
  groupWishlistArtists,
  parseWishlistTrack,
  trackCountLabel,
} from '../-wishlist.helpers';
import { WishlistOrb } from './wishlist-orb';

export function WishlistPage() {
  useReactPageShell('wishlist');

  const { profileId } = useProfile();

  const statsQuery = useQuery(wishlistStatsQueryOptions(profileId));
  const cycleQuery = useQuery(wishlistCycleQueryOptions(profileId));
  const albumsQuery = useQuery(wishlistTracksQueryOptions(profileId, 'albums'));
  const singlesQuery = useQuery(wishlistTracksQueryOptions(profileId, 'singles'));
  const photosQuery = useQuery(wishlistArtistPhotosQueryOptions(profileId));

  const total = statsQuery.data?.total ?? 0;
  const albumCount = statsQuery.data?.albums ?? 0;
  const singleCount = statsQuery.data?.singles ?? 0;
  const currentCycle = cycleQuery.data?.cycle || 'albums';

  const artistImages = useMemo(
    () =>
      buildArtistImageMap(
        [albumsQuery.data ?? {}, singlesQuery.data ?? {}],
        photosQuery.data ?? [],
      ),
    [albumsQuery.data, singlesQuery.data, photosQuery.data],
  );

  const groups = useMemo(() => {
    const parse = (rows: unknown[] | undefined, type: 'album' | 'single') =>
      (rows ?? [])
        .map((row) => parseWishlistTrack(row as never, type))
        .filter((t): t is ParsedWishlistTrack => t !== null);

    return groupWishlistArtists(
      parse(albumsQuery.data?.tracks, 'album'),
      parse(singlesQuery.data?.tracks, 'single'),
    );
  }, [albumsQuery.data?.tracks, singlesQuery.data?.tracks]);

  return (
    <div className="page-shell wishlist-page-container">
      <div className="wishlist-page-header">
        <div className="wishlist-page-header-left">
          <h2 className="wishlist-page-title">
            <span className="wishlist-page-title-icon">⭐</span>
            Wishlist
          </h2>
          <div className="wishlist-page-meta">
            <span className="wishlist-page-count">{trackCountLabel(total)}</span>
            <span className="wishlist-page-timer">Next Auto: --</span>
          </div>
        </div>
      </div>

      {total === 0 ? (
        <div className="wishlist-page-empty">
          <div className="wishlist-page-empty-icon">
            <svg
              width="64"
              height="64"
              viewBox="0 0 24 24"
              fill="none"
              stroke="rgba(255,255,255,0.15)"
              strokeWidth="1.5"
            >
              <path d="M9 18V5l12-2v13" />
              <circle cx="6" cy="18" r="3" />
              <circle cx="18" cy="16" r="3" />
            </svg>
          </div>
          <h3>Your wishlist is empty</h3>
          <p>Failed downloads and tracks from watchlist scans will appear here automatically.</p>
        </div>
      ) : (
        <>
          {/* Stats strip is hidden alongside the nebula on an empty wishlist,
              exactly as the vanilla initializer did. */}
          <div className="wishlist-stats-strip">
            <div className="wishlist-stat-item">
              <span className="wishlist-stat-value">{albumCount}</span>
              <span className="wishlist-stat-label">Album Tracks</span>
            </div>
            <div className="wishlist-stat-divider" />
            <div className="wishlist-stat-item">
              <span className="wishlist-stat-value">{singleCount}</span>
              <span className="wishlist-stat-label">Singles</span>
            </div>
            <div className="wishlist-stat-divider" />
            <div className="wishlist-stat-item">
              <span className="wishlist-stat-value wishlist-stat-cycle">
                {currentCycle === 'albums' ? 'Albums/EPs' : 'Singles'}
              </span>
              <span className="wishlist-stat-label">Next Cycle</span>
            </div>
          </div>

          <div className="wl-nebula">
            <div className="wl-nebula-field">
              {groups.length === 0 ? (
                <div className="wl-nebula-empty">Your wishlist is empty</div>
              ) : (
                groups.map((group, index) => (
                  <WishlistOrb
                    key={group.name}
                    group={group}
                    index={index}
                    artistImages={artistImages}
                    currentCycle={currentCycle}
                  />
                ))
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
