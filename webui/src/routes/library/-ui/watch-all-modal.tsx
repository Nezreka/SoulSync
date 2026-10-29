import { useEffect, useRef, useState } from 'react';

import { useModalA11y } from '@/components/dialog/use-modal-a11y';

import type { WatchAllArtist, WatchAllResult } from '../-library.watch-all';

import {
  loadUnwatchedArtists,
  watchAllSourceField,
  watchAllUnwatchedRequest,
} from '../-library.watch-all';

/**
 * Monitor All Unmonitored (openWatchAllUnwatchedModal, library.js:15): loads
 * every unmonitored artist (paginated with a live count), splits
 * ready-to-monitor from no-provider-id, then a confirmed action adds them all.
 * Closing after a successful add announces `ss:library-changed` so the React
 * list refreshes.
 *
 * "Monitor" is the LIBRARY's word for it. The feature, the API
 * (`/api/library/watchlist-all-unwatched`), the tables and every identifier
 * below stay `watchlist`/`watch` -- renaming those would be a migration, and
 * the Watchlist page still calls itself that. Only what the user reads on this
 * page changes.
 *
 * The confirm is deliberately TWO clicks. It is a single irreversible action
 * over the entire library -- potentially thousands of artists, each of which
 * then starts fetching discographies -- sitting next to Automatic Search in
 * the header, so a mis-click has to be impossible rather than merely unlikely.
 * The first click arms; the second fires; anything else disarms.
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
  const [armed, setArmed] = useState(false);
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

  // Escape must DISARM before it closes: arming is the safety step, so the
  // reflex "press Escape to back out" has to undo it rather than skip past it.
  const a11yRef = useModalA11y<HTMLDivElement>(() => {
    if (armed && !result) setArmed(false);
    else close();
  });

  const confirm = async () => {
    if (!data || adding) return;
    if (!armed) {
      setArmed(true);
      return;
    }
    setAdding(true);
    try {
      setResult(await watchAllUnwatchedRequest());
    } catch (error) {
      console.error('Error in watch all:', error);
      window.showToast?.('Failed to start monitoring these artists', 'error');
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
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        ref={a11yRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="Monitor all unmonitored artists"
        className="watch-all-modal"
      >
        <div className="watch-all-header">
          <div className="watch-all-header-content">
            <div className="watch-all-header-icon">👁</div>
            <div>
              <h2 className="watch-all-title">Monitor All Unmonitored</h2>
              <p className="watch-all-subtitle">
                Start monitoring every unmonitored artist that has a {sourceName} ID
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
                Now monitoring {result.added} artist{result.added !== 1 ? 's' : ''}
              </div>
              {result.skipped_already > 0 ? (
                <div className="watch-all-results-detail">
                  {result.skipped_already} already monitored
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
              <div>No unmonitored artists found</div>
            </div>
          ) : (
            <>
              <div className="watch-all-stats">
                <div className="watch-all-stat-card eligible">
                  <div className="watch-all-stat-value">{data.eligible.length}</div>
                  <div className="watch-all-stat-label">Ready to monitor</div>
                </div>
                <div className="watch-all-stat-card ineligible">
                  <div className="watch-all-stat-value">{data.ineligible.length}</div>
                  <div className="watch-all-stat-label">No {sourceName} ID</div>
                </div>
                <div className="watch-all-stat-card total">
                  <div className="watch-all-stat-value">
                    {data.eligible.length + data.ineligible.length}
                  </div>
                  <div className="watch-all-stat-label">Total unmonitored</div>
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
                  <div className="watch-all-section-label">Artists to be monitored</div>
                  <div className="watch-all-grid" id="watch-all-eligible-grid">
                    {visibleEligible.map((artist, index) => (
                      <WatchAllCell artist={artist} key={index} />
                    ))}
                  </div>
                </>
              ) : (
                <div className="watch-all-empty-state">
                  <div className="watch-all-empty-icon">🔌</div>
                  <div>None of your unmonitored artists have a {sourceName} ID yet</div>
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
          {armed && !result ? (
            <span className="watch-all-confirm-hint" role="status">
              This monitors {data?.eligible.length ?? 0} artists at once. Click again to confirm.
            </span>
          ) : null}
          <button
            className="watch-all-btn watch-all-btn-cancel"
            type="button"
            onClick={() => (armed && !result ? setArmed(false) : close())}
          >
            {result ? 'Close' : armed ? 'Back' : 'Cancel'}
          </button>
          {!result ? (
            <button
              className={`watch-all-btn watch-all-btn-primary${armed ? ' armed' : ''}`}
              id="watch-all-confirm-btn"
              type="button"
              disabled={!data || data.eligible.length === 0 || adding}
              onClick={() => void confirm()}
            >
              {adding
                ? 'Adding...'
                : !data || data.eligible.length === 0
                  ? 'Monitor All'
                  : armed
                    ? `Yes, monitor ${data.eligible.length}`
                    : `Monitor All (${data.eligible.length})`}
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
