/**
 * Pure helpers for the Tools page — transcribed 1:1 from the vanilla source.
 *
 * Every function here is a straight port of a named vanilla function; the
 * comments name it so the two can be diffed later. No behaviour is "tidied up"
 * on the way across, including the quirks — those are called out individually
 * and covered by tests, so a future change to one is a deliberate decision
 * rather than an accident.
 */

import type {
  BulkFixStatus,
  CacheHealthScore,
  CacheHealthStats,
  RepairJob,
  RepairJobRun,
} from './-tools.types';

// ── Duplicate "Keep Best" ranking ────────────────────────────────────────────
//
// From `_dupFmtRank` / `_dupKey` in enrichment.js (_renderFindingDetail's
// `duplicate_tracks` case). This MIRRORS core/library/duplicate_keep.py and has
// to stay mirrored: the backend docstring records that the two drifted once
// (both ranked bitrate-first, which kept an MP3 whenever the FLAC's bitrate was
// missing from the DB). -tools.core.dupe-parity.test.ts differential-tests this
// against a fixture generated from the Python.

const DUPLICATE_FORMAT_RANK: Record<string, number> = {
  flac: 10,
  wav: 9,
  aiff: 9,
  aif: 9,
  ape: 8,
  m4a: 7,
  ogg: 6,
  opus: 6,
  mp3: 5,
  aac: 5,
  wma: 3,
};

/** Quality rank by file extension; higher is better, unknown is 1. */
export function duplicateFormatRank(filePath: string | null | undefined): number {
  const extension = String(filePath || '')
    .split('.')
    .pop()
    ?.toLowerCase();
  return (extension && DUPLICATE_FORMAT_RANK[extension]) || 1;
}

export interface DuplicateTrackLike {
  id?: number | string | null;
  file_path?: string | null;
  bitrate?: number | null;
  duration?: number | null;
  track_number?: number | null;
}

/** Sort key for picking the keeper — higher tuple wins. Format tier FIRST, so
 *  lossless beats lossy even when the lossless copy has no bitrate recorded. */
export function duplicateSortKey(track: DuplicateTrackLike): [number, number, number, number] {
  return [
    duplicateFormatRank(track.file_path),
    track.bitrate || 0,
    track.duration || 0,
    track.track_number || 0,
  ];
}

/**
 * The copy to keep from a duplicate group, or null when the group is empty.
 *
 * Ties keep the EARLIER track, matching both the vanilla reduce (which only
 * swaps on a strict `>`) and Python's `max` (which returns the first maximum).
 */
export function pickDuplicateToKeep<T extends DuplicateTrackLike>(tracks: readonly T[]): T | null {
  if (!tracks.length) return null;
  return tracks.reduce((best, candidate) => {
    const bestKey = duplicateSortKey(best);
    const candidateKey = duplicateSortKey(candidate);
    for (let index = 0; index < bestKey.length; index += 1) {
      if (candidateKey[index] !== bestKey[index]) {
        return candidateKey[index] > bestKey[index] ? candidate : best;
      }
    }
    return best;
  }, tracks[0]);
}

// ── Repair job settings labels ───────────────────────────────────────────────

/** From `_prettifyRepairSettingKey` (enrichment.js). Full-key overrides first,
 *  then acronym fix-ups the naive Title Case would botch. */
const SETTING_FULL_KEY_LABELS: Record<string, string> = {
  deep_audio_verify: 'Deep Audio Verify (ffmpeg decode — CPU heavy)',
};

const SETTING_ACRONYMS: Record<string, string> = {
  eps: 'EPs',
  id: 'ID',
  url: 'URL',
  mb: 'MB',
  ac: 'AC',
  os: 'OS',
  api: 'API',
  mp3: 'MP3',
  flac: 'FLAC',
  cd: 'CD',
};

export function prettifyRepairSettingKey(key: string): string {
  const override = SETTING_FULL_KEY_LABELS[key];
  if (override) return override;
  return key
    .replace(/^_+/, '')
    .split('_')
    .map(
      (word) =>
        SETTING_ACRONYMS[word.toLowerCase()] || word.charAt(0).toUpperCase() + word.slice(1),
    )
    .join(' ');
}

/** A `_section_*` key is a group divider, not a setting; its VALUE is the title. */
export function isRepairSettingSection(key: string): boolean {
  return key.startsWith('_section_');
}

// ── Job card badges ──────────────────────────────────────────────────────────

