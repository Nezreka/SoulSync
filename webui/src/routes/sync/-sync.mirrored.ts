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
  track_count?: number;
  total_count?: number;
  discovered_count?: number;
  quality_profile_id?: number | null;
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
