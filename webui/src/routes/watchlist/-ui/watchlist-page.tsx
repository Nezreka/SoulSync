import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';

import {
  addWatchlistArtist,
  cancelWatchlistScan,
  removeWatchlistArtistsBatch,
  searchProviderArtists,
  similarArtistsStatusQueryOptions,
  startSimilarArtistsUpdate,
  startWatchlistScan,
  WATCHLIST_QUERY_KEY,
  watchlistArtistConfigQueryOptions,
  watchlistArtistsQueryOptions,
  watchlistCountQueryOptions,
  watchlistGlobalConfigQueryOptions,
  watchlistLabelsQueryOptions,
  watchlistRecentReleasesQueryOptions,
  watchlistScanStatusQueryOptions,
} from '../-watchlist.api';
import {
  artistPills,
  artistSourceKeys,
  batchSelectionState,
  filterArtists,
  formatArtistCount,
  formatCountdown,
  formatRelativeScanTime,
  formatTimeAgo,
  primaryArtistId,
  selectedVisibleIds,
  sortArtists,
  timestampValue,
  WATCHLIST_SOURCE_BADGES,
} from '../-watchlist.helpers';
import { useCountdown, useLiveWatchlistScan } from '../-watchlist.scan';
import {
  WATCHLIST_SORT_VALUES,
  type ProviderSearchResult,
  type WatchlistArtist,
  type WatchlistRecentReleaseRow,
} from '../-watchlist.types';
import { Route } from '../route';
import { WatchlistArtistConfigModal } from './watchlist-artist-config-modal';
import { WatchlistArtistDetail } from './watchlist-artist-detail';
import { WatchlistGlobalSettingsModal } from './watchlist-global-settings-modal';
import { WatchlistLabelsTab } from './watchlist-labels-tab';
import styles from './watchlist-page.module.css';
import { WatchlistScanDeck } from './watchlist-scan-deck';

const SORT_LABELS: Record<(typeof WATCHLIST_SORT_VALUES)[number], string> = {
  'name-asc': 'Name A-Z',
  'name-desc': 'Name Z-A',
  'scan-oldest': 'Oldest Scanned',
  'scan-newest': 'Recently Scanned',
  'added-newest': 'Recently Added',
};

const ADD_PROVIDER_OPTIONS = [
  { value: 'deezer', label: 'Search Deezer' },
  { value: 'spotify', label: 'Search Spotify' },
  { value: 'itunes', label: 'Search Apple Music' },
  { value: 'discogs', label: 'Search Discogs' },
  { value: 'musicbrainz', label: 'Search MusicBrainz' },
] as const;

function daysSince(iso: string | null | undefined): number | null {
  const value = timestampValue(iso);
  if (!value) return null;
  return Math.max(0, Math.floor((Date.now() - value) / 86_400_000));
}

function releaseTypeText(artist: WatchlistArtist): string {
  const enabled = [
    artist.include_albums ? 'Albums' : null,
    artist.include_eps ? 'EPs' : null,
    artist.include_singles ? 'Singles' : null,
  ].filter(Boolean);
  return enabled.length > 0 ? enabled.join(' / ') : 'None';
}

function ruleCount(artist: WatchlistArtist): number {
  return artistPills(artist).length;
}

function scanFreshnessLabel(artist: WatchlistArtist): string {
  const days = daysSince(artist.last_scan_timestamp);
  if (days === null) return 'Needs first scan';
  if (days === 0) return 'Fresh scan';
  if (days < 30) return `${days}d since scan`;
  return 'Scan stale';
}

function providerLabel(source: string | null | undefined): string {
  if (!source) return 'Watchlist';
  const match = ADD_PROVIDER_OPTIONS.find((option) => option.value === source);
  return match?.label.replace(/^Search /, '') ?? source;
}

