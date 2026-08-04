/**
 * P7 pure core — the Library Status card's five-state machine
 * (wishlist-tools.js updateLibraryStatusCard), the two relative-time
 * formatters (_formatTimeAgo api-monitor.js:1342, _relativeTime
 * pages-extra.js:1117), and the Recent Syncs card view
 * (loadDashboardSyncHistory's per-entry math). Transcribed 1:1.
 */

import type { ServiceStatusPayload, SyncHistoryEntry } from './-dash.api';

// ── _formatTimeAgo (api-monitor.js:1342) ─────────────────────────────────────

export function formatTimeAgo(date: Date, now: Date): string {
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 1) return 'just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays === 1) return 'yesterday';
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}

// ── _relativeTime (pages-extra.js:1117) — the sync cards' variant ────────────

export function relativeTime(dateStr: string, nowMs: number): string {
  try {
    const d = new Date(dateStr);
    const diffMs = nowMs - d.getTime();
    const mins = Math.floor(diffMs / 60000);
    if (isNaN(mins)) return d.toLocaleDateString(); // NaN falls through every < in the vanilla too
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days < 7) return `${days}d ago`;
    return d.toLocaleDateString();
  } catch {
    return '';
  }
}

// ── The Library Status card (wishlist-tools.js:7170) ─────────────────────────

export interface DbStats {
  artists?: number;
  albums?: number;
  tracks?: number;
  database_size_mb?: number;
  last_update?: string | null;
  server_source?: string | null;
  [key: string]: unknown;
}

function capitalize(s: string | null | undefined): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : '';
}

/** The vanilla's DB-size format: sub-MB flips to KB. */
export function formatDbSize(sizeMb: number): string {
  return sizeMb < 1 ? `${Math.round(sizeMb * 1024)} KB` : `${sizeMb.toFixed(1)} MB`;
}

export type LibraryMessageKind = 'no-server' | 'disconnected' | 'empty';

export interface LibraryCardView {
  /** The card's full class string after the reset-then-add. */
  cardClass: string;
  title: string;
  subtitle: string;
  scanVisible: boolean;
  scanScanning: boolean;
  scanLabel: string;
  deepVisible: boolean;
  statsVisible: boolean;
  stats: { artists: string; albums: string; tracks: string; size: string } | null;
  progressVisible: boolean;
  /** Which message block shows, or null; `serverName` fills the disconnected
   *  copy. The message MARKUP (links, strong) is the UI's. */
  message: { kind: LibraryMessageKind; serverName: string } | null;
}

