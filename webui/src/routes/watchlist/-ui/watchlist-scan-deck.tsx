import { useState } from 'react';

import {
  prettyScanPhase,
  scanAlbumLine,
  scanProgressPercent,
  scanProgressText,
  type WatchlistScanFrame,
} from '../-watchlist.scan';
import { hideOnError } from './hide-on-error';
import home from './watchlist-home.module.css';

/**
 * The scan, live and finished.
 *
 * While a scan runs: the deck, with the artist it is on, the album and track,
 * the progress and what it has added so far. Afterwards: one row saying what
 * the last scan did, with its track ledger a click away. The old page showed
 * the finished scan twice, a box and a strip under it with the same numbers.
 */
export function WatchlistScanDeck({
  frame,
  scanAgo,
}: {
  frame: WatchlistScanFrame;
  /** "3h ago", when the backend says when it finished */
  scanAgo?: string;
}) {
  if (frame.status === 'scanning') return <ScanningDeck frame={frame} />;
  if (frame.summary && Object.keys(frame.summary).length > 0) {
    return <ScanCompletion frame={frame} scanAgo={scanAgo} />;
  }
  return null;
}

function ScanningDeck({ frame }: { frame: WatchlistScanFrame }) {
  const additions = frame.recent_wishlist_additions ?? [];
  const progressText = scanProgressText(frame);
  const pct = scanProgressPercent(frame);

  return (
    <div className={home.deck} role="status" aria-label="Watchlist scan">
      <div className={home.deckHead}>
        <span className={home.deckLive}>
          <span className={home.spin} aria-hidden="true" />
          Scanning
        </span>
        <span className={home.deckProgressText}>{progressText}</span>
        <div className={home.deckCounters}>
          <span className={home.deckCounter}>
            <b>{frame.tracks_found_this_scan || 0}</b> found
          </span>
          <span className={`${home.deckCounter} ${home.deckCounterAdded}`}>
            <b>{frame.tracks_added_this_scan || 0}</b> added
          </span>
        </div>
      </div>

      <div className={home.bar} aria-hidden="true">
        <span style={{ width: `${pct}%` }} />
      </div>

      <div className={home.deckBody}>
        <div className={home.deckHero}>
          <div className={home.deckPortrait}>
            {/* Hidden rather than left stale when THIS artist has no photo,
                so the previous artist's portrait does not linger. */}
            {frame.current_artist_image_url ? (
              <img src={frame.current_artist_image_url} alt="" onError={hideOnError} />
            ) : null}
            <div className={home.deckAlbum}>
              {frame.current_album_image_url ? (
                <img src={frame.current_album_image_url} alt="" onError={hideOnError} />
              ) : null}
            </div>
          </div>
          <div className={home.deckHeroText}>
            <div className={home.deckArtist}>{frame.current_artist_name || 'Starting…'}</div>
            <div className={home.deckPhase}>{prettyScanPhase(frame.current_phase)}</div>
            <div className={home.deckNow}>{scanAlbumLine(frame)}</div>
            <div className={home.deckNow}>{frame.current_track_name || '—'}</div>
          </div>
        </div>

        <div>
          <div className={home.feedLabel}>Added to wishlist this run</div>
          <div className={home.feed}>
            {additions.length > 0 ? (
              additions.map((item, index) => (
                <div key={`${item.track_name ?? ''}-${index}`} className={home.feedItem}>
                  {item.album_image_url ? (
                    <img src={item.album_image_url} alt="" onError={hideOnError} />
                  ) : null}
                  <div className={home.feedText}>
                    <div className={home.feedTrack}>{item.track_name || ''}</div>
                    <div className={home.feedArtist}>{item.artist_name || ''}</div>
                  </div>
                </div>
              ))
            ) : (
              <div className={home.feedEmpty}>No tracks added yet…</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function ScanCompletion({ frame, scanAgo }: { frame: WatchlistScanFrame; scanAgo?: string }) {
  const summary = frame.summary ?? {};
  const events = frame.scan_track_events ?? [];
  const cancelled = frame.status === 'cancelled';
  const [open, setOpen] = useState(false);
  const total = summary.total_artists || 0;
  const scanned = summary.successful_scans ?? total;
  const found = summary.new_tracks_found || 0;
  const added = summary.tracks_added_to_wishlist || 0;

  return (
    <div className={home.scanRow} role="status" aria-label="Last watchlist scan">
      <div className={home.scanRowHead}>
        <div className={home.scanRowText}>
          <b>
            {cancelled
              ? `Scan cancelled after ${total} artist${total !== 1 ? 's' : ''}`
              : scanAgo
                ? `Last scan ${scanAgo}`
                : 'Scan finished'}
          </b>
        </div>
        <div className={home.scanStats}>
          {!cancelled ? (
            <span>
              <b>{scanned}</b>of {total} artists checked
            </span>
          ) : null}
          <span>
            <b>{found}</b>new track{found !== 1 ? 's' : ''} found
          </span>
          <span>
            <b>{added}</b>added to wishlist
          </span>
        </div>
        {events.length > 0 ? (
          <button
            type="button"
            className={home.txt}
            aria-expanded={open}
            onClick={() => setOpen((previous) => !previous)}
          >
            {open ? 'Hide tracks' : 'Show tracks'}
          </button>
        ) : null}
      </div>
      {events.length > 0 ? <ScanTrackLedger events={events} open={open} /> : null}
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

function ScanTrackLedger({ events, open }: { events: LedgerEvent[]; open: boolean }) {
  const added = events.filter((event) => event.status === 'added');
  const skipped = events.filter((event) => event.status !== 'added');

  return (
    <div className={home.feed} style={{ display: open ? '' : 'none', maxHeight: 360 }}>
      <LedgerSection label="Added to wishlist" list={added} />
      <LedgerSection label="Found but skipped — already queued or blocklisted" list={skipped} />
    </div>
  );
}

function LedgerSection({ label, list }: { label: string; list: LedgerEvent[] }) {
  if (list.length === 0) return null;
  return (
    <>
      <div className={home.ledgerHead}>
        {label} ({list.length})
      </div>
      {list.map((event, index) => (
        <div key={`${event.track_name ?? ''}-${index}`} className={home.feedItem}>
          {event.album_image_url ? (
            <img src={event.album_image_url} alt="" onError={hideOnError} />
          ) : null}
          <div className={home.feedText}>
            <div className={home.feedTrack}>{event.track_name || ''}</div>
            <div className={home.feedArtist}>
              {event.artist_name || ''}
              {event.album_name ? ` — ${event.album_name}` : ''}
            </div>
          </div>
          <span
            className={`${home.feedBadge}${event.status === 'added' ? ` ${home.feedBadgeAdded}` : ''}`}
          >
            {event.status === 'added' ? 'added' : 'skipped'}
          </span>
        </div>
      ))}
    </>
  );
}
