/**
 * Playlist export (#903) — the pure core of stats-automations.js 662-819.
 *
 * The 📤 button on a mirrored card opens a four-destination picker, POSTs a
 * background job, and then polls it, writing a live status line into the card's
 * own .card-meta row. Two endpoints sit behind the four choices: Spotify and
 * Deezer go to the SERVICE endpoint with a {backfill} body, ListenBrainz and
 * .jspf keep the LB one with a {mode} body (735-741).
 *
 * The two families also report differently at the end — the service arm reads
 * job.stats and job.push.url, the ListenBrainz arm reads job.summary and
 * job.push.playlist_url (771-790). That asymmetry is the backend's, not a
 * transcription slip, so both spellings are preserved here.
 *
 * DECLARED DIVERGENCE: the vanilla threads a `name` argument through
 * _startPlaylistExport and _pollPlaylistExport and never reads it in either
 * body — it is only ever handed to the next recursive call. It is dropped here
 * rather than carried as a dead parameter.
 */

/* ── Destinations (the four .pl-export-choice buttons, 669-684) ───────────── */

export type ExportMode = 'push' | 'download' | 'spotify' | 'deezer';

export interface ExportDestination {
  mode: ExportMode;
  title: string;
  detail: string;
  /** Only the first choice carries the accent border/fill (669). */
  primary?: boolean;
}

export const EXPORT_DESTINATIONS: readonly ExportDestination[] = [
  {
    mode: 'push',
    title: 'Sync to ListenBrainz',
    detail: 'Create the playlist directly on your ListenBrainz account (needs your LB token).',
    primary: true,
  },
  {
    mode: 'download',
    title: 'Download .jspf file',
    detail: 'Save a JSPF playlist you can upload to ListenBrainz manually.',
  },
  {
    mode: 'spotify',
    title: 'Sync to Spotify',
    detail:
      "Create a Spotify playlist in your account (the first time, you'll grant permission to create playlists).",
  },
  {
    mode: 'deezer',
    title: 'Sync to Deezer',
    detail: 'Create a Deezer playlist from this list (uses your Deezer login).',
  },
];

/** Spotify/Deezer take the service endpoint; push/download keep the LB one (736-738). */
export function isServiceExport(mode: string): boolean {
  return mode === 'spotify' || mode === 'deezer';
}

/**
 * 'spotify' → 'Spotify' (694, 771, 780). The vanilla writes `mode[0]`, which
 * would throw on '' — charAt keeps the same answer for every reachable input
 * without the crash on one that cannot occur.
 */
export function exportServiceLabel(mode: string): string {
  return mode.charAt(0).toUpperCase() + mode.slice(1);
}

/** Who the job is pushing TO — the service by name, else ListenBrainz (768). */
export function exportDestinationLabel(mode: string): string {
  return isServiceExport(mode) ? exportServiceLabel(mode) : 'ListenBrainz';
}

/* ── Connection gating (the /api/discover/your-albums/sources probe, 715) ─── */

/**
 * A service choice the account is not connected to (717-719). ListenBrainz and
 * .jspf are never gated, and a probe that failed leaves `connected` empty —
 * the vanilla swallows that error and gates nothing, so pass null to match.
 */
export function isExportModeGated(mode: string, connected: string[] | null): boolean {
  if (connected === null) return false;
  return isServiceExport(mode) && !connected.includes(mode);
}

/* ── Status lines (_setExportStatus's html, as data) ──────────────────────── */

export interface ExportStatusLine {
  text: string;
  color: string;
  /**
   * Rendered after `text`, separated by a space. Only the authorize link is
   * underlined (748); the two finished-job links are not (776, 788).
   */
  link?: { url: string; label: string; underline?: boolean };
  /** Trailing text after the link (the needs_auth sentence, 748). */
  suffix?: string;
  /** The span is dropped this long after it is painted (_setExportStatus). */
  autoHideMs?: number;
}

/** The link colour every anchor in an export status uses (748, 776, 788). */
export const EXPORT_LINK_COLOR = '#38bdf8';

/** Painted before the start POST goes out (733). */
export const EXPORT_STARTING_STATUS: ExportStatusLine = {
  text: 'Starting export…',
  color: '#a78bfa',
};

/** The start POST threw — network or bad JSON (753). */
export const EXPORT_START_ERROR_STATUS: ExportStatusLine = {
  text: 'Export error',
  color: '#ef4444',
};

/** Clicking a greyed-out service choice (697-700). */
export function exportNotConnectedStatus(mode: string): ExportStatusLine {
  return {
    text: `Connect ${exportServiceLabel(mode)} in Settings → Connections to export here`,
    color: '#f59e0b',
    autoHideMs: 9000,
  };
}

/* ── Starting the job (_startPlaylistExport, 731-755) ─────────────────────── */