export type RepairJobBadge =
  | { kind: 'pending'; count: number }
  | { kind: 'historical'; count: number }
  | { kind: 'none' };

/**
 * From the badge block in `loadRepairJobs`.
 *
 * Prefers the CURRENT pending count so the badge agrees with what the Findings
 * tab actually shows, and falls back to the last run's `findings_created` only
 * when pending is zero — otherwise a scan that found 372 duplicates and then had
 * them all bulk-fixed still reads "372 findings" against an empty Pending tab,
 * which looks like a bug.
 */
export function repairJobBadge(
  job: Pick<RepairJob, 'pending_findings_count' | 'last_run'>,
): RepairJobBadge {
  const pending = job.pending_findings_count || 0;
  if (pending > 0) return { kind: 'pending', count: pending };
  const lastScan = job.last_run ? job.last_run.findings_created || 0 : 0;
  if (lastScan > 0) return { kind: 'historical', count: lastScan };
  return { kind: 'none' };
}

export type RepairJobDot = 'running' | 'enabled' | 'disabled';

/** From the `dotClass` ternary in `loadRepairJobs`. Running wins over disabled. */
export function repairJobDot(job: Pick<RepairJob, 'is_running' | 'enabled'>): RepairJobDot {
  if (job.is_running) return 'running';
  return job.enabled ? 'enabled' : 'disabled';
}

/** From `cardClass`: note this is NOT the same ternary as the dot — an enabled,
 *  idle job gets no class at all rather than 'enabled'. */
export function repairJobCardClass(job: Pick<RepairJob, 'is_running' | 'enabled'>): string {
  if (job.is_running) return 'running';
  return job.enabled ? '' : 'disabled';
}

/** A job runs in dry-run mode when its settings say so; drives the flow badge. */
export function isRepairJobDryRun(job: Pick<RepairJob, 'settings'>): boolean {
  return Boolean(job.settings && (job.settings as Record<string, unknown>).dry_run === true);
}

// ── Findings ─────────────────────────────────────────────────────────────────

export const FINDING_SEVERITY_ICONS: Record<string, string> = {
  info: 'ℹ️',
  warning: '⚠️',
  // The backend's top severity is `error`; `critical` stays mapped because
  // older rows and the CSS class both use that word.
  error: '🔴',
  critical: '🔴',
};

export function findingSeverityIcon(severity: string | null | undefined): string {
  return FINDING_SEVERITY_ICONS[severity as string] || FINDING_SEVERITY_ICONS.info;
}

/** CSS modifier for a severity. The stylesheet has `.repair-finding-card.critical`
 *  and no `.error`, so the emitted `error` maps onto it rather than churning
 *  the stylesheet (and losing the styling for rows already stored either way). */
export function findingSeverityClass(severity: string | null | undefined): string {
  const value = String(severity || 'info');
  if (value === 'error' || value === 'critical') return 'critical';
  return value === 'warning' ? 'warning' : 'info';
}

export const FINDING_TYPE_LABELS: Record<string, string> = {
  dead_file: 'Dead File',
  orphan_file: 'Orphan',
  acoustid_mismatch: 'Wrong Song',
  acoustid_no_match: 'No Match',
  fake_lossless: 'Fake Lossless',
  duplicate_tracks: 'Duplicate',
  incomplete_album: 'Incomplete',
  path_mismatch: 'Path Mismatch',
  metadata_gap: 'Missing Metadata',
  missing_cover_art: 'Missing Art',
  track_number_mismatch: 'Track Number',
  missing_lyrics: 'Missing Lyrics',
  expired_download: 'Expired',
  missing_replaygain: 'No ReplayGain',
  replaygain_retag: 'RG Re-analyze',
  empty_folder: 'Empty Folder',
  missing_lossy_copy: 'No Lossy Copy',
  library_retag: 'Re-tag',
  quality_upgrade: 'Low Quality',
  short_preview_track: 'Preview Clip',
  genre_cleanup: 'Genres',
  genre_enrichment: 'Genre Enrichment',
  comma_artist_split: 'Comma Artist',
};

/** Unknown types fall back to the raw id with underscores spaced out. */
export function findingTypeLabel(findingType: string): string {
  return FINDING_TYPE_LABELS[findingType] || findingType.replace(/_/g, ' ');
}

/** Finding types with an automated fix, mapped to their button label. Note
 *  `missing_discography_track` is fixable but has NO entry in the type labels
 *  above — that asymmetry is in the vanilla and is preserved. */
