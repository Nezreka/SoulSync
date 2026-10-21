/**
 * Pure bits shared by the music and video download cards: time stamps, the
 * format tag, and the step list the detail sheet draws.
 *
 * Kept free of React so every rule here is a plain unit test.
 */

import { rejectionSummary } from '@/features/downloads/decisions';

import type { AdlDownload, AdlTaskDetail } from './-adl.types';
import type { VdlDownload } from './-vdl.types';

import { formatBytes, formatDuration, formatSpeed, statusClass } from './-adl.helpers';
import { vdlFmtSize, vdlFmtSpeed, vdlParseCtx } from './-vdl.helpers';

// ── time ──────────────────────────────────────────────────────────────────

/**
 * A server stamp to epoch ms, or null.
 *
 * sqlite's CURRENT_TIMESTAMP and the video monitor both write utc as
 * "YYYY-MM-DD HH:MM:SS" with no zone, so a bare stamp is read as utc. a
 * number is epoch seconds (task status_change_time) unless it's already ms.
 */
export function parseStamp(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || value <= 0) return null;
    return value < 1e12 ? value * 1000 : value;
  }
  const text = String(value).trim();
  const bare = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$/.exec(text);
  if (bare) {
    const [, y, mo, d, h, mi, s] = bare;
    return Date.UTC(+y, +mo - 1, +d, +h, +mi, +(s || 0));
  }
  const ms = Date.parse(text);
  return Number.isFinite(ms) ? ms : null;
}