export function WatchlistPage() {
  useReactPageShell('watchlist');

  const { profileId } = useProfile();
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const queryClient = useQueryClient();

  // Selection is keyed by primary artist id rather than row index so that a
  // refetch which reorders or drops rows cannot silently reassign a tick to a
  // different artist.
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(() => new Set());

  // Declared before the live-scan hook so the poll interval can read it. Only
  // polls while a scan is running AND the socket has gone quiet — the vanilla
  // fallback condition, which keeps this off entirely on a healthy socket.
  const [pollWhileScanning, setPollWhileScanning] = useState(false);
  const scanStatusQuery = useQuery({
    ...watchlistScanStatusQueryOptions(profileId),
    refetchInterval: pollWhileScanning ? 2000 : false,
  });

  // The route loader has already primed all four, so these resolve from cache
  // on first paint. They stay `useQuery` rather than suspense so that a later
  // refetch (after a scan, say) re-renders in place instead of unmounting the
  // page into a fallback.
  const countQuery = useQuery(watchlistCountQueryOptions(profileId));
  const artistsQuery = useQuery(watchlistArtistsQueryOptions(profileId));
  const globalConfigQuery = useQuery(watchlistGlobalConfigQueryOptions(profileId));
  const recentReleasesQuery = useQuery(watchlistRecentReleasesQueryOptions(profileId, 12));

  const artists = useMemo(() => artistsQuery.data ?? [], [artistsQuery.data]);
  const count = countQuery.data?.count ?? artists.length;
  const recentReleases = recentReleasesQuery.data ?? [];

  // The socket is the primary source of scan frames, exactly as in the vanilla
  // page; the polled status is the fallback and the initial value on load.
  const { frame: liveFrame, needsPolling } = useLiveWatchlistScan();
  const scanStatus = liveFrame ?? scanStatusQuery.data;
  const isScanning = scanStatus?.status === 'scanning';

  const countdown = useCountdown(countQuery.data?.nextRunInSeconds ?? 0);

  useEffect(() => {
    setPollWhileScanning(isScanning && needsPolling);
  }, [isScanning, needsPolling]);

  // A finished scan changes the artist rows (last_scan_timestamp) and the
  // wishlist, so refresh once on the transition rather than on every frame.
  const previousScanStatus = useRef<string | undefined>(undefined);
  useEffect(() => {
    const status = scanStatus?.status;
    if (previousScanStatus.current === 'scanning' && status && status !== 'scanning') {
      void queryClient.invalidateQueries({ queryKey: WATCHLIST_QUERY_KEY });
      try {
        window.updateWatchlistButtonCount?.();
      } catch {
        /* non-fatal */
      }
    }
    previousScanStatus.current = status;
  }, [scanStatus?.status, queryClient]);

  const visibleArtists = useMemo(
    () => sortArtists(filterArtists(artists, search.q), search.sort),
    [artists, search.q, search.sort],
  );
  const [activeArtistId, setActiveArtistId] = useState<string | null>(null);

  useEffect(() => {
    setActiveArtistId((previous) => {
      if (previous && visibleArtists.some((artist) => primaryArtistId(artist) === previous)) {
        return previous;
      }
      const fallback = visibleArtists[0] ?? artists[0];
      return fallback ? primaryArtistId(fallback) : null;
    });
  }, [artists, visibleArtists]);

  const activeArtist = useMemo(
    () =>
      artists.find((artist) => primaryArtistId(artist) === activeArtistId) ??
      visibleArtists[0] ??
      null,
    [activeArtistId, artists, visibleArtists],
  );

  const neverScanned = useMemo(
    () => artists.filter((artist) => !artist.last_scan_timestamp).length,
    [artists],
  );
  const staleArtists = useMemo(
    () => artists.filter((artist) => (daysSince(artist.last_scan_timestamp) ?? 999) >= 30).length,
    [artists],
  );
  const customRuleArtists = useMemo(
    () =>
      artists.filter(
        (artist) =>
          !artist.include_albums ||
          !artist.include_eps ||
          !artist.include_singles ||
          artist.include_live ||
          artist.include_remixes ||
          artist.include_acoustic ||
          artist.include_compilations,
      ).length,
    [artists],
  );

  const globalOverrideActive = Boolean(globalConfigQuery.data?.global_override_enabled);
  const isLabelsTab = search.tab === 'labels';

  // The header chip counts labels while the Labels tab is open, exactly as the
  // vanilla `switchWatchlistTab` rewrote it. `enabled` keeps the artists tab
  // from paying for the labels round trip; the tab body shares this cache entry.
  const labelsQuery = useQuery({
    ...watchlistLabelsQueryOptions(profileId),
    enabled: isLabelsTab,
  });
  const labelCount = labelsQuery.data?.length ?? 0;
  const headerCount = isLabelsTab
    ? `${labelCount} label${labelCount !== 1 ? 's' : ''}`
    : formatArtistCount(count);

  const selection = useMemo(
    () => batchSelectionState(visibleArtists, selectedIds),
    [visibleArtists, selectedIds],
  );

  // A tick on an artist that has since been removed (or filtered away by a
  // refetch) must not linger and get swept into the next batch remove.
  useEffect(() => {
    setSelectedIds((previous) => {
      if (previous.size === 0) return previous;
      const live = new Set(
        artists.map((artist) => primaryArtistId(artist)).filter((id): id is string => id !== null),
      );
      const next = new Set([...previous].filter((id) => live.has(id)));
      return next.size === previous.size ? previous : next;
    });
  }, [artists]);

  const toggleArtist = useCallback((artistId: string) => {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(artistId)) {
        next.delete(artistId);
      } else {
        next.add(artistId);
      }
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback(
    (checked: boolean) => {
      // Only the VISIBLE cards, matching the vanilla behaviour: with a filter
      // applied, Select All means "all of what I can see", never the whole
      // watchlist.
      const visibleIds = visibleArtists
        .map((artist) => primaryArtistId(artist))
        .filter((id): id is string => id !== null);

      setSelectedIds((previous) => {
        const next = new Set(previous);
        for (const id of visibleIds) {
          if (checked) {
            next.add(id);
          } else {
            next.delete(id);
          }
        }
        return next;
      });
    },
    [visibleArtists],
  );

  const startScan = useMutation({
    mutationFn: () => startWatchlistScan(),
    onError: (error: Error) =>
      // The vanilla path used a raw alert() here; toast matches the rest of
      // the page and the project's no-alert rule.
      window.showToast?.(`Error starting scan: ${error.message}`, 'error'),
  });

  const cancelScan = useMutation({
    mutationFn: () => cancelWatchlistScan(),
    onSuccess: () =>
      window.showToast?.('Cancel request sent — scan will stop after current artist', 'info'),
    onError: (error: Error) =>
      window.showToast?.(`Error cancelling scan: ${error.message}`, 'error'),
  });

  // Similar-artists update: kicked off here, then polled until it reports done.
  // It shares the scan worker, so the Scan button is disabled while it runs —
  // the same coupling the vanilla page enforced by disabling both chips.
  const [similarRunning, setSimilarRunning] = useState(false);
  const similarStatus = useQuery(similarArtistsStatusQueryOptions(profileId, similarRunning));

  useEffect(() => {
    if (!similarRunning) return;
    const status = similarStatus.data?.status;
    if (status === 'completed') {
      setSimilarRunning(false);
      window.showToast?.(
        `Updated similar artists for ${similarStatus.data?.artists_processed || 0} artists!`,
        'success',
      );
    } else if (status === 'error') {
      setSimilarRunning(false);
      window.showToast?.('Error updating similar artists', 'error');
    }
  }, [similarRunning, similarStatus.data?.status, similarStatus.data?.artists_processed]);

  const startSimilar = useMutation({
    mutationFn: () => startSimilarArtistsUpdate(),
    onSuccess: () => {
      setSimilarRunning(true);
      window.showToast?.('Updating similar artists in background...', 'success');
    },
    onError: (error: Error) => window.showToast?.(`Error: ${error.message}`, 'error'),
  });

  const batchRemove = useMutation({
    mutationFn: (artistIds: string[]) => removeWatchlistArtistsBatch(artistIds),
    onSuccess: async () => {
      setSelectedIds(new Set());
      await queryClient.invalidateQueries({ queryKey: WATCHLIST_QUERY_KEY });
      // Nav badge + hero count, and any artist cards on other pages. Both are
      // vanilla-owned DOM outside this route.
      try {
        window.updateWatchlistButtonCount?.();
      } catch {
        /* non-fatal */
      }
    },
    onError: (error: Error) => {
      // The vanilla path used a raw alert() here; the app's toast is the
      // house style and matches every other error in this page.
      window.showToast?.(`Error removing artists: ${error.message}`, 'error');
    },
  });

  const addArtist = useMutation({
    mutationFn: addWatchlistArtist,
    onSuccess: async (_ignored, input) => {
      window.showToast?.(`Added ${input.artistName} to watchlist`, 'success');
      await queryClient.invalidateQueries({ queryKey: WATCHLIST_QUERY_KEY });
      try {
        window.updateWatchlistButtonCount?.();
      } catch {
        /* non-fatal */
      }
    },
    onError: (error: Error) => window.showToast?.(`Error adding artist: ${error.message}`, 'error'),
  });

  const onBatchRemove = async () => {
    const ids = selectedVisibleIds(visibleArtists, selectedIds);
    if (ids.length === 0) return;

    const confirmed = await window.showConfirmDialog?.({
      title: 'Remove Artists',
      message: `Remove ${ids.length} artist${ids.length !== 1 ? 's' : ''} from your watchlist?`,
      confirmText: 'Remove',
      destructive: true,
    });
    if (confirmed === false) return;
    batchRemove.mutate(ids);
  };

  const onRemoveOne = async (artistId: string, artistName: string) => {
    const confirmed = await window.showConfirmDialog?.({
      title: 'Remove Artist',
      message: `Remove ${artistName} from your watchlist?`,
      confirmText: 'Remove',
      destructive: true,
    });
    if (confirmed === false) return;
    batchRemove.mutate([artistId]);
  };

  const lastScanText = useMemo(() => {
    if (!scanStatus?.completed_at || !scanStatus.summary) return null;
    const found = scanStatus.summary.new_tracks_found || 0;
    const added = scanStatus.summary.tracks_added_to_wishlist || 0;
    return `Last scan: ${formatTimeAgo(scanStatus.completed_at)} — ${found} new track${
      found !== 1 ? 's' : ''
    } found, ${added} added to wishlist`;
  }, [scanStatus?.completed_at, scanStatus?.summary]);

  return (
    <div className="page-shell watchlist-page-container">
      <div className="watchlist-page-header">
        <div className="watchlist-page-header-left">
          <h2 className="watchlist-page-title">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="rgb(var(--accent-rgb))">
              <path d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z" />
            </svg>
            Watchlist
          </h2>
          <div className="watchlist-page-meta">
            <span className="wl-meta-chip">{headerCount}</span>
            <span className="wl-meta-chip wl-meta-chip--accent">{formatCountdown(countdown)}</span>
          </div>
        </div>
      </div>

      <div className="watchlist-page-actions">
        <button
          type="button"
          className={`wl-chip wl-chip--cta${isScanning ? ' btn-processing' : ''}`}
          // Also blocked while similar-artists runs: both drive the same worker.
          disabled={isScanning || startScan.isPending || similarRunning}
          onClick={() => startScan.mutate()}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="12" cy="12" r="10" />
            <polyline points="12 6 12 12 16 14" />
          </svg>
          {startScan.isPending
            ? 'Starting scan...'
            : isScanning
              ? 'Scanning...'
              : 'Scan for New Releases'}
          <span className="wl-chip-shimmer" />
        </button>

        {isScanning ? (
          <button
            type="button"
            className="wl-chip wl-chip--red"
            disabled={cancelScan.isPending}
            onClick={() => cancelScan.mutate()}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="15" y1="9" x2="9" y2="15" />
              <line x1="9" y1="9" x2="15" y2="15" />
            </svg>
            {cancelScan.isPending ? 'Cancelling...' : 'Cancel Scan'}
          </button>
        ) : null}

        <button
          type="button"
          className={`wl-chip wl-chip--slate${similarRunning ? ' btn-processing' : ''}`}
          disabled={similarRunning || startSimilar.isPending || isScanning}
          onClick={() => startSimilar.mutate()}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
            <circle cx="9" cy="7" r="4" />
            <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
            <path d="M16 3.13a4 4 0 0 1 0 7.75" />
          </svg>
          {similarRunning ? 'Updating...' : 'Update Similar Artists'}
        </button>

        <button
          type="button"
          className={`wl-chip wl-chip--slate${
            globalOverrideActive ? ' watchlist-global-settings-active' : ''
          }`}
          onClick={() => void navigate({ search: (prev) => ({ ...prev, settings: true }) })}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
          {globalOverrideActive ? 'Global Override ON' : 'Global Settings'}
        </button>

        {/* These three open modals owned by other vanilla files
            (origin-history.js, watchlist-history.js, blocklist.js) and shared
            with other pages. They stay where they are and are invoked as
            globals — unlike `socket`, top-level `function` declarations in a
            classic script ARE window properties. Porting them belongs to
            whichever page migration owns those modals. */}
        <button
          type="button"
          className="wl-chip wl-chip--slate"
          title="See every track your watchlist downloaded"
          onClick={() => window.openDownloadOriginsModal?.('watchlist')}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="7 10 12 15 17 10" />
            <line x1="12" y1="15" x2="12" y2="3" />
          </svg>
          Download Origins
        </button>

        <button
          type="button"
          className="wl-chip wl-chip--slate"
          title="Every past scan and the tracks it added to the wishlist"
          onClick={() => window.openWatchlistHistoryModal?.()}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M3 3v5h5" />
            <path d="M3.05 13A9 9 0 1 0 6 5.3L3 8" />
            <polyline points="12 7 12 12 15 15" />
          </svg>
          History
        </button>

        <button
          type="button"
          className="wl-chip wl-chip--red"
          title="Block artists, albums or tracks from ever being downloaded"
          onClick={() => window.openBlocklistModal?.('artist')}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="12" cy="12" r="10" />
            <line x1="4.9" y1="4.9" x2="19.1" y2="19.1" />
          </svg>
          Blocklist
        </button>
      </div>

      {search.settings ? (
        <WatchlistGlobalSettingsModal
          profileId={profileId}
          initialConfig={globalConfigQuery.data ?? null}
          onClose={() => void navigate({ search: (prev) => ({ ...prev, settings: false }) })}
        />
      ) : null}

      {search.configId ? (
        <WatchlistArtistConfigModal
          profileId={profileId}
          artistId={search.configId}
          globalOverrideActive={globalOverrideActive}
          onClose={() => void navigate({ search: (prev) => ({ ...prev, configId: undefined }) })}
        />
      ) : null}

      {search.detailId ? (
        <WatchlistArtistDetail
          profileId={profileId}
          artistId={search.detailId}
          onClose={() => void navigate({ search: (prev) => ({ ...prev, detailId: undefined }) })}
          // Settings replaces the detail view with the config modal, so the
          // panel cannot sit on top of it — the vanilla code removed the
          // overlay before opening the modal for the same reason.
          onOpenSettings={() =>
            void navigate({
              search: (prev) => ({ ...prev, detailId: undefined, configId: search.detailId }),
            })
          }
        />
      ) : null}

      {globalOverrideActive ? (
        <div className="watchlist-global-override-banner">
          <span>⚠️</span>
          <span>
            Global override is active — per-artist settings are being ignored during scans.
          </span>
        </div>
      ) : null}

      <div className={styles.tabs}>
        <button
          type="button"
          className={`${styles.tab} ${!isLabelsTab ? styles.tabActive : ''}`}
          onClick={() => void navigate({ search: (prev) => ({ ...prev, tab: 'artists' }) })}
        >
          Artists
        </button>
        <button
          type="button"
          className={`${styles.tab} ${isLabelsTab ? styles.tabActive : ''}`}
          onClick={() => void navigate({ search: (prev) => ({ ...prev, tab: 'labels' }) })}
        >
          Labels
        </button>
      </div>

      {scanStatus ? <WatchlistScanDeck frame={scanStatus} /> : null}

      {isLabelsTab ? (
        <WatchlistLabelsTab profileId={profileId} />
      ) : (
        <>
          {lastScanText ? (
            <div className="watchlist-last-scan-strip">
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
              <span>{lastScanText}</span>
            </div>
          ) : null}

          {count === 0 ? (
            <WatchlistEmptyState
              profileId={profileId}
              onAddArtist={(artistId, artistName, source) =>
                addArtist.mutate({ artistId, artistName, source })
              }
              adding={addArtist.isPending}
            />
          ) : (
            <>
              <WatchlistAttentionStrip
                total={count}
                neverScanned={neverScanned}
                staleArtists={staleArtists}
                customRuleArtists={customRuleArtists}
                recentCount={recentReleases.length}
                globalOverrideActive={globalOverrideActive}
              />

              <WatchlistAddArtistSearch
                profileId={profileId}
                onAddArtist={(artistId, artistName, source) =>
                  addArtist.mutate({ artistId, artistName, source })
                }
                adding={addArtist.isPending}
              />

              <WatchlistRecentReleasesPanel
                releases={recentReleases}
                loading={recentReleasesQuery.isLoading}
              />

              <div className="watchlist-toolbar">
                <div className="watchlist-search-container">
                  <svg
                    className="watchlist-search-icon"
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="rgba(255,255,255,0.35)"
                  >
                    <path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z" />
                  </svg>
                  <input
                    type="text"
                    className="watchlist-search-input"
                    placeholder="Filter watchlist…"
                    value={search.q}
                    onChange={(event) =>
                      void navigate({
                        search: (prev) => ({ ...prev, q: event.target.value }),
                        replace: true,
                      })
                    }
                  />
                </div>
                <select
                  className="watchlist-sort-select"
                  value={search.sort}
                  onChange={(event) =>
                    void navigate({
                      search: (prev) => ({
                        ...prev,
                        sort: event.target.value as typeof search.sort,
                      }),
                    })
                  }
                >
                  {WATCHLIST_SORT_VALUES.map((value) => (
                    <option key={value} value={value}>
                      {SORT_LABELS[value]}
                    </option>
                  ))}
                </select>
              </div>

              <div className="watchlist-batch-bar">
                <label
                  className="watchlist-select-all-label"
                  onClick={(event) => event.stopPropagation()}
                >
                  <SelectAllCheckbox
                    checked={selection.allSelected}
                    indeterminate={selection.indeterminate}
                    onChange={toggleSelectAll}
                  />
                  <span>Select All</span>
                </label>
                <span className="watchlist-batch-count">
                  {selection.selectedCount > 0 ? `${selection.selectedCount} selected` : ''}
                </span>
                {selection.selectedCount > 0 ? (
                  <button
                    type="button"
                    className="btn btn--secondary watchlist-batch-remove-btn"
                    disabled={batchRemove.isPending}
                    onClick={() => void onBatchRemove()}
                  >
                    Remove Selected
                  </button>
                ) : null}
              </div>

              <div className="watchlist-command-layout">
                <div className="watchlist-roster-panel">
                  <div className="watchlist-roster-head">
                    <span>Artists</span>
                    <span>{visibleArtists.length} shown</span>
                  </div>
                  <div className="watchlist-artists-grid watchlist-artists-grid--rows">
                    {visibleArtists.map((artist) => {
                      const artistId = primaryArtistId(artist);
                      return (
                        <WatchlistArtistCard
                          key={artist.id}
                          artist={artist}
                          selected={artistId !== null && selectedIds.has(artistId)}
                          active={artistId !== null && artistId === activeArtistId}
                          onToggleSelect={() => artistId && toggleArtist(artistId)}
                          onOpenConfig={() =>
                            artistId &&
                            void navigate({ search: (prev) => ({ ...prev, configId: artistId }) })
                          }
                          onOpenDetail={() => {
                            if (artistId) setActiveArtistId(artistId);
                          }}
                          onOpenFullDetail={() =>
                            artistId &&
                            void navigate({ search: (prev) => ({ ...prev, detailId: artistId }) })
                          }
                          onRemove={() => {
                            if (artistId) void onRemoveOne(artistId, artist.artist_name);
                          }}
                        />
                      );
                    })}
                  </div>
                </div>
                <WatchlistArtistInspector
                  profileId={profileId}
                  artist={activeArtist}
                  globalOverrideActive={globalOverrideActive}
                  onOpenConfig={(artistId) =>
                    void navigate({ search: (prev) => ({ ...prev, configId: artistId }) })
                  }
                  onOpenDetail={(artistId) =>
                    void navigate({ search: (prev) => ({ ...prev, detailId: artistId }) })
                  }
                  onRemove={(artistId) => {
                    const name = activeArtist?.artist_name ?? 'this artist';
                    void onRemoveOne(artistId, name);
                  }}
                />
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}

interface WatchlistArtistCardProps {
  artist: WatchlistArtist;
  selected: boolean;
  active: boolean;
  onToggleSelect: () => void;
  onOpenConfig: () => void;
  onOpenDetail: () => void;
  onOpenFullDetail: () => void;
  onRemove: () => void;
}

function WatchlistArtistCard({
  artist,
  selected,
  active,
  onToggleSelect,
  onOpenConfig,
  onOpenDetail,
  onOpenFullDetail,
  onRemove,
}: WatchlistArtistCardProps) {
  const pills = artistPills(artist);
  const sources = artistSourceKeys(artist);
  const artistId = primaryArtistId(artist);
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPosition, setMenuPosition] = useState<{ top: number; right: number } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const closeOnOutside = (event: MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (menuRef.current?.contains(target) || menuButtonRef.current?.contains(target)) return;
      setMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuOpen(false);
    };
    const closeOnScroll = () => setMenuOpen(false);
    document.addEventListener('mousedown', closeOnOutside);
    document.addEventListener('keydown', closeOnEscape);
    window.addEventListener('scroll', closeOnScroll, true);
    window.addEventListener('resize', closeOnScroll);
    return () => {
      document.removeEventListener('mousedown', closeOnOutside);
      document.removeEventListener('keydown', closeOnEscape);
      window.removeEventListener('scroll', closeOnScroll, true);
      window.removeEventListener('resize', closeOnScroll);
    };
  }, [menuOpen]);

  const runMenuAction = (action: () => void) => {
    setMenuOpen(false);
    action();
  };

  const toggleMenu = () => {
    const rect = menuButtonRef.current?.getBoundingClientRect();
    if (rect) {
      setMenuPosition({
        top: rect.bottom + 8,
        right: Math.max(12, window.innerWidth - rect.right),
      });
    }
    setMenuOpen((open) => !open);
  };

  return (
    <div
      className={`watchlist-artist-card${active ? ' watchlist-artist-card--active' : ''}`}
      data-artist-id={artistId ?? ''}
      onClick={onOpenDetail}
    >
      {/* The checkbox and overflow menu sit inside the row, so both stop the
          click from also switching the sidebar selection. */}
      <label className="watchlist-card-checkbox" onClick={(event) => event.stopPropagation()}>
        <input
          type="checkbox"
          className="watchlist-select-cb"
          checked={selected}
          onChange={onToggleSelect}
          aria-label={`Select ${artist.artist_name}`}
        />
        <span className="watchlist-checkbox-custom" />
      </label>
      <div className="watchlist-card-image">
        <ArtistImage url={artist.image_url} name={artist.artist_name} />
      </div>
      <div className="watchlist-card-info">
        <span
          className="watchlist-card-name"
          onClick={(event) => {
            event.stopPropagation();
            onOpenFullDetail();
          }}
        >
          {artist.artist_name}
        </span>
        <span className="watchlist-card-meta">
          {formatRelativeScanTime(artist.last_scan_timestamp)}
        </span>
      </div>
      <div className="watchlist-row-stats">
        <span>{releaseTypeText(artist)}</span>
        <span>
          {artist.date_added
            ? `Added ${new Date(artist.date_added).toLocaleDateString()}`
            : 'Added date unknown'}
        </span>
      </div>
      <div className="watchlist-row-tags">
        {sources.length > 0 ? (
          <div
            className="watchlist-card-sources"
            aria-label={`Matched sources for ${artist.artist_name}`}
          >
            {sources.map((key) => (
              <span
                key={key}
                className={`watchlist-source-badge ${WATCHLIST_SOURCE_BADGES[key].className}`}
              >
                {WATCHLIST_SOURCE_BADGES[key].label}
              </span>
            ))}
          </div>
        ) : null}
        {pills.length > 0 ? (
          <div
            className="watchlist-card-pills"
            aria-label={`Watch rules for ${artist.artist_name}`}
          >
            {pills.map((pill) => (
              <span key={pill.label} className={`watchlist-pill watchlist-pill-${pill.kind}`}>
                {pill.label}
              </span>
            ))}
          </div>
        ) : (
          <span className="watchlist-row-empty-rule">Default rules</span>
        )}
      </div>
      <div className="watchlist-row-health">
        <span>{sources.length} matches</span>
        <span>{ruleCount(artist)} rules</span>
        <span>{scanFreshnessLabel(artist)}</span>
      </div>
      <button
        ref={menuButtonRef}
        type="button"
        className="watchlist-row-menu-trigger"
        aria-label={`More actions for ${artist.artist_name}`}
        title="More actions"
        aria-expanded={menuOpen}
        onClick={(event) => {
          event.stopPropagation();
          toggleMenu();
        }}
      >
        ...
      </button>
      {menuOpen && menuPosition
        ? createPortal(
            <div
              ref={menuRef}
              className="watchlist-row-menu"
              style={{ top: menuPosition.top, right: menuPosition.right }}
              role="menu"
              aria-label={`Actions for ${artist.artist_name}`}
              onClick={(event) => event.stopPropagation()}
            >
              <button type="button" role="menuitem" onClick={() => runMenuAction(onOpenDetail)}>
                Manage in sidebar
              </button>
              <button type="button" role="menuitem" onClick={() => runMenuAction(onOpenFullDetail)}>
                Full profile
              </button>
              <button type="button" role="menuitem" onClick={() => runMenuAction(onOpenConfig)}>
                Edit rules
              </button>
              <button type="button" role="menuitem" onClick={() => runMenuAction(onToggleSelect)}>
                {selected ? 'Clear selection' : 'Select for batch'}
              </button>
              <div className="watchlist-row-menu-sep" />
              <button
                type="button"
                role="menuitem"
                className="watchlist-row-menu-danger"
                onClick={() => runMenuAction(onRemove)}
              >
                Remove from watchlist
              </button>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}

function WatchlistAttentionStrip({
  total,
  neverScanned,
  staleArtists,
  customRuleArtists,
  recentCount,
  globalOverrideActive,
}: {
  total: number;
  neverScanned: number;
  staleArtists: number;
  customRuleArtists: number;
  recentCount: number;
  globalOverrideActive: boolean;
}) {
  const items = [
    { label: 'Watched artists', value: total, tone: 'neutral' },
    { label: 'Fresh releases', value: recentCount, tone: 'green' },
    { label: 'Never scanned', value: neverScanned, tone: neverScanned > 0 ? 'amber' : 'neutral' },
    { label: 'Stale scans', value: staleArtists, tone: staleArtists > 0 ? 'amber' : 'neutral' },
    {
      label: 'Custom rules',
      value: customRuleArtists,
      tone: customRuleArtists > 0 ? 'blue' : 'neutral',
    },
  ];

  return (
    <div className="watchlist-attention-strip">
      {items.map((item) => (
        <div key={item.label} className={`watchlist-attention-card is-${item.tone}`}>
          <span className="watchlist-attention-value">{item.value}</span>
          <span className="watchlist-attention-label">{item.label}</span>
        </div>
      ))}
      {globalOverrideActive ? (
        <div className="watchlist-attention-card is-amber watchlist-attention-card--wide">
          <span className="watchlist-attention-value">ON</span>
          <span className="watchlist-attention-label">Global override active</span>
        </div>
      ) : null}
    </div>
  );
}

function WatchlistAddArtistSearch({
  profileId,
  onAddArtist,
  adding,
}: {
  profileId: number;
  onAddArtist: (artistId: string, artistName: string, source: string) => void;
  adding: boolean;
}) {
  const [query, setQuery] = useState('');
  const [provider, setProvider] =
    useState<(typeof ADD_PROVIDER_OPTIONS)[number]['value']>('deezer');
  const [debouncedQuery, setDebouncedQuery] = useState('');

  useEffect(() => {
    const next = query.trim();
    if (next.length < 2) {
      setDebouncedQuery('');
      return;
    }
    const timer = window.setTimeout(() => setDebouncedQuery(next), 320);
    return () => window.clearTimeout(timer);
  }, [query, provider]);

  const resultsQuery = useQuery({
    queryKey: [
      ...WATCHLIST_QUERY_KEY,
      'artist-search',
      profileId,
      provider,
      debouncedQuery,
    ] as const,
    queryFn: () => searchProviderArtists(provider, debouncedQuery),
    enabled: debouncedQuery.length >= 2,
  });

  const results = resultsQuery.data ?? [];
  const trimmedQuery = query.trim();
  const pendingDebounce = trimmedQuery.length >= 2 && trimmedQuery !== debouncedQuery;
  const searching = resultsQuery.isFetching || pendingDebounce;
  const showResults = trimmedQuery.length >= 2;

  return (
    <div className="watchlist-add-panel">
      <div className="watchlist-add-copy">
        <span className="watchlist-add-kicker">Add artist</span>
        <span>Search a provider and follow without leaving the watchlist.</span>
      </div>
      <div className="watchlist-add-form" role="search">
        <select
          className="watchlist-sort-select"
          value={provider}
          aria-label="Provider"
          onChange={(event) =>
            setProvider(event.target.value as (typeof ADD_PROVIDER_OPTIONS)[number]['value'])
          }
        >
          {ADD_PROVIDER_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <input
          type="text"
          className="watchlist-add-input"
          value={query}
          placeholder="Search artist to watch..."
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="watchlist-add-status" aria-live="polite">
          {searching
            ? 'Searching...'
            : debouncedQuery
              ? `${results.length} found`
              : 'Type 2+ letters'}
        </div>
        {query ? (
          <button
            type="button"
            className="watchlist-add-clear"
            aria-label="Clear artist search"
            onClick={() => setQuery('')}
          >
            ×
          </button>
        ) : null}
      </div>
      {showResults ? (
        <div className="watchlist-add-results">
          {resultsQuery.isError ? (
            <div className="watchlist-add-empty">Could not search {providerLabel(provider)}.</div>
          ) : results.length > 0 ? (
            results
              .slice(0, 5)
              .map((result) => (
                <WatchlistAddResult
                  key={`${provider}-${result.id}`}
                  result={result}
                  provider={provider}
                  adding={adding}
                  onAddArtist={onAddArtist}
                />
              ))
          ) : searching ? (
            <div className="watchlist-add-empty">Searching {providerLabel(provider)}...</div>
          ) : (
            <div className="watchlist-add-empty">No artists found for "{debouncedQuery}".</div>
          )}
        </div>
      ) : null}
    </div>
  );
}

function WatchlistAddResult({
  result,
  provider,
  adding,
  onAddArtist,
}: {
  result: ProviderSearchResult;
  provider: string;
  adding: boolean;
  onAddArtist: (artistId: string, artistName: string, source: string) => void;
}) {
  return (
    <div className="watchlist-add-result">
      {result.image ? (
        <img src={result.image} alt="" />
      ) : (
        <div className="watchlist-add-avatar">{result.name.slice(0, 1).toUpperCase()}</div>
      )}
      <div className="watchlist-add-result-main">
        <span>{result.name}</span>
        <small>
          <b>{providerLabel(provider)}</b>
          {result.extra ? ` · ${result.extra}` : ''}
        </small>
      </div>
      <button
        type="button"
        className="watchlist-add-result-action"
        disabled={adding}
        onClick={() => onAddArtist(result.id, result.name, provider)}
      >
        {adding ? 'Adding' : 'Add'}
      </button>
    </div>
  );
}

function WatchlistRecentReleasesPanel({
  releases,
  loading,
}: {
  releases: WatchlistRecentReleaseRow[];
  loading: boolean;
}) {
  if (!loading && releases.length === 0) return null;

  return (
    <div className="watchlist-recent-panel">
      <div className="watchlist-panel-head">
        <div>
          <span className="watchlist-panel-kicker">Fresh from watched artists</span>
          <h3>Recent releases</h3>
        </div>
        <span>{loading ? 'Loading...' : `${releases.length} shown`}</span>
      </div>
      {releases.length > 0 ? (
        <div className="watchlist-recent-row">
          {releases.slice(0, 8).map((release, index) => (
            <div
              key={`${release.artist_name ?? ''}-${release.album_name}-${index}`}
              className="watchlist-release-card"
            >
              {release.album_cover_url ? (
                <img src={release.album_cover_url} alt="" />
              ) : (
                <div className="watchlist-release-fallback">♪</div>
              )}
              <div>
                <span>{release.album_name}</span>
                <small>
                  {release.artist_name || 'Unknown artist'}
                  {release.release_date ? ` · ${release.release_date}` : ''}
                </small>
              </div>
              {release.owned ? <em>Owned</em> : null}
            </div>
          ))}
        </div>
      ) : (
        <div className="watchlist-recent-empty">
          {loading ? 'Loading recent releases...' : 'No recent releases have been cached yet.'}
        </div>
      )}
    </div>
  );
}

function WatchlistArtistInspector({
  profileId,
  artist,
  globalOverrideActive,
  onOpenConfig,
  onOpenDetail,
  onRemove,
}: {
  profileId: number;
  artist: WatchlistArtist | null;
  globalOverrideActive: boolean;
  onOpenConfig: (artistId: string) => void;
  onOpenDetail: (artistId: string) => void;
  onRemove: (artistId: string) => void;
}) {
  const artistId = artist ? primaryArtistId(artist) : null;
  const configQuery = useQuery({
    ...watchlistArtistConfigQueryOptions(profileId, artistId ?? ''),
    enabled: Boolean(artistId),
  });
  const payload = configQuery.data;
  const releases = payload?.recent_releases ?? [];
  const pills = artist ? artistPills(artist) : [];
  const sources = artist ? artistSourceKeys(artist) : [];

  if (!artist || !artistId) {
    return (
      <aside className="watchlist-inspector">
        <div className="watchlist-inspector-empty">Select an artist to manage watch rules.</div>
      </aside>
    );
  }

  return (
    <aside className="watchlist-inspector">
      <div className="watchlist-inspector-hero">
        <ArtistImage
          url={artist.image_url || payload?.artist?.image_url || null}
          name={artist.artist_name}
        />
        <div>
          <span className="watchlist-panel-kicker">Selected artist</span>
          <h3 title={artist.artist_name} data-artist-name={artist.artist_name}>
            Managing
          </h3>
          <small>{formatRelativeScanTime(artist.last_scan_timestamp)}</small>
        </div>
      </div>

      <div className="watchlist-inspector-actions">
        <button
          type="button"
          className="wl-chip wl-chip--cta"
          onClick={() => onOpenConfig(artistId)}
        >
          Edit Rules
        </button>
        <button
          type="button"
          className="wl-chip wl-chip--slate"
          onClick={() => onOpenDetail(artistId)}
        >
          Full Profile
        </button>
        <button type="button" className="wl-chip wl-chip--red" onClick={() => onRemove(artistId)}>
          Remove
        </button>
      </div>

      {globalOverrideActive ? (
        <div className="watchlist-inspector-warning">
          Artist-specific release rules are currently ignored during scans.
        </div>
      ) : null}

      <div className="watchlist-inspector-section">
        <div className="watchlist-panel-head compact">
          <h4>Watch rules</h4>
          <span>{releaseTypeText(artist)}</span>
        </div>
        <div className="watchlist-card-pills inspector-pills">
          {pills.length > 0 ? (
            pills.map((pill) => (
              <span key={pill.label} className={`watchlist-pill watchlist-pill-${pill.kind}`}>
                {pill.label} rule
              </span>
            ))
          ) : (
            <span className="watchlist-card-meta">No release types enabled</span>
          )}
        </div>
      </div>

      <div className="watchlist-inspector-section">
        <div className="watchlist-panel-head compact">
          <h4>Provider links</h4>
          <span>{sources.length} matched</span>
        </div>
        <div className="watchlist-inspector-sources">
          {sources.map((key) => (
            <span
              key={key}
              className={`watchlist-source-badge ${WATCHLIST_SOURCE_BADGES[key].className}`}
            >
              {WATCHLIST_SOURCE_BADGES[key].label} match
            </span>
          ))}
        </div>
      </div>

      <div className="watchlist-inspector-section">
        <div className="watchlist-panel-head compact">
          <h4>Recent releases</h4>
          <span>{configQuery.isFetching ? 'Loading...' : `${releases.length}`}</span>
        </div>
        <div className="watchlist-inspector-releases">
          {releases.slice(0, 4).map((release) => (
            <div key={`${release.album_name}-${release.release_date ?? ''}`}>
              {release.album_cover_url ? <img src={release.album_cover_url} alt="" /> : null}
              <span>{release.album_name}</span>
              <small>{release.release_date || 'Unknown date'}</small>
            </div>
          ))}
          {!configQuery.isFetching && releases.length === 0 ? (
            <span className="watchlist-card-meta">No cached releases for this artist yet.</span>
          ) : null}
        </div>
      </div>
    </aside>
  );
}

/**
 * Select All, including its half-ticked state.
 *
 * `indeterminate` is a DOM property with no HTML attribute, so React cannot set
 * it from JSX — it has to be assigned to the node directly after every render.
 */
function SelectAllCheckbox({
  checked,
  indeterminate,
  onChange,
}: {
  checked: boolean;
  indeterminate: boolean;
  onChange: (checked: boolean) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);

  return (
    <input
      ref={ref}
      type="checkbox"
      checked={checked}
      onChange={(event) => onChange(event.target.checked)}
      aria-label="Select all visible artists"
    />
  );
}

/**
 * The vanilla card retried a failed image once before falling back, because
 * artist art is fetched from provider CDNs that intermittently 503. Keeping
 * that: one retry, then the emoji placeholder.
 */
function ArtistImage({ url, name }: { url: string | null; name: string }) {
  const [attempt, setAttempt] = useState(0);

  if (!url || attempt > 1) {
    return <div className="watchlist-card-image-fallback">🎤</div>;
  }

  return (
    <img
      // Remounting on retry is what actually re-requests the image; without a
      // changing key React keeps the failed element and onError never refires.
      key={attempt}
      src={url}
      alt={name}
      onError={() => setAttempt((n) => n + 1)}
    />
  );
}

function WatchlistEmptyState({
  profileId,
  onAddArtist,
  adding,
}: {
  profileId: number;
  onAddArtist: (artistId: string, artistName: string, source: string) => void;
  adding: boolean;
}) {
  const navigate = useNavigate();

  return (
    <div className="watchlist-page-empty">
      <div className="watchlist-page-empty-icon">
        <svg
          width="64"
          height="64"
          viewBox="0 0 24 24"
          fill="none"
          stroke="rgba(255,255,255,0.15)"
          strokeWidth="1.5"
        >
          <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
          <circle cx="12" cy="12" r="3" />
        </svg>
      </div>
      <h3>Your watchlist is empty</h3>
      <p>Use Search to find an artist, then add them to your watchlist from the artist page.</p>
      <WatchlistAddArtistSearch profileId={profileId} onAddArtist={onAddArtist} adding={adding} />
      {/* Search is still a legacy page, so this goes out as an href and lands
          on the splat route, which hands off to the vanilla renderer. */}
      <button
        className="btn btn--primary"
        type="button"
        onClick={() => void navigate({ href: '/search' })}
      >
        Open Search
      </button>
    </div>
  );
}
