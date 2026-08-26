/**
 * Recommended Stations — the user's heaviest recent artists as one-click
 * radio. Click a card and the player starts THAT artist's library tracks
 * immediately through the existing startArtistRadioById seam; the radio
 * refill keeps it going by similarity. Spotify's row, minus the internet.
 */

import { useEffect, useState } from 'react';

export interface Station {
  artist_id: string | number;
  name: string;
  image_url: string;
  with: string[];
}

export async function fetchStations(): Promise<Station[]> {
  try {
    const response = await fetch('/api/discover/stations');
    const data = (await response.json()) as { success?: boolean; stations?: Station[] };
    return data?.success && Array.isArray(data.stations) ? data.stations : [];
  } catch {
    return [];
  }
}

export function stationSubtitle(station: Station): string {
  if (!station.with.length) return 'Artist radio from your library';
  return `With ${station.with.join(', ')} and more`;
}

export function StationsRow() {
  const [stations, setStations] = useState<Station[] | null>(null);

  useEffect(() => {
    let live = true;
    void fetchStations().then((rows) => {
      if (live) setStations(rows);
    });
    return () => {
      live = false;
    };
  }, []);

  // no listening history yet -> no row at all (the page's empty-section rule)
  if (stations !== null && stations.length === 0) return null;

  return (
    <div className="discovery-zone-section" id="recommended-stations-section">
      <div className="discover-stations-header">
        <div>
          <div className="discover-stations-title">Recommended Stations</div>
          <div className="discover-stations-sub">
            Non-stop radio from the artists you play most — starts instantly from your library.
          </div>
        </div>
      </div>
      <div className="discover-stations-row">
        {(stations ?? []).map((station) => (
          <button
            key={String(station.artist_id)}
            type="button"
            className="discover-station-card"
            title={`Play ${station.name} radio`}
            onClick={() =>
              void window.startArtistRadioById?.(String(station.artist_id), station.name)
            }
          >
            <span className="discover-station-badge">RADIO</span>
            <span
              className="discover-station-art"
              style={
                station.image_url ? { backgroundImage: `url(${station.image_url})` } : undefined
              }
            >
              {!station.image_url ? '🎵' : ''}
            </span>
            <span className="discover-station-name">{station.name}</span>
            <span className="discover-station-with">{stationSubtitle(station)}</span>
          </button>
        ))}
        {stations === null
          ? [1, 2, 3, 4].map((i) => (
              <div key={i} className="discover-station-card discover-station-card--loading" />
            ))
          : null}
      </div>
    </div>
  );
}
