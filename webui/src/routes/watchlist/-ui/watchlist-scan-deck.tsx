import { useState } from 'react';

import {
  prettyScanPhase,
  scanAlbumLine,
  scanCompletionMessage,
  scanProgressPercent,
  scanProgressText,
  type WatchlistScanFrame,
} from '../-watchlist.scan';

/**
 * The live "scan deck" and the completion summary.
 *
 * Both are the same status region the vanilla page swapped between: the deck
 * while status is 'scanning', the summary afterwards.
 */
export function WatchlistScanDeck({ frame }: { frame: WatchlistScanFrame }) {
  if (frame.status === 'scanning') return <ScanningDeck frame={frame} />;
  if (frame.summary && Object.keys(frame.summary).length > 0) {
    return <ScanCompletion frame={frame} />;
  }
  return null;
}

function ScanningDeck({ frame }: { frame: WatchlistScanFrame }) {
  const additions = frame.recent_wishlist_additions ?? [];
  const progressText = scanProgressText(frame);

  return (
    <div className="watchlist-page-scan-status">
      <div className="wl-scan-deck" style={{ display: 'block' }}>
        <div className="wl-scan-deck-head">
          <span className="wl-scan-live-dot" />
          <span className="wl-scan-live-label">Scanning</span>
          <span className="wl-scan-progress-text">{progressText}</span>
          <div className="wl-scan-counters">
            <div className="wl-scan-counter">
              <span className="wl-scan-counter-num">{frame.tracks_found_this_scan || 0}</span>
              <span className="wl-scan-counter-label">found</span>
            </div>
            <div className="wl-scan-counter added">
              <span className="wl-scan-counter-num">{frame.tracks_added_this_scan || 0}</span>
              <span className="wl-scan-counter-label">added</span>
            </div>
          </div>
        </div>

        <div className="wl-scan-progress">
          <div
            className="wl-scan-progress-bar"
            style={{ width: `${scanProgressPercent(frame)}%` }}
          />
        </div>

        <div className="wl-scan-deck-body">
          <div className="wl-scan-hero">
            <div className="wl-scan-portrait">
              {/* Hidden rather than left stale when THIS artist has no photo,
                  so the previous artist's portrait does not linger. */}
              {frame.current_artist_image_url ? (
                <img className="wl-scan-portrait-img" src={frame.current_artist_image_url} alt="" />
              ) : null}
              <div className="wl-scan-album-thumb">
                {frame.current_album_image_url ? (
                  <img src={frame.current_album_image_url} alt="" />
                ) : null}
              </div>
            </div>
            <div className="wl-scan-hero-text">
              <div className="wl-scan-artist-name">{frame.current_artist_name || 'Starting…'}</div>
              <div className="wl-scan-phase">{prettyScanPhase(frame.current_phase)}</div>
              <div className="wl-scan-now">
                <div className="wl-scan-album-name">{scanAlbumLine(frame)}</div>
                <div className="wl-scan-track-name">{frame.current_track_name || '—'}</div>
              </div>
            </div>
          </div>

          <div className="wl-scan-feed">
            <div className="wl-scan-feed-label">Added to wishlist this run</div>
            <div className="wl-scan-feed-list">
              {additions.length > 0 ? (
                additions.map((item, index) => (
                  <div
                    key={`${item.track_name ?? ''}-${index}`}
                    className="watchlist-live-addition-item"
                  >
                    {item.album_image_url ? <img src={item.album_image_url} alt="" /> : null}
                    <div className="watchlist-live-addition-item-info">
                      <div className="watchlist-live-addition-item-track">
                        {item.track_name || ''}
                      </div>
                      <div className="watchlist-live-addition-item-artist">
                        {item.artist_name || ''}
                      </div>
                    </div>
                  </div>
                ))
              ) : (
                <div className="watchlist-live-addition-empty">No tracks added yet…</div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ScanCompletion({ frame }: { frame: WatchlistScanFrame }) {
  const summary = frame.summary ?? {};
  const events = frame.scan_track_events ?? [];
  const cancelled = frame.status === 'cancelled';

  return (
    <div className="watchlist-page-scan-status">
      <div className="watchlist-scan-completion">
        <div className="watchlist-scan-completion-message">
          {cancelled
            ? `Scan cancelled after ${summary.total_artists || 0} artist${
                (summary.total_artists || 0) !== 1 ? 's' : ''
              }`
            : scanCompletionMessage(summary)}
        </div>
        <div style={{ fontSize: 13, opacity: 0.8 }}>
          <span className="sync-stat">Artists: {summary.total_artists || 0}</span>
          <span className="sync-separator"> • </span>
          <span className="sync-stat">New tracks: {summary.new_tracks_found || 0}</span>
          <span className="sync-separator"> • </span>
          <span className="sync-stat">
            Added to wishlist: {summary.tracks_added_to_wishlist || 0}
          </span>
        </div>
        {events.length > 0 ? <ScanTrackLedger events={events} /> : null}
      </div>
    </div>
  );
}

/**
 * The per-run track ledger (#831).
 *
 * The summary used to say "New tracks: 19 · Added to wishlist: 10" with no way
 * to see WHICH tracks; this lists them, added first.
 */
type LedgerEvent = NonNullable<WatchlistScanFrame['scan_track_events']>[number];

function ScanTrackLedger({ events }: { events: LedgerEvent[] }) {
  const [open, setOpen] = useState(false);
  const added = events.filter((event) => event.status === 'added');
  const skipped = events.filter((event) => event.status !== 'added');

  return (
    <>
      <button
        type="button"
        className="watchlist-scan-tracks-toggle"
        onClick={() => setOpen((previous) => !previous)}
      >
        {open ? 'Hide tracks' : 'Show tracks'}{' '}
        <span className="watchlist-scan-tracks-caret">▾</span>
      </button>
      <div className="watchlist-scan-tracks" style={{ display: open ? '' : 'none' }}>
        <LedgerSection label="Added to wishlist" list={added} />
        <LedgerSection label="Found but skipped — already queued or blocklisted" list={skipped} />
      </div>
    </>
  );
}

function LedgerSection({ label, list }: { label: string; list: LedgerEvent[] }) {
  if (list.length === 0) return null;
  return (
    <>
      <div className="watchlist-scan-tracks-section">
        {label} ({list.length})
      </div>
      {list.map((event, index) => (
        <div key={`${event.track_name ?? ''}-${index}`} className="watchlist-live-addition-item">
          {event.album_image_url ? <img src={event.album_image_url} alt="" /> : null}
          <div className="watchlist-live-addition-item-info">
            <div className="watchlist-live-addition-item-track">{event.track_name || ''}</div>
            <div className="watchlist-live-addition-item-artist">
              {event.artist_name || ''}
              {event.album_name ? ` — ${event.album_name}` : ''}
            </div>
          </div>
          <span
            className={`watchlist-scan-track-badge ${event.status === 'added' ? 'added' : 'skipped'}`}
          >
            {event.status === 'added' ? 'added' : 'skipped'}
          </span>
        </div>
      ))}
    </>
  );
}
