/**
 * The inbox's pure half: which rows a filter shows, what a status means to
 * a person, which actions a row earns, and the small formatters the rows
 * share. Nothing here touches the network or the DOM.
 */

import type {
  ImportInboxFilter,
  ImportInboxFile,
  ImportInboxItem,
  ImportInboxStatus,
  ImportOutcomeSummary,
  ImportTrackOutcome,
} from './-import.types';

import { formatDuration, formatImportBytes } from './-import.helpers';

export type InboxTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger';

export interface InboxStatusMeta {
  label: string;
  tone: InboxTone;
  /** one line under the pill when the row has nothing better to say */
  hint: string;
}

export const INBOX_STATUS_META: Record<ImportInboxStatus, InboxStatusMeta> = {
  waiting: { label: 'Waiting', tone: 'neutral', hint: 'Not looked at yet' },
  identifying: { label: 'Identifying', tone: 'info', hint: 'Looking it up' },
  needs_review: { label: 'Needs review', tone: 'warning', hint: 'Probable match, wants a look' },
  needs_identify: {
    label: 'Needs identifying',
    tone: 'danger',
    hint: 'Could not work out what this is',
  },
  queued: { label: 'Queued', tone: 'info', hint: 'Approved, importing on the next pass' },
  importing: { label: 'Importing', tone: 'info', hint: 'Tagging and moving files' },
  imported: { label: 'Imported', tone: 'success', hint: 'In the library' },
  partial: {
    label: 'Partially imported',
    tone: 'warning',
    hint: 'Some tracks imported — the rest did not make it',
  },
  failed: { label: 'Failed', tone: 'danger', hint: 'The import did not finish' },
  dismissed: { label: 'Dismissed', tone: 'neutral', hint: 'Left in the import folder' },
};

/** statuses a person has to do something about, in the order they should see them. */
const ATTENTION_ORDER: ImportInboxStatus[] = ['needs_review', 'needs_identify', 'failed'];
const HISTORY: ImportInboxStatus[] = ['imported', 'partial', 'failed', 'dismissed'];

/**
 * Waiting counts as attention only while auto-import is OFF. With it on, a
 * fresh drop is about to be picked up (next scan, once its files stop
 * changing) and needs nobody; showing it under Needs attention read as
 * "something is wrong" the moment files landed.
 */
export function isAttention(status: ImportInboxStatus, workerRunning: boolean): boolean {
  if (status === 'waiting') return !workerRunning;
  return ATTENTION_ORDER.includes(status);
}

export function filterInboxItems(
  items: ImportInboxItem[],
  filter: ImportInboxFilter,
  workerRunning: boolean,
): ImportInboxItem[] {
  switch (filter) {
    case 'attention':
      return sortInbox(
        items.filter((item) => item.in_staging && isAttention(item.status, workerRunning)),
      );
    case 'history':
      return items.filter((item) => HISTORY.includes(item.status)).sort(byNewest);
    case 'all':
      return sortInbox(items);
  }
}

/** live work first, then what needs a person, then the rest; newest inside each */
const STATUS_RANK: ImportInboxStatus[] = [
  'importing',
  'queued',
  'identifying',
  'needs_review',
  'needs_identify',
  'partial',
  'failed',
  'waiting',
  'imported',
  'dismissed',
];

function byNewest(a: ImportInboxItem, b: ImportInboxItem): number {
  return String(b.processed_at || b.created_at || '').localeCompare(
    String(a.processed_at || a.created_at || ''),
  );
}

export function sortInbox(items: ImportInboxItem[]): ImportInboxItem[] {
  return [...items].sort((a, b) => {
    const rank = STATUS_RANK.indexOf(a.status) - STATUS_RANK.indexOf(b.status);
    if (rank !== 0) return rank;
    return byNewest(a, b) || a.name.localeCompare(b.name);
  });
}

export interface InboxCounts {
  attention: number;
  all: number;
  history: number;
}

export function countInbox(items: ImportInboxItem[], workerRunning: boolean): InboxCounts {
  return {
    attention: items.filter((item) => item.in_staging && isAttention(item.status, workerRunning))
      .length,
    all: items.length,
    history: items.filter((item) => HISTORY.includes(item.status)).length,
  };
}

export type InboxAction = 'approve' | 'identify' | 'dismiss' | 'retry';

/**
 * Which buttons a row shows. The rule is "what can a person do about this
 * state", not "what endpoints exist": a waiting item can be identified by
 * hand, a needs-review item can be approved or corrected, a failure retried
 * or corrected, and history rows earn nothing.
 */
export function inboxActions(item: ImportInboxItem): InboxAction[] {
  if (!item.in_staging) return [];
  switch (item.status) {
    case 'needs_review':
      return ['approve', 'identify', 'dismiss'];
    case 'needs_identify':
      return ['identify', 'dismiss'];
    case 'failed':
      return ['retry', 'identify'];
    case 'waiting':
      return ['identify'];
    default:
      return [];
  }
}

/** "12 tracks · FLAC · 48:12 · 412 MB", dropping the parts that are zero */
export function describeItemFiles(item: ImportInboxItem): string {
  const parts: string[] = [];
  parts.push(
    item.kind === 'single'
      ? '1 file'
      : `${item.file_count} track${item.file_count === 1 ? '' : 's'}`,
  );
  if (item.formats.length) parts.push(item.formats.join('/'));
  if (item.total_duration_ms) parts.push(formatDuration(item.total_duration_ms));
  if (item.total_size) parts.push(formatImportBytes(item.total_size));
  return parts.join(' · ');
}

