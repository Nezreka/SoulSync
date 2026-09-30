/**
 * Recommended Stations — the user's heaviest recent artists.
 *
 * The first version was one clickable card with one meaning: start endless
 * radio. There was no way to see what a station contained, and nothing to hand
 * to download or sync. It also collapsed every fetch failure to an empty array,
 * so a broken backend rendered exactly like an empty library.
 *
 * Each card carries TWO named controls:
 *
 *   - **Play radio**, the round play button on the artist's photo. it's the
 *     one thing most people want from a station, so it's the one that looks
 *     like the primary action.
 *   - **View station**, the name and line under it. opens a finite preview
 *     (up to forty library tracks) with selection, download and sync. It
 *     never starts, pauses or requeues audio.
 *
 * sept 29: the cards used to stack two full-width buttons each, plus a RADIO
 * badge on every card of a row called Stations. and Library Radio lived alone
 * at the bottom of the page as a banner; it's a station, so it's the first
 * card here now.
 */

import { useRef, useState } from 'react';

import type { Station } from '../-discover.stations';

import { stationSubtitle } from '../-discover.stations';
import { FeedbackMenu } from './feedback-menu';

export type { Station } from '../-discover.stations';
export { fetchStations, stationSubtitle } from '../-discover.stations';

export interface StationsRowProps {
  stations: Station[] | null;
  /** true while the first fetch is in flight. */
  loading?: boolean;
  /** A failed fetch is a failure, not an empty row. */
  error?: string | null;
  onRetry?: () => void;
  onView: (station: Station) => void;
  onPlayRadio: (station: Station) => void | Promise<void>;
  /** The station whose preview or radio is still resolving. */
  pendingId?: string | null;
  /** Per-card failures, keyed by artist id. */
  cardErrors?: Record<string, string>;
  /** endless radio over the whole library. the first card, when given. */
  onPlayLibraryRadio?: () => void;
}

export function StationsRow({
  stations,
  loading = false,
  error = null,
  onRetry,
  onView,
  onPlayRadio,
  pendingId = null,
  cardErrors = {},
  onPlayLibraryRadio,
}: StationsRowProps) {
  // no listening history yet -> no row at all (the page's empty-section rule).
  // a FAILURE is different and keeps the row, so the user can retry.
  if (!loading && !error && stations !== null && stations.length === 0) return null;

  return (
    <div className="discovery-zone-section" id="recommended-stations-section">
      <div className="discover-stations-header">
        <div>
          <div className="discover-stations-title">Stations</div>
          <div className="discover-stations-sub">
            Non-stop radio from the artists you play most. Open one to see what&apos;s in it.
          </div>
        </div>
      </div>

      {error ? (
        <div className="discover-stations-error" role="alert">
          <span>{error}</span>
          {onRetry ? (
            <button type="button" className="btn btn--sm btn--secondary" onClick={onRetry}>
              Try again
            </button>
          ) : null}
        </div>
      ) : null}

      <div className="discover-stations-row">
        {onPlayLibraryRadio ? <LibraryRadioCard onPlay={onPlayLibraryRadio} /> : null}
        {(stations ?? []).map((station) => (
          <StationCard
            key={String(station.artist_id)}
            station={station}
            pending={pendingId === String(station.artist_id)}
            error={cardErrors[String(station.artist_id)]}
            onView={onView}
            onPlayRadio={onPlayRadio}
          />
        ))}
        {loading && !stations
          ? [1, 2, 3, 4].map((i) => (
              <div key={i} className="discover-station-card discover-station-card--loading" />
            ))
          : null}
      </div>
    </div>
  );
}

function StationCard({
  station,
  pending,
  error,
  onView,
  onPlayRadio,
}: {
  station: Station;
  pending?: boolean;
  error?: string;
  onView: (station: Station) => void;
  onPlayRadio: (station: Station) => void | Promise<void>;
}) {
  const startingRef = useRef(false);
  const [starting, setStarting] = useState(false);
  const [radioError, setRadioError] = useState<string | null>(null);
  const [hidden, setHidden] = useState(false);
  const startRadio = async () => {
    if (startingRef.current) return;
    startingRef.current = true;
    setStarting(true);
    setRadioError(null);
    try {
      await onPlayRadio(station);
    } catch (err) {
      setRadioError(err instanceof Error ? err.message : 'Could not start radio. Try again.');
    } finally {
      startingRef.current = false;
      setStarting(false);
    }
  };
  if (hidden) return null;
  return (
    <div className="discover-station-card">
      <FeedbackMenu
        entity={{ type: 'artist', name: station.name }}
        explanation={station.explanation}
        onHidden={() => setHidden(true)}
        className="discover-station-feedback-btn"
      />
      <div className="discover-station-art-wrap">
        <span
          className="discover-station-art"
          style={station.image_url ? { backgroundImage: `url(${station.image_url})` } : undefined}
        >
          {!station.image_url ? station.name.slice(0, 1) : ''}
        </span>
        <button
          type="button"
          className="discover-station-play"
          aria-label={`Play ${station.name} radio`}
          title={`Play ${station.name} radio`}
          disabled={starting}
          aria-busy={starting || undefined}
          onClick={() => void startRadio()}
        >
          {starting ? (
            <span className="discover-station-spinner" aria-hidden="true" />
          ) : (
            <PlayGlyph />
          )}
        </button>
      </div>
      <button
        type="button"
        className="discover-station-open"
        aria-label={`View the ${station.name} station`}
        disabled={pending}
        aria-busy={pending || undefined}
        onClick={() => onView(station)}
      >
        <span className="discover-station-name" title={station.name}>
          {station.name}
        </span>
        <span className="discover-station-with">
          {pending ? 'Opening…' : stationSubtitle(station)}
        </span>
      </button>
      {/* failure reported next to the control that failed, not in a page toast */}
      {radioError || error ? (
        <span className="discover-station-error" role="alert">
          {radioError || error}
        </span>
      ) : null}
    </div>
  );
}

function PlayGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <path d="M8 5.5v13l11-6.5z" fill="currentColor" />
    </svg>
  );
}

/** the whole library as one station. play only: there's nothing finite to preview. */
function LibraryRadioCard({ onPlay }: { onPlay: () => void }) {
  return (
    <div className="discover-station-card discover-station-card--library">
      <div className="discover-station-art-wrap">
        <span className="discover-station-art discover-station-art--library" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="40" height="40">
            <path
              d="M4 10a8 8 0 0 1 16 0M7 10a5 5 0 0 1 10 0"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
            <circle cx="12" cy="10" r="1.8" fill="currentColor" />
            <path d="M12 12v8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
        </span>
        <button
          type="button"
          className="discover-station-play"
          aria-label="Play library radio"
          title="Play library radio"
          onClick={onPlay}
        >
          <PlayGlyph />
        </button>
      </div>
      <button type="button" className="discover-station-open" onClick={onPlay}>
        <span className="discover-station-name">Library Radio</span>
        <span className="discover-station-with">Your whole collection, weighted by plays</span>
      </button>
    </div>
  );
}
