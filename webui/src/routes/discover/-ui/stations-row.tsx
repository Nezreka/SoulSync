/**
 * Recommended Stations — the user's heaviest recent artists.
 *
 * The first version was one clickable card with one meaning: start endless
 * radio. There was no way to see what a station contained, and nothing to hand
 * to download or sync. It also collapsed every fetch failure to an empty array,
 * so a broken backend rendered exactly like an empty library.
 *
 * Each card now carries TWO named controls:
 *
 *   - **View station** opens a finite preview (up to forty library tracks) with
 *     selection, download and sync. It never starts, pauses or requeues audio.
 *   - **Play radio** is the existing non-stop behaviour, unchanged.
 *
 * The card itself is no longer a button, so neither action is reached by
 * accident, and nothing is nested inside anything clickable.
 */

import { useRef, useState } from 'react';

import type { Station } from '../-discover.stations';

import { stationSubtitle } from '../-discover.stations';

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
}: StationsRowProps) {
  // no listening history yet -> no row at all (the page's empty-section rule).
  // a FAILURE is different and keeps the row, so the user can retry.
  if (!loading && !error && stations !== null && stations.length === 0) return null;

  return (
    <div className="discovery-zone-section" id="recommended-stations-section">
      <div className="discover-stations-header">
        <div>
          <div className="discover-stations-title">Recommended Stations</div>
          <div className="discover-stations-sub">
            From the artists you play most. View a station for a finite list you can download or
            sync, or start non-stop radio from your library.
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
  return (
    <div className="discover-station-card">
      <span className="discover-station-badge">RADIO</span>
      <span
        className="discover-station-art"
        style={station.image_url ? { backgroundImage: `url(${station.image_url})` } : undefined}
      >
        {!station.image_url ? '🎵' : ''}
      </span>
      <span className="discover-station-name" title={station.name}>
        {station.name}
      </span>
      <span className="discover-station-with">{stationSubtitle(station)}</span>
      <div className="discover-station-actions">
        <button
          type="button"
          className="btn btn--sm btn--secondary"
          aria-label={`View the ${station.name} station`}
          disabled={pending}
          aria-busy={pending || undefined}
          onClick={() => onView(station)}
        >
          {pending ? 'Opening…' : 'View station'}
        </button>
        <button
          type="button"
          className="btn btn--sm btn--primary"
          aria-label={`Play ${station.name} radio`}
          disabled={starting}
          aria-busy={starting || undefined}
          onClick={() => void startRadio()}
        >
          {starting ? 'Starting...' : '▶ Play radio'}
        </button>
      </div>
      {/* failure reported next to the control that failed, not in a page toast */}
      {radioError || error ? (
        <span className="discover-station-error" role="alert">
          {radioError || error}
        </span>
      ) : null}
    </div>
  );
}
