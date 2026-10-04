/**
 * The Overview's data model — one hook that joins the mirrored playlist rows
 * with the Auto-Sync schedule state.
 *
 * Everything here already exists: `fetchMirroredPlaylists` and
 * `fetchAutomations` are the same endpoints `useAutoSync` reads, and the join
 * is `cardSchedulesFrom`. This hook is deliberately separate from
 * `useAutoSync` because that hook owns the board modal's polling, history and
 * drag state — the Overview needs a read-only snapshot, not the board.
 */

import { useCallback, useEffect, useState } from 'react';

import type { MirroredPlaylistRow } from '../../-sync.mirrored';

import { fetchAutomations, fetchMirroredPlaylists } from '../../-sync.api';
import { autoSyncParseUTC, type AutomationRow } from '../../-sync.autosync';
import { cardSchedulesFrom } from '../../-sync.card-schedule';
import { libraryDiscovered, libraryIsRunning, libraryNeedsAttention } from '../../-sync.library';

/** A playlist with a next run, for the timeline. */
export interface OverviewTimelineEntry {
  row: MirroredPlaylistRow;
  /** Epoch ms of the next scheduled run. */
  nextRunMs: number;
  /** "Every 1 day" / "Weekly" — the cadence without the next-run suffix. */
  cadenceLabel: string;
}

export interface OverviewModel {
  loading: boolean;
  error: string | null;
  now: number;
  /** Next runs within the 48h window, ascending. */
  scheduled: OverviewTimelineEntry[];
  /** Scheduled runs beyond the 48h window. */
  scheduledLater: number;
  /** Rows with no schedule at all. */
  unscheduled: MirroredPlaylistRow[];
  /** Rows whose pipeline is running right now. */
  running: MirroredPlaylistRow[];
  /** Rows needing the user, excluding running ones. */
  attention: MirroredPlaylistRow[];
  /** Rows with undiscovered tracks (the old "Discovered" filter). */
  discovered: MirroredPlaylistRow[];
  totalCount: number;
  scheduledCount: number;
  /** One quiet line for the activity strip, derived from rows — no extra fetch. */
  summary: string;
  refresh: () => void;
}

const WINDOW_MS = 48 * 3600 * 1000;

function cadenceLabelFor(hours: number | null, weekly: boolean): string {
  if (weekly) return 'Weekly';
  if (hours == null) return 'Not scheduled';
  if (hours < 24) return `Every ${hours} hour${hours === 1 ? '' : 's'}`;
  const days = hours / 24;
  return `Every ${days} day${days === 1 ? '' : 's'}`;
}

export function useOverviewModel(active: boolean): OverviewModel {
  const [rows, setRows] = useState<MirroredPlaylistRow[]>([]);
  const [schedules, setSchedules] = useState<ReturnType<typeof cardSchedulesFrom>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(async () => {
    try {
      const [playlistRows, automationRes] = await Promise.all([
        fetchMirroredPlaylists(),
        fetchAutomations(),
      ]);
      const rows = playlistRows as unknown as MirroredPlaylistRow[];
      let automations: AutomationRow[] = [];
      if (automationRes.ok) {
        const data = (await automationRes.json()) as unknown;
        // Same tolerant parsing as useCardSchedules: the endpoint returns
        // either a raw array or { automations: [...] }.
        automations = (
          Array.isArray(data) ? data : ((data as { automations?: unknown }).automations ?? [])
        ) as AutomationRow[];
      }
      setRows(rows);
      setSchedules(cardSchedulesFrom(automations));
      setNow(Date.now());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!active) return;
    setLoading(true);
    void load();
  }, [active, load]);

  // "in 2h"-style labels drift on a long-open tab. A quiet 60s tick keeps
  // them honest without refetching anything.
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(timer);
  }, [active]);

  const scheduled: OverviewTimelineEntry[] = [];
  let scheduledLater = 0;
  const unscheduled: MirroredPlaylistRow[] = [];
  for (const row of rows) {
    const s = schedules[String(row.id)];
    if (s?.nextRun) {
      const nextRunMs = autoSyncParseUTC(s.nextRun);
      const entry: OverviewTimelineEntry = {
        row,
        nextRunMs,
        cadenceLabel: cadenceLabelFor(s.hours, s.weekly),
      };
      if (nextRunMs <= now + WINDOW_MS) scheduled.push(entry);
      else scheduledLater += 1;
    } else if (!libraryIsRunning(row)) {
      // A run in flight is not "unscheduled" — it is being worked on right
      // now, and it already has a home in Now Syncing.
      unscheduled.push(row);
    }
  }
  scheduled.sort((a, b) => a.nextRunMs - b.nextRunMs);

  const running = rows.filter(libraryIsRunning);
  const runningIds = new Set(running.map((r) => r.id));
  const attention = rows.filter((r) => !runningIds.has(r.id) && libraryNeedsAttention(r));
  const discovered = rows.filter((r) => libraryDiscovered(r) > 0);

  const summary = `${rows.length} playlists · ${scheduled.length + scheduledLater} scheduled · ${attention.length} need${attention.length === 1 ? 's' : ''} attention`;

  return {
    loading,
    error,
    now,
    scheduled,
    scheduledLater,
    unscheduled,
    running,
    attention,
    discovered,
    totalCount: rows.length,
    scheduledCount: scheduled.length + scheduledLater,
    summary,
    refresh: load,
  };
}
