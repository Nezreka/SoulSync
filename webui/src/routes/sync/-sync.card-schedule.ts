/**
 * A playlist's sync interval, on its card.
 *
 * The Auto-Sync modal expresses one integer per playlist with ten preset lanes
 * (plus one more for every custom interval anyone has ever created), a
 * filterable sidebar with source groups and expandable variant sub-groups,
 * drop highlighting, and a bulk popover with its own custom-interval expander.
 * A dropdown on the card sets the same value with none of that.
 *
 * The board is not removed — it stays as a VIEW, which is what it is good at:
 * seeing everything on one cadence at a glance. What changes is that it is no
 * longer the only way to set the value.
 *
 * ONE ENDPOINT, LAZILY. `useAutoSync` loads five endpoints and only when the
 * modal opens; cards need intervals on every visit, and paying five requests
 * per page load to render a dropdown would undo the page's own speed. So this
 * fetches `/api/automations` alone and reuses `buildAutoSyncScheduleState` to
 * read it — the same pure builder the board uses, so the card and the board can
 * never disagree about what a playlist is scheduled for.
 */

import { useCallback, useEffect, useState } from 'react';

import type { AutoSyncHistoryEntry, MirroredRow } from './-sync.autosync';

import {
  AUTO_SYNC_BUCKETS,
  autoSyncCanSchedulePlaylist,
  autoSyncIntervalLabel,
  autoSyncNextRunLabel,
  autoSyncSchedulePayload,
  autoSyncTriggerForHours,
  autoSyncWeeklyLabel,
  autoSyncWeeklyTrigger,
  buildAutoSyncScheduleState,
  detectBrowserTimezone,
} from './-sync.autosync';
import {
  createAutomation,
  deleteAutomation,
  fetchAutomations,
  fetchPipelineHistory,
  updateAutomation,
} from './-sync.api';

/** What a card knows about its own schedule. */
export interface CardSchedule {
  /** Hours between runs, or null when it runs on a weekly day instead. */
  hours: number | null;
  /** The automation backing it, needed to update or delete. */
  automationId: number | string;
  /** True when the schedule is a weekly day rather than an interval. */
  weekly: boolean;
  /** The weekly trigger itself, so the editor can open on what is set. */
  weeklyConfig?: { days: string[]; time: string; tz: string };
  /** When it next runs, as the automation reports it. */
  nextRun?: string | null;
}

export type CardScheduleMap = Readonly<Record<string, CardSchedule>>;

/** The options a card's dropdown offers, plus its "off" entry. */
export const CARD_SCHEDULE_OPTIONS: readonly { value: string; label: string }[] = [
  { value: '', label: 'Not scheduled' },
  ...AUTO_SYNC_BUCKETS.map((h) => ({ value: String(h), label: autoSyncIntervalLabel(h) })),
];

/**
 * Read every owned schedule out of an automations payload.
 *
 * Delegates to the board's own builder rather than re-deriving: the mapping
 * from an automation to "this playlist runs every N hours" has real subtlety in
 * it (ownership, trigger shape, id extraction) and two copies would drift.
 */
export function cardSchedulesFrom(automations: unknown[]): CardScheduleMap {
  const state = buildAutoSyncScheduleState([], (automations || []) as never[]);
  const out: Record<string, CardSchedule> = {};
  for (const [playlistId, entry] of Object.entries(state.playlistSchedules)) {
    out[playlistId] = {
      hours: entry.hours,
      automationId: entry.automation_id,
      weekly: false,
      nextRun: entry.next_run ?? null,
    };
  }
  for (const [playlistId, entry] of Object.entries(state.weeklySchedules)) {
    // A weekly schedule has no interval to show. The card says so rather than
    // pretending it is unscheduled, which would invite someone to overwrite a
    // weekly plan by picking an hourly one without realising.
    out[playlistId] = {
      hours: null,
      automationId: entry.automation_id,
      weekly: true,
      nextRun: entry.next_run ?? null,
      weeklyConfig: {
        days: Array.isArray(entry.days) ? [...entry.days] : [],
        // autoSyncWeeklyTrigger's own fallback, kept in step with it.
        time: entry.time || '09:00',
        tz: entry.tz || detectBrowserTimezone(),
      },
    };
  }
  return out;
}

/**
 * The label a card shows for its current schedule.
 *
 * `now` is optional so the cadence alone is still available; when it is given,
 * the NEXT RUN rides along. The board showed both ("Every 6 hours · next in
 * 3h") and the cadence on its own answers a different, weaker question — how
 * often, but not whether anything is about to happen.
 */
export function cardScheduleLabel(schedule: CardSchedule | undefined, now?: number): string {
  if (!schedule) return 'Not scheduled';
  const base = schedule.weekly
    ? 'Weekly'
    : schedule.hours
      ? autoSyncIntervalLabel(schedule.hours)
      : 'Not scheduled';
  if (now === undefined || base === 'Not scheduled') return base;
  const next = autoSyncNextRunLabel(schedule.nextRun, now);
  return next ? `${base} · ${next}` : base;
}

export interface CardScheduleController {
  schedules: CardScheduleMap;
  /**
   * Recent pipeline runs, for the repeated-failure signal the board's `!` / `⚠`
   * glyph carried. The card's ring reports the CURRENT run; this reports the
   * pattern — "the last three all failed" — which nothing else on the page says.
   */
  history: AutoSyncHistoryEntry[];
  /** Null until the first load lands — cards render no control before then. */
  loaded: boolean;
  /** Set an interval, or pass null to unschedule. */
  set: (row: MirroredRow, hours: number | null) => Promise<void>;
  /** Set a weekly cadence. Replaces an hourly one rather than sitting beside it. */
  setWeekly: (
    row: MirroredRow,
    weekly: { days: string[]; time?: string; tz?: string },
  ) => Promise<void>;
  /** Playlist ids currently being written, so a card can disable its select. */
  busy: ReadonlySet<string>;
}

