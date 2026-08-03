import { useEffect, useRef, useState } from 'react';

import type { WatchAllArtist, WatchAllResult } from '../-library.watch-all';

import {
  loadUnwatchedArtists,
  watchAllSourceField,
  watchAllUnwatchedRequest,
} from '../-library.watch-all';

/**
 * Watch All Unwatched (openWatchAllUnwatchedModal, library.js:15): loads every
 * unwatched artist (paginated with a live count), splits ready-to-watch from
 * no-provider-id, then one confirm adds them all. Closing after a successful
 * add announces `ss:library-changed` so the React list refreshes.
 */
export function WatchAllModal({ onClose }: { onClose: () => void }) {
  const sourceName = window.currentMusicSourceName || 'Spotify';
  const [loadedCount, setLoadedCount] = useState<number | null>(null);
  const [data, setData] = useState<{
    eligible: WatchAllArtist[];
    ineligible: WatchAllArtist[];
  } | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [filter, setFilter] = useState('');
  const [ineligibleOpen, setIneligibleOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const [result, setResult] = useState<WatchAllResult | null>(null);
  const [retrySeq, setRetrySeq] = useState(0);
  const openRef = useRef(true);

  useEffect(() => {
    openRef.current = true;
    setLoadFailed(false);
    setData(null);
    loadUnwatchedArtists(watchAllSourceField(sourceName), setLoadedCount, () => openRef.current)
      .then((loaded) => {
        if (openRef.current) setData(loaded);
      })
      .catch((error: unknown) => {
        console.error('Error loading unwatched artists:', error);
        if (openRef.current) setLoadFailed(true);
      });
    return () => {
      openRef.current = false;
    };
    // Re-runs only on an explicit retry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [retrySeq]);

  const close = () => {
    // The list is React now — announcing the change is the whole refresh path.
    if (result) window.dispatchEvent(new CustomEvent('ss:library-changed'));
    onClose();
  };

  const confirm = async () => {
    if (!data || adding) return;
    setAdding(true);
    try {
      setResult(await watchAllUnwatchedRequest());
    } catch (error) {
      console.error('Error in watch all:', error);
      window.showToast?.('Failed to add artists to watchlist', 'error');
      setAdding(false);
    }
  };

  const query = filter.toLowerCase().trim();
  const visibleEligible = (data?.eligible ?? []).filter(
    (a) => !query || a.name.toLowerCase().includes(query),
  );

  return (
    <div
      id="watch-all-modal-overlay"
      className="modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div className="watch-all-modal">
        <div className="watch-all-header">
          <div className="watch-all-header-content">
            <div className="watch-all-header-icon">👁</div>
            <div>
              <h2 className="watch-all-title">Watch All Unwatched</h2>
              <p className="watch-all-subtitle">
                Add unwatched artists with {sourceName} IDs to your watchlist
              </p>
            </div>
          </div>
          <button className="watch-all-close" type="button" onClick={close}>
            ×
          </button>
        </div>
        <div className="watch-all-body">
          {result ? (
            <div className="watch-all-results">
              <div className="watch-all-results-icon">✓</div>
              <div className="watch-all-results-title">
                Added {result.added} artist{result.added !== 1 ? 's' : ''} to watchlist
              </div>
              {result.skipped_already > 0 ? (
                <div className="watch-all-results-detail">
                  {result.skipped_already} already watched
                </div>
              ) : null}
              {result.skipped_no_id > 0 ? (
                <div className="watch-all-results-detail">
                  {result.skipped_no_id} skipped (no external ID)
                </div>
              ) : null}
            </div>
          ) : loadFailed ? (
            <div className="watch-all-empty-state">
              <div className="watch-all-empty-icon">⚠</div>
              <div>Failed to load artists</div>
              <a
                href="#"
                className="watch-all-retry-link"
                onClick={(e) => {
                  e.preventDefault();
                  setRetrySeq((n) => n + 1);
                }}
              >
                Retry
              </a>
            </div>
          ) : !data ? (
            <div className="watch-all-loading-state">
              <div className="watch-all-loading-spinner" />
              <div className="watch-all-loading-text">Loading unwatched artists...</div>
              <div className="watch-all-loading-count" id="watch-all-load-count">
                {loadedCount != null ? `${loadedCount} artists loaded...` : ''}
              </div>
            </div>
          ) : data.eligible.length === 0 && data.ineligible.length === 0 ? (
            <div className="watch-all-empty-state">
              <div className="watch-all-empty-icon">🎵</div>
              <div>No unwatched artists found</div>
            </div>
          ) : (
            <>
              <div className="watch-all-stats">
                <div className="watch-all-stat-card eligible">
                  <div className="watch-all-stat-value">{data.eligible.length}</div>
                  <div className="watch-all-stat-label">Ready to watch</div>
                </div>
                <div className="watch-all-stat-card ineligible">
                  <div className="watch-all-stat-value">{data.ineligible.length}</div>
                  <div className="watch-all-stat-label">No {sourceName} ID</div>
                </div>
                <div className="watch-all-stat-card total">
                  <div className="watch-all-stat-value">
                    {data.eligible.length + data.ineligible.length}
                  </div>
                  <div className="watch-all-stat-label">Total unwatched</div>
                </div>
              </div>

              {data.eligible.length > 10 ? (
                <div className="watch-all-search-wrap">
                  <input
                    type="text"
                    className="watch-all-search"
                    id="watch-all-search"
                    placeholder="Filter artists…"
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                  />
                </div>
              ) : null}

              {data.eligible.length > 0 ? (
                <>
                  <div className="watch-all-section-label">Artists to be watched</div>
                  <div className="watch-all-grid" id="watch-all-eligible-grid">
                    {visibleEligible.map((artist, index) => (
                      <WatchAllCell artist={artist} key={index} />
                    ))}
                  </div>
                </>
              ) : (
                <div className="watch-all-empty-state">
                  <div className="watch-all-empty-icon">🔌</div>
                  <div>None of your unwatched artists have a {sourceName} ID yet</div>
                  <div className="watch-all-empty-hint">
                    The background enrichment worker will match them over time.
                  </div>
                </div>
              )}

              {data.ineligible.length > 0 ? (
                <div className={`watch-all-ineligible${ineligibleOpen ? ' expanded' : ''}`}>
                  <div
                    className="watch-all-ineligible-header"
                    onClick={() => setIneligibleOpen((open) => !open)}
                  >
                    <div className="watch-all-ineligible-label">
                      <span className="watch-all-ineligible-icon">⚠</span>
                      <span>
                        {data.ineligible.length} artist
                        {data.ineligible.length !== 1 ? 's' : ''} without {sourceName} ID
                      </span>
                    </div>
                    <span className="watch-all-chevron">▼</span>
                  </div>
                  <div className="watch-all-ineligible-body">
                    <div className="watch-all-ineligible-hint">
                      These artists haven't been matched to {sourceName} yet. The background
                      enrichment worker will match them over time.
                    </div>
                    <div className="watch-all-grid" id="watch-all-ineligible-grid">
                      {data.ineligible.map((artist, index) => (
                        <WatchAllCell artist={artist} dimmed key={index} />
                      ))}
                    </div>
                  </div>
                </div>
              ) : null}
            </>
          )}
        </div>
        <div className="watch-all-footer">
          <button className="watch-all-btn watch-all-btn-cancel" type="button" onClick={close}>
            {result ? 'Close' : 'Cancel'}
          </button>
          {!result ? (
            <button
              className="watch-all-btn watch-all-btn-primary"
              id="watch-all-confirm-btn"
              type="button"
              disabled={!data || data.eligible.length === 0 || adding}
              onClick={() => void confirm()}
            >
              {adding
                ? 'Adding...'
                : data && data.eligible.length > 0
                  ? `Watch All (${data.eligible.length})`
                  : 'Watch All'}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function WatchAllCell({ artist, dimmed = false }: { artist: WatchAllArtist; dimmed?: boolean }) {
  const [imageBroken, setImageBroken] = useState(false);
  return (
    <div
      className={`watch-all-cell${dimmed ? ' dimmed' : ''}`}
      data-name={artist.name.toLowerCase()}
    >
      <div className="watch-all-cell-img">
        {artist.image_url && !imageBroken ? (
          <img src={artist.image_url} alt="" loading="lazy" onError={() => setImageBroken(true)} />
        ) : (
          <div className="watch-all-cell-placeholder">🎵</div>
        )}
      </div>
      <div className="watch-all-cell-name" title={artist.name}>
        {artist.name}
      </div>
      <div className="watch-all-cell-meta">{artist.track_count || 0} tracks</div>
    </div>
  );
}
