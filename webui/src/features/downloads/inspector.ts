import { decisionPill, type CandidateDecision } from './decisions';

/**
 * The candidate inspector's data side: what one source line carries, how the
 * stream is read, and the rules for overriding a rejection. Shared by the
 * redownload modal and the standalone inspector (wishlist, failed downloads).
 */

export const DOWNLOAD_SERVICE_ICONS: Record<string, string> = {
  soulseek: '🔍',
  youtube: '▶️',
  tidal: '🌊',
  qobuz: '🎵',
  hifi: '🎧',
  deezer_dl: '💜',
  hybrid: '⚡',
  lidarr: '📦',
  amazon: '🛒',
  soundcloud: '☁️',
  torrent: '🧲',
  usenet: '📰',
};

export const DOWNLOAD_SERVICE_LABELS: Record<string, string> = {
  soulseek: 'Soulseek',
  youtube: 'YouTube',
  tidal: 'Tidal',
  qobuz: 'Qobuz',
  hifi: 'HiFi',
  deezer_dl: 'Deezer',
  hybrid: 'Auto',
  lidarr: 'Lidarr',
  amazon: 'Amazon Music',
  soundcloud: 'SoundCloud',
  torrent: 'Torrent',
  usenet: 'Usenet',
};

/** 90/70 score banding shared by both steps (3446, 3640). */
export function scoreClass(pct: number): 'high' | 'medium' | 'low' {
  return pct >= 90 ? 'high' : pct >= 70 ? 'medium' : 'low';
}

/** m:ss from milliseconds, empty when unknown (3447, 3643). */
export function msClock(ms: unknown): string {
  const value = Number(ms);
  if (!value) return '';
  return `${Math.floor(value / 60000)}:${String(Math.floor((value % 60000) / 1000)).padStart(2, '0')}`;
}

export interface InspectorCandidate {
  display_name?: string;
  filename?: string;
  username?: string;
  source_service?: string;
  quality?: string;
  quality_label?: string;
  bitrate?: number;
  bit_depth?: number | null;
  sample_rate?: number | null;
  size?: number;
  size_display?: string;
  duration?: number;
  upload_speed?: number;
  queue_length?: number;
  artist?: string;
  title?: string;
  confidence?: number;
  blacklisted?: boolean;
  free_upload_slots?: number | null;
  decision?: CandidateDecision;
  _globalIdx: number;
}

/**
 * Why this surface can't take a given hit at all, or null. The wishlist can't
 * pin a torrent release to one track; the redownload modal has no such rule.
 */
export type GrabRule = (candidate: InspectorCandidate) => string | null;

/** The rejected half of one source's line. Never selectable by radio. */
export interface RejectedSummary {
  rows: InspectorCandidate[];
  total: number;
  counts: Record<string, number>;
}

/**
 * Overrides that would download a file the import pipeline refuses anyway.
 * A manual pick skips AcoustID (and, for quality, the quality guard) but never
 * the integrity check, and the download worker skips blacklisted sources.
 */
export function overrideBlockedReason(row: InspectorCandidate): string | null {
  switch (row.decision?.code) {
    case 'preview':
      return 'The file check after download rejects preview clips.';
    case 'duration_mismatch':
      return 'The file check after download rejects a file this far off the expected length.';
    case 'blacklisted':
      return 'You blacklisted this file. Remove it from the blacklist to use it.';
    default:
      return null;
  }
}

const IDENTITY_OVERRIDE_STAGES = new Set(['identity', 'version']);

export function overrideConfirm(
  row: InspectorCandidate,
  expected: { name?: string; artist?: string },
  expectedMs?: number,
): { title: string; message: string; confirmText: string; destructive: boolean } {
  const d = row.decision;
  const reason = `${decisionPill(row, expectedMs)}${d?.detail ? `: ${d.detail}` : ''}`;
  const scope = 'This only affects this download. Your settings stay as they are.';
  if (d && IDENTITY_OVERRIDE_STAGES.has(d.stage)) {
    const song = [expected.name ? `"${expected.name}"` : 'the song', expected.artist]
      .filter(Boolean)
      .join(' by ');
    return {
      title: 'Download what may be the wrong song?',
      message:
        `SoulSync passed over this file (${reason}). It may not be ${song}. ` +
        `Files you pick yourself skip the AcoustID check, so nothing will catch a wrong match. ${scope}`,
      confirmText: 'Download anyway',
      destructive: true,
    };
  }
  if (d?.code === 'quarantined') {
    return {
      title: 'Download a file that failed before?',
      message: `This exact file was quarantined after an earlier download. ${scope}`,
      confirmText: 'Download anyway',
      destructive: true,
    };
  }
  if (d?.stage === 'quality') {
    return {
      title: 'Download below your quality profile?',
      message: `${reason}. The quality check after download is skipped for this one file. ${scope}`,
      confirmText: 'Download anyway',
      destructive: false,
    };
  }
  return {
    title: 'Download this file anyway?',
    message: `SoulSync passed over it (${reason}). ${scope}`,
    confirmText: 'Download anyway',
    destructive: false,
  };
}

/** The best pick auto-follows the stream: highest non-blacklisted confidence (3626-3630). */
export function bestCandidateIndex(
  candidates: InspectorCandidate[],
  cannotGrab?: GrabRule,
): number {
  let best = -1;
  let bestConf = 0;
  candidates.forEach((c, i) => {
    if (!c.blacklisted && !cannotGrab?.(c) && (c.confidence || 0) > bestConf) {
      bestConf = c.confidence || 0;
      best = i;
    }
  });
  return best;
}

/**
 * Read an inspector stream (NDJSON, one line per source). Each line's
 * accepted candidates get global indices and are handed to onSource as they
 * land; rejected rows come separately and never join the selectable list.
 * Malformed lines are skipped, matching the vanilla reader.
 */
export async function streamInspection(
  url: string,
  body: unknown,
  onSource: (
    source: string,
    candidates: InspectorCandidate[],
    all: InspectorCandidate[],
    rejected: RejectedSummary,
  ) => void,
): Promise<InspectorCandidate[]> {
  const all: InspectorCandidate[] = [];
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  });
  if (!response.ok) {
    let message = `Search failed (${response.status})`;
    try {
      const data = (await response.json()) as { error?: string };
      if (data.error) message = data.error;
    } catch {
      /* not json */
    }
    throw new Error(message);
  }
  if (!response.body) return all;

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const data = JSON.parse(line);
        if (data.done) continue;
        const candidates = (data.candidates || []) as InspectorCandidate[];
        const startIdx = all.length;
        candidates.forEach((c, i) => {
          c._globalIdx = startIdx + i;
        });
        all.push(...candidates);
        const rejectedRows = ((data.rejected || []) as InspectorCandidate[]).map((c) => ({
          ...c,
          _globalIdx: -1,
        }));
        onSource(String(data.source), candidates, all, {
          rows: rejectedRows,
          total: Number(data.rejected_total) || rejectedRows.length,
          counts: (data.rejected_counts || {}) as Record<string, number>,
        });
      } catch {
        /* skip malformed lines */
      }
    }
  }
  return all;
}
