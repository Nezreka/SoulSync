/**
 * Sync history — the pure half.
 *
 * The vanilla modal (wishlist-tools.js 3907-4310) built its rows by string
 * concatenation and drove its live progress by writing into six ids per row.
 * Everything here is the part that DECIDES: which entries belong on this
 * screen, what the tabs say, which of two very different re-sync paths an entry
 * takes, and what a poll payload means. The rendering and the fetching live
 * elsewhere, so all of this is testable without a DOM.
 *
 * The single most important function is `syncHistoryResyncKind`. Re-syncing a
 * Discover row and re-syncing a Spotify row are not variations on one action —
 * one opens the download modal, the other starts a server sync and polls it.
 * Picking wrong does not fail loudly, it does the wrong thing successfully.
 */

/** One row as `/api/sync/history` returns it. */
export interface SyncHistoryEntry {
  id: number;
  playlist_id?: string | number | null;
  playlist_name?: string | null;
  artist_name?: string | null;
  album_name?: string | null;
  source?: string | null;
  sync_type?: string | null;
  thumb_url?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  total_tracks?: number | null;
  tracks_found?: number | null;
  tracks_downloaded?: number | null;
  tracks_failed?: number | null;
  is_album_download?: boolean | null;
  tracks?: unknown[] | null;
  album_context?: Record<string, unknown> | null;
  artist_context?: Record<string, unknown> | null;
}

export const SYNC_HISTORY_SOURCE_LABELS: Record<string, string> = {
  spotify: 'Spotify',
  beatport: 'Beatport',
  youtube: 'YouTube',
  tidal: 'Tidal',
  deezer: 'Deezer',
  wishlist: 'Wishlist',
  library: 'Library',
  discover: 'Discover',
  listenbrainz: 'ListenBrainz',
  spotify_public: 'Spotify Public',
  mirrored: 'Mirrored',
};

/** Fallback mark for a row whose artwork failed or was never stored. */
export const SYNC_HISTORY_SOURCE_ICONS: Record<string, string> = {
  spotify: '\u{1F3B5}',
  beatport: '\u{1F3B6}',
  youtube: '\u{25B6}',
  tidal: '\u{1F30A}',
  deezer: '\u{1F3A7}',
  wishlist: '\u{2B50}',
  library: '\u{1F4DA}',
  discover: '\u{1F50D}',
  mirrored: '\u{1F517}',
  listenbrainz: '\u{1F3A7}',
  spotify_public: '\u{1F3B5}',
};

export function syncHistorySourceIcon(source: string | null | undefined): string {
  return SYNC_HISTORY_SOURCE_ICONS[source ?? ''] ?? '\u{1F4E5}';
}

export function syncHistorySourceLabel(source: string): string {
  return SYNC_HISTORY_SOURCE_LABELS[source] ?? source;
}

export interface SyncHistoryTab {
  /** null is the All tab; the API takes no `source` param for it. */
  source: string | null;
  label: string;
  count: number;
  active: boolean;
}

/**
 * Tabs from the stats block, busiest source first.
 *
 * All's count is the SUM of the stats rather than the page total, because the
 * page total moves with the active filter and a tab strip whose first number
 * changed as you clicked around would be reporting on itself.
 */
export function syncHistorySourceTabs(
  stats: Record<string, number> | null | undefined,
  active: string | null,
): SyncHistoryTab[] {
  const entries = Object.entries(stats ?? {}).filter(([, count]) => typeof count === 'number');
  const total = entries.reduce((sum, [, count]) => sum + count, 0);
  const tabs: SyncHistoryTab[] = [
    { source: null, label: 'All', count: total, active: active === null },
  ];
  for (const [source, count] of [...entries].sort((a, b) => b[1] - a[1])) {
    tabs.push({
      source,
      label: syncHistorySourceLabel(source),
      count,
      active: active === source,
    });
  }
  return tabs;
}

/**
 * Playlist syncs only.
 *
 * The same table stores album downloads, wishlist runs and redownloads, and
 * this screen is about playlists. A missing `sync_type` counts as a playlist —
 * older rows predate the column, and dropping them would silently shorten
 * everyone's history.
 */
export function syncHistoryVisibleEntries(
  entries: SyncHistoryEntry[] | null | undefined,
): SyncHistoryEntry[] {
  return (entries ?? []).filter((e) => e.sync_type === 'playlist' || !e.sync_type);
}

export interface SyncHistoryStat {
  kind: 'found' | 'downloaded' | 'failed' | 'pending';
  label: string;
}

/**
 * The chips on a row.
 *
 * A run still going says so instead of showing zeroes, and a finished run with
 * nothing to report falls back to the library count — "0 found" on a playlist
 * that was already complete reads as a failure when it was a no-op.
 */
export function syncHistoryStats(entry: SyncHistoryEntry): SyncHistoryStat[] {
  if (!entry.completed_at) return [{ kind: 'pending', label: 'In progress' }];
  const stats: SyncHistoryStat[] = [];
  if ((entry.tracks_found ?? 0) > 0) {
    stats.push({ kind: 'found', label: `${entry.tracks_found} found` });
  }
  if ((entry.tracks_downloaded ?? 0) > 0) {
    stats.push({ kind: 'downloaded', label: `${entry.tracks_downloaded} downloaded` });
  }
  if ((entry.tracks_failed ?? 0) > 0) {
    stats.push({ kind: 'failed', label: `${entry.tracks_failed} failed` });
  }
  if (stats.length === 0) {
    stats.push({ kind: 'found', label: `${entry.total_tracks ?? 0} in library` });
  }
  return stats;
}