export const FINDING_FIXABLE_TYPES: Record<string, string> = {
  dead_file: 'Re-download',
  orphan_file: 'Resolve',
  track_number_mismatch: 'Fix',
  missing_cover_art: 'Apply Art',
  missing_lyrics: 'Apply Lyrics',
  missing_replaygain: 'Apply RG',
  replaygain_retag: 'Re-analyze',
  empty_folder: 'Delete Folder',
  expired_download: 'Delete',
  metadata_gap: 'Apply',
  duplicate_tracks: 'Keep Best',
  incomplete_album: 'Auto-Fill',
  missing_lossy_copy: 'Convert',
  acoustid_mismatch: 'Fix',
  quality_upgrade: 'Upgrade',
  missing_discography_track: 'Add to Wishlist',
  library_retag: 'Apply Tags',
  short_preview_track: 'Re-download',
  genre_cleanup: 'Clean Genres',
  genre_enrichment: 'Apply Genres',
  comma_artist_split: 'Split Artists',
};

export function findingFixLabel(findingType: string): string | null {
  return FINDING_FIXABLE_TYPES[findingType] ?? null;
}

/** Types whose fix ends in a re-download — but only while the finding names a
 *  catalogue track. The corruption detector also walks the library folders, so
 *  it raises rows with `entity_type: 'file'` and no id for audio no lib2 row
 *  points at. Nothing can be re-requested for those, so the fix is a plain
 *  delete; saying "Re-download" was a promise the backend answered with
 *  "No track ID associated with this finding". */
const SUBJECTLESS_DELETE_ONLY_TYPES = new Set(['corrupt_audio', 'short_preview_track']);

/** The button label for ONE finding row — `findingFixLabel` by type, unless the
 *  row has no track behind it and the type's verb only makes sense with one. */
export function findingRowFixLabel(finding: {
  finding_type: string;
  entity_type?: string | null;
  entity_id?: string | number | null;
}): string | null {
  if (!finding.entity_id && SUBJECTLESS_DELETE_ONLY_TYPES.has(finding.finding_type)) {
    return 'Delete File';
  }
  return findingFixLabel(finding.finding_type);
}

export const FINDING_ACTION_LABELS: Record<string, string> = {
  removed_db_entry: 'Entry Removed',
  added_to_wishlist: 'Wishlisted',
  deleted_file: 'File Deleted',
  genres_cleaned: 'Genres Cleaned',
  artists_split: 'Artists Split',
  already_gone: 'Already Gone',
  fixed_track_number: 'Track # Fixed',
  applied_cover_art: 'Art Applied',
  applied_metadata: 'Metadata Applied',
  applied_lyrics: 'Lyrics Applied',
  deleted_expired: 'Deleted',
  removed_duplicates: 'Duplicates Removed',
  genres_applied: 'Genres Applied',
};

/** The badge shown on a non-pending finding: the user action if we have a label
 *  for it, else the raw status. */
export function findingStatusBadge(
  status: string,
  userAction: string | null | undefined,
): string | null {
  if (status === 'pending') return null;
  return FINDING_ACTION_LABELS[userAction as string] || status;
}

/** From `loadRepairFindings`: the path shown under a finding, first hit wins. */
export function findingFilePath(finding: {
  file_path?: string | null;
  details?: Record<string, unknown> | null;
}): string {
  const details = finding.details || {};
  return (
    finding.file_path || (details.original_path as string) || (details.file_path as string) || ''
  );
}

// ── Pagination ───────────────────────────────────────────────────────────────

export const REPAIR_PAGE_SIZE_OPTIONS = [30, 60, 100] as const;
export const REPAIR_DEFAULT_PAGE_SIZE = 30;

/** From the IIFE behind `REPAIR_FINDINGS_PAGE_SIZE` — anything not in the
 *  allowed set (including a NaN parse) falls back to 30. */
export function normalizeFindingsPageSize(raw: string | number | null | undefined): number {
  const size = typeof raw === 'number' ? raw : Number.parseInt(String(raw ?? ''), 10);
  return (REPAIR_PAGE_SIZE_OPTIONS as readonly number[]).includes(size)
    ? size
    : REPAIR_DEFAULT_PAGE_SIZE;
}

export interface FindingsPagination {
  totalPages: number;
  /** Numbered buttons to render, 0-based. Empty when there's a single page. */
  pages: number[];
  showFirst: boolean;
  showFirstEllipsis: boolean;
  showLast: boolean;
  showLastEllipsis: boolean;
  showPrev: boolean;
  showNext: boolean;
}

