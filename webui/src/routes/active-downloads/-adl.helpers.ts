/**
 * Pure formatting and classification for the downloads page.
 *
 * Ported from pages-extra.js 2510–3684. These are the functions whose output is
 * user-visible text, so each one is a byte-for-byte port of the original rather
 * than a tidier equivalent — the tests pin the exact strings.
 */

import type { AdlAlbumBundle, AdlBatch, AdlDownload, AdlQuarantineEntry } from './-adl.types';

// ── Status ────────────────────────────────────────────────────────────────

export type AdlStatusClass = 'active' | 'queued' | 'completed' | 'failed' | 'cancelled';

/**
 * The row's visual class.
 *
 * `pending` maps to queued, and anything unrecognised ALSO maps to queued —
 * an unknown status renders as a pending row rather than disappearing.
 */
export function statusClass(status: string): AdlStatusClass {
  switch (status) {
    case 'downloading':
    case 'searching':
    case 'post_processing':
      return 'active';
    case 'queued':
    case 'pending':
      return 'queued';
    case 'completed':
    case 'skipped':
    case 'already_owned':
      return 'completed';
    case 'failed':
    case 'not_found':
      return 'failed';
    case 'cancelled':
      return 'cancelled';
    default:
      return 'queued';
  }
}

/**
 * The label text. Three statuses prefix a spinner element, which is why the
 * caller renders a node rather than a string.
 */
export function statusLabel(status: string): { spinner: boolean; text: string } {
  switch (status) {
    case 'downloading':
      return { spinner: true, text: 'Downloading' };
    case 'searching':
      return { spinner: true, text: 'Searching' };
    case 'post_processing':
      return { spinner: true, text: 'Processing' };
    case 'queued':
    case 'pending':
      return { spinner: false, text: 'Queued' };
    case 'completed':
      return { spinner: false, text: 'Completed' };
    case 'skipped':
      return { spinner: false, text: 'Skipped' };
    case 'already_owned':
      return { spinner: false, text: 'Owned' };
    case 'failed':
      return { spinner: false, text: 'Failed' };
    case 'not_found':
      return { spinner: false, text: 'Not Found' };
    case 'cancelled':
      return { spinner: false, text: 'Cancelled' };
    default:
      // The raw status, so an unmapped one is visible rather than blank.
      return { spinner: false, text: status };
  }
}

// ── Formatting ────────────────────────────────────────────────────────────

/**
 * `1.5 MB` / `12 GB`. Empty string for anything non-finite or <= 0.
 *
 * The decimal rule is the original's and is not the obvious one: ONE decimal
 * only when the number is under 10 AND the unit is not bytes, so you get
 * "1.5 MB" but "12 MB" and "900 B".
 */
export function formatBytes(bytes: unknown): string {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  const decimals = size >= 10 || unit === 0 ? 0 : 1;
  return `${size.toFixed(decimals)} ${units[unit]}`;
}

export function formatSpeed(bytesPerSecond: unknown): string {
  const formatted = formatBytes(bytesPerSecond);
  return formatted ? `${formatted}/s` : '';
}

/** `45s` / `12m` / `1h 5m`. Empty for falsy, negative or non-finite. */
export function formatDuration(sec: number): string {
  if (!sec || sec < 0 || !Number.isFinite(sec)) return '';
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  return `${Math.floor(sec / 3600)}h ${Math.round((sec % 3600) / 60)}m`;
}

// ── Album bundle (release downloads) ──────────────────────────────────────

/**
 * 0–100.
 *
 * Accepts BOTH a fraction and a percentage: a value <= 1 is multiplied by 100.
 * That means a genuine 1% reads as 100% — a real edge in the original, kept
 * because the server sends fractions for some sources and percents for others,
 * and changing it would silently mis-scale one of them.
 */
export function bundleProgressPercent(bundle: AdlAlbumBundle | null | undefined): number {
  if (!bundle) return 0;
  const raw = bundle.progress_percent ?? bundle.progress ?? 0;
  let progress = Number(raw);
  if (!Number.isFinite(progress)) progress = 0;
  if (progress <= 1) progress *= 100;
  return Math.max(0, Math.min(100, Math.round(progress)));
}

const SOURCE_LABELS: Record<string, string> = {
  torrent: 'Torrent',
  usenet: 'Usenet',
  soulseek: 'Soulseek',
  youtube: 'YouTube',
  tidal: 'Tidal',
  qobuz: 'Qobuz',
  hifi: 'HiFi',
  deezer_dl: 'Deezer',
  amazon: 'Amazon',
  lidarr: 'Lidarr',
  soundcloud: 'SoundCloud',
};

/** Known source → display name; an unknown one shows raw, none shows 'Release'. */
export function sourceLabel(source: unknown): string {
  const key = String(source ?? '').toLowerCase();
  return SOURCE_LABELS[key] || (source ? String(source) : 'Release');
}

