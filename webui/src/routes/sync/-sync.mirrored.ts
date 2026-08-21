/**
 * The mirrored-playlist card's pure core (stats-automations.js 500-1061).
 *
 * Mirrored is the odd vertical: its card is NOT the shared archetype
 * (.mirrored-playlist-card, its own DOM) and its phase line is NOT the shared
 * phase map — the colours are inline hex and the vocabulary is its own
 * ("Discovered M/T", "Synced", "Pipeline complete"). Nothing here may reach
 * for -sync.core's phaseText/phaseColor or -ui/source-card.
 *
 * The pipeline phases (pipeline_running / _complete / _error) layer OVER the
 * seven-phase machine: they are synthesised from the row's pipeline_state
 * ONLY when no live per-hash state exists (534-542).
 */

import type { MirroredPipelineState } from './-sync.pipeline';
import type { SourceVertical } from './-sync.use-vertical';

import { postRetryFailedDiscovery } from './-sync.api';
import { applyPipelineState } from './-sync.pipeline';

/** The row as /api/mirrored-playlists returns it. */
export interface MirroredPlaylistRow {
  id: number;
  name?: string;
  display_name?: string;
  custom_name?: string;
  source?: string;
  source_playlist_id?: string;
  source_ref?: string;
  description?: string;
  /** The poster the SOURCE supplied, when it supplied one. */
  image_url?: string;
  /**
   * Up to four distinct album covers borrowed from the playlist's discovered
   * tracks, as a JSON array string. The fallback for the many sources that send
   * no poster at all — see backfill_missing_mirrored_covers.
   */
  cover_tiles?: string | null;
  track_count?: number;
  total_count?: number;
  discovered_count?: number;
  quality_profile_id?: number | null;
  /**
   * Download missing tracks into a playlist-named folder.
   *
   * A per-playlist setting whose ONLY UI was a scheduled card on the Auto-Sync
   * board, so it became unreachable for any playlist that was not scheduled.
   * It belongs with the playlist, not with its cadence.
   */
  organize_by_playlist?: boolean;
  updated_at?: string;
  mirrored_at?: string;
  pipeline_state?: {
    status?: string;
    progress?: number;
    phase?: string;
    error?: string;
  } | null;
}

/** Per-source card icon (571-572); anything unlisted gets the clipboard. */
export const MIRRORED_SOURCE_ICONS: Readonly<Record<string, string>> = {
  spotify: '🎵',
  tidal: '🌊',
  youtube: '▶',
  beatport: '🎛',
  file: '📄',
};

export function mirroredSourceIcon(source: string | null | undefined): string {
  return MIRRORED_SOURCE_ICONS[source ?? ''] ?? '📋';
}

/* ── The DETAIL modal's own tables (openMirroredPlaylistModal, 1086-1089) ──── */

/**
 * The detail modal's icon table is NOT the card's. They overlap but neither is
 * a superset: the card (571) knows `file` and not spotify_public/deezer/qobuz;
 * the detail modal knows those three and not `file`. Sharing one table would
 * silently change what either surface shows, so they stay separate — checked
 * key by key against both lines.
 */
export const MIRRORED_DETAIL_SOURCE_ICONS: Readonly<Record<string, string>> = {
  spotify: '🎵',
  spotify_public: '🎵',
  tidal: '🌊',
  youtube: '▶',
  beatport: '🎛',
  deezer: '🎧',
  qobuz: '♫',
};

/** Same seven keys; an unlisted source falls back to its RAW name (1089). */
export const MIRRORED_DETAIL_SOURCE_LABELS: Readonly<Record<string, string>> = {
  spotify: 'Spotify',
  spotify_public: 'Spotify',
  tidal: 'Tidal',
  youtube: 'YouTube',
  beatport: 'Beatport',
  deezer: 'Deezer',
  qobuz: 'Qobuz',
};

export function mirroredDetailSourceIcon(source: string): string {
  return MIRRORED_DETAIL_SOURCE_ICONS[source] ?? '📋';
}

export function mirroredDetailSourceLabel(source: string): string {
  return MIRRORED_DETAIL_SOURCE_LABELS[source] ?? source;
}

export interface MirroredTrack {
  position?: number;
  track_name?: string;
  artist_name?: string;
  album_name?: string;
  duration_ms?: number;
  image_url?: string;
}

/** Hero artwork: playlist cover → the first track that has art → none (1092). */
export function mirroredHeroArt(
  imageUrl: string | null | undefined,
  tracks: readonly MirroredTrack[],
): string {
  return imageUrl || tracks.find((t) => t.image_url)?.image_url || '';
}