/**
 * From `renderRepairFindingsPagination`. The window is 7 wide, anchored 3 before
 * the current page and shifted back when it would overrun the end.
 */
export function findingsPagination(
  total: number,
  currentPage: number,
  pageSize: number,
): FindingsPagination {
  const totalPages = Math.ceil(total / pageSize);
  if (totalPages <= 1) {
    return {
      totalPages,
      pages: [],
      showFirst: false,
      showFirstEllipsis: false,
      showLast: false,
      showLastEllipsis: false,
      showPrev: false,
      showNext: false,
    };
  }

  let startPage = Math.max(0, currentPage - 3);
  const endPage = Math.min(totalPages, startPage + 7);
  if (endPage - startPage < 7) startPage = Math.max(0, endPage - 7);

  const pages: number[] = [];
  for (let page = startPage; page < endPage; page += 1) pages.push(page);

  return {
    totalPages,
    pages,
    showFirst: startPage > 0,
    showFirstEllipsis: startPage > 1,
    showLast: endPage < totalPages,
    showLastEllipsis: endPage < totalPages - 1,
    showPrev: currentPage > 0,
    showNext: currentPage < totalPages - 1,
  };
}

// ── Mass-orphan safety gate ──────────────────────────────────────────────────

/** From core.js. Deliberately duplicated as a constant here so the React page
 *  doesn't depend on a vanilla global — the value must stay in step. */
export const MASS_ORPHAN_THRESHOLD = 20;

/**
 * Whether a bulk orphan fix needs the type-the-phrase dialog.
 *
 * From `_isMassOrphanFix`: the count alone isn't enough — at least one visible
 * finding must carry the backend's `mass_orphan` flag (set when >50% of scanned
 * files look like orphans, i.e. probably a path mismatch rather than real
 * orphans). The vanilla reads that flag off the DOM; the caller passes it in
 * here instead.
 */
export function isMassOrphanFix(
  jobId: string | null | undefined,
  count: number,
  anyFindingFlaggedMassOrphan: boolean,
): boolean {
  if (count <= MASS_ORPHAN_THRESHOLD) return false;
  if (jobId === 'orphan_file_detector' || !jobId) return anyFindingFlaggedMassOrphan;
  return false;
}

// ── Cache health ─────────────────────────────────────────────────────────────

/** From the shared scoring expression in `_loadCacheHealthStats` and
 *  `openCacheHealthModal` (they compute it identically). */
export function cacheHealthScore(
  stats: Pick<CacheHealthStats, 'junk_entities' | 'stale_mb_nulls'>,
): CacheHealthScore {
  if (stats.junk_entities === 0 && stats.stale_mb_nulls === 0) return 'healthy';
  return stats.junk_entities > 50 ? 'poor' : 'fair';
}

export function cacheHealthLabel(score: CacheHealthScore): string {
  if (score === 'healthy') return 'Healthy';
  return score === 'fair' ? 'Needs Cleanup' : 'Needs Attention';
}

/** The modal words the same score differently from the inline bar. */
export function cacheHealthModalLabel(score: CacheHealthScore): string {
  if (score === 'healthy') return 'Cache is healthy';
  return score === 'fair' ? 'Minor issues detected' : 'Cleanup recommended';
}

const CACHE_SOURCE_COLORS: Record<string, string> = {
  spotify: '#1DB954',
  itunes: '#FC3C44',
  deezer: '#A238FF',
  musicbrainz: '#BA478F',
};

export function cacheSourceColor(source: string): string {
  return CACHE_SOURCE_COLORS[source] || '#666';
}

export function cacheSourceLabel(source: string): string {
  return source === 'musicbrainz' ? 'MusicBrainz' : source;
}

/** From the by-source bar block: MusicBrainz is folded in from its own total,
 *  and every bar is scaled against the largest source (min 1, so an all-zero
 *  set can't divide by zero). */
export function cacheSourceBars(
  stats: Pick<CacheHealthStats, 'by_source' | 'total_musicbrainz'>,
): Array<{ source: string; label: string; count: number; percent: number; color: string }> {
  const sources: Record<string, number> = { ...stats.by_source };
  if (stats.total_musicbrainz) sources.musicbrainz = stats.total_musicbrainz;
  const max = Math.max(...Object.values(sources), 1);
  return Object.entries(sources).map(([source, count]) => ({
    source,
    label: cacheSourceLabel(source),
    count,
    percent: Math.round((count / max) * 100),
    color: cacheSourceColor(source),
  }));
}