const BUNDLE_STATE_LABELS: Record<string, string> = {
  searching: 'searching for release',
  downloading: 'downloading release',
  staged: 'matching tracks',
  failed: 'release failed',
};

/** Unknown states fall back to the raw value with underscores as spaces. */
export function bundleStateLabel(state: unknown): string {
  const key = String(state ?? '').toLowerCase();
  return (
    BUNDLE_STATE_LABELS[key] || (state ? String(state).replace(/_/g, ' ') : 'downloading release')
  );
}

/** `Torrent downloading release 42% - Album (1.2 MB/s of 300 MB)` */
export function bundleProgressText(bundle: AdlAlbumBundle | null | undefined): string {
  const pct = bundleProgressPercent(bundle);
  const source = sourceLabel(bundle?.source);
  const state = bundleStateLabel(bundle?.state);
  const release = bundle?.release ? ` - ${bundle.release}` : '';
  // NB: the bundle's speed/size are pre-formatted strings from the downloader,
  // not byte counts — formatSpeed/formatBytes would return '' for them.
  const speed = formatSpeed(bundle?.speed);
  const size = formatBytes(bundle?.size);
  const detail = speed || size ? ` (${[speed, size].filter(Boolean).join(' of ')})` : '';
  return `${source} ${state} ${pct}%${release}${detail}`;
}

// ── Batch colour ──────────────────────────────────────────────────────────

/**
 * A stable colour index 0–7 for a batch, or -1 for no batch.
 *
 * Hashed from the id rather than assigned in arrival order, so a batch keeps
 * its colour across reloads and across the list/panel.
 */
export function batchColorIndex(batchId: string | null | undefined): number {
  if (!batchId) return -1;
  let hash = 0;
  for (let i = 0; i < batchId.length; i++) {
    hash = ((hash << 5) - hash + batchId.charCodeAt(i)) | 0;
  }
  return Math.abs(hash) % 8;
}

// ── Verification ──────────────────────────────────────────────────────────

/**
 * The library_history id a review action needs, or null when there is none.
 *
 * Two sources, and both are needed: a persisted history row encodes it in
 * `task_id` as `history-<n>`, while a still-live completed task carries
 * `history_id` directly. Without the second branch the review buttons only
 * appear once a task has aged into persistent history.
 */
export function verificationHistoryId(dl: AdlDownload): string | null {
  if (dl.is_persistent_history && dl.task_id) {
    const match = /^history-(\d+)$/.exec(String(dl.task_id));
    if (match) return match[1];
  }
  if (dl.history_id) return String(dl.history_id);
  return null;
}

/** Stable key for an unverified row's expanded state. */
export function unverifiedKey(dl: AdlDownload): string {
  return verificationHistoryId(dl) || dl.task_id || '';
}

/**
 * The probed-quality chip (`FLAC 16/44`, `MP3 320`, …).
 *
 * Completed rows with a probed quality only — this is read from the file
 * itself after import, so an in-flight row has nothing truthful to show.
 */
export function qualityChipTitle(): string {
  return 'Audio quality of the downloaded file (read from the file itself)';
}

export function showQualityChip(dl: AdlDownload): boolean {
  return dl.status === 'completed' && Boolean(dl.quality);
}

export interface VerifBadge {
  className: string;
  glyph: string;
  title: string;
}

/** The ✔/⚠/⚑/🛡✔ chip. Completed rows only; null for anything else. */
export function verificationBadge(dl: AdlDownload): VerifBadge | null {
  if (dl.status !== 'completed') return null;
  switch (dl.verification_status) {
    case 'force_imported':
      return {
        className: 'verif-badge verif-force',
        glyph: '⚑',
        title:
          'Force-imported: accepted as best available candidate after repeated mismatches (version-mismatch fallback). Library AcoustID scans report these as informational.',
      };
    case 'unverified':
      return {
        className: 'verif-badge verif-unverified',
        glyph: '⚠',
        title:
          'Imported but not hard-verified (AcoustID could not confirm — e.g. cross-script metadata or no fingerprint match).',
      };
    case 'verified':
      return {
        className: 'verif-badge verif-ok',
        glyph: '✔',
        title: 'AcoustID verified: audio fingerprint matches the expected track.',
      };
    case 'human_verified':
      return {
        className: 'verif-badge verif-human',
        glyph: '🛡✔',
        title:
          'Human verified: you confirmed this file is the right track. The AcoustID scanner skips it.',
      };
    default:
      return null;
  }
}

export interface ReasonBadge {
  className: string;
  label: string;
  title: string;
}

