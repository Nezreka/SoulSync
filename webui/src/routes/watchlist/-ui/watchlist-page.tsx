import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { AudiobookRouteBadge } from '@/features/audiobook-routes/audiobook-route-badge';
import { useRoutedArtists } from '@/features/audiobook-routes/audiobook-routes.queries';
import { useFeatureOn } from '@/platform/features';
import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';
import { ActionMenu } from '@/routes/artist-detail/-ui/action-menu';

import {
  addWatchlistArtist,
  cancelWatchlistScan,
  removeWatchlistArtistsBatch,
  searchProviderArtists,
  similarArtistsStatusQueryOptions,
  startSimilarArtistsUpdate,
  startWatchlistScan,
  WATCHLIST_QUERY_KEY,
  watchlistArtistsQueryOptions,
  watchlistCountQueryOptions,
  watchlistGlobalConfigQueryOptions,
  watchlistLabelsQueryOptions,
  watchlistPodcastsQueryOptions,
  watchlistFoundQueryOptions,
  watchlistLastRunQueryOptions,
  watchlistRecentReleasesQueryOptions,
  watchlistScanStatusQueryOptions,
} from '../-watchlist.api';
import {
  artistPills,
  hasCustomRules,
  artistSourceKeys,
  batchSelectionState,
  filterArtists,
  formatArtistCount,
  formatCountdown,
  formatRelativeScanTime,
  primaryArtistId,
  selectedVisibleIds,
  sortArtists,
  timestampValue,
  WATCHLIST_SOURCE_BADGES,
} from '../-watchlist.helpers';
import { useCountdown, useLiveWatchlistScan } from '../-watchlist.scan';
import {
  WATCHLIST_SORT_VALUES,
  type WatchlistArtist,
  type WatchlistFoundRelease,
  type WatchlistRecentReleaseRow,
} from '../-watchlist.types';
import { fetchLibraryV2Album } from '../../library/-library-v2.api';
import { albumQueueRows } from '../../library/-library-v2.play';
import { SourceIcon } from '../../sync/-ui/source-icon';
import { Route } from '../route';
import { WatchlistArtistPanel } from './watchlist-artist-panel';
import { WatchlistAudiobooksTab } from './watchlist-audiobooks-tab';
import { WatchlistGlobalSettingsModal } from './watchlist-global-settings-modal';
import home from './watchlist-home.module.css';
import { WatchlistLabelsTab } from './watchlist-labels-tab';
import { WatchlistPodcastsTab } from './watchlist-podcasts-tab';
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
  const foundQuery = useQuery(watchlistFoundQueryOptions(profileId));
  const lastRunQuery = useQuery(watchlistLastRunQueryOptions(profileId));
  const [pickMode, setPickMode] = useState(false);

  const artists = useMemo(() => artistsQuery.data ?? [], [artistsQuery.data]);
  // One lookup for the whole page; the cards only read the result.
  const audiobookArtists = useRoutedArtists(artists.map((artist) => artist.artist_name));
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

  type AttentionFilterKey = 'all' | 'fresh' | 'never' | 'stale' | 'custom';
  const [attentionFilter, setAttentionFilter] = useState<AttentionFilterKey>('all');
  const [isAddArtistOpen, setIsAddArtistOpen] = useState(false);
  /** the Sort by / Show menu */
  const [viewOpen, setViewOpen] = useState(false);

  const filteredByAttention = useMemo(() => {
    if (attentionFilter === 'fresh') {
      const freshNames = new Set(
        recentReleases.map((r) => r.artist_name?.toLowerCase()).filter(Boolean),
      );
      return artists.filter((a) => freshNames.has(a.artist_name.toLowerCase()));
    }
    if (attentionFilter === 'never') {
      return artists.filter((a) => !a.last_scan_timestamp);
    }
    if (attentionFilter === 'stale') {
      return artists.filter((a) => (daysSince(a.last_scan_timestamp) ?? 999) >= 30);
    }
    if (attentionFilter === 'custom') {
      return artists.filter((a) => hasCustomRules(a));
    }
    return artists;
  }, [artists, attentionFilter, recentReleases]);

  const visibleArtists = useMemo(
    () => sortArtists(filterArtists(filteredByAttention, search.q), search.sort),
    [filteredByAttention, search.q, search.sort],
  );
  // the artist whose panel is open, highlighted in the roster
  const panelArtistId = search.detailId ?? search.configId ?? null;

  const neverScanned = useMemo(
    () => artists.filter((artist) => !artist.last_scan_timestamp).length,
    [artists],
  );
  const staleArtists = useMemo(
    () => artists.filter((artist) => (daysSince(artist.last_scan_timestamp) ?? 999) >= 30).length,
    [artists],
  );
  const customRuleArtists = useMemo(
    () => artists.filter((artist) => hasCustomRules(artist)).length,
    [artists],
  );

  const isLabelsTab = search.tab === 'labels';
  // podcasts / audiobooks switched off (#1909) drop their tab
  const podcastsOn = useFeatureOn('podcasts');
  const audiobooksOn = useFeatureOn('audiobooks');
  const isPodcastsTab = search.tab === 'podcasts';
  const isAudiobooksTab = search.tab === 'audiobooks';

  // The header chip counts labels or podcasts while their tab is open.
  // `enabled` keeps inactive tabs from paying for unnecessary round trips.
  const labelsQuery = useQuery({
    ...watchlistLabelsQueryOptions(profileId),
    enabled: isLabelsTab,
  });
  const podcastsQuery = useQuery({
    ...watchlistPodcastsQueryOptions(profileId),
    enabled: isPodcastsTab,
  });
  const labelCount = labelsQuery.data?.length ?? 0;
  const podcastCount = podcastsQuery.data?.length ?? 0;
  const headerCount = isLabelsTab
    ? `${labelCount} label${labelCount !== 1 ? 's' : ''}`
    : isPodcastsTab
      ? `${podcastCount} podcast${podcastCount !== 1 ? 's' : ''}`
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

  const foundReleases = foundQuery.data ?? [];
  // the newest find per artist; the list is newest first
  const foundByArtist = new Map<string, string>();
  for (const release of foundReleases) {
    const key = release.artist_name.toLowerCase();
    if (!foundByArtist.has(key)) foundByArtist.set(key, release.album_name);
  }
  const picking = pickMode || selection.selectedCount > 0;
  const nextScan = nextScanText(countdown);
  const showFilters: { id: AttentionFilterKey; label: string; count: number }[] = [
    { id: 'all', label: 'All', count },
    { id: 'fresh', label: 'New releases', count: recentReleases.length },
    { id: 'never', label: 'Never scanned', count: neverScanned },
    { id: 'stale', label: 'Not scanned in 30 days', count: staleArtists },
    { id: 'custom', label: 'Own rules', count: customRuleArtists },
  ];
  const openArtistByName = (name: string | null | undefined) => {
    const match = artists.find(
      (artist) => artist.artist_name.toLowerCase() === (name ?? '').toLowerCase(),
    );
    const id = match ? primaryArtistId(match) : null;
    if (id) {
      void navigate({ search: (prev) => ({ ...prev, detailId: id, configId: undefined }) });
    }
  };
  const setTab = (tab: 'artists' | 'labels' | 'podcasts' | 'audiobooks') =>
    void navigate({ search: (prev) => ({ ...prev, tab }) });
  const sectionTabs: {
    id: 'artists' | 'labels' | 'podcasts' | 'audiobooks';
    label: string;
    count?: number;
  }[] = [
    { id: 'artists', label: 'Artists', count },
    { id: 'labels', label: 'Labels', count: labelsQuery.data ? labelCount : undefined },
    ...(podcastsOn || isPodcastsTab
      ? [
          {
            id: 'podcasts' as const,
            label: 'Podcasts',
            count: podcastsQuery.data ? podcastCount : undefined,
          },
        ]
      : []),
    ...(audiobooksOn || isAudiobooksTab
      ? [{ id: 'audiobooks' as const, label: 'Audiobooks' }]
      : []),
  ];

  return (
    <div className="page-shell">
      <header className={home.top}>
        <div className={home.topText}>
          <h2 className={home.title}>Watchlist</h2>
          {/* one line that says where things stand: what is watched, when the
              next scan runs, what the last one found */}
          <p className={home.sub}>{headerCount}</p>
        </div>

        {/* #1822: one loud action. scan is why you come here; adding an artist
            is the other everyday job. everything monthly sits in Tools. */}
        <div className={home.topActions}>
          {/* Origins, History and Blocklist open modals owned by other vanilla
              files (origin-history.js, watchlist-history.js, blocklist.js) and
              shared with other pages, so they are invoked as globals. */}
          <ActionMenu
            className="wl-more-menu"
            align="right"
            items={[
              {
                key: 'similar',
                label: toolLabel(
                  similarRunning ? 'Updating similar artists…' : 'Update similar artists',
                  'Refresh who sounds like the artists you watch.',
                ),
                disabled: similarRunning || startSimilar.isPending || isScanning,
                onSelect: () => startSimilar.mutate(),
              },
              {
                key: 'settings',
                label: toolLabel(
                  'Watchlist defaults',
                  'The rules every artist follows by default.',
                ),
                onSelect: () => void navigate({ search: (prev) => ({ ...prev, settings: true }) }),
              },
              {
                key: 'origins',
                label: toolLabel('Download origins', 'Every track your watchlist downloaded.'),
                title: 'See every track your watchlist downloaded',
                onSelect: () => window.openDownloadOriginsModal?.('watchlist'),
              },
              {
                key: 'history',
                label: toolLabel('Scan history', 'Past scans and what each added to the wishlist.'),
                title: 'Every past scan and the tracks it added to the wishlist',
                onSelect: () => window.openWatchlistHistoryModal?.(),
              },
              {
                key: 'blocklist',
                label: toolLabel('Blocklist', 'Artists, albums or tracks never to download.'),
                title: 'Block artists, albums or tracks from ever being downloaded',
                onSelect: () => window.openBlocklistModal?.('artist'),
              },
            ]}
            trigger={(props) => (
              <button
                type="button"
                className={home.tools}
                title="Similar artists, defaults, origins, history, blocklist"
                {...props}
                ref={props.ref as (el: HTMLButtonElement | null) => void}
              >
                <svg
                  viewBox="0 0 24 24"
                  width="18"
                  height="18"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  aria-hidden="true"
                >
                  <path d="M4 7h10" />
                  <path d="M18 7h2" />
                  <circle cx="16" cy="7" r="2" />
                  <path d="M4 17h4" />
                  <path d="M12 17h8" />
                  <circle cx="10" cy="17" r="2" />
                </svg>
                Tools
              </button>
            )}
          />
          <button
            type="button"
            className={home.ghost}
            aria-expanded={isAddArtistOpen}
            onClick={() => setIsAddArtistOpen(true)}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.6"
              strokeLinecap="round"
              aria-hidden="true"
            >
              <path d="M12 5v14" />
              <path d="M5 12h14" />
            </svg>
            Add artist
          </button>
          {isScanning ? (
            <button
              type="button"
              className={home.ghost}
              disabled={cancelScan.isPending}
              onClick={() => cancelScan.mutate()}
            >
              <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                <rect x="1" y="1" width="10" height="10" rx="2" fill="currentColor" />
              </svg>
              {cancelScan.isPending ? 'Stopping…' : 'Stop scan'}
            </button>
          ) : (
            <button
              type="button"
              className={home.cta}
              // Also blocked while similar-artists runs: both drive the same worker.
              disabled={startScan.isPending || similarRunning}
              title="Check every watched artist for new releases"
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
                aria-hidden="true"
              >
                <path d="M20 11a8 8 0 0 0-14.3-4.9L4 8" />
                <path d="M4 4v4h4" />
                <path d="M4 13a8 8 0 0 0 14.3 4.9L20 16" />
                <path d="M20 20v-4h-4" />
              </svg>
              {startScan.isPending
                ? 'Starting…'
                : similarRunning
                  ? 'Updating similar artists…'
                  : 'Scan now'}
            </button>
          )}
        </div>
      </header>

      {isAddArtistOpen ? (
        <WatchlistAddArtistSheet
          profileId={profileId}
          onAddArtist={(artistId, artistName, source) =>
            addArtist.mutate({ artistId, artistName, source })
          }
          adding={addArtist.isPending}
          onClose={() => setIsAddArtistOpen(false)}
        />
      ) : null}

      {/* the panel and the defaults modal still dress themselves from
          watchlist-premier.css, which scopes its rules (and the drawer's
          surface colour) under .watchlist-page-container. display: contents
          keeps that scope without the old page box. */}
      <div className={`watchlist-page-container ${home.legacyScope}`}>
        {search.settings ? (
          <WatchlistGlobalSettingsModal
            profileId={profileId}
            initialConfig={globalConfigQuery.data ?? null}
            onClose={() => void navigate({ search: (prev) => ({ ...prev, settings: false }) })}
          />
        ) : null}

        {panelArtistId ? (
          <WatchlistArtistPanel
            profileId={profileId}
            artistId={panelArtistId}
            initialTab={search.configId ? 'rules' : 'overview'}
            onClose={() =>
              void navigate({
                search: (prev) => ({ ...prev, configId: undefined, detailId: undefined }),
              })
            }
          />
        ) : null}
      </div>

      <div className={home.pills} role="tablist" aria-label="Watchlist sections">
        {sectionTabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={search.tab === tab.id}
            className={`${home.pill}${search.tab === tab.id ? ` ${home.pillOn}` : ''}`}
            onClick={() => setTab(tab.id)}
          >
            {tab.label}
            {tab.count !== undefined ? <span className={home.pillCount}>{tab.count}</span> : null}
          </button>
        ))}
      </div>

      {/* live while a scan runs, on every section: a scan covers artists and
          labels alike. where things stand belongs with the artists. */}
      {isScanning || (search.tab === 'artists' && count > 0) ? (
        <WatchlistScanDeck
          frame={scanStatus ?? null}
          lastRun={lastRunQuery.data ?? null}
          nextScan={nextScan}
          onOpenHistory={() => window.openWatchlistHistoryModal?.()}
        />
      ) : null}

      {isLabelsTab ? (
        <WatchlistLabelsTab profileId={profileId} />
      ) : isPodcastsTab ? (
        <div className={`watchlist-page-container ${home.legacyScope}`}>
          <WatchlistPodcastsTab profileId={profileId} searchFilter={search.q} />
        </div>
      ) : isAudiobooksTab ? (
        <div className={`watchlist-page-container ${home.legacyScope}`}>
          <WatchlistAudiobooksTab searchFilter={search.q} />
        </div>
      ) : count === 0 ? (
        <WatchlistEmptyState onAddArtist={() => setIsAddArtistOpen(true)} />
      ) : (
        <>
          <WatchlistFoundShelf
            releases={foundReleases}
            onOpenArtist={openArtistByName}
            onShowAll={() => window.openWatchlistHistoryModal?.()}
          />
          <WatchlistRecentReleasesShelf
            releases={recentReleases}
            loading={recentReleasesQuery.isLoading}
            onOpenArtist={openArtistByName}
          />

          <section className={home.sect}>
            <div className={home.sectHead}>
              <div className={home.sectHeadText}>
                <h3 className={home.sectTitle}>Your artists</h3>
                <span className={home.sectCount}>
                  {search.q.trim() || attentionFilter !== 'all'
                    ? `${visibleArtists.length} of ${count}`
                    : count}
                </span>
              </div>
            </div>

            <div className={home.bar2}>
              <label className={home.search}>
                <svg
                  width="18"
                  height="18"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  aria-hidden="true"
                >
                  <circle cx="11" cy="11" r="7" />
                  <path d="M20 20l-3.6-3.6" />
                </svg>
                <input
                  type="search"
                  aria-label="Filter watchlist"
                  placeholder="Find an artist"
                  value={search.q}
                  onChange={(event) =>
                    void navigate({
                      search: (prev) => ({ ...prev, q: event.target.value }),
                      replace: true,
                    })
                  }
                />
              </label>
              <div className={home.chips} role="group" aria-label="Show">
                {showFilters
                  // an empty filter is still offered while it is the one picked
                  .filter(
                    (filter) =>
                      filter.id === 'all' || filter.count > 0 || attentionFilter === filter.id,
                  )
                  .map((filter) => (
                    <button
                      key={filter.id}
                      type="button"
                      aria-pressed={attentionFilter === filter.id}
                      className={`${home.chip}${attentionFilter === filter.id ? ` ${home.chipOn}` : ''}`}
                      onClick={() => setAttentionFilter(filter.id)}
                    >
                      {filter.label}
                      {filter.id !== 'all' ? (
                        <span className={home.chipCount}>{filter.count}</span>
                      ) : null}
                    </button>
                  ))}
              </div>
              <span className={home.grow} />
              <div className={`${home.anchor}${viewOpen ? ` ${home.anchorRaised}` : ''}`}>
                <button
                  type="button"
                  className={home.txt}
                  aria-haspopup="menu"
                  aria-expanded={viewOpen}
                  onClick={() => setViewOpen((v) => !v)}
                >
                  {SORT_LABELS[search.sort]}
                  <svg
                    width="14"
                    height="14"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.4"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    <path d="M6 9l6 6 6-6" />
                  </svg>
                </button>
                {viewOpen ? (
                  <>
                    <button
                      type="button"
                      className={home.menuScrim}
                      aria-label="Close menu"
                      tabIndex={-1}
                      onClick={() => setViewOpen(false)}
                    />
                    <div className={home.menu} role="menu">
                      <div className={home.menuHead}>Sort by</div>
                      {WATCHLIST_SORT_VALUES.map((value) => (
                        <button
                          key={value}
                          type="button"
                          role="menuitemradio"
                          aria-checked={search.sort === value}
                          className={`${home.menuItem}${search.sort === value ? ` ${home.menuItemOn}` : ''}`}
                          onClick={() => {
                            setViewOpen(false);
                            void navigate({ search: (prev) => ({ ...prev, sort: value }) });
                          }}
                        >
                          <span>{SORT_LABELS[value]}</span>
                        </button>
                      ))}
                    </div>
                  </>
                ) : null}
              </div>
              {picking ? (
                <label className={home.txt}>
                  <SelectAllCheckbox
                    checked={selection.allSelected}
                    indeterminate={selection.indeterminate}
                    onChange={toggleSelectAll}
                  />
                  Select all
                </label>
              ) : (
                <button
                  type="button"
                  className={home.txt}
                  aria-label="Select artists"
                  onClick={() => setPickMode(true)}
                >
                  Select
                </button>
              )}
            </div>

            {visibleArtists.length === 0 ? (
              <div className={home.empty}>
                <p className={home.emptyText}>
                  {search.q.trim()
                    ? `No artist matches “${search.q.trim()}”.`
                    : 'Nobody here right now.'}
                </p>
              </div>
            ) : (
              <div className={`wl-roster ${home.grid}${picking ? ` ${home.picking}` : ''}`}>
                {visibleArtists.map((artist) => {
                  const artistId = primaryArtistId(artist);
                  return (
                    <WatchlistArtistCard
                      key={artist.id}
                      artist={artist}
                      showAdded={search.sort === 'added-newest'}
                      found={foundByArtist.get(artist.artist_name.toLowerCase())}
                      selected={artistId !== null && selectedIds.has(artistId)}
                      active={artistId !== null && artistId === panelArtistId}
                      audiobook={audiobookArtists.has(artist.artist_name)}
                      onToggleSelect={() => artistId && toggleArtist(artistId)}
                      onOpenConfig={() =>
                        artistId &&
                        void navigate({
                          search: (prev) => ({
                            ...prev,
                            configId: artistId,
                            detailId: undefined,
                          }),
                        })
                      }
                      onOpenDetail={() =>
                        artistId &&
                        void navigate({
                          search: (prev) => ({
                            ...prev,
                            detailId: artistId,
                            configId: undefined,
                          }),
                        })
                      }
                      onRemove={() => {
                        if (artistId) void onRemoveOne(artistId, artist.artist_name);
                      }}
                    />
                  );
                })}
              </div>
            )}
          </section>

          {/* the batch bar only once something is picked (#1909) */}
          {picking ? (
            <div className={home.selbar} role="toolbar" aria-label="Selected artists">
              <b>{`${selection.selectedCount} selected`}</b>
              <button
                type="button"
                className={home.sb}
                onClick={() => toggleSelectAll(!selection.allSelected)}
              >
                {selection.allSelected ? 'Clear visible' : 'Select all visible'}
              </button>
              <button
                type="button"
                className={`${home.sb} ${home.sbDanger}`}
                disabled={batchRemove.isPending || selection.selectedCount === 0}
                onClick={() => void onBatchRemove()}
              >
                Remove Selected
              </button>
              <button
                type="button"
                className={home.sb}
                onClick={() => {
                  setSelectedIds(new Set());
                  setPickMode(false);
                }}
              >
                Done
              </button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

/** A Tools menu entry: its name, and a line saying what it is for. */
function toolLabel(name: string, desc: string) {
  return (
    <span className={home.toolItem}>
      <span className={home.toolName}>{name}</span>
      <span className={home.toolDesc}>{desc}</span>
    </span>
  );
}

/** "next scan in 5h 19m", or '' when nothing is scheduled. */
function nextScanText(seconds: number): string {
  const text = formatCountdown(seconds);
  return text.endsWith('--') ? '' : text.replace(/^Next Auto: /, 'in ');
}

interface WatchlistArtistCardProps {
  artist: WatchlistArtist;
  selected: boolean;
  active: boolean;
  /** Downloads by this artist are filed as audiobooks. */
  audiobook: boolean;
  /** Recently Added is the sort, so the card says when. */
  showAdded: boolean;
  /** the newest release a scan found for this artist, if any */
  found?: string;
  onToggleSelect: () => void;
  onOpenConfig: () => void;
  onOpenDetail: () => void;
  onRemove: () => void;
}

function WatchlistArtistCard({
  artist,
  selected,
  active,
  audiobook,
  showAdded,
  found,
  onToggleSelect,
  onOpenConfig,
  onOpenDetail,
  onRemove,
}: WatchlistArtistCardProps) {
  const pills = artistPills(artist);
  const sources = artistSourceKeys(artist);
  const artistId = primaryArtistId(artist);
  const custom = hasCustomRules(artist);
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPosition, setMenuPosition] = useState<{ top: number; right: number } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const days = daysSince(artist.last_scan_timestamp);
  const scanWarn = days === null || days >= 30;
  const added = artist.date_added
    ? `Added ${new Date(artist.date_added).toLocaleDateString()}`
    : 'Added date unknown';
  const scanText = formatRelativeScanTime(artist.last_scan_timestamp);
  const notes: { key: string; text: string; className: string }[] = [];
  if (showAdded) notes.push({ key: 'added', text: added, className: home.quiet });
  if (found) notes.push({ key: 'found', text: `New: ${found}`, className: home.noteNew });
  if (scanWarn) notes.push({ key: 'scan', text: scanText, className: home.warn });
  if (pills.length === 0) {
    notes.push({ key: 'types', text: 'No release types', className: home.warn });
  }
  if (custom)
    notes.push({ key: 'custom', text: 'Own rules', className: `wl-custom ${home.quiet}` });
  const tooltip = [
    artist.artist_name,
    scanText,
    pills.length > 0 ? pills.map((pill) => pill.label).join(', ') : 'No release types',
    sources.length > 0
      ? `Matched on ${sources.map((key) => WATCHLIST_SOURCE_BADGES[key].label).join(', ')}`
      : 'Not matched on any source',
    added,
  ].join('\n');

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
      className={`${home.artist}${active ? ` ${home.artistActive}` : ''}${
        selected ? ` ${home.artistSelected}` : ''
      }`}
      data-artist-id={artistId ?? ''}
      title={tooltip}
      onClick={onOpenDetail}
    >
      <div className={home.portrait}>
        <ArtistImage url={artist.image_url} name={artist.artist_name} />
      </div>
      {/* The tick and the menu sit on the portrait, and both stop the click
          from also opening the artist. */}
      <label
        className={`${home.pick}${selected ? ` ${home.pickOn}` : ''}`}
        onClick={(event) => event.stopPropagation()}
      >
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggleSelect}
          aria-label={`Select ${artist.artist_name}`}
        />
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="3.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M5 12l5 5 9-10" />
        </svg>
      </label>
      <button
        ref={menuButtonRef}
        type="button"
        className={home.kebab}
        aria-label={`More actions for ${artist.artist_name}`}
        title="More actions"
        aria-expanded={menuOpen}
        onClick={(event) => {
          event.stopPropagation();
          toggleMenu();
        }}
      >
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
          <circle cx="5" cy="12" r="2" />
          <circle cx="12" cy="12" r="2" />
          <circle cx="19" cy="12" r="2" />
        </svg>
      </button>

      <span className={home.artistName}>{artist.artist_name}</span>
      {/* one quiet line, and only when there is something to say. the
          rules and matched sources live in the artist panel and the tooltip. */}
      {notes.length > 0 ? (
        <div className={home.notes}>
          {notes.map((note) => (
            <span key={note.key} className={note.className}>
              {note.text}
            </span>
          ))}
        </div>
      ) : null}
      {audiobook ? <AudiobookRouteBadge /> : null}

      {menuOpen && menuPosition
        ? createPortal(
            <div
              ref={menuRef}
              className={home.floatMenu}
              style={{ top: menuPosition.top, right: menuPosition.right }}
              role="menu"
              aria-label={`Actions for ${artist.artist_name}`}
              onClick={(event) => event.stopPropagation()}
            >
              <button
                type="button"
                role="menuitem"
                className={home.menuItem}
                onClick={() => runMenuAction(onOpenDetail)}
              >
                Open
              </button>
              <button
                type="button"
                role="menuitem"
                className={home.menuItem}
                onClick={() => runMenuAction(onOpenConfig)}
              >
                Edit rules
              </button>
              <button
                type="button"
                role="menuitem"
                className={home.menuItem}
                onClick={() => runMenuAction(onToggleSelect)}
              >
                {selected ? 'Clear selection' : 'Select for batch'}
              </button>
              <div className={home.menuSep} />
              <button
                type="button"
                role="menuitem"
                className={`${home.menuItem} ${home.menuItemDanger}`}
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

const PROVIDER_MARKS: Record<(typeof ADD_PROVIDER_OPTIONS)[number]['value'], string | null> = {
  deezer: 'deezer',
  spotify: 'spotify',
  itunes: 'itunes',
  discogs: null,
  musicbrainz: null,
};

function WatchlistAddArtistSheet({
  profileId,
  onAddArtist,
  adding,
  onClose,
}: {
  profileId: number;
  onAddArtist: (artistId: string, artistName: string, source: string) => void;
  adding: boolean;
  onClose: () => void;
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

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

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
    <>
      <button
        type="button"
        className={home.scrim}
        aria-label="Close add artist"
        tabIndex={-1}
        onClick={onClose}
      />
      <div className={home.sheet} role="dialog" aria-modal="true" aria-label="Add an artist">
        <div className={home.sheetHead}>
          <h3 className={home.sheetTitle}>Add an artist</h3>
          <button
            type="button"
            className={`${home.iconBtn} ${home.iconBtnSm}`}
            aria-label="Close"
            onClick={onClose}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.6"
              strokeLinecap="round"
              aria-hidden="true"
            >
              <path d="M6 6l12 12" />
              <path d="M18 6L6 18" />
            </svg>
          </button>
        </div>
        <div className={home.providers} role="radiogroup" aria-label="Provider">
          {ADD_PROVIDER_OPTIONS.map((option) => {
            const on = provider === option.value;
            const mark = PROVIDER_MARKS[option.value];
            return (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={on}
                className={`${home.pill} ${home.pillProvider}${on ? ` ${home.pillOn}` : ''}`}
                onClick={() => setProvider(option.value)}
              >
                {mark ? <SourceIcon source={mark} /> : null}
                {providerLabel(option.value)}
              </button>
            );
          })}
        </div>
        <label className={home.searchBig}>
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            aria-hidden="true"
          >
            <circle cx="11" cy="11" r="7" />
            <path d="M20 20l-3.6-3.6" />
          </svg>
          <input
            type="text"
            autoFocus
            aria-label="Artist to watch"
            value={query}
            placeholder={`Search ${providerLabel(provider)} for an artist`}
            onChange={(event) => setQuery(event.target.value)}
          />
          {query ? (
            <button
              type="button"
              className={`${home.iconBtn} ${home.iconBtnSm}`}
              aria-label="Clear artist search"
              onClick={() => setQuery('')}
            >
              ×
            </button>
          ) : null}
        </label>
        <p className={home.hint} aria-live="polite">
          {searching
            ? `Searching ${providerLabel(provider)}…`
            : debouncedQuery
              ? `${results.length} found`
              : 'Type 2 or more letters.'}
        </p>
        {showResults ? (
          <div className={home.results}>
            {resultsQuery.isError ? (
              <p className={home.hint}>Could not search {providerLabel(provider)}.</p>
            ) : results.length > 0 ? (
              results.slice(0, 8).map((result) => (
                <div key={`${provider}-${result.id}`} className={home.result}>
                  {result.image ? (
                    <img className={home.resultArt} src={result.image} alt="" />
                  ) : (
                    <span className={home.resultArt} aria-hidden="true">
                      {result.name.slice(0, 1).toUpperCase()}
                    </span>
                  )}
                  <div className={home.resultText}>
                    <div className={home.name}>{result.name}</div>
                    <div className={home.quiet}>
                      {providerLabel(provider)}
                      {result.extra ? ` · ${result.extra}` : ''}
                    </div>
                  </div>
                  <button
                    type="button"
                    className={home.add}
                    disabled={adding}
                    onClick={() => onAddArtist(result.id, result.name, provider)}
                  >
                    {adding ? 'Adding' : 'Add'}
                  </button>
                </div>
              ))
            ) : searching ? null : (
              <p className={home.hint}>No artists found for “{debouncedQuery}”.</p>
            )}
          </div>
        ) : null}
      </div>
    </>
  );
}

/**
 * Play a Library v2 album, the way the artist panel and the library page do:
 * every queued row has a file.
 */
async function playLibraryAlbum(
  albumId: number,
  title: string,
  artistName: string,
  fallbackImage: string | null,
) {
  try {
    const tracks = albumQueueRows(await fetchLibraryV2Album(albumId), artistName)
      .filter((row) => row.is_library)
      .map((row) => ({
        ...row,
        playback_status: 'ready',
        image_url: row.image_url || fallbackImage || null,
      }));
    if (!tracks.length) {
      window.showToast?.(`No playable tracks in ${title}`, 'info');
      return;
    }
    await window.playTrackList?.(tracks, title);
  } catch {
    window.showToast?.(`Could not play ${title}`, 'error');
  }
}

/** "Nov 5", or with the year when it is not this year */
function shortDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    ...(sameYear ? {} : { year: 'numeric' }),
  });
}

/**
 * What scans actually added to the wishlist, one cover per release. Read from
 * the saved scan runs, so it is there after a restart too.
 */
function WatchlistFoundShelf({
  releases,
  onOpenArtist,
  onShowAll,
}: {
  releases: WatchlistFoundRelease[];
  onOpenArtist: (artistName: string | null | undefined) => void;
  onShowAll: () => void;
}) {
  if (releases.length === 0) return null;

  return (
    <section className={home.sect} aria-label="Found by your watchlist">
      <div className={home.sectHead}>
        <div className={home.sectHeadText}>
          <h3 className={home.sectTitle}>Found by your watchlist</h3>
          <p className={home.sectSub}>New music your scans added to the wishlist.</p>
        </div>
        <button type="button" className={home.txt} onClick={onShowAll}>
          Show all
        </button>
      </div>
      <div className={home.shelf}>
        {releases.map((release) => {
          const owned = release.library_album_id != null;
          return (
            <div key={`${release.artist_name}-${release.album_name}`} className={home.release}>
              {/* owned: the cover plays it. not yet: it opens the artist. */}
              <button
                type="button"
                className={home.releaseHit}
                aria-label={
                  owned
                    ? `Play ${release.album_name} by ${release.artist_name}`
                    : `Open ${release.artist_name}`
                }
                title={release.tracks.join('\n') || undefined}
                onClick={() =>
                  owned
                    ? void playLibraryAlbum(
                        Number(release.library_album_id),
                        release.album_name,
                        release.artist_name,
                        release.album_image_url,
                      )
                    : onOpenArtist(release.artist_name)
                }
              >
                <div className={home.releaseArt}>
                  <ReleaseCover url={release.album_image_url} />
                  {owned ? (
                    <>
                      <span className={home.releaseOwned}>In library</span>
                      <span className={home.releasePlay} aria-hidden="true">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
                          <path d="M8 5.5v13a1 1 0 0 0 1.5.86l11-6.5a1 1 0 0 0 0-1.72l-11-6.5A1 1 0 0 0 8 5.5z" />
                        </svg>
                      </span>
                    </>
                  ) : null}
                </div>
                <div className={home.name}>{release.album_name}</div>
              </button>
              <button
                type="button"
                className={home.metaLink}
                onClick={() => onOpenArtist(release.artist_name)}
              >
                {release.artist_name}
              </button>
              <div className={home.metaNew}>
                {`${release.track_count} track${release.track_count === 1 ? '' : 's'}`}
                {shortDate(release.found_at) ? ` · ${shortDate(release.found_at)}` : ''}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function WatchlistRecentReleasesShelf({
  releases,
  loading,
  onOpenArtist,
}: {
  releases: WatchlistRecentReleaseRow[];
  loading: boolean;
  onOpenArtist: (artistName: string | null | undefined) => void;
}) {
  if (!loading && releases.length === 0) return null;

  return (
    <section className={home.sect} aria-label="New releases from your artists">
      <div className={home.sectHead}>
        <div className={home.sectHeadText}>
          <h3 className={home.sectTitle}>New releases from your artists</h3>
          <p className={home.sectSub}>
            {loading ? 'Loading…' : 'The latest releases the watchlist has seen.'}
          </p>
        </div>
      </div>
      {releases.length > 0 ? (
        <div className={home.shelf}>
          {releases.map((release, index) => (
            <button
              key={`${release.artist_name ?? ''}-${release.album_name}-${index}`}
              type="button"
              className={home.release}
              title={`Open ${release.artist_name || 'this artist'}`}
              onClick={() => onOpenArtist(release.artist_name)}
            >
              <div className={home.releaseArt}>
                <ReleaseCover url={release.album_cover_url} />
                {release.owned ? <span className={home.releaseOwned}>In library</span> : null}
              </div>
              <div className={home.name}>{release.album_name}</div>
              <div className={home.meta}>
                {release.artist_name || 'Unknown artist'}
                {release.release_date ? ` · ${release.release_date}` : ''}
              </div>
            </button>
          ))}
        </div>
      ) : null}
    </section>
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

function MusicGlyph() {
  return (
    <svg
      width="36"
      height="36"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M9 18V5l12-2v13" />
      <circle cx="6" cy="18" r="3" />
      <circle cx="18" cy="16" r="3" />
    </svg>
  );
}

/**
 * The vanilla card retried a failed image once before falling back, because
 * artist art is fetched from provider CDNs that intermittently 503. Keeping
 * that: one retry, then a quiet placeholder.
 */
function ArtistImage({ url, name }: { url: string | null; name: string }) {
  const [attempt, setAttempt] = useState(0);

  if (!url || attempt > 1) {
    return (
      <span className={home.portraitEmpty} aria-hidden="true">
        <svg
          width="44"
          height="44"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          aria-hidden="true"
        >
          <circle cx="12" cy="8" r="4" />
          <path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" />
        </svg>
      </span>
    );
  }

  return (
    <img
      // Remounting on retry is what actually re-requests the image; without a
      // changing key React keeps the failed element and onError never refires.
      key={attempt}
      src={url}
      alt={name}
      loading="lazy"
      onError={() => setAttempt((n) => n + 1)}
    />
  );
}

/** A dead cover falls back to the note, not the browser's broken image. */
function ReleaseCover({ url }: { url: string | null | undefined }) {
  const [failed, setFailed] = useState(false);
  if (!url || failed) {
    return (
      <span className={home.portraitEmpty} aria-hidden="true">
        <MusicGlyph />
      </span>
    );
  }
  return <img src={url} alt="" loading="lazy" onError={() => setFailed(true)} />;
}

function WatchlistEmptyState({ onAddArtist }: { onAddArtist: () => void }) {
  const navigate = useNavigate();

  return (
    <div className={home.empty}>
      <span className={home.emptyIcon} aria-hidden="true">
        <svg
          width="64"
          height="64"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
        >
          <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
          <circle cx="12" cy="12" r="3" />
        </svg>
      </span>
      <h3 className={home.emptyTitle}>Your watchlist is empty</h3>
      <p className={home.emptyText}>
        Watch an artist and SoulSync checks them for new releases on every scan.
      </p>
      <div className={home.emptyActions}>
        <button type="button" className={home.cta} onClick={onAddArtist}>
          Add artist
        </button>
        {/* Search is still a legacy page, so this goes out as an href and lands
            on the splat route, which hands off to the vanilla renderer. */}
        <button
          className={`${home.ghost} ${home.ghostSm}`}
          type="button"
          onClick={() => void navigate({ href: '/search' })}
        >
          Open Search
        </button>
      </div>
    </div>
  );
}
