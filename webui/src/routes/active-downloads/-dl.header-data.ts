/**
 * What the shared header shows, worked out from each side's own poll: the
 * count on every filter chip, the live strip, and the quiet line. Pure, so
 * every rule here is a plain unit test.
 */

import type { AdlBatch, AdlDownload } from './-adl.types';
import type { DlLive } from './-ui/dl-header';
import type { VdlDownload } from './-vdl.types';

import { formatSpeed, statusClass } from './-adl.helpers';
import { ADL_FILTER_STATUSES, ADL_FILTERS } from './-adl.types';
import { clockTime, dayKey, parseStamp } from './-dl.format';
import { quietLine } from './-ui/dl-header';
import {
  vdlCardTitle,
  vdlFmtEta,
  vdlFmtSpeed,
  vdlIsActive,
  vdlIsFail,
  vdlMatchesFilter,
} from './-vdl.helpers';
import { VDL_FILTERS } from './-vdl.types';

/** quiet line from a list of finish stamps (ms). */
function quietFrom(finished: number[], now: number): string {
  const today = finished.filter((ms) => dayKey(ms) === dayKey(now));
  const last = today.length ? Math.max(...today) : null;
  return quietLine(today.length, clockTime(last));
}

// ── music ─────────────────────────────────────────────────────────────────

/** every chip's count: the rows that chip would show. */
export function musicChipCounts(rows: AdlDownload[]): Record<string, number> {
  const out: Record<string, number> = { all: rows.length };
  for (const f of ADL_FILTERS) {
    if (f.value === 'all') continue;
    const statuses = ADL_FILTER_STATUSES[f.value] ?? [];
    out[f.value] = rows.filter((dl) => statuses.includes(dl.status)).length;
  }
  return out;
}

/**
 * The music live strip, or null when nothing moves.
 *
 * The done/active/queued/failed bar counts the live BATCHES, not the list:
 * the list carries the history tail too, and "50 done, 1 active" for a
 * one-track grab is a lie about this session.
 */
export function musicLive(
  rows: AdlDownload[],
  liveBatches: AdlBatch[],
  speedBps: number,
  etaText: string,
): DlLive | null {
  const moving = rows.filter((dl) => statusClass(dl.status) === 'active');
  if (!moving.length) return null;
  const downloading = rows.filter((dl) => dl.status === 'downloading');
  const lead = downloading[0] ?? moving[0];
  const sum = (pick: (b: AdlBatch) => number) =>
    liveBatches.reduce((n, b) => n + (pick(b) || 0), 0);
  const fromBatches = liveBatches.length > 0;
  const live = rows.filter((dl) => !dl.is_persistent_history);
  return {
    speedText: formatSpeed(speedBps),
    speedBps,
    downloading: downloading.length,
    done: fromBatches
      ? sum((b) => b.completed)
      : live.filter((dl) => statusClass(dl.status) === 'completed').length,
    active: fromBatches ? sum((b) => b.active) : moving.length,
    queued: fromBatches
      ? sum((b) => b.queued)
      : live.filter((dl) => statusClass(dl.status) === 'queued').length,
    failed: fromBatches
      ? sum((b) => b.failed)
      : live.filter((dl) => statusClass(dl.status) === 'failed').length,
    nowTitle: lead.title || 'Unknown Track',
    nowSub: lead.artist || '',
    nowMore: Math.max(0, moving.length - 1),
    etaText,
  };
}

export function musicQuiet(rows: AdlDownload[], now: number): string {
  const finished = rows
    .filter((dl) => dl.status === 'completed')
    .map((dl) => (dl.is_persistent_history ? parseStamp(dl.created_at) : parseStamp(dl.timestamp)))
    .filter((ms): ms is number => ms !== null);
  return quietFrom(finished, now);
}

// ── video ─────────────────────────────────────────────────────────────────

export function videoChipCounts(rows: VdlDownload[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const f of VDL_FILTERS)
    out[f.value] = rows.filter((d) => vdlMatchesFilter(d.status, f.value)).length;
  return out;
}

export function videoLive(rows: VdlDownload[], speedBps: number): DlLive | null {
  const moving = rows.filter((d) => vdlIsActive(d.status));
  if (!moving.length) return null;
  const downloading = rows.filter((d) => d.status === 'downloading');
  const lead = downloading[0] ?? moving[0];
  const eta = Math.max(0, ...downloading.map((d) => Number(d.eta_seconds) || 0));
  return {
    speedText: vdlFmtSpeed(speedBps),
    speedBps,
    downloading: downloading.length,
    done: rows.filter((d) => d.status === 'completed').length,
    active: moving.filter((d) => d.status !== 'queued').length,
    queued: rows.filter((d) => d.status === 'queued').length,
    failed: rows.filter((d) => vdlIsFail(d.status)).length,
    nowTitle: vdlCardTitle(lead).replace(/\s{2,}/g, ' '),
    nowSub: lead.username || '',
    nowMore: Math.max(0, moving.length - 1),
    // the slowest one decides when the whole lot is done
    etaText: eta ? vdlFmtEta(eta).replace(/^~/, 'about ') : '',
  };
}

export function videoQuiet(rows: VdlDownload[], now: number): string {
  const finished = rows
    .filter((d) => d.status === 'completed')
    .map((d) => parseStamp(d.completed_at))
    .filter((ms): ms is number => ms !== null);
  return quietFrom(finished, now);
}