export interface UseCardSchedulesOptions {
  toast?: (message: string, kind: string) => void;
}

export function useCardSchedules(options: UseCardSchedulesOptions = {}): CardScheduleController {
  const [schedules, setSchedules] = useState<CardScheduleMap>({});
  const [history, setHistory] = useState<AutoSyncHistoryEntry[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState<ReadonlySet<string>>(() => new Set());

  const load = useCallback(async () => {
    try {
      // In PARALLEL, so the health signal costs no added latency. The history
      // window is bounded (the endpoint caps at 100), which means a playlist
      // whose runs fall outside it simply shows no signal — the same as it
      // looked to anyone who never opened the Auto-Sync modal.
      const [res, historyRes] = await Promise.all([
        fetchAutomations(),
        fetchPipelineHistory(100).catch(() => null),
      ]);
      const data = (await res.json()) as { automations?: unknown[] } | unknown[];
      const rows = Array.isArray(data) ? data : (data.automations ?? []);
      setSchedules(cardSchedulesFrom(rows));

      if (historyRes) {
        const payload = (await historyRes.json()) as { history?: AutoSyncHistoryEntry[] };
        setHistory(Array.isArray(payload.history) ? payload.history : []);
      }
    } catch {
      // Best-effort: a card without its interval still works, and a failed
      // read must not stop the library rendering.
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const set = useCallback(
    async (row: MirroredRow, hours: number | null) => {
      // MirroredRow.id is optional on the type; a row without one cannot be
      // scheduled at all, and guessing an id would schedule the wrong playlist.
      if (row.id === undefined) return;
      const playlistId = String(row.id);
      const toast = options.toast ?? ((m: string, k: string) => window.showToast?.(m, k));

      if (hours !== null && !autoSyncCanSchedulePlaylist(row)) {
        toast('That playlist source cannot be refreshed by Auto-Sync.', 'info');
        return;
      }

      setBusy((prev) => new Set(prev).add(playlistId));
      try {
        const existing = schedules[playlistId];
        if (hours === null) {
          if (!existing) return;
          const res = await deleteAutomation(existing.automationId);
          if (!res.ok) throw new Error('Failed to unschedule');
          toast(`${row.name || 'Playlist'} is no longer scheduled`, 'success');
        } else {
          const payload = autoSyncSchedulePayload(row, row.id, {
            trigger_type: 'schedule',
            trigger_config: autoSyncTriggerForHours(hours),
          });
          // A weekly schedule is REPLACED rather than updated in place: the two
          // are different trigger shapes, and updating one into the other is
          // how you end up with a row that claims both.
          const res =
            existing && !existing.weekly
              ? await updateAutomation(existing.automationId, payload)
              : await createAutomation(payload);
          const data = (await res.json()) as { error?: string };
          if (!res.ok || data.error) throw new Error(data.error || 'Failed to save schedule');
          if (existing?.weekly) await deleteAutomation(existing.automationId);
          toast(`${row.name || 'Playlist'} syncs ${autoSyncIntervalLabel(hours)}`, 'success');
        }
        await load();
      } catch (err) {
        toast(`Error: ${err instanceof Error ? err.message : String(err)}`, 'error');
      } finally {
        setBusy((prev) => {
          const next = new Set(prev);
          next.delete(playlistId);
          return next;
        });
      }
    },
    [schedules, load, options.toast],
  );

  const setWeekly = useCallback(
    async (row: MirroredRow, weekly: { days: string[]; time?: string; tz?: string }) => {
      if (row.id === undefined) return;
      const playlistId = String(row.id);
      const toast = options.toast ?? ((m: string, k: string) => window.showToast?.(m, k));

      if (!autoSyncCanSchedulePlaylist(row)) {
        toast('That playlist source cannot be refreshed by Auto-Sync.', 'info');
        return;
      }
      const config = autoSyncWeeklyTrigger(weekly);
      // Refused here as well as in the editor, because a preset reaches this
      // without passing through the editor at all.
      if (!config.days.length) {
        toast('Pick at least one day for the weekly schedule.', 'error');
        return;
      }

      setBusy((prev) => new Set(prev).add(playlistId));
      try {
        const existing = schedules[playlistId];
        const payload = autoSyncSchedulePayload(row, row.id, {
          trigger_type: 'weekly_time',
          trigger_config: config,
        });
        // An HOURLY row is a different trigger shape, so it is replaced rather
        // than updated into a weekly one — updating across shapes is how you
        // end up with a row that claims both.
        const res =
          existing?.weekly
            ? await updateAutomation(existing.automationId, payload)
            : await createAutomation(payload);
        const data = (await res.json()) as { error?: string };
        if (!res.ok || data.error) throw new Error(data.error || 'Failed to save schedule');
        if (existing && !existing.weekly) await deleteAutomation(existing.automationId);
        toast(`${row.name || 'Playlist'} syncs ${autoSyncWeeklyLabel(config)}`, 'success');
        await load();
      } catch (err) {
        toast(`Error: ${err instanceof Error ? err.message : String(err)}`, 'error');
      } finally {
        setBusy((prev) => {
          const next = new Set(prev);
          next.delete(playlistId);
          return next;
        });
      }
    },
    [schedules, load, options.toast],
  );

  return { schedules, history, loaded, set, setWeekly, busy };
}