/**
 * The meta line's runtime (1095-1097). Rounded to whole MINUTES first, so the
 * hour/minute split derives from the rounded value, not the raw ms. `totalMs`
 * comes back because the vanilla gates the whole segment on it being truthy
 * (1133), not on the label.
 */
export function mirroredTotalRuntime(tracks: readonly MirroredTrack[]): {
  totalMs: number;
  label: string;
} {
  const totalMs = tracks.reduce((sum, t) => sum + (t.duration_ms || 0), 0);
  const totalMin = Math.round(totalMs / 60000);
  const label =
    totalMin >= 60 ? `${Math.floor(totalMin / 60)} hr ${totalMin % 60} min` : `${totalMin} min`;
  return { totalMs, label };
}

/**
 * The track row's duration (1100) — an INLINE duplicate, not formatDuration: a
 * missing or zero duration renders EMPTY here where formatDuration renders
 * '0:00'. Transcribed rather than reused so the cell stays blank.
 */
export function mirroredRowDuration(durationMs: number | null | undefined): string {
  if (!durationMs) return '';
  const minutes = Math.floor(durationMs / 60000);
  const seconds = String(Math.floor((durationMs % 60000) / 1000)).padStart(2, '0');
  return `${minutes}:${seconds}`;
}

/**
 * The track shape the discovery modal expects, built from the mirror's own
 * rows (discoverMirroredPlaylist, 2073-2079).
 *
 * The id ladder matters: a mirror row that never carried a provider id falls
 * back to `mirrored_<row id>`, which is what the backend then matches on.
 * `artists` is a single-element array of the flat artist_name, not a list.
 */
export function mirroredDiscoveryTracks(
  tracks: readonly { id?: number; source_track_id?: string; [key: string]: unknown }[],
): {
  id: string;
  name: unknown;
  artists: unknown[];
  album: unknown;
  duration_ms: number;
}[] {
  return tracks.map((t) => ({
    id: t.source_track_id || `mirrored_${t.id}`,
    name: t.track_name,
    artists: [t.artist_name],
    album: t.album_name || '',
    duration_ms: (t.duration_ms as number) || 0,
  }));
}

/**
 * retryFailedMirroredDiscovery (2155-2194) — the discovery modal's
 * "Retry Failed (N)" button.
 *
 * It lives here, and SourceModals calls it directly, so no mount site can
 * forget to supply it: the button is gated on the callback existing, which is
 * exactly how it silently never rendered before.
 */
export async function retryFailedMirroredDiscovery(
  hash: string,
  vertical: Pick<SourceVertical, 'patchState' | 'resumeDiscovery'>,
): Promise<void> {
  const playlistId = hash.replace('mirrored_', '');
  try {
    const data = await postRetryFailedDiscovery(playlistId);
    if (data.error) {
      window.showToast?.(`Error: ${data.error}`, 'error');
      return;
    }
    if (data.retry_count === 0) {
      window.showToast?.('All tracks already found!', 'success');
      return;
    }
    const retryCount = data.retry_count ?? 0;
    vertical.patchState(hash, (s) => ({
      ...s,
      phase: 'discovering',
      discoveryProgress: 0,
      // #815: the baseline the completion toast reports against (2178-2181).
      retryDiscovery: { matchesBefore: s.spotifyMatches || 0, retryCount },
    }));
    vertical.resumeDiscovery(hash);
    window.showToast?.(`Retrying ${retryCount} failed tracks...`, 'info');
  } catch (err) {
    window.showToast?.(
      `Error retrying discovery: ${err instanceof Error ? err.message : 'unknown error'}`,
      'error',
    );
  }
}

/**
 * The pipeline phase a row carries when there is no live state (530-533).
 * Null when the row has no pipeline_state or an unrecognised status.
 */
export function pipelinePhaseFor(row: MirroredPlaylistRow): string | null {
  const s = row.pipeline_state;
  if (!s) return null;
  if (s.status === 'running') return 'pipeline_running';
  if (s.status === 'finished') return 'pipeline_complete';
  if (s.status === 'error' || s.status === 'skipped') return 'pipeline_error';
  return null;
}

export interface MirroredPhaseLine {
  text: string;
  color: string;
}