/** "Artist — Album", or the sync type when there is neither. */
export function syncHistoryMeta(entry: SyncHistoryEntry): string {
  const parts = [entry.artist_name, entry.album_name].filter(Boolean);
  return parts.length > 0 ? parts.join(' — ') : (entry.sync_type ?? '');
}

export function syncHistoryPageCount(total: number, limit: number): number {
  if (limit <= 0) return 0;
  return Math.ceil((total || 0) / limit);
}

/** Sources whose re-sync means "match these tracks to the media server". */
export const SYNC_HISTORY_SERVER_SOURCES = new Set([
  'spotify',
  'tidal',
  'deezer',
  'youtube',
  'mirrored',
  'listenbrainz',
  'spotify_public',
  'beatport',
]);

/** Sources whose re-sync means "go download the missing files". */
export const SYNC_HISTORY_DOWNLOAD_SOURCES = new Set(['discover', 'library', 'wishlist']);

/**
 * Which of the two re-sync paths an entry takes.
 *
 * `is_album_download` OVERRIDES the source: a Spotify row that was an album
 * download is a download, not a server sync, and sending it down the server
 * path would start a playlist sync against an album's tracks.
 *
 * Everything else is a server sync, INCLUDING sources in neither set. That is
 * the vanilla's behaviour and it is deliberate here: the vanilla computed an
 * `isServerSync` flag on the server-sources set and then never branched on it
 * (4084), so an unrecognised source has always fallen through to the server
 * path. Narrowing it now would break re-sync for any source added since.
 */
export function syncHistoryResyncKind(entry: SyncHistoryEntry): 'download' | 'server' {
  if (entry.is_album_download) return 'download';
  if (SYNC_HISTORY_DOWNLOAD_SOURCES.has(entry.source ?? '')) return 'download';
  return 'server';
}

export interface SyncHistoryResyncTrack {
  id: string;
  name: string;
  artists: string[];
  album: string;
  duration_ms: number;
  popularity: number;
}

/**
 * Text from an unknown, and NEVER "[object Object]".
 *
 * These fields are stored from whatever the original source handed over, so an
 * unexpected object is possible. `String(someObject)` yields "[object Object]",
 * which the matcher would then dutifully search Soulseek for. An empty string
 * is a visibly missing value; "[object Object]" is a plausible-looking wrong
 * one.
 */
function asText(value: unknown, fallback = ''): string {
  if (value === null || value === undefined) return fallback;
  if (typeof value === 'string') return value || fallback;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return fallback;
}

/**
 * Flatten stored tracks into what `/api/sync/start` accepts.
 *
 * The stored shape is whatever the original source handed over, so `artists`
 * arrives as objects, as strings, or as a bare string, and `album` as an object
 * or a string. The endpoint takes exactly one of those shapes.
 */
export function syncHistoryResyncTracks(
  tracks: unknown[] | null | undefined,
): SyncHistoryResyncTrack[] {
  return (tracks ?? []).map((raw) => {
    const t = (raw ?? {}) as Record<string, unknown>;
    let artists: string[];
    if (Array.isArray(t.artists)) {
      artists = t.artists.map((a) =>
        a && typeof a === 'object' ? asText((a as Record<string, unknown>).name) : asText(a),
      );
    } else {
      artists = [asText(t.artists, 'Unknown Artist')];
    }
    const album =
      t.album && typeof t.album === 'object'
        ? asText((t.album as Record<string, unknown>).name)
        : asText(t.album);
    return {
      id: asText(t.id),
      name: asText(t.name),
      artists,
      album,
      duration_ms: Number(t.duration_ms ?? 0) || 0,
      popularity: Number(t.popularity ?? 0) || 0,
    };
  });
}

export interface SyncHistoryProgress {
  percent: number;
  step: string;
  matched: number;
  failed: number;
  total: number;
  /** Whether the run is over, and how it ended. */
  phase: 'running' | 'finished' | 'cancelled' | 'error';
}

/**
 * Read one poll of `/api/sync/status/<id>`.
 *
 * The percent counts matched PLUS failed against the total: a failed track is
 * processed, and a bar that only advanced on success would sit still through a
 * run that was making steady progress at finding nothing.
 */
export function syncHistoryProgress(state: unknown): SyncHistoryProgress {
  const s = (state ?? {}) as Record<string, unknown>;
  const status = asText(s.status);
  const raw = (s.progress ?? s.result ?? {}) as Record<string, unknown>;

  const matched = Number(raw.matched_tracks ?? 0) || 0;
  const failed = Number(raw.failed_tracks ?? 0) || 0;
  const total = Number(raw.total_tracks ?? 0) || 0;
  const synced = Number(raw.synced_tracks ?? 0) || 0;

  if (status === 'finished') {
    return {
      percent: 100,
      step: `Sync complete — ${matched}/${total} matched, ${synced} synced`,
      matched,
      failed,
      total,
      phase: 'finished',
    };
  }
  if (status === 'cancelled') {
    return { percent: 0, step: 'Sync cancelled', matched, failed, total, phase: 'cancelled' };
  }
  if (status === 'error') {
    return {
      percent: 0,
      step: `Sync error: ${asText(s.error, 'Unknown')}`,
      matched,
      failed,
      total,
      phase: 'error',
    };
  }

  const processed = matched + failed;
  const step = asText(raw.current_step, 'Processing');
  const track = asText(raw.current_track);
  return {
    percent: total > 0 ? Math.round((processed / total) * 100) : 0,
    step: track ? `${step} — ${track}` : step,
    matched,
    failed,
    total,
    phase: 'running',
  };
}
