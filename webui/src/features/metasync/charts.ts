/**
 * MetaSync's SIN charts: the SoulSync network's weekly charts, offered as
 * playlists (core/metasync_charts.py on the server). SoulSync pulls them from
 * MetaSync once a day (the "Refresh MetaSync Charts" automation) and matches
 * every track in the background; a chart only shows here once it is READY.
 *
 * They are offers, nothing more. Listing them never touches a playlist;
 * opening one gives the active profile its own playlist that the server has
 * ALREADY matched in the background, and lands on it in Sync, where the
 * existing "Sync & download" and Auto-Sync buttons are. Nothing is synced,
 * wishlisted or downloaded until somebody presses one of those.
 *
 * Shared by the dashboard rail and the Discover shelf, so the two can never
 * open a chart differently.
 */

export interface SinChart {
  key: string;
  name: string;
  week: string | null;
  image_url: string | null;
  /** up to four covers from the chart, for a collage when there is no image */
  cover_tiles: string[];
  track_count: number;
  /** tracks the background identify has matched so far */
  matched: number;
  /** every track matched: opens ready to sync */
  ready: boolean;
  /** the active profile's own copy, once they opened it */
  playlist_id: number | null;
}

/** The charts that are ready to open: every track already matched. A chart
 *  still matching in the background is not offered yet. */
export async function fetchSinCharts(): Promise<SinChart[]> {
  try {
    const response = await fetch('/api/metasync/charts');
    if (!response.ok) return [];
    const payload = (await response.json()) as { charts?: SinChart[] };
    return (payload.charts ?? []).filter((chart) => chart.ready);
  } catch {
    return [];
  }
}

/** What a chart card says under its name. */
export function sinChartStatus(chart: SinChart): string {
  const tracks = `${chart.track_count} track${chart.track_count === 1 ? '' : 's'}`;
  return chart.playlist_id ? `${tracks} · in your playlists` : tracks;
}

const PENDING_KEY = 'soulsync:open-mirrored';

/**
 * Open a chart: make it the profile's own (already matched) playlist and
 * show it on the Sync page. Returns an error message, or null on success.
 */
export async function openSinChart(chart: SinChart): Promise<string | null> {
  let playlistId: number;
  try {
    const response = await fetch(`/api/metasync/charts/${encodeURIComponent(chart.key)}/open`, {
      method: 'POST',
    });
    const payload = (await response.json().catch(() => ({}))) as {
      playlist_id?: number;
      error?: string;
    };
    if (!response.ok || !payload.playlist_id) return payload.error || 'Could not open that chart';
    playlistId = payload.playlist_id;
  } catch {
    return 'Could not open that chart';
  }
  try {
    window.sessionStorage.setItem(PENDING_KEY, String(playlistId));
  } catch {
    // without storage the Sync page still opens; the playlist is in its list
  }
  // navigateToPage, not the router bridge: it also moves the sidebar highlight
  void window.navigateToPage?.('sync');
  return null;
}

/** The playlist a chart click asked the Sync page to open, read once. */
export function takePendingMirroredOpen(): number | null {
  try {
    const raw = window.sessionStorage.getItem(PENDING_KEY);
    if (raw) window.sessionStorage.removeItem(PENDING_KEY);
    const id = raw ? Number(raw) : NaN;
    return Number.isInteger(id) && id > 0 ? id : null;
  } catch {
    return null;
  }
}
