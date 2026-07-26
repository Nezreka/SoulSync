import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';

import {
  removeWatchlistArtistsBatch,
  WATCHLIST_QUERY_KEY,
  watchlistArtistsQueryOptions,
  watchlistCountQueryOptions,
  watchlistGlobalConfigQueryOptions,
  watchlistLabelsQueryOptions,
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
  WATCHLIST_SOURCE_BADGES,
} from '../-watchlist.helpers';
import { WATCHLIST_SORT_VALUES, type WatchlistArtist } from '../-watchlist.types';
import { Route } from '../route';
import { WatchlistGlobalSettingsModal } from './watchlist-global-settings-modal';
import { WatchlistLabelsTab } from './watchlist-labels-tab';
import styles from './watchlist-page.module.css';

const SORT_LABELS: Record<(typeof WATCHLIST_SORT_VALUES)[number], string> = {
  'name-asc': 'Name A-Z',
  'name-desc': 'Name Z-A',
  'scan-oldest': 'Oldest Scanned',
  'scan-newest': 'Recently Scanned',
  'added-newest': 'Recently Added',
};

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

  // The route loader has already primed all four, so these resolve from cache
  // on first paint. They stay `useQuery` rather than suspense so that a later
  // refetch (after a scan, say) re-renders in place instead of unmounting the
  // page into a fallback.
  const countQuery = useQuery(watchlistCountQueryOptions(profileId));
  const artistsQuery = useQuery(watchlistArtistsQueryOptions(profileId));
  const scanStatusQuery = useQuery(watchlistScanStatusQueryOptions(profileId));
  const globalConfigQuery = useQuery(watchlistGlobalConfigQueryOptions(profileId));

  const artists = useMemo(() => artistsQuery.data ?? [], [artistsQuery.data]);
  const count = countQuery.data?.count ?? artists.length;
  const nextRunInSeconds = countQuery.data?.nextRunInSeconds ?? 0;
  const scanStatus = scanStatusQuery.data;

  const visibleArtists = useMemo(
    () => sortArtists(filterArtists(artists, search.q), search.sort),
    [artists, search.q, search.sort],
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
            <span className="wl-meta-chip wl-meta-chip--accent">
              {formatCountdown(nextRunInSeconds)}
            </span>
          </div>
        </div>
      </div>

      <div className="watchlist-page-actions">
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
      </div>

      {search.settings ? (
        <WatchlistGlobalSettingsModal
          profileId={profileId}
          initialConfig={globalConfigQuery.data ?? null}
          onClose={() => void navigate({ search: (prev) => ({ ...prev, settings: false }) })}
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
            <WatchlistEmptyState />
          ) : (
            <>
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

              <div className="watchlist-artists-grid">
                {visibleArtists.map((artist) => {
                  const artistId = primaryArtistId(artist);
                  return (
                    <WatchlistArtistCard
                      key={artist.id}
                      artist={artist}
                      selected={artistId !== null && selectedIds.has(artistId)}
                      onToggleSelect={() => artistId && toggleArtist(artistId)}
                      onOpenConfig={() =>
                        artistId &&
                        void navigate({ search: (prev) => ({ ...prev, configId: artistId }) })
                      }
                      onOpenDetail={() =>
                        artistId &&
                        void navigate({ search: (prev) => ({ ...prev, detailId: artistId }) })
                      }
                    />
                  );
                })}
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
  onToggleSelect: () => void;
  onOpenConfig: () => void;
  onOpenDetail: () => void;
}

function WatchlistArtistCard({
  artist,
  selected,
  onToggleSelect,
  onOpenConfig,
  onOpenDetail,
}: WatchlistArtistCardProps) {
  const pills = artistPills(artist);
  const sources = artistSourceKeys(artist);
  const artistId = primaryArtistId(artist);

  return (
    <div className="watchlist-artist-card" data-artist-id={artistId ?? ''} onClick={onOpenDetail}>
      {/* The checkbox and the gear sit inside the card, so both stop the click
          from also opening the detail view — the vanilla handler bailed on
          `closest('.watchlist-card-gear')` / `.watchlist-card-checkbox`. */}
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
      <button
        type="button"
        className="watchlist-card-gear"
        title="Artist settings"
        aria-label={`Settings for ${artist.artist_name}`}
        onClick={(event) => {
          event.stopPropagation();
          onOpenConfig();
        }}
      >
        <svg viewBox="0 0 24 24">
          <path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 00.12-.61l-1.92-3.32a.49.49 0 00-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.48.48 0 00-.48-.41h-3.84a.48.48 0 00-.48.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96a.49.49 0 00-.59.22L2.74 8.87a.48.48 0 00.12.61l2.03 1.58c-.05.3-.07.62-.07.94s.02.64.07.94l-2.03 1.58a.49.49 0 00-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.26.41.48.41h3.84c.24 0 .44-.17.48-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6A3.6 3.6 0 1115.6 12 3.6 3.6 0 0112 15.6z" />
        </svg>
      </button>
      <div className="watchlist-card-image">
        <ArtistImage url={artist.image_url} name={artist.artist_name} />
      </div>
      <div className="watchlist-card-info">
        <span className="watchlist-card-name">{artist.artist_name}</span>
        <span className="watchlist-card-meta">
          {formatRelativeScanTime(artist.last_scan_timestamp)}
        </span>
      </div>
      {sources.length > 0 ? (
        <div className="watchlist-card-sources">
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
        <div className="watchlist-card-pills">
          {pills.map((pill) => (
            <span key={pill.label} className={`watchlist-pill watchlist-pill-${pill.kind}`}>
              {pill.label}
            </span>
          ))}
        </div>
      ) : null}
    </div>
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

function WatchlistEmptyState() {
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
