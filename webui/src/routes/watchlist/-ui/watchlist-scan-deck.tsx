import { useState } from 'react';

import type { WatchlistScanRun } from '../-watchlist.types';

import { formatTimeAgo } from '../-watchlist.helpers';
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
  lastRun,
  nextScan,
  onOpenHistory,
}: {
  /** the live or polled scan frame, when there is one */
  frame: WatchlistScanFrame | null;
  /** the newest saved run, so the card still knows after a restart */
  lastRun: WatchlistScanRun | null;
  /** "in 9h 28m", or '' when no auto scan is due */
  nextScan: string;
  onOpenHistory: () => void;
}) {
  if (frame?.status === 'scanning') return <ScanningDeck frame={frame} />;
  return (
    <ScanStatusCard
      frame={frame}
      lastRun={lastRun}
      nextScan={nextScan}
      onOpenHistory={onOpenHistory}
    />
  );
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

interface ScanFacts {
  cancelled: boolean;
  total: number;
  scanned: number;
  found: number;
  added: number;
  finishedAt: string | null;
}

/** this session's finished frame wins; the saved run covers a restart */
function scanFacts(frame: WatchlistScanFrame | null, lastRun: WatchlistScanRun | null) {
  const summary = frame?.summary;
  if (frame && summary && Object.keys(summary).length > 0) {
    const total = summary.total_artists || 0;
    return {
      cancelled: frame.status === 'cancelled',
      total,
      scanned: summary.successful_scans ?? total,
      found: summary.new_tracks_found || 0,
      added: summary.tracks_added_to_wishlist || 0,
      finishedAt: frame.completed_at ?? null,
    } satisfies ScanFacts;
  }
  if (lastRun) {
    return {
      cancelled: lastRun.status === 'cancelled',
      total: lastRun.total_artists || 0,
      scanned: lastRun.artists_scanned || 0,
      found: lastRun.tracks_found || 0,
      added: lastRun.tracks_added || 0,
      finishedAt: lastRun.completed_at ?? lastRun.started_at,
    } satisfies ScanFacts;
  }
  return null;
}

function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

/**
 * Where the watchlist stands, in a sentence: up to date, or what the last
 * scan found, and when the next one runs. The ledger of the scan this session
 * watched is a click away; older ones are in Scan history.
 */
function ScanStatusCard({
  frame,
  lastRun,
  nextScan,
  onOpenHistory,
}: {
  frame: WatchlistScanFrame | null;
  lastRun: WatchlistScanRun | null;
  nextScan: string;
  onOpenHistory: () => void;
}) {
  const [open, setOpen] = useState(false);
  const facts = scanFacts(frame, lastRun);
  const events = frame?.scan_track_events ?? [];
  const ago = facts?.finishedAt ? formatTimeAgo(facts.finishedAt) : '';
  const when = ago ? `The last scan, ${ago},` : 'The last scan';

  let tone: 'ok' | 'new' | 'idle' = 'idle';
  let headline = 'No scans yet';
  let line = 'Scan now checks every artist for new releases.';
  if (facts?.cancelled) {
    headline = 'Last scan stopped';
    line = `It was stopped after ${plural(facts.scanned, 'artist')} and added ${facts.added} to the wishlist.`;
  } else if (facts && facts.added > 0) {
    tone = 'new';
    headline = `${plural(facts.added, 'new track')} added`;
    line = `${when} checked ${facts.scanned} of ${plural(facts.total, 'artist')}, found ${plural(facts.found, 'new track')} and added ${facts.added} to the wishlist.`;
  } else if (facts && facts.found > 0) {
    tone = 'ok';
    headline = 'Up to date';
    line = `${when} checked ${facts.scanned} of ${plural(facts.total, 'artist')} and found ${plural(facts.found, 'new track')}, all already queued or blocklisted.`;
  } else if (facts) {
    tone = 'ok';
    headline = 'Up to date';
    line =
      facts.scanned >= facts.total
        ? `${when} checked all ${plural(facts.total, 'artist')} and found nothing new.`
        : `${when} checked ${facts.scanned} of ${plural(facts.total, 'artist')} and found nothing new.`;
  }

  return (
    <section className={home.status} role="status" aria-label="Last watchlist scan">
      <div className={home.statusMain}>
        <span
          className={`${home.statusIcon}${tone === 'idle' ? '' : ` ${home.statusIconOn}`}`}
          aria-hidden="true"
        >
          {tone === 'new' ? (
            <svg
              width="22"
              height="22"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M9 18V5l12-2v13" />
              <circle cx="6" cy="18" r="3" />
              <circle cx="18" cy="16" r="3" />
            </svg>
          ) : tone === 'ok' ? (
            <svg
              width="22"
              height="22"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M5 12.5l5 5 9-10.5" />
            </svg>
          ) : (
            <svg
              width="22"
              height="22"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
            >
              <circle cx="12" cy="12" r="9" />
              <path d="M12 7v5l3 2" />
            </svg>
          )}
        </span>
        <div className={home.statusText}>
          <b>{headline}</b>
          <span>{line}</span>
        </div>
        <div className={home.statusSide}>
          {nextScan ? (
            <div className={home.statusNext}>
              <span>Next scan</span>
              <b>{nextScan}</b>
            </div>
          ) : null}
          {events.length > 0 ? (
            <button
              type="button"
              className={home.statusBtn}
              aria-expanded={open}
              onClick={() => setOpen((previous) => !previous)}
            >
              {open ? 'Hide tracks' : 'Show tracks'}
            </button>
          ) : null}
          <button type="button" className={home.statusBtn} onClick={onOpenHistory}>
            Scan history
          </button>
        </div>
      </div>
      {events.length > 0 ? <ScanTrackLedger events={events} open={open} /> : null}
    </section>
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
      <LedgerSection label="Found but skipped, already queued or blocklisted" list={skipped} />
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