export function libraryCardView(
  dbStats: DbStats | null,
  statusPayload: ServiceStatusPayload | null,
  isScanning: boolean,
  now: Date,
): LibraryCardView {
  const artists = dbStats ? dbStats.artists || 0 : 0;
  const albums = dbStats ? dbStats.albums || 0 : 0;
  const tracks = dbStats ? dbStats.tracks || 0 : 0;
  const sizeMb = dbStats ? dbStats.database_size_mb || 0 : 0;
  const lastUpdate = dbStats ? dbStats.last_update : null;

  const serverConnected = !!(
    statusPayload &&
    statusPayload.media_server &&
    statusPayload.media_server.connected
  );
  const serverType = (statusPayload && statusPayload.active_media_server) || null;
  const hasData = tracks > 0;
  const hasServer = !!serverType && serverType !== 'none';

  if (isScanning) {
    return {
      cardClass: 'library-status-card scanning',
      title: 'Library Scan',
      subtitle: 'Updating library database...',
      scanVisible: true,
      scanScanning: true,
      scanLabel: 'Stop',
      deepVisible: false,
      statsVisible: hasData,
      stats: hasData
        ? {
            artists: artists.toLocaleString(),
            albums: albums.toLocaleString(),
            tracks: tracks.toLocaleString(),
            size: formatDbSize(sizeMb),
          }
        : null,
      progressVisible: true,
      message: null,
    };
  }

  if (!hasServer) {
    return {
      cardClass: 'library-status-card needs-setup',
      title: 'No Media Server',
      subtitle: 'Connect a server to get started',
      scanVisible: false,
      scanScanning: false,
      scanLabel: 'Refresh',
      deepVisible: false,
      statsVisible: false,
      stats: null,
      progressVisible: false,
      message: { kind: 'no-server', serverName: '' },
    };
  }

  if (!serverConnected) {
    const serverName = capitalize(serverType);
    return {
      cardClass: 'library-status-card needs-setup',
      title: `${serverName} — Disconnected`,
      subtitle: 'Cannot reach your media server',
      scanVisible: false,
      scanScanning: false,
      scanLabel: 'Refresh',
      deepVisible: false,
      statsVisible: false,
      stats: null,
      progressVisible: false,
      message: { kind: 'disconnected', serverName },
    };
  }

  if (!hasData) {
    const serverName = capitalize(serverType);
    return {
      cardClass: 'library-status-card empty-library',
      title: `${serverName} Connected`,
      subtitle: 'Library database is empty',
      scanVisible: true,
      scanScanning: false,
      scanLabel: 'Scan Now',
      deepVisible: false,
      statsVisible: false,
      stats: null,
      progressVisible: false,
      message: { kind: 'empty', serverName },
    };
  }

  const serverName = capitalize(serverType);
  let lastRefreshText = 'Never';
  if (lastUpdate) {
    const d = new Date(lastUpdate);
    if (!isNaN(d.getTime())) lastRefreshText = formatTimeAgo(d, now);
  }
  return {
    cardClass: 'library-status-card has-data',
    title: `${serverName} Library`,
    subtitle: `Last refreshed ${lastRefreshText}`,
    scanVisible: true,
    scanScanning: false,
    scanLabel: 'Refresh',
    deepVisible: true,
    statsVisible: true,
    stats: {
      artists: artists.toLocaleString(),
      albums: albums.toLocaleString(),
      tracks: tracks.toLocaleString(),
      size: formatDbSize(sizeMb),
    },
    progressVisible: false,
    message: null,
  };
}

// ── The Recent Syncs card (pages-extra.js loadDashboardSyncHistory) ─────────

export const SYNC_SOURCE_LABELS: Record<string, string> = {
  spotify: 'Spotify',
  tidal: 'Tidal',
  deezer: 'Deezer',
  youtube: 'YouTube',
  beatport: 'Beatport',
  wishlist: 'Wishlist',
};

export interface SyncCardView {
  id: number | string | undefined;
  healthClass: 'health-good' | 'health-warn' | 'health-bad';
  sourceLabel: string;
  timeStr: string;
  name: string;
  pct: number;
  counts: string;
  thumbUrl: string | null;
}

export function syncCardView(entry: SyncHistoryEntry, nowMs: number): SyncCardView {
  const found = (entry.tracks_found as number) || 0;
  const total = (entry.total_tracks as number) || 0;
  const downloaded = (entry.tracks_downloaded as number) || 0;
  const failed = (entry.tracks_failed as number) || 0;
  const pct = total > 0 ? Math.round((found / total) * 100) : 0;

  let healthClass: SyncCardView['healthClass'] = 'health-good';
  if (pct < 50) healthClass = 'health-bad';
  else if (pct < 80) healthClass = 'health-warn';

  const source = entry.source as string | undefined;
  const sourceLabel = (source && SYNC_SOURCE_LABELS[source]) || source || 'Unknown';

  const startedAt = entry.started_at as string | undefined;
  const timeStr = startedAt ? relativeTime(startedAt, nowMs) : '';

  const artistName = entry.artist_name as string | undefined;
  const name = artistName
    ? `${artistName} — ${(entry.album_name as string) || entry.playlist_name}`
    : entry.playlist_name || 'Unknown';

  const counts =
    `${found}/${total} matched` +
    (downloaded > 0 ? ` · ${downloaded} ⬇` : '') +
    (failed > 0 ? ` · ${failed} ✗` : '');

  return {
    id: entry.id,
    healthClass,
    sourceLabel,
    timeStr,
    name: String(name),
    pct,
    counts,
    thumbUrl: (entry.thumb_url as string) || null,
  };
}
