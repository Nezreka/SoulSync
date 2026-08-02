/**
 * Tag writing + ReplayGain (library.js: showTagPreview 5334, executeWriteTags
 * 5408, batch previews 5469, _pollBatchWriteTagsStatus 5654, ReplayGain 5709-
 * 5834). Requests, poller loops and their exact toast streams; the two modal
 * UIs live in -ui/tag-preview-modal.tsx and -ui/batch-tag-preview-modal.tsx.
 *
 * The pollers are module-level setTimeout chains, as the vanilla's were: they
 * outlive whatever component started them, because the batch keeps running
 * server-side after the modal closes and the user still gets its progress
 * toasts.
 */

export interface TagDiffRow {
  field: string;
  file_value?: string;
  db_value?: string;
  changed?: boolean;
}

export interface TagPreview {
  diff: TagDiffRow[];
  hasChanges: boolean;
  /** Sync-to-server offer: plex/jellyfin only — navidrome auto-detects. */
  serverType: string | null;
  error?: string;
}

export async function fetchTagPreview(trackId: unknown): Promise<TagPreview> {
  const response = await fetch(`/api/library/track/${trackId}/tag-preview`);
  const result = await response.json();
  if (!result.success)
    return { diff: [], hasChanges: false, serverType: null, error: result.error };
  return {
    diff: result.diff || [],
    hasChanges: Boolean(result.has_changes),
    serverType: result.server_type || null,
  };
}

export function offersServerSync(serverType: string | null): boolean {
  return Boolean(serverType && serverType !== 'navidrome');
}

export function serverSyncLabel(serverType: string | null): string {
  return `Sync to ${serverType === 'plex' ? 'Plex' : 'Jellyfin'}`;
}

/** POST write-tags; returns the vanilla's toast line (5429-5435). */
export async function writeTagsRequest(
  trackId: unknown,
  embedCover: boolean,
  syncToServer: boolean,
  serverType: string | null,
): Promise<string> {
  const response = await fetch(`/api/library/track/${trackId}/write-tags`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ embed_cover: embedCover, sync_to_server: syncToServer }),
  });
  const result = await response.json();
  if (!result.success) throw new Error(result.error);

  const fieldCount = (result.written_fields || []).length;
  let message = `Tags written successfully (${fieldCount} fields)`;
  if (result.server_sync) {
    if (result.server_sync.synced > 0) {
      message += ` — synced to ${serverType === 'plex' ? 'Plex' : 'Jellyfin'}`;
    } else if (result.server_sync.failed > 0) {
      message += ` — server sync failed`;
    }
  }
  return message;
}

// ---- Batch tag preview / write ----

export interface BatchTagTrack {
  title?: string;
  track_number?: number | string;
  has_changes?: boolean;
  error?: string;
  changed_count?: number;
  diff?: TagDiffRow[];
}

export interface BatchTagPreview {
  tracks: BatchTagTrack[];
  serverType: string | null;
  error?: string;
}

export async function fetchBatchTagPreview(trackIds: unknown[]): Promise<BatchTagPreview> {
  const response = await fetch('/api/library/tracks/tag-preview-batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ track_ids: trackIds }),
  });
  const result = await response.json();
  if (!result.success) return { tracks: [], serverType: null, error: result.error };
  return { tracks: result.tracks || [], serverType: result.server_type || null };
}

/** The vanilla's done-toast wording, first error included (5672-5688). */
export function batchWriteDoneMessage(state: Record<string, unknown>): {
  message: string;
  tone: 'success' | 'warning';
} {
  let message = `Tags written: ${state.written} updated`;
  if (((state.skipped as number) || 0) > 0) message += `, ${state.skipped} unchanged`;
  if (((state.failed as number) || 0) > 0) message += `, ${state.failed} failed`;
  if (state.sync_phase === 'done') {
    const server = state.sync_server;
    const serverName =
      server === 'plex' ? 'Plex' : server === 'jellyfin' ? 'Jellyfin' : String(server);
    if (((state.sync_synced as number) || 0) > 0 && ((state.sync_failed as number) || 0) === 0) {
      message += ` — synced to ${serverName}`;
    } else if (((state.sync_failed as number) || 0) > 0) {
      message += ` — ${serverName} sync: ${state.sync_synced} synced, ${state.sync_failed} failed`;
    }
  }
  const errors = state.errors as { error?: string }[] | undefined;
  if (((state.failed as number) || 0) > 0 && errors && errors.length > 0) {
    message += ` (${errors[0].error || 'Unknown error'})`;
  }
  const tone =
    ((state.failed as number) || 0) > 0 || ((state.sync_failed as number) || 0) > 0
      ? 'warning'
      : 'success';
  return { message, tone };
}

let batchWriteTimer: ReturnType<typeof setTimeout> | null = null;

/** Start the background batch write, then poll its status into toasts. */
export async function startBatchWriteTags(
  trackIds: unknown[],
  embedCover: boolean,
  syncToServer: boolean,
): Promise<void> {
  try {
    const response = await fetch('/api/library/tracks/write-tags-batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        track_ids: trackIds,
        embed_cover: embedCover,
        sync_to_server: syncToServer,
      }),
    });
    const result = await response.json();
    if (!result.success) throw new Error(result.error);
    window.showToast?.(`Writing tags for ${trackIds.length} tracks...`, 'info');
    pollBatchWriteTagsStatus();
  } catch (error) {
    window.showToast?.(`Failed to start tag write: ${(error as Error).message}`, 'error');
  }
}

