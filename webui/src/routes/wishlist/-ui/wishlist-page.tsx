import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useEffect, useMemo, useState } from 'react';

import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';
import { clearAudiobookWishlist } from '@/routes/audiobooks/-audiobooks.api';

import type {
  ParsedWishlistTrack,
  WishlistAlbumGroup,
  WishlistArtistGroup,
  WishlistBulkAction,
} from '../-wishlist.types';

import {
  bulkWishlistActionChunked,
  removeWishlistAlbum,
  removeWishlistTrack,
  setWishlistRetryProfile,
  WISHLIST_QUERY_KEY,
  wishlistArtistPhotosQueryOptions,
  wishlistCycleQueryOptions,
  wishlistRetryProfileQueryOptions,
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
import { WishlistAudiobooks } from './wishlist-audiobooks';
import { WishlistList } from './wishlist-list';
import { WishlistOrb } from './wishlist-orb';

type NebulaSort = 'busiest' | 'failing' | 'name';

const NEBULA_SORTS: { key: NebulaSort; label: string }[] = [
  { key: 'busiest', label: 'Busiest first' },
  { key: 'failing', label: 'Failing first' },
  { key: 'name', label: 'A–Z' },
];

const RING_CIRCUMFERENCE = 2 * Math.PI * 26;

function groupTrackIds(group: WishlistArtistGroup): string[] {
  return [...group.albums.flatMap((album) => album.tracks), ...group.singles].map(
    (track) => track.id,
  );
}

export function WishlistPage() {
  useReactPageShell('wishlist');

  const { profileId } = useProfile();
  const search = Route.useSearch();
  const media = search.media;
  const navigate = useNavigate({ from: Route.fullPath });
  const queryClient = useQueryClient();

  const [audiobookCount, setAudiobookCount] = useState<number | null>(null);
  const [abReloadKey, setAbReloadKey] = useState(0);

  // Only one orb open at a time, matching the vanilla accordion which cleared
  // `.expanded` from every group before setting it on the clicked one.
  const [expandedArtist, setExpandedArtist] = useState<string | null>(null);
  // Display-only view toggle: the nebula stays the default face; the list is
  // its operational twin. Persisted so the choice survives navigation.
  const [view, setView] = useState<'nebula' | 'list'>(() => {
    try {
      const stored = window.localStorage.getItem('wishlistView');
      return stored === 'list' ? 'list' : 'nebula';
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
  // The nebula keeps its own sort, independent of the list's triage sort —
  // each view remembers how you like to scan it.
  const [nebulaSort, setNebulaSort] = useState<NebulaSort>('busiest');

  const statsQuery = useQuery(wishlistStatsQueryOptions(profileId));
  const cycleQuery = useQuery(wishlistCycleQueryOptions(profileId));
  const retryProfileQuery = useQuery(wishlistRetryProfileQueryOptions(profileId));
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

  const nebulaGroups = useMemo(() => {
    const list = [...visibleGroups];
    if (nebulaSort === 'name') list.sort((a, b) => a.name.localeCompare(b.name));
    else if (nebulaSort === 'failing')
      list.sort(
        (a, b) =>
          b.failingCount - a.failingCount || b.total - a.total || a.name.localeCompare(b.name),
      );
    // 'busiest' is the helper's native order.
    return list;
  }, [visibleGroups, nebulaSort]);

  const failingIds = useMemo(
    () =>
      groups
        .flatMap((group) => [...group.albums.flatMap((album) => album.tracks), ...group.singles])
        .filter((track) => track.failing)
        .map((track) => track.id),
    [groups],
  );
  const failingTotal = failingIds.length;

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

  const bulkAction = useMutation({
    // Chunked: the endpoint caps a call at 200 ids, and "grab the artist"
    // on a 500-track wishlist must not 400.
    mutationFn: ({ action, ids }: { action: WishlistBulkAction; ids: string[] }) =>
      bulkWishlistActionChunked(action, ids),
    onSuccess: (response, { action, ids }) => {
      const results = response.results ?? [];
      const okCount = results.filter((r) => r.ok).length;
      const allOk = results.length > 0 && okCount === results.length;
      window.showToast?.(
        `${action[0].toUpperCase()}${action.slice(1)}: ${okCount}/${ids.length} succeeded`,
        allOk ? 'success' : 'warning',
      );
      void refresh();
      window.updateWishlistCount?.();
    },
    onError: (error: Error) => window.showToast?.(`Error: ${error.message}`, 'error'),
  });

  // The retry profile select lives in the hero stats. A custom ladder stays
  // API-only: it shows as the current value when active, but can't be picked.
  const retryProfileData = retryProfileQuery.data;
  const retryProfileOptions = retryProfileData?.profiles ?? [];
  const activeIsCustom =
    retryProfileData?.profile?.name === 'custom' &&
    !retryProfileOptions.some((p) => p.name === 'custom');
  const retryProfileMutation = useMutation({
    mutationFn: (name: string) => setWishlistRetryProfile(name),
    onSuccess: (response) => {
      window.showToast?.(`Retry profile: ${response.profile?.label ?? 'updated'}`, 'success');
      void queryClient.invalidateQueries({
        queryKey: [...WISHLIST_QUERY_KEY, 'retry-profile'],
      });
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

  /** New: one grab queues every wanted track by the artist, right now. */
  const onGrabArtist = (group: WishlistArtistGroup) => {
    const ids = groupTrackIds(group);
    if (ids.length > 0) bulkAction.mutate({ action: 'grab', ids });
  };

  /** New: queue one album's tracks without touching the rest of the artist. */
  const onGrabAlbum = (album: WishlistAlbumGroup) => {
    const ids = album.tracks.map((track) => track.id);
    if (ids.length > 0) bulkAction.mutate({ action: 'grab', ids });
  };

  /** New: clear the retry backoff on every stuck track at once. */
  const onRetryAllFailing = () => {
    if (failingIds.length > 0) bulkAction.mutate({ action: 'retry', ids: failingIds });
  };

  /** New: drop a whole artist from the wishlist with a single confirm.
      Removal goes through the bulk skip endpoint with exact track ids —
      the album-name endpoint matches by name across the whole wishlist,
      so a shared album title ("Greatest Hits") could nuke another
      artist's tracks. Skip removes by id and records the same ignore
      gate a manual remove does. */
  const onRemoveArtist = async (group: WishlistArtistGroup) => {
    const confirmed = await window.showConfirmDialog?.({
      title: 'Remove Artist',
      message: `Remove ${trackCountLabel(group.total)} by "${group.name}" from the wishlist?`,
      confirmText: 'Remove',
      destructive: true,
    });
    if (confirmed === false) return;
    const ids = groupTrackIds(group);
    if (ids.length === 0) return;
    try {
      const response = await bulkWishlistActionChunked('skip', ids);
      const results = response.results ?? [];
      const okCount = results.filter((result) => result.ok).length;
      if (okCount === ids.length) {
        window.showToast?.(`Removed ${trackCountLabel(okCount)} by "${group.name}"`, 'success');
      } else {
        window.showToast?.(
          `Removed ${okCount} of ${ids.length} tracks by "${group.name}" — ${ids.length - okCount} failed`,
          'warning',
        );
      }
    } catch (error) {
      window.showToast?.(
        `Error: ${error instanceof Error ? error.message : String(error)}`,
        'error',
      );
    }
    await refresh();
    window.updateWishlistCount?.();
  };

  const onClearAudiobooks = async () => {
    const confirmed = await window.showConfirmDialog?.({
      title: 'Clear Audiobook Wishlist',
      message: 'Remove all audiobooks from your wishlist? This cannot be undone.',
      confirmText: 'Clear All',
      destructive: true,
    });
    if (confirmed === false) return;
    const ok = await clearAudiobookWishlist();
    if (ok) {
      window.showToast?.('Audiobook wishlist cleared', 'success');
      setAudiobookCount(0);
      setAbReloadKey((k) => k + 1);
    } else {
      window.showToast?.('Failed to clear wishlist', 'error');
    }
  };

  const showFailing = () => void navigate({ search: (prev) => ({ ...prev, failing: true }) });

  return (
    <div className="page-shell wlp">
      {/* Media tabs. Music and audiobooks are kept as separate lists, the same
          isolation the video side keeps between movies, shows and channels:
          they share a page and nothing else — different database, different
          search, different acquisition chain. */}
      <div className="wlp-media-tabs" role="tablist" aria-label="Wishlist media type">
        <button
          type="button"
          role="tab"
          aria-selected={media === 'music'}
          onClick={() => void navigate({ search: (prev) => ({ ...prev, media: 'music' }) })}
        >
          Music
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={media === 'audiobooks'}
          onClick={() => void navigate({ search: (prev) => ({ ...prev, media: 'audiobooks' }) })}
        >
          Audiobooks
        </button>
      </div>

      {media === 'audiobooks' ? (
        <>
          {/* Audiobooks keep their existing presentation — this overhaul is
              the music side. Same header and actions as before; only the
              shared tab strip above is new. */}
          <div className="wishlist-page-header">
            <div className="wishlist-page-header-left">
              <h2 className="wishlist-page-title">
                <span className="wishlist-page-title-icon">⭐</span>
                Wishlist
              </h2>
              <div className="wishlist-page-meta">
                <span className="wishlist-page-count">
                  {audiobookCount !== null
                    ? `${audiobookCount} ${audiobookCount === 1 ? 'audiobook' : 'audiobooks'}`
                    : ''}
                </span>
              </div>
            </div>
          </div>

          <div className="wishlist-page-actions">
            <button
              className="btn btn--danger"
              type="button"
              onClick={() => void onClearAudiobooks()}
            >
              Clear All
            </button>
          </div>

          <WishlistAudiobooks key={abReloadKey} onCountChange={setAudiobookCount} />
        </>
      ) : (
        <>
          <header className="wlp-hero">
            <div className="wlp-hero-top">
              <div>
                <div className="wlp-eyebrow">Music</div>
                <h1 className="wlp-title">Wishlist</h1>
                <p className="wlp-sub">
                  Everything you&apos;re hunting down. The automation works this list around the
                  clock — triage what&apos;s stuck, grab what you want now.
                </p>
              </div>
              <div className="wlp-nextrun">
                <div className="wlp-ring" aria-hidden="true">
                  <svg viewBox="0 0 64 64">
                    <circle className="wlp-ring-bg" cx="32" cy="32" r="26" />
                    <circle
                      className={`wlp-ring-fg${processing ? ' is-live' : ''}`}
                      cx="32"
                      cy="32"
                      r="26"
                      strokeDasharray={`${RING_CIRCUMFERENCE * 0.22} ${RING_CIRCUMFERENCE}`}
                      strokeDashoffset={0}
                    />
                  </svg>
                  <span className="wlp-ring-center">{processing ? '⟳' : '◷'}</span>
                </div>
                <div className="wlp-nextrun-meta">
                  <span className="wlp-nextrun-label">
                    {processing ? 'Auto-run working' : 'Next auto-run'}
                  </span>
                  <span className="wlp-nextrun-time" id="wishlist-next-auto-timer">
                    --
                  </span>
                  <span className="wlp-cycle-badge">
                    {currentCycle === 'albums' ? 'Albums/EPs' : 'Singles'}
                  </span>
                </div>
              </div>
            </div>

            {/* The stats strip hides on an empty wishlist, exactly as the old
                page did — the hero keeps its title and next-run card. */}
            {total > 0 && (
              <div className="wlp-stats">
                <div className="wlp-stat">
                  <span className="wlp-stat-value wishlist-page-count">
                    {trackCountLabel(total)}
                  </span>
                  <span className="wlp-stat-label">Wanted</span>
                </div>
                <div className="wlp-stat">
                  <span className="wlp-stat-value" id="wishlist-stat-albums">
                    {albumCount}
                  </span>
                  <span className="wlp-stat-label">Album Tracks</span>
                </div>
                <div className="wlp-stat">
                  <span className="wlp-stat-value" id="wishlist-stat-singles">
                    {singleCount}
                  </span>
                  <span className="wlp-stat-label">Singles</span>
                </div>
                {failingTotal > 0 && (
                  <div className="wlp-stat">
                    <span className="wlp-stat-value wlp-stat-value--warn">{failingTotal}</span>
                    <span className="wlp-stat-label">Failing</span>
                  </div>
                )}
                <div className="wlp-stat">
                  <span className="wlp-stat-value">
                    <select
                      className="wlp-retry-select"
                      aria-label="Retry profile"
                      title={
                        retryProfileData?.profile?.description ||
                        'How long repeatedly-failing tracks wait between scheduled retries'
                      }
                      value={retryProfileData?.profile?.name ?? 'standard'}
                      disabled={retryProfileQuery.isPending || retryProfileMutation.isPending}
                      onChange={(event) => retryProfileMutation.mutate(event.target.value)}
                    >
                      {retryProfileOptions.map((p) => (
                        <option key={p.name} value={p.name} disabled={p.name === 'custom'}>
                          {p.label}
                          {p.name === 'custom' ? ' (API only)' : ''}
                        </option>
                      ))}
                      {activeIsCustom && (
                        <option value="custom" disabled>
                          {retryProfileData?.profile?.label ?? 'Custom'} (API only)
                        </option>
                      )}
                    </select>
                  </span>
                  <span className="wlp-stat-label">Retry Profile</span>
                </div>
              </div>
            )}
          </header>

          {failingTotal > 0 && (
            <div className="wlp-triage" role="alert">
              <span className="wlp-triage-icon" aria-hidden="true">
                ⚠
              </span>
              <span className="wlp-triage-text">
                <strong>
                  {failingTotal} {failingTotal === 1 ? 'track keeps' : 'tracks keep'} failing.
                </strong>{' '}
                Stuck in the retry loop — clear their backoff to try them right now, or review
                what&apos;s wrong.
              </span>
              <div className="wlp-triage-actions">
                <button
                  type="button"
                  className="wlp-btn wlp-btn--sm wlp-btn--primary"
                  disabled={bulkAction.isPending}
                  onClick={onRetryAllFailing}
                >
                  <span aria-hidden="true">↻ </span>Retry all now
                </button>
                <button type="button" className="wlp-btn wlp-btn--sm" onClick={showFailing}>
                  Review
                </button>
              </div>
            </div>
          )}

          {total === 0 ? (
            <div className="wlp-empty">
              <div className="wlp-empty-icon" aria-hidden="true">
                <svg
                  width="40"
                  height="40"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="rgba(245,185,66,0.85)"
                  strokeWidth="1.5"
                >
                  <path d="M9 18V5l12-2v13" />
                  <circle cx="6" cy="18" r="3" />
                  <circle cx="18" cy="16" r="3" />
                </svg>
              </div>
              <h3>Your wishlist is empty</h3>
              <p>
                Failed downloads and tracks from watchlist scans will appear here automatically —
                then the hunt begins.
              </p>
            </div>
          ) : (
            <>
              <div className="wlp-cmdbar">
                <div className="wlp-search">
                  <input
                    type="text"
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
                </div>
                <button
                  type="button"
                  className={`wlp-chip wlp-chip--warn${search.failing ? ' active' : ''}`}
                  title="Show only artists with tracks that keep failing to download"
                  aria-pressed={search.failing}
                  onClick={() =>
                    void navigate({ search: (prev) => ({ ...prev, failing: !prev.failing }) })
                  }
                >
                  <span aria-hidden="true">⚠ </span>Failing
                  {failingTotal > 0 && <span className="wlp-count">{failingTotal}</span>}
                </button>
                {view === 'nebula' && (
                  <label className="wlp-sort">
                    Sort
                    <select
                      aria-label="Sort nebula"
                      value={nebulaSort}
                      onChange={(event) => setNebulaSort(event.target.value as NebulaSort)}
                    >
                      {NEBULA_SORTS.map((s) => (
                        <option key={s.key} value={s.key}>
                          {s.label}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <div className="wlp-segmented" role="tablist" aria-label="Wishlist view">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={view === 'nebula'}
                    title="The orbital view"
                    onClick={() => pickView('nebula')}
                  >
                    <span aria-hidden="true">✦ </span>Nebula
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={view === 'list'}
                    title="Dense list with retry status — sortable"
                    onClick={() => pickView('list')}
                  >
                    <span aria-hidden="true">☰ </span>List
                  </button>
                </div>
                {/* All three open modals owned by downloads.js and shared with the
                    wishlist hero button, so they are invoked rather than reimplemented. */}
                <button
                  type="button"
                  className="wlp-btn"
                  title="Tracks you removed or cancelled — auto-skipped until they expire. Un-ignore to allow auto-download again."
                  onClick={() => window.openWishlistIgnoreModal?.()}
                >
                  Ignored
                </button>
                <button
                  type="button"
                  className="wlp-btn"
                  onClick={() => window.cleanupWishlistOverview?.()}
                >
                  Cleanup
                </button>
                <button
                  type="button"
                  className="wlp-btn wlp-btn--danger"
                  onClick={() => window.clearEntireWishlist?.()}
                >
                  Clear All
                </button>
                <button
                  type="button"
                  className="wlp-btn wlp-btn--primary"
                  onClick={() => void window._nebulaDownload?.()}
                >
                  <span aria-hidden="true">⬇ </span>Download Wishlist
                </button>
              </div>

              {view === 'list' ? (
                <WishlistList
                  groups={visibleGroups}
                  artistImages={artistImages}
                  filterActive={Boolean(search.q?.trim()) || search.failing}
                  onRemoveAlbum={(albumName) => void onRemoveAlbum(albumName)}
                  onRemoveTrack={(trackId) => removeTrack.mutate(trackId)}
                  onGrabArtist={onGrabArtist}
                  onBulkAction={(action, ids) => bulkAction.mutateAsync({ action, ids })}
                  bulkBusy={bulkAction.isPending}
                />
              ) : (
                <div className="wl-nebula">
                  <div className={`wl-nebula-field${processing ? ' nebula-processing' : ''}`}>
                    {nebulaGroups.length === 0 ? (
                      <div className="wl-nebula-empty">
                        Nothing matches — clear the filter to see the whole hunt.
                      </div>
                    ) : (
                      nebulaGroups.map((group, index) => (
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
                            setExpandedArtist((current) =>
                              current === group.name ? null : group.name,
                            )
                          }
                          onRemoveAlbum={(albumName) => void onRemoveAlbum(albumName)}
                          onRemoveTrack={(trackId) => removeTrack.mutate(trackId)}
                          onGrabArtist={() => onGrabArtist(group)}
                          onGrabAlbum={onGrabAlbum}
                          onRemoveArtist={() => void onRemoveArtist(group)}
                          actionBusy={bulkAction.isPending}
                        />
                      ))
                    )}
                  </div>
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