/** "3:01 AM", in the viewer's own clock. */
export function clockTime(ms: number | null): string {
  if (ms === null) return '';
  return new Date(ms).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

/** local yyyy-mm-dd, the key the history sections group on. */
export function dayKey(ms: number | null): string {
  if (ms === null) return '';
  const d = new Date(ms);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** "Today" / "Yesterday" / "Mon, Oct 18" / "Oct 18, 2025". */
export function dayLabel(ms: number | null, now: number): string {
  if (ms === null) return 'Earlier';
  const key = dayKey(ms);
  if (key === dayKey(now)) return 'Today';
  if (key === dayKey(now - 86_400_000)) return 'Yesterday';
  const d = new Date(ms);
  if (d.getFullYear() === new Date(now).getFullYear()) {
    return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
  }
  return d.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
}

/** "just now" / "12m ago" / "3h ago" / "yesterday" / "4 days ago" / "Oct 2". */
export function relTime(ms: number | null, now: number): string {
  if (ms === null) return '';
  const mins = Math.floor((now - ms) / 60_000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  if (dayKey(ms) === dayKey(now)) return `${Math.floor(mins / 60)}h ago`;
  if (dayKey(ms) === dayKey(now - 86_400_000)) return 'yesterday';
  const days = Math.round((now - ms) / 86_400_000);
  if (days < 7) return `${days} days ago`;
  return new Date(ms).toLocaleDateString([], { month: 'short', day: 'numeric' });
}

/** a step's time: the clock if it was today, otherwise the day. */
export function stepTime(ms: number | null, now: number): string {
  if (ms === null) return '';
  return dayKey(ms) === dayKey(now) ? clockTime(ms) : dayLabel(ms, now);
}

// ── format tag ────────────────────────────────────────────────────────────

const MIME_FORMATS: Record<string, string> = {
  'audio/mpeg': 'MP3',
  'audio/mp3': 'MP3',
  'audio/mp4': 'M4A',
  'audio/x-m4a': 'M4A',
  'audio/aac': 'AAC',
  'audio/ogg': 'OGG',
  'audio/opus': 'OPUS',
  'audio/flac': 'FLAC',
  'audio/x-flac': 'FLAC',
  'audio/wav': 'WAV',
  'audio/x-wav': 'WAV',
};

/**
 * The quality tag text. podcasts store the feed's mime type as their quality,
 * so "audio/mpeg" turns into "MP3" here instead of showing up raw.
 */
export function formatTag(quality: string | null | undefined): string {
  const q = String(quality || '').trim();
  if (!q) return '';
  return MIME_FORMATS[q.toLowerCase()] || q;
}

/** a lossless or hi-res tag gets the accent, everything else stays grey. */
export function isHighQuality(tag: string): boolean {
  return /flac|alac|wav|aiff|2160p|4k|remux/i.test(tag);
}

// ── the step list ─────────────────────────────────────────────────────────

export type StepState = 'done' | 'now' | 'warn' | 'fail' | 'todo';

export interface JourneyStep {
  key: string;
  state: StepState;
  title: string;
  detail?: string;
  time?: string;
}

const QUARANTINE_WORDS = [
  'integrity check failed',
  'bit depth filter',
  'verification failed',
  'quarantin',
];

/** "Not found on 4 sources" + "Rejected: …" → "Not found on 4 sources. Rejected: …" */
function sentences(parts: string[]): string {
  return parts
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => (/[.!?]$/.test(p) ? p : `${p}.`))
    .join(' ');
}

function sourceOf(dl: AdlDownload, detail?: AdlTaskDetail | null): string {
  return dl.download_source || dl.live_detail?.source || detail?.source || '';
}

/** "1.2 of 41 MB · 1.8 MB/s · 12s left", from the live narration. */
export function transferLine(dl: AdlDownload): string {
  const d = dl.live_detail;
  if (!d) return '';
  const bits: string[] = [];
  if (d.size)
    bits.push(d.bytes ? `${formatBytes(d.bytes)} of ${formatBytes(d.size)}` : formatBytes(d.size));
  const speed = formatSpeed(d.speed);
  if (speed) bits.push(speed);
  const eta = transferEta(dl);
  if (eta) bits.push(`${eta} left`);
  return bits.join(' · ');
}

/** seconds left on the transfer, as "12s" / "3m", or '' when unknown. */
export function transferEta(dl: AdlDownload): string {
  const d = dl.live_detail;
  if (!d?.size || !d.speed || d.speed <= 0) return '';
  const left = d.size - (d.bytes || 0);
  return left > 0 ? formatDuration(left / d.speed) : '';
}

function searchLine(dl: AdlDownload): string {
  const d = dl.live_detail;
  if (!d) return 'Looking for a match';
  const bits: string[] = [];
  if (d.source) bits.push(`Trying ${d.source}`);
  if (d.query_count) bits.push(`query ${(d.query_index ?? 0) + 1} of ${d.query_count}`);
  if (d.results != null) bits.push(`${d.results} result${d.results === 1 ? '' : 's'} so far`);
  let line = bits.join(' · ') || 'Looking for a match';
  if (d.exhausted_sources?.length) line += `. Nothing usable on ${d.exhausted_sources.join(', ')}.`;
  return line;
}

function foundLine(dl: AdlDownload, detail?: AdlTaskDetail | null): string {
  const d = dl.live_detail;
  const bits: string[] = [];
  if (d?.candidate_count)
    bits.push(`candidate ${(d.candidate_index ?? 0) + 1} of ${d.candidate_count}`);
  if (d?.picked?.quality) bits.push(d.picked.quality.toUpperCase());
  const conf = d?.picked?.confidence;
  // the engine sends 0..1; guard a percent anyway so it can't read 9600%
  if (conf != null && conf > 0) bits.push(`${Math.round(conf <= 1 ? conf * 100 : conf)}% match`);
  const dec = detail?.decision;
  if (!bits.length && dec?.accepted_total != null) {
    bits.push(`${dec.accepted_total} passed, ${dec.rejected_total || 0} rejected`);
  }
  return bits.join(' · ');
}

function downloadingLine(dl: AdlDownload): string {
  const d = dl.live_detail;
  if (d?.slskd_state && /queued/i.test(d.slskd_state)) {
    const who = d.username ? `${d.username}'s` : "the peer's";
    const waited = d.queued_seconds ? ` for ${formatDuration(d.queued_seconds)}` : '';
    return `Waiting in ${who} queue${waited}`;
  }
  const line = transferLine(dl);
  const peer = d?.picked;
  const bits = [line];
  if (peer?.free_upload_slots != null) bits.push(`${peer.free_upload_slots} free slots`);
  return bits.filter(Boolean).join(' · ') || 'Starting the transfer';
}

function verifyStep(dl: AdlDownload, detail?: AdlTaskDetail | null): JourneyStep | null {
  switch (dl.verification_status) {
    case 'verified':
      return {
        key: 'verify',
        state: 'done',
        title: 'Verified',
        detail: 'AcoustID fingerprint matched',
      };
    case 'human_verified':
      return {
        key: 'verify',
        state: 'done',
        title: 'You verified it',
        detail: 'The AcoustID scanner skips it now',
      };
    case 'unverified':
      return {
        key: 'verify',
        state: 'warn',
        title: "AcoustID couldn't confirm it",
        detail: "Imported anyway, so it's waiting in Review.",
      };
    case 'force_imported':
      return {
        key: 'verify',
        state: 'warn',
        title: 'Force-imported',
        detail: 'Kept as the best available copy after repeated mismatches.',
      };
    default: {
      const result = dl.acoustid_result || detail?.acoustid_result;
      // no verdict at all means acoustid is off for this install. no step.
      return result
        ? { key: 'verify', state: 'done', title: 'Checked', detail: `AcoustID: ${result}` }
        : null;
    }
  }
}

/**
 * What happened to one music download, step by step.
 *
 * Built only from what the row (and the fetched detail, for finished live
 * tasks) actually carries. A failure stops the list at the step that broke,
 * so the last line is always the thing to read.
 */
export function musicJourney(
  dl: AdlDownload,
  detail: AdlTaskDetail | null,
  now: number,
): JourneyStep[] {
  const source = sourceOf(dl, detail);
  const changed = parseStamp(dl.timestamp);
  const at = stepTime(changed, now);

  if (dl.status === 'skipped' || dl.status === 'already_owned') {
    return [
      {
        key: 'owned',
        state: 'done',
        title: 'Already in your library',
        detail: 'Nothing to download',
        time: at,
      },
    ];
  }
  if (dl.status === 'cancelled') {
    return [{ key: 'cancel', state: 'fail', title: 'Cancelled', time: at }];
  }

  const cls = statusClass(dl.status);
  if (dl.status === 'importing' || dl.status === 'staged') {
    // audiobooks and release downloads: the file is here, it's being filed
    const held = dl.live_detail?.held_reason;
    return [
      {
        key: 'search',
        state: 'done',
        title: source ? `Found on ${source}` : 'Found',
        detail: dl.live_detail?.release_title,
      },
      { key: 'download', state: 'done', title: 'Downloaded' },
      held
        ? { key: 'import', state: 'warn', title: 'Import held', detail: held, time: at }
        : {
            key: 'import',
            state: 'now',
            title: dl.status === 'staged' ? 'Matching tracks' : 'Importing',
            time: at,
          },
    ];
  }
  if (cls === 'queued') {
    return [
      {
        key: 'search',
        state: 'now',
        title: 'Waiting its turn',
        detail: 'Starts when a download slot frees up',
      },
      { key: 'download', state: 'todo', title: 'Download' },
      { key: 'import', state: 'todo', title: 'Import to library' },
    ];
  }
  if (dl.status === 'searching') {
    return [
      { key: 'search', state: 'now', title: 'Searching', detail: searchLine(dl), time: at },
      { key: 'download', state: 'todo', title: 'Download' },
      { key: 'import', state: 'todo', title: 'Import to library' },
    ];
  }
  if (dl.status === 'not_found') {
    const counts = detail?.decision?.rejected_counts;
    const why = counts && Object.keys(counts).length ? `Rejected: ${rejectionSummary(counts)}` : '';
    return [
      {
        key: 'search',
        state: 'fail',
        title: 'Nothing matched',
        detail:
          sentences([dl.error || detail?.reason || '', why]) || 'No source had a file that fit.',
        time: at,
      },
    ];
  }

  const found: JourneyStep = {
    key: 'search',
    state: 'done',
    title: source ? `Found on ${source}` : 'Found',
    detail: foundLine(dl, detail) || undefined,
  };

  if (dl.status === 'downloading') {
    return [
      found,
      {
        key: 'download',
        state: 'now',
        title: 'Downloading',
        detail: downloadingLine(dl),
        time: at,
      },
      { key: 'verify', state: 'todo', title: 'Verify' },
      { key: 'import', state: 'todo', title: 'Import to library' },
    ];
  }
  if (dl.status === 'post_processing') {
    return [
      found,
      { key: 'download', state: 'done', title: 'Downloaded' },
      {
        key: 'verify',
        state: 'now',
        title: 'Checking the file',
        detail: 'Verifying, tagging and moving it into place',
        time: at,
      },
    ];
  }
  if (cls === 'failed') {
    const reason = dl.error || detail?.reason || '';
    const quarantined =
      detail?.status_kind === 'quarantined' ||
      QUARANTINE_WORDS.some((w) => reason.toLowerCase().includes(w));
    if (quarantined) {
      return [
        found,
        { key: 'download', state: 'done', title: 'Downloaded' },
        {
          key: 'verify',
          state: 'fail',
          title: 'Quarantined',
          detail: reason || 'The file failed its checks',
          time: at,
        },
      ];
    }
    return [
      found,
      {
        key: 'download',
        state: 'fail',
        title: 'Download failed',
        detail: reason || undefined,
        time: at,
      },
    ];
  }

  // completed
  const quality = dl.quality || detail?.quality || '';
  const steps: JourneyStep[] = [
    {
      key: 'download',
      state: 'done',
      title: source ? `Downloaded from ${source}` : 'Downloaded',
      detail: formatTag(quality) || undefined,
    },
  ];
  const verify = verifyStep(dl, detail);
  if (verify) steps.push(verify);
  const imported = dl.is_persistent_history ? parseStamp(dl.created_at) : changed;
  steps.push({
    key: 'import',
    state: 'done',
    title: dl.event_type === 'podcast' ? 'Saved' : 'Imported',
    detail: dl.event_type === 'podcast' ? undefined : 'Tagged and placed in your library',
    time: stepTime(imported, now),
  });
  return steps;
}

// ── video ─────────────────────────────────────────────────────────────────

function jsonCount(value: string | unknown[] | null | undefined): number {
  if (Array.isArray(value)) return value.length;
  if (!value) return 0;
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.length : 0;
  } catch {
    return 0;
  }
}

/** how many backup releases the monitor can still fall back on. */
export function vdlBackups(d: VdlDownload): number {
  return jsonCount(d.candidates);
}

/** "0.7 of 2.1 GB · 309 KB/s · 1h 6m left". */
export function vdlTransferLine(d: VdlDownload): string {
  const pct = Math.max(0, Math.min(100, Number(d.progress) || 0));
  const bits: string[] = [];
  if (d.size_bytes) {
    const got = (d.size_bytes * pct) / 100;
    bits.push(
      got > 0 ? `${vdlFmtSize(got)} of ${vdlFmtSize(d.size_bytes)}` : vdlFmtSize(d.size_bytes),
    );
  }
  if (d.speed_bps) bits.push(vdlFmtSpeed(d.speed_bps));
  if (d.eta_seconds && d.eta_seconds > 0) bits.push(`${formatDuration(d.eta_seconds)} left`);
  return bits.join(' · ');
}

/** What happened to one video download, step by step. Same rules as music. */
export function videoJourney(d: VdlDownload, now: number): JourneyStep[] {
  const ctx = vdlParseCtx(d);
  const grabbedAt = stepTime(parseStamp(d.created_at), now);
  const endedAt = stepTime(parseStamp(d.completed_at), now);
  const backups = vdlBackups(d);
  const tried = jsonCount(d.tried_files);

  const grabDetail = [
    d.release_title,
    backups ? `${backups} backup release${backups === 1 ? '' : 's'} lined up` : '',
  ]
    .filter(Boolean)
    .join(' · ');
  const grabbed: JourneyStep = {
    key: 'grab',
    state: 'done',
    title: d.username ? `Grabbed from ${d.username}` : 'Grabbed',
    detail: grabDetail || undefined,
    time: grabbedAt,
  };

  switch (d.status) {
    case 'queued':
      return [
        { key: 'grab', state: 'now', title: 'Waiting for a free slot', time: grabbedAt },
        { key: 'download', state: 'todo', title: 'Download' },
        { key: 'import', state: 'todo', title: 'Import to library' },
      ];
    case 'searching':
      return [
        {
          key: 'grab',
          state: 'now',
          title: 'Looking for another release',
          detail: tried ? `${tried} tried so far` : undefined,
          time: grabbedAt,
        },
        { key: 'download', state: 'todo', title: 'Download' },
        { key: 'import', state: 'todo', title: 'Import to library' },
      ];
    case 'downloading': {
      const moved = parseStamp(d.progress_at);
      const stale = moved !== null && now - moved > 10 * 60_000;
      return [
        grabbed,
        {
          key: 'download',
          state: 'now',
          title: stale ? 'Downloading, but stuck' : 'Downloading',
          detail:
            [vdlTransferLine(d), stale ? `no progress since ${clockTime(moved)}` : '']
              .filter(Boolean)
              .join(' · ') || undefined,
        },
        { key: 'import', state: 'todo', title: 'Import to library' },
      ];
    }
    case 'importing': {
      const pct = Math.round(Number(d.import_progress) || 0);
      const what =
        d.import_phase === 'merging'
          ? 'Merging video and audio'
          : d.import_phase === 'moving' && pct > 0
            ? `Moving into your library, ${pct}%`
            : 'Moving into your library';
      return [
        grabbed,
        { key: 'download', state: 'done', title: 'Downloaded' },
        { key: 'import', state: 'now', title: what },
      ];
    }
    case 'completed':
      return [
        grabbed,
        {
          key: 'download',
          state: 'done',
          title: 'Downloaded',
          detail: d.size_bytes ? vdlFmtSize(d.size_bytes) : undefined,
        },
        {
          key: 'import',
          state: d.upgrade_watch ? 'warn' : 'done',
          title: 'In your library',
          detail: d.upgrade_watch
            ? 'Below your quality cutoff, still watching for a better copy'
            : ctx.season != null
              ? 'Filed with the rest of the season'
              : undefined,
          time: endedAt,
        },
      ];
    case 'import_failed':
      return [
        grabbed,
        { key: 'download', state: 'done', title: 'Downloaded' },
        {
          key: 'import',
          state: 'fail',
          title: "Couldn't import it",
          detail: d.error || undefined,
          time: endedAt,
        },
      ];
    case 'cancelled':
      return [grabbed, { key: 'download', state: 'fail', title: 'Cancelled', time: endedAt }];
    default:
      return [
        grabbed,
        {
          key: 'download',
          state: 'fail',
          title: 'Download failed',
          detail:
            [d.error, Number(d.attempts) > 1 ? `${d.attempts} attempts` : '']
              .filter(Boolean)
              .join(' · ') || undefined,
          time: endedAt,
        },
      ];
  }
}