/** 800ms first tick, then every second — the vanilla's cadence (5670, 5697). */
export function pollBatchWriteTagsStatus(): void {
  if (batchWriteTimer) clearTimeout(batchWriteTimer);

  async function poll() {
    try {
      const response = await fetch('/api/library/tracks/write-tags-batch/status');
      const state = await response.json();
      if (state.status === 'running') {
        if (state.sync_phase === 'syncing') {
          const server = state.sync_server;
          const serverName =
            server === 'plex' ? 'Plex' : server === 'jellyfin' ? 'Jellyfin' : String(server);
          window.showToast?.(`Syncing to ${serverName}...`, 'info');
        } else {
          const pct = state.total > 0 ? Math.round((state.processed / state.total) * 100) : 0;
          window.showToast?.(
            `Writing tags: ${state.processed}/${state.total} (${pct}%) — ${state.current_track}`,
            'info',
          );
        }
        batchWriteTimer = setTimeout(poll, 1000);
      } else if (state.status === 'done') {
        const { message, tone } = batchWriteDoneMessage(state);
        window.showToast?.(message, tone);
        batchWriteTimer = null;
      }
    } catch (error) {
      console.error('Poll write-tags status failed:', error);
      batchWriteTimer = null;
    }
  }

  batchWriteTimer = setTimeout(poll, 800);
}

// ---- ReplayGain ----

/** Single track: synchronous on the server (~1-3s); the button shows '…'. */
export async function analyzeTrackReplayGainRequest(trackId: unknown): Promise<void> {
  try {
    const response = await fetch(`/api/library/track/${trackId}/analyze-replaygain`, {
      method: 'POST',
    });
    const result = await response.json();
    if (result.success) {
      window.showToast?.(
        `ReplayGain written: ${result.track_gain} (${result.lufs} LUFS)`,
        'success',
      );
    } else {
      window.showToast?.(`ReplayGain failed: ${result.error}`, 'error');
    }
  } catch {
    window.showToast?.('ReplayGain analysis failed', 'error');
  }
}

let rgAlbumTimer: ReturnType<typeof setTimeout> | null = null;
let rgBatchTimer: ReturnType<typeof setTimeout> | null = null;

/** Album job: kick off, then 1s first tick / 1.2s cadence; onDone re-enables the button. */
export async function analyzeAlbumReplayGainRequest(
  albumId: unknown,
  onDone: () => void,
): Promise<void> {
  try {
    const response = await fetch(`/api/library/album/${albumId}/analyze-replaygain`, {
      method: 'POST',
    });
    const result = await response.json();
    if (!result.success) {
      window.showToast?.(`ReplayGain: ${result.error}`, 'error');
      onDone();
      return;
    }
    window.showToast?.('Album ReplayGain analysis started…', 'info');
  } catch {
    window.showToast?.('Failed to start album ReplayGain analysis', 'error');
    onDone();
    return;
  }

  if (rgAlbumTimer) clearTimeout(rgAlbumTimer);
  async function poll() {
    try {
      const response = await fetch(`/api/library/album/${albumId}/analyze-replaygain/status`);
      const state = await response.json();
      if (state.status === 'running') {
        const pct = state.total > 0 ? Math.round((state.processed / state.total) * 100) : 0;
        window.showToast?.(
          `ReplayGain: ${state.processed}/${state.total} tracks (${pct}%)`,
          'info',
        );
        rgAlbumTimer = setTimeout(poll, 1200);
      } else if (state.status === 'done') {
        window.showToast?.(
          `ReplayGain done: ${state.analyzed} analyzed, ${state.failed} failed`,
          state.failed > 0 ? 'warning' : 'success',
        );
        onDone();
        rgAlbumTimer = null;
      }
    } catch (error) {
      console.error('ReplayGain album poll failed:', error);
      onDone();
      rgAlbumTimer = null;
    }
  }
  rgAlbumTimer = setTimeout(poll, 1000);
}

/** Selected-tracks job (track gain only — they may span albums): 800ms/1s cadence. */
export function pollBatchRgStatus(): void {
  if (rgBatchTimer) clearTimeout(rgBatchTimer);
  async function poll() {
    try {
      const response = await fetch('/api/library/tracks/analyze-replaygain-batch/status');
      const state = await response.json();
      if (state.status === 'running') {
        const pct = state.total > 0 ? Math.round((state.processed / state.total) * 100) : 0;
        window.showToast?.(
          `ReplayGain: ${state.processed}/${state.total} (${pct}%) — ${state.current_track}`,
          'info',
        );
        rgBatchTimer = setTimeout(poll, 1000);
      } else if (state.status === 'done') {
        window.showToast?.(
          `ReplayGain done: ${state.analyzed} written, ${state.failed} failed`,
          state.failed > 0 ? 'warning' : 'success',
        );
        rgBatchTimer = null;
      }
    } catch (error) {
      console.error('ReplayGain batch poll failed:', error);
      rgBatchTimer = null;
    }
  }
  rgBatchTimer = setTimeout(poll, 800);
}

/** Test hook: the pollers are module timers and must not leak between tests. */
export function _stopAllTagRgPollers(): void {
  for (const timer of [batchWriteTimer, rgAlbumTimer, rgBatchTimer]) {
    if (timer) clearTimeout(timer);
  }
  batchWriteTimer = rgAlbumTimer = rgBatchTimer = null;
}