// ── Formatting ───────────────────────────────────────────────────────────────

/** From `_formatFileSize`. Note 0 bytes renders as '-', not '0 B'. */
export function formatFileSize(bytes: number | null | undefined): string {
  if (!bytes) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

/** From `formatCacheAge` (wishlist-tools.js). Compact form: 'now', '5m', '3h',
 *  '2d', '4mo'. Months are a flat 30 days. */
/**
 * Parse a timestamp the way its WRITER meant it.
 *
 * Every timestamp on this page comes from SQLite's CURRENT_TIMESTAMP, which
 * is UTC and formatted `YYYY-MM-DD HH:MM:SS` — no `T`, no zone. JS parses
 * exactly that shape as LOCAL time, so west of Greenwich every run and every
 * finding was read as happening in the future: ages came out negative and
 * printed as "now", and a run from this morning claimed to be minutes old.
 * That is what made the run history unreadable.
 *
 * Anything already carrying a `T`, a `Z` or an offset is a real ISO string
 * and is left alone.
 */
export function parseDbTimestamp(timestamp: string): number {
  const bare = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d+)?$/.test(timestamp.trim());
  return new Date(bare ? `${timestamp.trim().replace(' ', 'T')}Z` : timestamp).getTime();
}

export function formatCacheAge(
  timestamp: string | null | undefined,
  now: number = Date.now(),
): string {
  if (!timestamp) return '—';
  const minutes = Math.floor((now - parseDbTimestamp(timestamp)) / 60000);
  // A future stamp is clock skew, not a negative age. Say "now" rather than
  // printing "-3h" at somebody.
  if (minutes < 1) return 'now';
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d`;
  return `${Math.floor(days / 30)}mo`;
}

/**
 * From `timeAgo` (stats-automations.js), used by the backup list.
 *
 * The bare-timestamp branch is load-bearing: backups are written with
 * `datetime.utcfromtimestamp(...)` (web_server.py, list_backups_endpoint), i.e.
 * naive UTC, so without the 'Z' the browser reads them as LOCAL and every backup
 * looks hours off. `includes('-', 10)` looks for a timezone offset AFTER the date
 * part, so the date's own dashes don't count as one.
 *
 * IMPORTANT — the opposite is true elsewhere, and "fixing" it would break it:
 * `last_full_refresh` is written with `datetime.now().isoformat()`
 * (music_database.py), i.e. naive LOCAL, and the DB-updater card parses it with a
 * bare `new Date(...)`. Local-written + local-parsed is already correct there.
 * Do NOT normalise that one to UTC when the card is ported.
 */
export function timeAgo(dateStr: string | null | undefined, now: number = Date.now()): string {
  if (!dateStr) return '';
  let stamp = dateStr;
  if (!stamp.includes('Z') && !stamp.includes('+') && !stamp.includes('-', 10)) stamp += 'Z';
  const seconds = Math.floor((now - new Date(stamp).getTime()) / 1000);
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

/** From `_renderScoreBar`: a 0..1 score to a percent plus its band class. */
export function scoreBar(value: number | null | undefined): {
  percent: number;
  band: 'good' | 'warn' | 'bad';
} {
  const percent = Math.round((value || 0) * 100);
  const band = percent >= 80 ? 'good' : percent >= 50 ? 'warn' : 'bad';
  return { percent, band };
}

/** From `updateBackupManagerUI` — 'Never'/'—' when there are no backups. */
export function backupSummary(
  backups: readonly { created: string; size_mb: number }[] | null | undefined,
  now: number = Date.now(),
): { lastBackup: string; latestSize: string } {
  if (backups && backups.length > 0) {
    return { lastBackup: timeAgo(backups[0].created, now), latestSize: `${backups[0].size_mb} MB` };
  }
  return { lastBackup: 'Never', latestSize: '—' };
}

/**
 * From `renderBackupList`: the API returns naive UTC, so a 'Z' is appended when
 * the string doesn't already carry one. NB this check is weaker than `timeAgo`'s
 * — it only looks for 'Z', not for a `+hh:mm` offset. Kept as-is.
 */
export function backupTimestamp(created: string): Date {
  return new Date(created + (created.includes('Z') ? '' : 'Z'));
}

/** From `updateDuplicateCleanProgressUI` — GB above 1024 MB, else MB. Note the
 *  stat row uses 2 decimals and the completion toast uses 1 for MB. */
export function formatFreedSpace(spaceMb: number, decimals: 1 | 2 = 2): string {
  if (spaceMb >= 1024) return `${(spaceMb / 1024).toFixed(2)} GB`;
  return `${spaceMb.toFixed(decimals)} MB`;
}

/** From `loadMetadataCacheStats`: the card sums the four first-party sources
 *  only — discogs and musicbrainz are deliberately not counted here. */
export function metadataCacheCardCount(bucket: Record<string, number> | null | undefined): number {
  const counts = bucket || {};
  return (
    (counts.spotify || 0) + (counts.itunes || 0) + (counts.deezer || 0) + (counts.beatport || 0)
  );
}

// ── Job card meta + settings ─────────────────────────────────────────────────

/**
 * The `metaParts` line under a job's description, joined with ' · ' by the card.
 *
 * From `loadRepairJobs`. Note the two different "nothing yet" answers: Last is
 * 'Never', but Next is 'Pending' when the job is enabled and '-' when it isn't —
 * a disabled job has no next run to wait for.
 */
export function repairJobMeta(
  job: Pick<RepairJob, 'enabled' | 'next_run' | 'last_run'>,
  now: number = Date.now(),
): string[] {
  const parts: string[] = [];
  parts.push('Last: ' + (job.last_run ? formatCacheAge(job.last_run.finished_at, now) : 'Never'));
  parts.push(
    'Next: ' + (job.next_run ? formatCacheAge(job.next_run, now) : job.enabled ? 'Pending' : '-'),
  );
  if (job.last_run) {
    parts.push(`Scanned: ${(job.last_run.items_scanned || 0).toLocaleString()}`);
    if (job.last_run.auto_fixed) parts.push(`Fixed: ${job.last_run.auto_fixed}`);
    if (job.last_run.duration_seconds) {
      parts.push(`${job.last_run.duration_seconds.toFixed(1)}s`);
    }
  }
  return parts;
}

export type RepairSettingInput =
  | { kind: 'section'; title: string }
  | { kind: 'select'; options: string[] }
  | { kind: 'checkbox' }
  | { kind: 'number'; allowNegative: boolean }
  | { kind: 'text' };

/**
 * How one settings row renders. From the `settingsRows` map in `loadRepairJobs`.
 *
 * The negative case is real: a number setting whose current value is below zero
 * gets no `min`, because some thresholds (e.g. a dB offset) are legitimately
 * negative and clamping them at 0 would make them un-editable.
 */
export function repairSettingInput(
  key: string,
  value: unknown,
  options?: unknown[] | null,
): RepairSettingInput {
  if (isRepairSettingSection(key)) return { kind: 'section', title: String(value) };
  if (Array.isArray(options) && options.length) {
    return { kind: 'select', options: options.map((option) => String(option)) };
  }
  if (typeof value === 'boolean') return { kind: 'checkbox' };
  if (typeof value === 'number') return { kind: 'number', allowNegative: value < 0 };
  return { kind: 'text' };
}

/** `saveRepairJobSettings` coerces 'true'/'false' text back to booleans. */
export function coerceRepairSettingValue(raw: string): string | boolean {
  if (raw === 'true') return true;
  if (raw === 'false') return false;
  return raw;
}

// ── History ──────────────────────────────────────────────────────────────────

export type RepairHistoryStatus = 'success' | 'error' | 'running';

/** From `loadRepairHistory`. Anything that is neither completed nor failed —
 *  including a run still in flight — shows as 'running'. */
export function repairHistoryStatusClass(status: string | null | undefined): RepairHistoryStatus {
  if (status === 'completed') return 'success';
  if (status === 'failed') return 'error';
  return 'running';
}

/** The stat pills on a history row; scanned is always shown, the rest only when
 *  non-zero. */
export function repairHistoryStats(
  run: Pick<RepairJobRun, 'items_scanned' | 'findings_created' | 'auto_fixed' | 'errors'>,
): Array<{ kind: string; count: number; label: string }> {
  const stats = [{ kind: '', count: run.items_scanned || 0, label: 'scanned' }];
  if (run.findings_created) {
    stats.push({ kind: 'findings', count: run.findings_created, label: 'findings' });
  }
  if (run.auto_fixed) stats.push({ kind: 'fixed', count: run.auto_fixed, label: 'fixed' });
  if (run.errors) stats.push({ kind: 'errors', count: run.errors, label: 'errors' });
  return stats;
}

// ── Finding detail ───────────────────────────────────────────────────────────

/** One `_gridRows` row: label, value, optional value class. */
export type FindingDetailRow = [key: string, value: string, cls?: string];

type Details = Record<string, unknown>;

/** Detail values are scalars in practice; the cast tells the linter so. An
 *  unexpected object still stringifies exactly as the vanilla's `String(v)` did. */
type Scalar = string | number | boolean | bigint | null | undefined;
const str = (v: unknown): string => (v == null ? '' : String(v as Scalar));
const num = (v: unknown): number => (typeof v === 'number' ? v : Number(v) || 0);
const arr = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);

/**
 * From the `fake_lossless` branch. The bar is only drawn when BOTH the detected
 * cutoff and the expected minimum are present and non-zero; the nyquist fallback
 * chain (`nyquist_khz` → `sample_rate / 2000` → 22.05) is the vanilla's.
 */
export function fakeLosslessSpectrum(
  details: Details | null | undefined,
): { cutoff: number; expectedMin: number; cutoffPct: number; expectedPct: number } | null {
  const d = details || {};
  const cutoff = num(d.detected_cutoff_khz);
  const expectedMin = num(d.expected_min_khz);
  if (!cutoff || !expectedMin) return null;
  const nyquist = num(d.nyquist_khz) || (d.sample_rate ? num(d.sample_rate) / 2000 : 22.05);
  return {
    cutoff,
    expectedMin,
    cutoffPct: Math.min(100, Math.round((cutoff / nyquist) * 100)),
    expectedPct: Math.min(100, Math.round((expectedMin / nyquist) * 100)),
  };
}

/** From the `incomplete_album` branch — the completion bar is skipped entirely
 *  when the expected track count is missing or zero. */
export function incompleteAlbumCompletion(
  details: Details | null | undefined,
): { actual: number; expected: number; percent: number } | null {
  const d = details || {};
  const actual = num(d.actual_tracks);
  const expected = num(d.expected_tracks);
  if (expected <= 0) return null;
  return { actual, expected, percent: Math.round((actual / expected) * 100) };
}

export interface LibraryRetagTrack {
  label: string;
  /** Shown under the label only when it differs from it — i.e. the track had a
   *  title, so the label is NOT already the filename. */
  filename: string | null;
  rows: FindingDetailRow[];
}

export interface LibraryRetagDetail {
  meta: string;
  /** True when nothing would change tag-wise but a cover refresh would happen. */
  coverOnly: boolean;
  tracks: LibraryRetagTrack[];
  overflow: number;
  unmatched: string[];
}

/** Cap from the vanilla — huge albums would otherwise render thousands of rows. */
export const LIBRARY_RETAG_TRACK_CAP = 40;

/** From the `library_retag` branch. An empty `old` renders as ∅ so a blank tag
 *  being filled in is visible rather than looking like a no-op. */
export function libraryRetagDetail(details: Details | null | undefined): LibraryRetagDetail {
  const d = details || {};
  const meta: string[] = [];
  if (d.source) meta.push(`Source: ${str(d.source)}`);
  if (d.mode) meta.push(`Mode: ${str(d.mode)}`);
  if (d.cover_action) meta.push(`Cover: ${str(d.cover_action)}`);

  const all = arr<{ file_path?: string; title?: string; changes?: Record<string, unknown> }>(
    d.tracks,
  );
  const changed = all.filter((t) => t.changes && Object.keys(t.changes).length > 0);

  const tracks = changed.slice(0, LIBRARY_RETAG_TRACK_CAP).map((t) => {
    const filename = (t.file_path || '').split(/[\\/]/).pop() || '';
    const label = t.title || filename || 'Unknown';
    const rows: FindingDetailRow[] = Object.entries(t.changes || {}).map(([field, change]) => {
      const c = (change || {}) as { old?: unknown; new?: unknown };
      const before = c.old === '' || c.old == null ? '∅' : str(c.old);
      return [field.replace(/_/g, ' '), `${before}   →   ${str(c.new)}`, 'highlight'];
    });
    return { label, filename: filename && filename !== label ? filename : null, rows };
  });

  return {
    meta: meta.join('  ·  '),
    coverOnly: changed.length === 0 && Boolean(d.cover_action),
    tracks,
    overflow: Math.max(0, changed.length - LIBRARY_RETAG_TRACK_CAP),
    unmatched: arr<string>(d.unmatched),
  };
}

export interface CommaSplitChip {
  name: string;
  /** The trailing verification mark, '' when the part is neither in the library
   *  nor verified against a source. */
  mark: string;
  inLibrary: boolean;
  libraryArtistId: string | null;
}

/** From the `comma_artist_split` branch. When the backend sent no per-part
 *  resolution we fall back to the bare split names, all unverified. */
export function commaSplitChips(details: Details | null | undefined): CommaSplitChip[] {
  interface ResolvedPart {
    name?: string;
    in_library?: boolean;
    verified_via?: string;
    library_artist_id?: string | number | null;
  }
  const d = details || {};
  const resolution = arr<ResolvedPart>(d.parts_resolution);
  const source: ResolvedPart[] = resolution.length
    ? resolution
    : arr<string>(d.split_artists).map((name) => ({ name }));

  return source.map((p) => {
    const inLibrary = Boolean(p.in_library);
    const id = p.library_artist_id;
    return {
      name: str(p.name),
      mark: inLibrary ? ' ✓ in your library' : p.verified_via ? ` ✓ ${p.verified_via}` : '',
      inLibrary,
      libraryArtistId: id != null && id !== '' ? String(id) : null,
    };
  });
}

/** Cap on the comma-split track list, same value and intent as the retag cap. */
export const COMMA_SPLIT_TRACK_CAP = 40;

/**
 * The `default:` arm — dump every scalar detail key as a row. Nested objects are
 * skipped (they have no sensible one-line form) and so are the `*_thumb_url`
 * keys, which are already rendered as images by the media strip.
 */
export function genericDetailRows(details: Details | null | undefined): FindingDetailRow[] {
  return Object.entries(details || {})
    .filter(([k, v]) => typeof v !== 'object' && !k.endsWith('_thumb_url'))
    .map(([k, v]) => [k.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase()), String(v)]);
}

// ── Bulk fix ─────────────────────────────────────────────────────────────────

export interface FindingsBulkBarState {
  showBar: boolean;
  countLabel: string;
  showFixAll: boolean;
  fixAllLabel: string;
  selectAllChecked: boolean;
  selectAllIndeterminate: boolean;
}

/**
 * From `_updateFindingsBulkBar`. "Fix All" only appears once the whole page is
 * selected AND there are more matching findings than that page holds — it is the
 * escape hatch from per-page selection to the filter-wide background run.
 */
export function findingsBulkBarState(
  selectedCount: number,
  cardsOnPage: number,
  total: number,
): FindingsBulkBarState {
  const allPageSelected = selectedCount > 0 && selectedCount >= cardsOnPage;
  return {
    showBar: selectedCount > 0,
    countLabel: selectedCount > 0 ? `${selectedCount} selected` : '',
    showFixAll: total > 0 && allPageSelected && total > selectedCount,
    fixAllLabel: `Fix All ${total}`,
    selectAllChecked: cardsOnPage > 0 && selectedCount >= cardsOnPage,
    selectAllIndeterminate: selectedCount > 0 && selectedCount < cardsOnPage,
  };
}

/**
 * From the tail of `_watchBulkFixRun`. A stopped run keeps the same sentence with
 * its first letter lowercased and a prefix in front — hence the charAt dance.
 * Severity is keyed on `fixed`, so a run that fixed nothing reports as an error
 * even when nothing actually failed.
 */
export function bulkFixRunMessage(
  status: Pick<BulkFixStatus, 'fixed' | 'failed' | 'total' | 'stopped' | 'errors'>,
): { message: string; type: 'success' | 'error' } {
  const fixed = status.fixed || 0;
  const failed = status.failed || 0;
  let message = `Fixed ${fixed}${failed ? `, ${failed} failed` : ''} of ${status.total || 0}`;
  if (status.stopped) {
    message = `Bulk fix stopped — ${message.charAt(0).toLowerCase()}${message.slice(1)}`;
  }
  const errors = status.errors;
  if (errors && errors.length > 0) message += `: ${errors[0].error}`;
  return { message, type: fixed > 0 ? 'success' : 'error' };
}

/** From the tail of `bulkFixFindings` — the per-id loop's own summary. Note it
 *  omits the "of N" that the background run reports. */
export function bulkFixLoopMessage(
  fixed: number,
  failed: number,
  lastError: string,
): { message: string; type: 'success' | 'error' } {
  let message = `Fixed ${fixed}${failed ? `, ${failed} failed` : ''}`;
  if (failed && lastError) message += `: ${lastError}`;
  return { message, type: fixed > 0 ? 'success' : 'error' };
}