/** "FLAC · 3:41 · 1,013 kbps · 31 MB" for one file */
export function describeFile(file: ImportInboxFile): string {
  const parts: string[] = [file.format];
  if (file.duration_ms) parts.push(formatDuration(file.duration_ms));
  if (file.bitrate) parts.push(formatBitrate(file.bitrate));
  if (file.size) parts.push(formatImportBytes(file.size));
  return parts.join(' · ');
}

export function formatBitrate(bitrate: number | null | undefined): string {
  if (!bitrate) return '';
  return `${Math.round(bitrate / 1000).toLocaleString()} kbps`;
}

/** The match summary a row shows: "9/12 tracks matched" or the live track. */
export function describeItemMatch(item: ImportInboxItem): string {
  if (item.live && item.live.track_total > 0) {
    return `Track ${item.live.track_index}/${item.live.track_total}${
      item.live.track_name ? `: ${item.live.track_name}` : ''
    }`;
  }
  if (item.match && item.match.total_tracks > 0) {
    return `${item.match.matched_count}/${item.match.total_tracks} tracks matched`;
  }
  return '';
}

/** what the import did, counted: the history row's summary, or tallied from
 * its tracks for rows written before the summary existed. null before it ran. */
export function importOutcomeCounts(item: ImportInboxItem): ImportOutcomeSummary | null {
  if (item.match?.summary) return item.match.summary;
  const ran = (item.match?.matches ?? []).filter((m) => m.import_status);
  if (!ran.length) return null;
  const count = (status: ImportTrackOutcome) =>
    ran.filter((m) => m.import_status === status).length;
  return {
    imported: count('completed'),
    upgraded: count('upgraded'),
    skipped_owned: count('skipped_owned'),
    failed: count('failed'),
  };
}

/** "12 imported · 47 already in library · 2 upgraded", '' before it ran */
export function describeImportOutcome(item: ImportInboxItem): string {
  const counts = importOutcomeCounts(item);
  if (!counts) return '';
  const parts: string[] = [];
  if (counts.imported) parts.push(`${counts.imported} imported`);
  if (counts.skipped_owned) parts.push(`${counts.skipped_owned} already in library`);
  if (counts.upgraded) parts.push(`${counts.upgraded} upgraded`);
  if (counts.failed) parts.push(`${counts.failed} failed`);
  return parts.join(' · ');
}

/** a run that found everything already in the library imported nothing, so
 * "Imported" would be the wrong word for it */
export function everythingWasOwned(item: ImportInboxItem): boolean {
  const counts = importOutcomeCounts(item);
  return Boolean(
    counts && counts.skipped_owned > 0 && !counts.imported && !counts.upgraded && !counts.failed,
  );
}

export const TRACK_OUTCOME_META: Record<ImportTrackOutcome, { label: string; tone: InboxTone }> = {
  completed: { label: 'Imported', tone: 'success' },
  upgraded: { label: 'Upgraded', tone: 'info' },
  skipped_owned: { label: 'Already in library', tone: 'neutral' },
  failed: { label: 'Failed', tone: 'danger' },
};

export interface ImportOutcomeLine {
  key: string;
  number: string;
  track: string;
  file: string;
  outcome: ImportTrackOutcome;
  why: string;
}

/** one line per track the import decided something about, in track order */
export function importOutcomeLines(item: ImportInboxItem): ImportOutcomeLine[] {
  return (item.match?.matches ?? [])
    .filter((m) => m.import_status)
    .map((m, index) => ({
      key: `${m.file_path || m.file}-${index}`,
      number: m.track_number != null && m.track_number !== '' ? String(m.track_number) : '',
      track: m.track_name || m.file,
      file: m.file,
      outcome: m.import_status as ImportTrackOutcome,
      why: (m.import_status === 'failed' ? m.import_error : m.import_reason) || '',
    }))
    .sort((a, b) => (Number(a.number) || 0) - (Number(b.number) || 0));
}

export function confidencePercent(item: ImportInboxItem): number | null {
  if (item.confidence == null) return null;
  return Math.round(item.confidence * 100);
}

export function confidenceTone(percent: number): 'high' | 'medium' | 'low' {
  if (percent >= 90) return 'high';
  if (percent >= 70) return 'medium';
  return 'low';
}

const METHOD_LABELS: Record<string, string> = {
  tags: 'from tags',
  folder_name: 'from folder name',
  acoustid: 'by fingerprint',
  filename: 'from filenames',
  exact_id: 'from embedded ids',
  manual: 'by hand',
  rematch_hint: 'by re-identify',
};

export function methodLabel(method: string | null | undefined): string {
  if (!method) return '';
  return METHOD_LABELS[method] ?? method.replace(/_/g, ' ');
}

/** "3 minutes ago" for the row's time column, blank when unknown */
export function timeAgo(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return '';
  const seconds = Math.max(0, Math.round((now - then) / 1000));
  if (seconds < 60) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

/** The row's subtitle: what it is, from the best thing we know. */
export function itemSubtitle(item: ImportInboxItem): string {
  if (item.kind === 'single') {
    return item.files[0]?.filename || item.folder_name;
  }
  return item.rel_path || item.folder_name;
}

/** Seconds until the worker's next pass, from its last scan and the interval. */
export function secondsToNextScan(
  lastScanIso: string | null | undefined,
  intervalSeconds: number,
  now: number = Date.now(),
): number | null {
  if (!lastScanIso) return null;
  const last = new Date(lastScanIso).getTime();
  if (!Number.isFinite(last)) return null;
  return Math.max(0, Math.round((last + intervalSeconds * 1000 - now) / 1000));
}
