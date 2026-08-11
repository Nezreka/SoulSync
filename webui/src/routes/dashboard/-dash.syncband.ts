/**
 * Pure core for the merged Sync band — the Auto Sync schedule rows and the
 * Recent Syncs history folded into ONE list (Boulder: the two cards were the
 * same system explained twice). One row per playlist:
 *
 * - scheduled rows come from the Auto-Sync board state (-dash.autosync),
 *   already carrying cadence / countdown / coverage / live pipeline state,
 *   and adopt their LATEST sync-history run for the result chips;
 * - history entries that match no schedule become 'manual' rows (album
 *   downloads, one-off syncs), deduped to their newest run.
 *
 * History ↔ schedule linking is by display name (case-insensitive), the only
 * key sync_history shares with mirrored playlists — the mirror pushes under
 * its display_name, so the names agree by construction.
 */

import type { AutoSyncCardRow } from './-dash.autosync';
import type { SyncCardView } from './-dash.library';

export interface SyncBandRow {
  rowKey: string;
  kind: 'scheduled' | 'manual';
  name: string;
  /** Schedule side — null on manual rows. */
  schedule: AutoSyncCardRow | null;
  /** The newest sync-history run for this playlist — null when a scheduled
   *  playlist has no recorded runs yet. */
  last: SyncCardView | null;
  /** ONE completeness number per row (Boulder: matched-vs-owned side by
   *  side read as two contradictory answers to the same question). The
   *  last run's matched count wins when a run exists — the sync engine's
   *  full fuzzy matcher against the real server, timestamped by the ago
   *  chip — and the db owned join only backs never-run schedules (it lags
   *  fresh downloads until the next library scan and undercounts sources
   *  whose ids aren't Spotify's). */
  coverage: { inLibrary: number; total: number; pct: number } | null;
  /** Art preference: the mirrored playlist's own cover, else the run's thumb. */
  thumbUrl: string | null;
  logo: string | null;
  sourceKey: string;
  /** Manual rows label their source from the history entry. */
  sourceLabel: string;
}

function coverageOf(last: SyncCardView | null, schedule: AutoSyncCardRow | null) {
  if (last && last.total > 0) {
    return { inLibrary: last.found, total: last.total, pct: last.pct };
  }
  return schedule?.coverage ?? null;
}

const norm = (s: string) => s.trim().toLowerCase();

export function syncBandRows(
  scheduleRows: AutoSyncCardRow[],
  historyViews: SyncCardView[],
): SyncBandRow[] {
  const used = new Set<number>();
  // rowKey -> the claimed history index. History arrives newest-first, so
  // this doubles as the recency rank the final sort uses.
  const recency = new Map<string, number>();

  const rows: SyncBandRow[] = scheduleRows.map((sr) => {
    // History arrives newest-first — the first unclaimed name match is this
    // schedule's latest run. Claiming stops a twin schedule (hourly+weekly on
    // one playlist) from double-consuming, and keeps older runs out of the
    // manual bucket via the seen-set below.
    const idx = historyViews.findIndex((v, i) => !used.has(i) && norm(v.name) === norm(sr.name));
    let last: SyncCardView | null = null;
    const rowKey = `s-${sr.key}-${sr.automationId}`;
    if (idx >= 0) {
      used.add(idx);
      last = historyViews[idx];
      recency.set(rowKey, idx);
    }
    return {
      rowKey,
      kind: 'scheduled' as const,
      name: sr.name,
      schedule: sr,
      last,
      coverage: coverageOf(last, sr),
      thumbUrl: sr.imageUrl || last?.thumbUrl || null,
      logo: sr.logo,
      sourceKey: sr.sourceKey,
      sourceLabel: sr.source,
    };
  });

  const seen = new Set(rows.map((r) => norm(r.name)));
  historyViews.forEach((v, i) => {
    if (used.has(i)) return;
    const key = norm(v.name);
    // Older runs of a listed playlist (scheduled or already-added manual)
    // collapse into it — the band shows latest state, the board's history
    // tab keeps the full log.
    if (seen.has(key)) return;
    seen.add(key);
    const rowKey = `m-${v.id ?? `i${i}`}`;
    recency.set(rowKey, i);
    rows.push({
      rowKey,
      kind: 'manual',
      name: v.name,
      schedule: null,
      last: v,
      coverage: coverageOf(v, null),
      thumbUrl: v.thumbUrl,
      logo: null,
      sourceKey: '',
      sourceLabel: v.sourceLabel,
    });
  });

  // Running rows lead; then EVERYTHING by newest run first (Boulder: the
  // newest syncs belong at the top) — scheduled and manual interleaved by
  // their claimed history index; schedules that never ran trail in the
  // board's urgency order. A stable sort keeps within-group order intact.
  const rank = (r: SyncBandRow) => (r.schedule?.running ? -1 : (recency.get(r.rowKey) ?? Infinity));
  return rows
    .map((r, i) => ({ r, i }))
    .sort((a, b) => rank(a.r) - rank(b.r) || a.i - b.i)
    .map(({ r }) => r);
}