/** The wordy FORCE-IMPORTED / ACOUSTID UNCONFIRMED chip in the review queue. */
export function reasonBadge(dl: AdlDownload): ReasonBadge | null {
  if (dl.verification_status === 'force_imported') {
    return {
      className: 'verif-reason-badge verif-rb-force',
      label: 'FORCE-IMPORTED',
      title:
        'Accepted as best candidate after the retry budget was exhausted (version-mismatch fallback)',
    };
  }
  if (dl.verification_status === 'unverified') {
    return {
      className: 'verif-reason-badge verif-rb-unv',
      label: 'ACOUSTID UNCONFIRMED',
      title:
        'AcoustID could not hard-confirm this file (ambiguous / cross-script / no fingerprint match)',
    };
  }
  return null;
}

/** Why an unverified row was flagged — the first line of its detail panel. */
export function unverifiedReasonText(dl: AdlDownload): string {
  return dl.verification_status === 'force_imported'
    ? 'Accepted as the best available candidate after the retry budget was exhausted (version-mismatch fallback). A library AcoustID scan reports these as informational.'
    : 'AcoustID could not hard-confirm this file (ambiguous / cross-script metadata / no fingerprint match). Imported, but not verified.';
}

// ── Quarantine ────────────────────────────────────────────────────────────

const QUARANTINE_TRIGGERS: Record<string, [string, string]> = {
  integrity: ['DURATION / INTEGRITY', 'verif-rb-int'],
  acoustid: ['ACOUSTID MISMATCH', 'verif-rb-force'],
  acoustid_unverified: ['ACOUSTID UNVERIFIED', 'verif-rb-unv'],
  bit_depth: ['BIT DEPTH FILTER', 'verif-rb-int'],
};

/** Trigger → [label, class]; anything unknown reads QUARANTINED. */
export function quarantineTrigger(trigger: string | undefined): [string, string] {
  return QUARANTINE_TRIGGERS[String(trigger ?? '')] ?? ['QUARANTINED', 'verif-rb-unv'];
}

/**
 * Sources that put their own service name in `source_username`.
 *
 * Anything else with a username came from Soulseek, where that field holds the
 * PEER's name — so it collapses to 'Soulseek' rather than showing a stranger's
 * handle as if it were a service.
 */
const STREAMING_SOURCES = [
  'youtube',
  'tidal',
  'qobuz',
  'hifi',
  'deezer_dl',
  'lidarr',
  'soundcloud',
  'amazon',
  'torrent',
  'usenet',
];

export function quarantineSourceLabel(entry: AdlQuarantineEntry): string {
  const username = String(entry.source_username ?? '').toLowerCase();
  if (STREAMING_SOURCES.includes(username)) return sourceLabel(username);
  return entry.source_username ? sourceLabel('soulseek') : '';
}

// ── Batch ETA ─────────────────────────────────────────────────────────────

export interface RateSample {
  t: number;
  done: number;
}

/** How many samples the rate window keeps. */
export const RATE_WINDOW = 8;

/**
 * Append a sample and return the completion rate in tracks/sec.
 *
 * A sample is only recorded when `done` actually CHANGED — otherwise a stalled
 * batch would accumulate identical samples and its rate would decay toward zero
 * while the window filled, making the ETA drift upward for no reason.
 *
 * Mutates `samples` in place (it is the caller's per-batch array) and returns 0
 * until there are two points to measure between.
 */
export function sampleRate(samples: RateSample[], done: number, now: number): number {
  const last = samples[samples.length - 1];
  if (!last || last.done !== done) samples.push({ t: now, done });
  while (samples.length > RATE_WINDOW) samples.shift();
  if (samples.length < 2) return 0;
  const first = samples[0];
  const latest = samples[samples.length - 1];
  const dt = (latest.t - first.t) / 1000;
  const dd = latest.done - first.done;
  return dt > 0 && dd > 0 ? dd / dt : 0;
}

/**
 * The ETA string on a batch card's stat line.
 *
 * A release download reports the downloader's own speed/size instead of a
 * track-completion estimate, because there is only one file in flight.
 */
export function batchEta(batch: AdlBatch, samples: RateSample[], now: number): string {
  if (batch.phase === 'album_downloading') {
    const bundle = batch.album_bundle ?? {};
    const bits: string[] = [];
    if (bundle.speed) bits.push(bundle.speed);
    if (bundle.downloaded && bundle.size) bits.push(`${bundle.downloaded} / ${bundle.size}`);
    return bits.join(' · ');
  }
  if (batch.phase !== 'downloading') return '';
  const total = batch.total || 0;
  const done = (batch.completed || 0) + (batch.failed || 0);
  const remaining = total - done;
  if (remaining <= 0) return '';
  const rate = sampleRate(samples, done, now);
  if (rate <= 0) return '';
  return `~${formatDuration(remaining / rate)} left`;
}
