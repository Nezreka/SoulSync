import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useEffect, useMemo, useState } from 'react';

import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';

import type { ParsedWishlistTrack } from '../-wishlist.types';

import {
  removeWishlistAlbum,
  removeWishlistTrack,
  WISHLIST_QUERY_KEY,
  wishlistArtistPhotosQueryOptions,
  wishlistCycleQueryOptions,
  wishlistStatsQueryOptions,
  wishlistTracksQueryOptions,
} from '../-wishlist.api';
import {
  buildArtistImageFallbackMap,
  buildArtistImageMap,
  filterWishlistGroups,
  groupWishlistArtists,
  parseWishlistTrack,
  trackCountLabel,
} from '../-wishlist.helpers';
import { useLiveWishlist } from '../-wishlist.live';
import { Route } from '../route';
import { WishlistList } from './wishlist-list';
import { WishlistOrb } from './wishlist-orb';

export function WishlistPage() {
  useReactPageShell('wishlist');

  const { profileId } = useProfile();
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const queryClient = useQueryClient();

  // Only one orb open at a time, matching the vanilla accordion which cleared
  // `.expanded` from every group before setting it on the clicked one.
  const [expandedArtist, setExpandedArtist] = useState<string | null>(null);
  // Display-only view toggle: the nebula stays the default face; the list is
  // its operational twin. Persisted so the choice survives navigation.
  const [view, setView] = useState<'nebula' | 'list'>(() => {
    try {
      return window.localStorage.getItem('wishlistView') === 'list' ? 'list' : 'nebula';
    } catch {
      return 'nebula';
    }
  });
  const pickView = (next: 'nebula' | 'list') => {
    setView(next);
    try {
      window.localStorage.setItem('wishlistView', next);
    } catch {
      /* private mode — the toggle still works for the session */
    }
  };

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

  // Painted only when a primary photo fails to load, which for a Library-v2
  // artist means the local artwork build is still cold.
  const artistImageFallbacks = useMemo(
    () => buildArtistImageFallbackMap([albumsQuery.data ?? {}, singlesQuery.data ?? {}]),
    [albumsQuery.data, singlesQuery.data],
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

  const visibleGroups = useMemo(
    () => filterWishlistGroups(groups, search.q, search.failing),
    [groups, search.q, search.failing],
  );

  const refresh = () => queryClient.invalidateQueries({ queryKey: WISHLIST_QUERY_KEY });

  const { processing } = useLiveWishlist(() => {
    void refresh();
  });

  // The countdown lives in downloads.js: it is bound to wishlistCountdownInterval,
  // socketConnected and _lastWishlistStats, all module-scoped, and writes into
  // #wishlist-next-auto-timer — which is why that id is rendered below.
  const nextRunSeconds = statsQuery.data?.next_run_in_seconds ?? 0;
  useEffect(() => {
    window.startWishlistCountdownTimer?.(currentCycle, nextRunSeconds);
  }, [currentCycle, nextRunSeconds]);

  const removeAlbum = useMutation({
    mutationFn: (albumName: string) => removeWishlistAlbum(albumName),
    onSuccess: async (_data, albumName) => {
      window.showToast?.(`Removed "${albumName}"`, 'success');
      await refresh();
      window.updateWishlistCount?.();
    },
    onError: (error: Error) => window.showToast?.(`Error: ${error.message}`, 'error'),
  });

  const removeTrack = useMutation({
    mutationFn: (trackId: string) => removeWishlistTrack(trackId),
    onSuccess: async () => {
      window.showToast?.('Removed', 'success');
      await refresh();
      window.updateWishlistCount?.();
    },
    onError: (error: Error) => window.showToast?.(`Error: ${error.message}`, 'error'),
  });

  const onRemoveAlbum = async (albumName: string) => {
    const confirmed = await window.showConfirmDialog?.({
      title: 'Remove Album',
      message: `Remove all tracks from "${albumName}"?`,
      confirmText: 'Remove',
      destructive: true,
    });
    if (confirmed === false) return;
    removeAlbum.mutate(albumName);
  };

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
            {/* startWishlistCountdownTimer writes this line. */}
            <span className="wishlist-page-timer" id="wishlist-next-auto-timer">
              Next Auto: --
            </span>
          </div>
        </div>
      </div>

      <div className="wishlist-page-actions">
        {/* All three open modals owned by downloads.js and shared with the
            wishlist hero button, so they are invoked rather than reimplemented. */}
        <button
          className="btn btn--secondary"
          type="button"
          title="Tracks you removed or cancelled — auto-skipped until they expire. Un-ignore to allow auto-download again."
          onClick={() => window.openWishlistIgnoreModal?.()}
        >
          Ignored
        </button>
        <button
          className="btn btn--secondary"
          type="button"
          onClick={() => window.cleanupWishlistOverview?.()}
        >
          Cleanup
        </button>
        <button
          className="btn btn--danger"
          type="button"
          onClick={() => window.clearEntireWishlist?.()}
        >
          Clear All
        </button>
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
              <span className="wishlist-stat-value" id="wishlist-stat-albums">
                {albumCount}
              </span>
              <span className="wishlist-stat-label">Album Tracks</span>
            </div>
            <div className="wishlist-stat-divider" />
            <div className="wishlist-stat-item">
              <span className="wishlist-stat-value" id="wishlist-stat-singles">
                {singleCount}
              </span>
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
            <div className="wl-nebula-bar">
              <input
                type="text"
                className="wl-nebula-search"
                placeholder="Filter wishlist…"
                aria-label="Filter wishlist"
                value={search.q}
                onChange={(event) =>
                  void navigate({
                    search: (prev) => ({ ...prev, q: event.target.value }),
                    replace: true,
                  })
                }
              />
              <button
                type="button"
                className={`wl-chip wl-failing-filter${search.failing ? ' active' : ''}`}
                title="Show only artists with tracks that keep failing to download"
                onClick={() =>
                  void navigate({ search: (prev) => ({ ...prev, failing: !prev.failing }) })
                }
              >
                ⚠ Failing
              </button>
              <div className="wl-view-toggle" role="tablist" aria-label="Wishlist view">
                <button
                  type="button"
                  role="tab"
                  aria-selected={view === 'nebula'}
                  className={`wl-chip${view === 'nebula' ? ' active' : ''}`}
                  title="The orbital view"
                  onClick={() => pickView('nebula')}
                >
                  ✦ Nebula
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={view === 'list'}
                  className={`wl-chip${view === 'list' ? ' active' : ''}`}
                  title="Dense list with retry status — sortable"
                  onClick={() => pickView('list')}
                >
                  ☰ List
                </button>
              </div>
              <button
                className="btn btn--primary"
                type="button"
                onClick={() => void window._nebulaDownload?.()}
              >
                Download Wishlist
              </button>
            </div>

            {view === 'list' ? (
              <WishlistList
                groups={visibleGroups}
                artistImages={artistImages}
                filterActive={Boolean(search.q?.trim()) || search.failing}
                onRemoveAlbum={(albumName) => void onRemoveAlbum(albumName)}
                onRemoveTrack={(trackId) => removeTrack.mutate(trackId)}
              />
            ) : (
              <div className={`wl-nebula-field${processing ? ' nebula-processing' : ''}`}>
                {groups.length === 0 ? (
                  <div className="wl-nebula-empty">Your wishlist is empty</div>
                ) : (
                  visibleGroups.map((group, index) => (
                    <WishlistOrb
                      key={group.name}
                      group={group}
                      index={index}
                      artistImages={artistImages}
                      artistImageFallbacks={artistImageFallbacks}
                      currentCycle={currentCycle}
                      processing={processing}
                      expanded={expandedArtist === group.name}
                      onToggleExpand={() =>
                        setExpandedArtist((current) => (current === group.name ? null : group.name))
                      }
                      onRemoveAlbum={(albumName) => void onRemoveAlbum(albumName)}
                      onRemoveTrack={(trackId) => removeTrack.mutate(trackId)}
                    />
                  ))
                )}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