export interface ExportStartResponse {
  success?: boolean;
  job_id?: string;
  error?: string;
  needs_auth?: boolean;
  auth_url?: string;
}

export interface ExportStartOutcome {
  /** null → leave "Starting export…" up; the poll paints next. */
  status: ExportStatusLine | null;
  /** Present only when a job actually started. */
  jobId?: string;
}

/**
 * Spotify's first export needs a one-time write grant. The vanilla renders the
 * authorize URL as a LINK rather than calling window.open, because a popup
 * opened after an await has lost its user-gesture and gets blocked (742-744).
 */
export function exportStartOutcome(data: ExportStartResponse): ExportStartOutcome {
  if (data.needs_auth && data.auth_url) {
    return {
      status: {
        text: 'Spotify needs permission to create playlists —',
        link: { url: data.auth_url, label: 'authorize', underline: true },
        suffix: ', then click Export again.',
        color: '#f59e0b',
        autoHideMs: 20000,
      },
    };
  }
  if (!data.success || !data.job_id) {
    return { status: { text: data.error || 'Export failed to start', color: '#ef4444' } };
  }
  return { status: null, jobId: data.job_id };
}

/* ── Polling the job (_pollPlaylistExport, 757-805) ───────────────────────── */

export interface ExportJob {
  phase?: string;
  done?: number;
  total?: number;
  error?: string;
  /** The service arm's counters. */
  stats?: { resolved?: number; from_search?: number; unmatched?: number };
  /** The ListenBrainz arm's counters. */
  summary?: { included?: number; total?: number; skipped?: number };
  /** `url` is the service arm's; `playlist_url` is ListenBrainz's. */
  push?: { url?: string; playlist_url?: string };
}

export interface ExportPollOutcome {
  /** null → paint nothing (an unrecognised phase); the tick still repeats. */
  status: ExportStatusLine | null;
  /** false → schedule the next tick. */
  terminal: boolean;
  toast?: { message: string; type: 'success' };
  /** The .jspf hand-off — the vanilla assigns it to window.location (784). */
  downloadUrl?: string;
}

/** GET target for the finished .jspf (784). */
export function exportDownloadUrl(jobId: string): string {
  return `/api/playlists/export/download/${jobId}`;
}

export function exportPollOutcome(
  job: ExportJob,
  mode: ExportMode,
  jobId: string,
): ExportPollOutcome {
  const st = job.stats || {};

  if (job.phase === 'resolving') {
    const pct = job.total ? Math.round((100 * (job.done || 0)) / job.total) : 0;
    return {
      status: {
        text:
          `Matching ${job.done || 0}/${job.total || 0} (${pct}%)` +
          (st.resolved != null ? ` · ${st.resolved} matched` : ''),
        color: EXPORT_LINK_COLOR,
      },
      terminal: false,
    };
  }

  if (job.phase === 'pushing') {
    return {
      status: { text: `Pushing to ${exportDestinationLabel(mode)}…`, color: '#a78bfa' },
      terminal: false,
    };
  }

  if (job.phase === 'done') {
    if (isServiceExport(mode)) {
      const dest = exportServiceLabel(mode);
      const push = job.push || {};
      const cov =
        `${st.resolved || 0} added` +
        (st.from_search ? ` (${st.from_search} matched live)` : '') +
        (st.unmatched ? ` · ${st.unmatched} not on ${dest}` : '');
      return {
        status: {
          text: `Exported to ${dest} · ${cov}`,
          color: '#22c55e',
          link: push.url ? { url: push.url, label: 'open' } : undefined,
          autoHideMs: 12000,
        },
        terminal: true,
        toast: { message: `Playlist exported to ${dest} (${cov})`, type: 'success' },
      };
    }

    const sum = job.summary || {};
    const cov =
      `${sum.included || 0}/${sum.total || 0} matched` +
      (sum.skipped ? ` · ${sum.skipped} unmatched` : '');

    if (mode === 'download') {
      return {
        status: { text: `Downloaded · ${cov}`, color: '#22c55e', autoHideMs: 8000 },
        terminal: true,
        downloadUrl: exportDownloadUrl(jobId),
      };
    }

    const url = (job.push && job.push.playlist_url) || '';
    return {
      status: {
        text: `Synced to ListenBrainz · ${cov}`,
        color: '#22c55e',
        link: url ? { url, label: 'view' } : undefined,
        autoHideMs: 12000,
      },
      terminal: true,
      toast: { message: `Playlist synced to ListenBrainz (${cov})`, type: 'success' },
    };
  }

  if (job.phase === 'error') {
    return {
      status: { text: job.error || 'Export failed', color: '#ef4444', autoHideMs: 10000 },
      terminal: true,
    };
  }

  return { status: null, terminal: false };
}

/** The two poll cadences: 1s while a job runs, 2s after a failed tick (803-805). */
export const EXPORT_POLL_MS = 1000;
export const EXPORT_POLL_RETRY_MS = 2000;