/**
 * The phase line. The vanilla has THREE writers for this span and they
 * disagree: renderMirroredCard (548-569) shows a discovery percent and falls
 * back to the row's track_count; updateMirroredCardPhase (839-875) shows a
 * bare "Discovering..." and no track_count fallback; and
 * hydrateMirroredDiscoveryStates (1021-1031) has no pipeline arms at all. So
 * a live card reads "Discovering 40%", drops to "Discovering...", then
 * returns to "40%" after a refresh.
 *
 * DECLARED UNIFICATION: renderMirroredCard wins — it is the only writer that
 * runs for every card and the only one with the pipeline arms. Colours agree
 * across all three, so those are uncontested.
 */
export function mirroredPhaseLine(
  phase: string | null | undefined,
  state: {
    pipeline_progress?: number;
    pipeline_phase?: string;
    discoveryProgress?: number;
    spotifyMatches?: number;
    spotifyTotal?: number;
  } | null,
  row: MirroredPlaylistRow,
): MirroredPhaseLine | null {
  if (!phase) return null;
  switch (phase) {
    case 'pipeline_running':
      return {
        text: `${state?.pipeline_phase || 'Pipeline running'} ${state?.pipeline_progress ?? 0}%`,
        color: '#38bdf8',
      };
    case 'pipeline_complete':
      return { text: 'Pipeline complete', color: '#22c55e' };
    case 'pipeline_error':
      return { text: 'Pipeline error', color: '#ef4444' };
    case 'discovering':
      return { text: `Discovering ${state?.discoveryProgress ?? 0}%`, color: '#a78bfa' };
    case 'discovered':
      // The track_count fallback is renderMirroredCard's (561), not the
      // live writer's — see the unification note above.
      return {
        text: `Discovered ${state?.spotifyMatches ?? 0}/${state?.spotifyTotal || row.track_count || 0}`,
        color: '#22c55e',
      };
    case 'syncing':
      return { text: 'Syncing...', color: '#3b82f6' };
    case 'sync_complete':
      return { text: 'Synced', color: '#3b82f6' };
    case 'downloading':
      return { text: 'Downloading...', color: '#f59e0b' };
    case 'download_complete':
      return { text: 'Downloaded', color: '#22c55e' };
    default:
      return null;
  }
}

export interface MirroredRatio {
  text: string;
  complete: boolean;
}

/**
 * The discovery ratio (575-582). Only rendered once something is discovered.
 * `sourceName` is the vanilla's currentMusicSourceName — a global that is
 * declared and never reassigned (live bug #5), so it always reads 'Spotify';
 * the caller passes whatever the React shell really knows.
 */
export function mirroredRatio(row: MirroredPlaylistRow, sourceName: string): MirroredRatio | null {
  const disc = row.discovered_count || 0;
  if (disc <= 0) return null;
  const tot = row.total_count || row.track_count || 0;
  return { text: `${disc}/${tot} discovered on ${sourceName}`, complete: disc >= tot };
}

/**
 * "Mirrored <ago>" (timeAgo, 1045-1061). A bare ISO string is assumed UTC —
 * the guard checks for 'Z', a '+' offset, or a '-' at index >= 10 (so the
 * date's own hyphens don't count).
 */
export function timeAgo(dateStr: string | null | undefined, now: number): string {
  if (!dateStr) return '';
  let ts = dateStr;
  if (!ts.includes('Z') && !ts.includes('+') && !ts.includes('-', 10)) ts += 'Z';
  const diff = now - new Date(ts).getTime();
  const secs = Math.floor(diff / 1000);
  if (secs < 5) return 'just now';
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

/** The registry key / fake hash — the 'mirrored_' marker is PART of the id. */
export function mirroredHash(id: number | string): string {
  return `mirrored_${id}`;
}

/**
 * The pipeline's state writer — applyMirroredPipelineState (auto-sync.js
 * 2443-2464). `patchState` both materialises an absent entry and merges, which
 * is what the vanilla's `{ ...(youtubePlaylistStates[hash] || {}), ... }`
 * assignment does.
 *
 * It lives HERE, not in the mirrored tab, because the page owns the one
 * pipeline controller now (the tab building its own gave the app two poller
 * maps on the same endpoint). The page needs this writer to construct that
 * controller, and re-deriving `mirroredHash` + `applyPipelineState` up there
 * would put one decision in two places — with the copy at the mount site being
 * the one that drifts.
 */
export function mirroredPipelineStateWriter(
  vertical: Pick<SourceVertical, 'patchState'>,
): (playlistId: number, state: MirroredPipelineState) => void {
  return (playlistId, state) => {
    vertical.patchState(mirroredHash(playlistId), (s) => ({
      ...s,
      ...applyPipelineState(s.phase, state),
    }));
  };
}
