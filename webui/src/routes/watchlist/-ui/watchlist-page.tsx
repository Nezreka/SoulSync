import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useMemo, useState } from 'react';

import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';

import {
  watchlistArtistsQueryOptions,
  watchlistCountQueryOptions,
  watchlistGlobalConfigQueryOptions,
  watchlistScanStatusQueryOptions,
} from '../-watchlist.api';
import {
  artistPills,
  artistSourceKeys,
  filterArtists,
  formatArtistCount,
  formatCountdown,
  formatRelativeScanTime,
  formatTimeAgo,
  primaryArtistId,
  sortArtists,
  WATCHLIST_SOURCE_BADGES,
} from '../-watchlist.helpers';
import { WATCHLIST_SORT_VALUES, type WatchlistArtist } from '../-watchlist.types';
import { Route } from '../route';
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
            <span className="wl-meta-chip">{formatArtistCount(count)}</span>
            <span className="wl-meta-chip wl-meta-chip--accent">
              {formatCountdown(nextRunInSeconds)}
            </span>
          </div>
        </div>
      </div>

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

      {isLabelsTab ? null : (
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

              <div className="watchlist-artists-grid">
                {visibleArtists.map((artist) => (
                  <WatchlistArtistCard key={artist.id} artist={artist} />
                ))}
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}

function WatchlistArtistCard({ artist }: { artist: WatchlistArtist }) {
  const pills = artistPills(artist);
  const sources = artistSourceKeys(artist);
  const artistId = primaryArtistId(artist);

  return (
    <div className="watchlist-artist-card" data-artist-id={artistId ?? ''}>
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
